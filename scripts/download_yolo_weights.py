#!/usr/bin/env python3
"""Download YOLOv8 weights used by the Jetson Watchdog prototype."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ultralytics import YOLO

DEFAULT_MODELS: tuple[str, ...] = (
    "yolov8n.pt",       # Person detection (smallest YOLOv8 model)
    "yolov8n-pose.pt",  # Pose estimation variant
)


def download_models(model_names: Iterable[str]) -> None:
    for name in model_names:
        print(f"[yolo] downloading {name} ...")
        YOLO(name)  # Instantiating triggers the download if missing.


def downloads_dir() -> Path:
    """Return the Ultralytics default downloads dir for reference."""
    return Path.home() / ".config" / "Ultralytics" / "weights"


def main() -> None:
    download_models(DEFAULT_MODELS)
    print(f"[yolo] done. Files stored under: {downloads_dir()}")


if __name__ == "__main__":
    main()
