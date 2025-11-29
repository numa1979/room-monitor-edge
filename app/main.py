import asyncio
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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


async def mjpeg_frame_generator() -> AsyncGenerator[bytes, None]:
    while True:
        frame = camera_streamer.latest_frame()
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(0.1)


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


@app.get("/camera/settings", response_class=JSONResponse)
async def camera_settings() -> JSONResponse:
    return JSONResponse({"current": camera_streamer.current_settings()})


@app.on_event("shutdown")
async def _shutdown() -> None:
    camera_streamer.shutdown()
