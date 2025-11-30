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

APP_TITLE = "見守り君 ver.1.0.0 Prototype"
APP_DESCRIPTION = "Jetson room-monitor prototype UI."

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
room_lock = threading.Lock()
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
YOLO_MIN_CONF = float(os.getenv("YOLO_MIN_CONF", "0.5"))
SLOT_RECALL_IOU = float(os.getenv("SLOT_RECALL_IOU", "0.3"))
SLOT_RECALL_CENTER = float(os.getenv("SLOT_RECALL_CENTER", "0.35"))
SLOT_RECALL_DESCRIPTOR = float(os.getenv("SLOT_RECALL_DESCRIPTOR", "0.6"))
slot_memory: Dict[int, Dict[str, object]] = {}
room_settings: Dict[str, object] = {
    "name": os.getenv("DEFAULT_ROOM_NAME", ""),
    "updated_at": time.time(),
}
if yolo_model is not None:
    try:
        yolo_class_names = yolo_model.model.names  # type: ignore[attr-defined]
    except Exception:
        yolo_class_names = getattr(yolo_model, "names", {})


class SelectionRequest(BaseModel):
    x: float
    y: float


class RoomSettingsRequest(BaseModel):
    name: str


def _detections_snapshot() -> Dict[str, object]:
    with detections_lock:
        return {
            "width": latest_detections["width"],
            "height": latest_detections["height"],
            "objects": list(latest_detections["objects"]),
            "timestamp": latest_detections["timestamp"],
        }


def _selected_snapshot() -> List[Dict[str, object]]:
    with targets_lock:
        snapshot: List[Dict[str, object]] = []
        for slot in sorted(slot_memory.keys()):
            info = slot_memory[slot]
            track_id = info.get("track_id")
            snapshot.append(
                {
                    "track_id": track_id,
                    "slot": slot,
                    "active": track_id in selected_tracks,
                    "last_seen": info.get("last_seen", 0.0),
                }
            )
        return snapshot


def _prune_selected(active_track_ids: List[int]) -> None:
    active_set = set(active_track_ids)
    inactivated_slots: List[int] = []
    with targets_lock:
        for tid, slot in list(selected_tracks.items()):
            if tid not in active_set:
                selected_tracks.pop(tid)
                info = slot_memory.get(slot)
                if info is not None:
                    info["track_id"] = None
                    info.setdefault("last_seen", time.time())
                inactivated_slots.append(slot)
    if inactivated_slots:
        logger.info("Tracks lost for slots %s", inactivated_slots)


def _next_available_slot() -> Optional[int]:
    for slot, info in sorted(slot_memory.items()):
        if info.get("track_id") is None:
            return slot
    used = set(slot_memory.keys())
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
        slot = _next_available_slot()
        if slot is None:
            return {"status": "full", "slot": None}
        selected_tracks[track_id] = slot
        slot_memory[slot] = {
            "track_id": track_id,
            "bbox": None,
            "descriptor": None,
            "last_seen": time.time(),
        }
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


def _bbox_center_ratio(a: List[float], b: List[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    acx = (ax1 + ax2) * 0.5
    acy = (ay1 + ay2) * 0.5
    bcx = (bx1 + bx2) * 0.5
    bcy = (by1 + by2) * 0.5
    aw = max(1.0, ax2 - ax1)
    ah = max(1.0, ay2 - ay1)
    bw = max(1.0, bx2 - bx1)
    bh = max(1.0, by2 - by1)
    diag_ref = max((aw**2 + ah**2) ** 0.5, (bw**2 + bh**2) ** 0.5, 1.0)
    dist = ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5
    return dist / diag_ref


def _extract_descriptor(frame: np.ndarray, bbox: np.ndarray) -> Optional[np.ndarray]:
    x1, y1, x2, y2 = bbox.astype(int)
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(frame.shape[1], x2)
    y2 = min(frame.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return None
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
    if hist is None:
        return None
    cv2.normalize(hist, hist)
    return hist.flatten()


def _descriptor_distance(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    if a is None or b is None:
        return 1.0
    if a.shape != b.shape:
        return 1.0
    return float(np.linalg.norm(a - b))


def _auto_reassign(
    tracked_objects: List[Dict[str, object]],
    descriptors: Dict[int, Optional[np.ndarray]],
) -> None:
    now = time.time()
    with targets_lock:
        inactive_slots = {
            slot: dict(info)
            for slot, info in slot_memory.items()
            if info.get("track_id") is None
        }
        active_ids = set(selected_tracks.keys())
    for obj in tracked_objects:
        track_id = obj["id"]
        if track_id in active_ids:
            continue
        descriptor = descriptors.get(track_id)
        best_slot = None
        best_score = -1.0
        for slot, info in inactive_slots.items():
            ref_descriptor = info.get("descriptor")
            score = -1.0
            if descriptor is not None and ref_descriptor is not None:
                dist = _descriptor_distance(descriptor, ref_descriptor)
                if dist <= SLOT_RECALL_DESCRIPTOR:
                    score = 1.0 - dist
            else:
                bbox_mem = info.get("bbox")
                if bbox_mem is None:
                    continue
                current_bbox = obj["bbox"].tolist()
                iou = _bbox_iou(current_bbox, bbox_mem)
                if iou >= SLOT_RECALL_IOU:
                    score = iou
                else:
                    center_ratio = _bbox_center_ratio(current_bbox, bbox_mem)
                    if center_ratio <= SLOT_RECALL_CENTER:
                        score = max(score, 1.0 - center_ratio)
            if score > best_score:
                best_score = score
                best_slot = slot
        if best_slot is not None:
            with targets_lock:
                info = slot_memory.get(best_slot)
                if info is None or info.get("track_id") is not None:
                    continue
                selected_tracks[track_id] = best_slot
                info["track_id"] = track_id
                info["bbox"] = obj["bbox"].tolist()
                info["last_seen"] = now
                if descriptor is not None:
                    info["descriptor"] = descriptor
                logger.info("Recalled slot %s for track %s", best_slot, track_id)
                active_ids.add(track_id)


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
                                conf_val = float(scores[i])
                                if conf_val < YOLO_MIN_CONF:
                                    continue
                                detections_input.append(
                                    {
                                        "bbox": boxes[i],
                                        "cls": det_cls,
                                        "conf": conf_val,
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
            object_descriptors: Dict[int, Optional[np.ndarray]] = {}
            for obj in tracked_objects:
                object_descriptors[obj["id"]] = _extract_descriptor(frame, obj["bbox"])
            _auto_reassign(tracked_objects, object_descriptors)
            active_ids = [obj["id"] for obj in tracked_objects]
            _prune_selected(active_ids)
            selected_snapshot = _selected_snapshot()
            selected_map = {
                item["track_id"]: item["slot"]
                for item in selected_snapshot
                if item.get("track_id") is not None
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
                    with targets_lock:
                        info = slot_memory.get(slot, {})
                        info.update(
                            {
                                "track_id": track_id,
                                "bbox": bbox.tolist(),
                                "last_seen": time.time(),
                            }
                        )
                        descriptor = object_descriptors.get(track_id)
                        if descriptor is not None:
                            info["descriptor"] = descriptor
                        slot_memory[slot] = info
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
    with room_lock:
        room_snapshot = dict(room_settings)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_title": APP_TITLE,
            "app_description": APP_DESCRIPTION,
            "now": now,
            "camera_settings": camera_settings,
            "yolo_error": yolo_error,
            "room_settings": room_snapshot,
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


@app.get("/room", response_class=JSONResponse)
async def get_room() -> JSONResponse:
    with room_lock:
        current = dict(room_settings)
    return JSONResponse({"room": current})


@app.post("/room", response_class=JSONResponse)
async def update_room(payload: RoomSettingsRequest) -> JSONResponse:
    name = payload.name.strip()
    with room_lock:
        room_settings["name"] = name
        room_settings["updated_at"] = time.time()
        current = dict(room_settings)
    return JSONResponse({"status": "ok", "room": current})


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


@app.delete("/targets/{slot_id}", response_class=JSONResponse)
async def delete_target(slot_id: int) -> JSONResponse:
    if not (1 <= slot_id <= MAX_TARGETS):
        raise HTTPException(status_code=404, detail="slot not found")
    with targets_lock:
        info = slot_memory.get(slot_id)
        if info is None:
            raise HTTPException(status_code=404, detail="slot not assigned")
        track_id = info.get("track_id")
        if track_id is not None:
            selected_tracks.pop(track_id, None)
        slot_memory.pop(slot_id, None)
    return JSONResponse(
        {
            "status": "removed",
            "slot": slot_id,
            "track_id": track_id,
            "selected": _selected_snapshot(),
        }
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    camera_streamer.shutdown()
