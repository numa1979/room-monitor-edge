from __future__ import annotations

import threading
import time
from typing import Optional

import cv2


class CameraStreamer:
    """Continuously grabs frames from a V4L2 camera for MJPEG streaming."""

    def __init__(
        self,
        device_index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 10,
    ) -> None:
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps

        self._capture = cv2.VideoCapture(self.device_index)
        self._configure_capture()

        self._lock = threading.Lock()
        self._frame: Optional[bytes] = None
        self._running = True

        self._thread = threading.Thread(target=self._update_loop, daemon=True)
        self._thread.start()

    def _configure_capture(self) -> None:
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._capture.set(cv2.CAP_PROP_FPS, self.fps)

    def _update_loop(self) -> None:
        retry_backoff = 0.2
        while self._running:
            if not self._capture.isOpened():
                self._capture.open(self.device_index)

            ok, frame = self._capture.read()
            if not ok:
                time.sleep(retry_backoff)
                continue

            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                continue

            with self._lock:
                self._frame = buffer.tobytes()

            time.sleep(1 / max(self.fps, 1))

    def latest_frame(self) -> Optional[bytes]:
        with self._lock:
            return self._frame

    def shutdown(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

        if self._capture.isOpened():
            self._capture.release()
