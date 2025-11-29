import importlib
import inspect
import pkgutil
import asyncio
import os
os.environ.setdefault("TORCH_LOAD_WEIGHTS_ONLY", "0")
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

import cv2
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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

APP_TITLE = "Jetson Watchdog"
APP_DESCRIPTION = "Prototype UI for the Jetson room-monitor project."

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION)

static_dir = BASE_DIR / "static"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")

camera_streamer = CameraStreamer(fps=30)
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolov8n.pt")
try:
    yolo_model: Optional[YOLO] = YOLO(YOLO_MODEL_PATH)
    yolo_error: Optional[str] = None
except Exception as exc:  # pragma: no cover
    yolo_model = None
    yolo_error = f"YOLO model load failed: {exc}"


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
        frame = camera_streamer.latest_frame_array()
        if frame is None:
            await asyncio.sleep(0.1)
            continue

        overlay = frame
        if yolo_model is not None:
            try:
                results = yolo_model.predict(frame, verbose=False)
                if results:
                    overlay = results[0].plot()
            except Exception as exc:  # pragma: no cover
                overlay = frame
                yolo_error = f"YOLO inference error: {exc}"

        ok, buffer = cv2.imencode(".jpg", overlay)
        if not ok:
            await asyncio.sleep(0.05)
            continue

        yield (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        )
        await asyncio.sleep(0)


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


@app.on_event("shutdown")
async def _shutdown() -> None:
    camera_streamer.shutdown()
