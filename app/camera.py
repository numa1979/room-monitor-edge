from __future__ import annotations

import threading
import time
from typing import Dict, Optional

import cv2
import numpy as np


class CameraStreamer:
    """Continuously grabs frames from a V4L2 camera for MJPEG streaming."""

    def __init__(
        self,
        device_index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        jpeg_quality: int = 80,
    ) -> None:
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.jpeg_quality = jpeg_quality

        self._capture_lock = threading.Lock()
        self._capture = cv2.VideoCapture(self.device_index)
        self._configure_capture()

        self._frame_lock = threading.Lock()
        self._frame: Optional[bytes] = None
        self._frame_bgr: Optional[np.ndarray] = None
        self._running = True

        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    def _configure_capture(self) -> None:
        with self._capture_lock:
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self._capture.set(cv2.CAP_PROP_FPS, self.fps)

    def _update_loop(self) -> None:
        retry_backoff = 0.2
        while self._running:
            with self._capture_lock:
                if not self._capture.isOpened():
                    self._capture.open(self.device_index)

                ok, frame = self._capture.read()
            if not ok:
                time.sleep(retry_backoff)
                continue

            ok, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)]
            )
            if not ok:
                continue

            with self._frame_lock:
                self._frame = buffer.tobytes()
                self._frame_bgr = frame.copy()

            time.sleep(1 / max(self.fps, 1))

    def latest_frame(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._frame

    def latest_frame_array(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            if self._frame_bgr is None:
                return None
            return self._frame_bgr.copy()

    def current_settings(self) -> Dict[str, int]:
        with self._capture_lock:
            actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH) or self.width)
            actual_height = int(
                self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.height
            )
            actual_fps = int(self._capture.get(cv2.CAP_PROP_FPS) or self.fps)
        return {
            "width": actual_width,
            "height": actual_height,
            "fps": actual_fps,
            "quality": self.jpeg_quality,
        }

    def shutdown(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

        with self._capture_lock:
            if self._capture.isOpened():
                self._capture.release()
