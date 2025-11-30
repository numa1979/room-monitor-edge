#!/usr/bin/env python3
"""Download YOLOv8 weights used by the Jetson Watchdog prototype."""

from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Iterable

DEFAULT_MODELS: tuple[str, ...] = (
    "yolov8n.pt",  # Person detection (smallest YOLOv8 model)
    "yolov8n-pose.pt",  # Pose estimation variant
)

MODEL_URLS = {
    "yolov8n.pt": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt",
    "yolov8n-pose.pt": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n-pose.pt",
}


def download_models(model_names: Iterable[str]) -> None:
    target = downloads_dir()
    target.mkdir(parents=True, exist_ok=True)
    for name in model_names:
        url = MODEL_URLS.get(name)
        if not url:
            raise ValueError(f"download URL for {name} is not defined")
        dest = target / name
        if dest.exists():
            print(f"[yolo] skip {name}: already exists at {dest}")
            continue
        print(f"[yolo] downloading {name} -> {dest}")
        urllib.request.urlretrieve(url, dest)


def downloads_dir() -> Path:
    """Return the Ultralytics default downloads dir for reference."""
    return Path.home() / ".config" / "Ultralytics" / "weights"


def main() -> None:
    download_models(DEFAULT_MODELS)
    print(f"[yolo] done. Files stored under: {downloads_dir()}")


if __name__ == "__main__":
    main()
