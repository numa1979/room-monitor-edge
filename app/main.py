import importlib
import inspect
import pkgutil
import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from ultralytics import YOLO


def _allowlist_serialization_classes() -> None:
    try:
        from torch.serialization import add_safe_globals
        import ultralytics.nn as nn_pkg
        import torch.nn as torch_nn

        classes = []
        for pkg in (nn_pkg, torch_nn):
            for _, module_name, _ in pkgutil.walk_packages(
                pkg.__path__, pkg.__name__ + "."
            ):
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
                for attr in vars(module).values():
                    if inspect.isclass(attr):
                        classes.append(attr)
        add_safe_globals(classes)
    except Exception:
        pass


_allowlist_serialization_classes()

from .camera import CameraStreamer
from .tracking import CentroidTracker

APP_TITLE = "Jetson Watchdog"
APP_DESCRIPTION = "Prototype UI for the Jetson room-monitor project."

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION)

static_dir = BASE_DIR / "static"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

if static_dir.exists():
    app.mount(
        "/static", StaticFiles(directory=str(static_dir), html=True), name="static"
    )

camera_streamer = CameraStreamer()
logger = logging.getLogger("jetson_watchdog")
if not logger.handlers:
    logger.setLevel(logging.INFO)
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
try:
    yolo_model: Optional[YOLO] = YOLO(YOLO_MODEL_PATH)
    yolo_error: Optional[str] = None
except Exception as exc:  # pragma: no cover
    yolo_model = None
    yolo_error = f"YOLO model load failed: {exc}"

tracker = CentroidTracker()
detections_lock = threading.Lock()
targets_lock = threading.Lock()
latest_detections: Dict[str, object] = {
    "width": 0,
    "height": 0,
    "objects": [],
    "timestamp": 0.0,
}
selected_tracks: Dict[int, int] = {}
MAX_TARGETS = 4
MAX_DETECTIONS = int(os.getenv("MAX_DETECTIONS", "6"))
yolo_person_only = os.getenv("YOLO_PERSON_ONLY", "1") == "1"
yolo_class_names: Dict[int, str] = {}
allowed_class_ids = {0} if yolo_person_only else None
SLOT_KEEP_SECONDS = int(os.getenv("SLOT_KEEP_SECONDS", "600"))
SLOT_RECALL_IOU = float(os.getenv("SLOT_RECALL_IOU", "0.3"))
slot_memory: Dict[int, Dict[str, object]] = {}
if yolo_model is not None:
    try:
        yolo_class_names = yolo_model.model.names  # type: ignore[attr-defined]
    except Exception:
        yolo_class_names = getattr(yolo_model, "names", {})


class SelectionRequest(BaseModel):
    x: float
    y: float


def _detections_snapshot() -> Dict[str, object]:
    with detections_lock:
        return {
            "width": latest_detections["width"],
            "height": latest_detections["height"],
            "objects": list(latest_detections["objects"]),
            "timestamp": latest_detections["timestamp"],
        }


def _selected_snapshot() -> List[Dict[str, int]]:
    with targets_lock:
        return [
            {"track_id": tid, "slot": slot} for tid, slot in selected_tracks.items()
        ]


def _prune_selected(active_track_ids: List[int]) -> None:
    active_set = set(active_track_ids)
    removed_slots: List[int] = []
    with targets_lock:
        for tid in list(selected_tracks.keys()):
            if tid not in active_set:
                removed_slots.append(selected_tracks.pop(tid))
    if removed_slots:
        logger.info("Pruned inactive targets, freed slots %s", removed_slots)


def _next_available_slot() -> Optional[int]:
    used = set(selected_tracks.values())
    for slot in range(1, MAX_TARGETS + 1):
        if slot not in used:
            return slot
    return None


def _find_track_by_point(norm_x: float, norm_y: float) -> Optional[int]:
    snapshot = _detections_snapshot()
    width = snapshot["width"] or 1
    height = snapshot["height"] or 1
    x = norm_x * width
    y = norm_y * height
    best_track = None
    best_conf = -1.0
    for obj in snapshot["objects"]:
        bbox = obj["bbox"]
        if bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
            if obj["conf"] > best_conf:
                best_conf = obj["conf"]
                best_track = obj["id"]
    return best_track


def _toggle_track(track_id: int) -> Dict[str, Optional[int]]:
    with targets_lock:
        if track_id in selected_tracks:
            slot = selected_tracks.pop(track_id)
            slot_memory.pop(slot, None)
            logger.info("Deselected track %s (slot %s)", track_id, slot)
            return {"status": "removed", "slot": slot}
        if len(selected_tracks) >= MAX_TARGETS:
            return {"status": "full", "slot": None}
        slot = _next_available_slot()
        if slot is None:
            return {"status": "full", "slot": None}
        selected_tracks[track_id] = slot
        slot_memory.setdefault(slot, {"bbox": None, "timestamp": 0.0})
        logger.info("Selected track %s as slot %s", track_id, slot)
        return {"status": "added", "slot": slot}


def _bbox_iou(a: List[float], b: List[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / (area_a + area_b - inter + 1e-6)


def _expire_slot_memory() -> None:
    now = time.time()
    for slot in list(slot_memory.keys()):
        if slot in selected_tracks.values():
            continue
        if now - slot_memory[slot].get("timestamp", 0.0) > SLOT_KEEP_SECONDS:
            slot_memory.pop(slot, None)


def _auto_reassign(tracked_objects: List[Dict[str, object]]) -> None:
    now = time.time()
    with targets_lock:
        active_ids = set(selected_tracks.keys())
        slots_in_use = set(selected_tracks.values())
    for obj in tracked_objects:
        track_id = obj["id"]
        if track_id in active_ids:
            continue
        best_slot = None
        best_iou = 0.0
        for slot, info in slot_memory.items():
            if slot in slots_in_use:
                continue
            last_ts = info.get("timestamp", 0.0)
            bbox_mem = info.get("bbox")
            if bbox_mem is None or now - last_ts > SLOT_KEEP_SECONDS:
                continue
            iou = _bbox_iou(obj["bbox"], bbox_mem)
            if iou > best_iou:
                best_iou = iou
                best_slot = slot
        if best_slot is not None and best_iou >= SLOT_RECALL_IOU:
            with targets_lock:
                if best_slot not in selected_tracks.values():
                    selected_tracks[track_id] = best_slot
                    slots_in_use.add(best_slot)
                    logger.info(
                        "Recalled slot %s for track %s (iou %.2f)",
                        best_slot,
                        track_id,
                        best_iou,
                    )


async def mjpeg_frame_generator() -> AsyncGenerator[bytes, None]:
    while True:
        frame = camera_streamer.latest_frame()
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.1)


async def inference_frame_generator() -> AsyncGenerator[bytes, None]:
    global yolo_error
    while True:
        try:
            frame = camera_streamer.latest_frame_array()
            if frame is None:
                await asyncio.sleep(0.1)
                continue

            overlay = frame.copy()
            detections_input: List[Dict[str, float]] = []
            if yolo_model is not None:
                try:
                    results = yolo_model.predict(frame, verbose=False)
                    if results:
                        first = results[0]
                        if first.boxes is not None and first.boxes.xyxy is not None:
                            boxes = first.boxes.xyxy.cpu().numpy()
                            classes = first.boxes.cls.cpu().numpy().astype(int)
                            scores = first.boxes.conf.cpu().numpy()
                            for i in range(len(boxes)):
                                det_cls = int(classes[i])
                                if (
                                    allowed_class_ids
                                    and det_cls not in allowed_class_ids
                                ):
                                    continue
                                detections_input.append(
                                    {
                                        "bbox": boxes[i],
                                        "cls": det_cls,
                                        "conf": float(scores[i]),
                                    }
                                )
                    if detections_input:
                        detections_input.sort(key=lambda d: d["conf"], reverse=True)
                        detections_input = detections_input[:MAX_DETECTIONS]
                except Exception as exc:  # pragma: no cover
                    overlay = frame.copy()
                    yolo_error = f"YOLO inference error: {exc}"
                    logger.exception("YOLO inference error")

            tracked_objects = tracker.update(detections_input)
            _auto_reassign(tracked_objects)
            active_ids = [obj["id"] for obj in tracked_objects]
            _prune_selected(active_ids)
            _expire_slot_memory()
            selected_snapshot = _selected_snapshot()
            selected_map = {
                item["track_id"]: item["slot"] for item in selected_snapshot
            }
            objects_payload = []
            for obj in tracked_objects:
                bbox = obj["bbox"]
                x1, y1, x2, y2 = bbox.astype(int)
                track_id = obj["id"]
                slot = selected_map.get(track_id)
                cls_name = yolo_class_names.get(obj["cls"], f"id:{obj['cls']}")
                if slot:
                    label = f"ID{slot} {cls_name} {obj['conf']:.2f}"
                else:
                    label = cls_name
                color = (0, 255, 0) if slot else (0, 0, 255)
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
                if slot:
                    cv2.putText(
                        overlay,
                        label,
                        (x1, max(20, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        color,
                        2,
                        cv2.LINE_AA,
                    )
                    slot_memory[slot] = {
                        "bbox": bbox.tolist(),
                        "timestamp": time.time(),
                    }
                objects_payload.append(
                    {
                        "id": track_id,
                        "bbox": bbox.tolist(),
                        "cls": obj["cls"],
                        "conf": obj["conf"],
                        "selected": bool(slot),
                        "slot": slot,
                    }
                )

            with detections_lock:
                latest_detections["width"] = overlay.shape[1]
                latest_detections["height"] = overlay.shape[0]
                latest_detections["objects"] = objects_payload
                latest_detections["timestamp"] = time.time()

            ok, buffer = cv2.imencode(".jpg", overlay)
            if not ok:
                yolo_error = "JPEG encode failed"
                logger.error("JPEG encode failed for inference overlay")
                await asyncio.sleep(0.05)
                continue

            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                + buffer.tobytes()
                + b"\r\n"
            )
            await asyncio.sleep(0)
        except Exception as exc:  # pragma: no cover
            yolo_error = f"inference stream error: {exc}"
            logger.exception("Inference stream error")
            await asyncio.sleep(0.5)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    now = datetime.now()
    camera_settings: Dict[str, object] = camera_streamer.current_settings()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_title": APP_TITLE,
            "app_description": APP_DESCRIPTION,
            "now": now,
            "camera_settings": camera_settings,
            "yolo_error": yolo_error,
        },
    )


@app.get("/health", response_class=JSONResponse)
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )


@app.get("/camera/stream")
async def camera_stream() -> StreamingResponse:
    return StreamingResponse(
        mjpeg_frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/camera/inference")
async def camera_inference_stream() -> StreamingResponse:
    return StreamingResponse(
        inference_frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/camera/settings", response_class=JSONResponse)
async def camera_settings() -> JSONResponse:
    return JSONResponse({"current": camera_streamer.current_settings()})


@app.get("/targets", response_class=JSONResponse)
async def get_targets() -> JSONResponse:
    return JSONResponse(
        {
            "selected": _selected_snapshot(),
            "detected": _detections_snapshot(),
        }
    )


@app.post("/targets/select", response_class=JSONResponse)
async def select_target(selection: SelectionRequest) -> JSONResponse:
    if not (0.0 <= selection.x <= 1.0 and 0.0 <= selection.y <= 1.0):
        raise HTTPException(
            status_code=400, detail="coordinates must be between 0 and 1"
        )
    track_id = _find_track_by_point(selection.x, selection.y)
    if track_id is None:
        return JSONResponse(
            {"status": "not_found", "selected": _selected_snapshot()}, status_code=200
        )
    result = _toggle_track(track_id)
    return JSONResponse(
        {
            "status": result["status"],
            "slot": result["slot"],
            "track_id": track_id,
            "selected": _selected_snapshot(),
        }
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    camera_streamer.shutdown()
