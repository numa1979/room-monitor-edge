from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


def _compute_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    x_a = max(box_a[0], box_b[0])
    y_a = max(box_a[1], box_b[1])
    x_b = min(box_a[2], box_b[2])
    y_b = min(box_a[3], box_b[3])

    inter_w = max(0.0, x_b - x_a)
    inter_h = max(0.0, y_b - y_a)
    inter_area = inter_w * inter_h
    if inter_area == 0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    return inter_area / float(area_a + area_b - inter_area + 1e-6)


@dataclass
class TrackState:
    bbox: np.ndarray
    cls: int
    conf: float
    lost: int = 0


class CentroidTracker:
    def __init__(self, max_lost: int = 15, match_iou: float = 0.3) -> None:
        self.max_lost = max_lost
        self.match_iou = match_iou
        self._next_id = 1
        self._tracks: Dict[int, TrackState] = {}

    def _register(self, detection: Dict[str, float]) -> int:
        track_id = self._next_id
        self._next_id += 1
        self._tracks[track_id] = TrackState(
            bbox=detection["bbox"],
            cls=detection["cls"],
            conf=detection["conf"],
            lost=0,
        )
        return track_id

    def update(self, detections: List[Dict[str, float]]) -> List[Dict[str, float]]:
        if not self._tracks:
            tracked = []
            for det in detections:
                tid = self._register(det)
                tracked.append(self._build_object(tid))
            return tracked

        unmatched_tracks = set(self._tracks.keys())
        unmatched_dets = list(range(len(detections)))
        matches: List[Tuple[int, int]] = []

        if detections:
            iou_matrix = np.zeros((len(self._tracks), len(detections)), dtype=float)
            track_ids = list(self._tracks.keys())
            for t_idx, tid in enumerate(track_ids):
                for d_idx, det_idx in enumerate(unmatched_dets):
                    iou_matrix[t_idx, d_idx] = _compute_iou(
                        self._tracks[tid].bbox, detections[det_idx]["bbox"]
                    )

            while True:
                max_idx = np.unravel_index(np.argmax(iou_matrix), iou_matrix.shape)
                max_value = iou_matrix[max_idx]
                if max_value < self.match_iou:
                    break
                t_idx, d_idx = max_idx
                tid = list(self._tracks.keys())[t_idx]
                det_idx = unmatched_dets[d_idx]
                matches.append((tid, det_idx))
                unmatched_tracks.discard(tid)
                unmatched_dets.remove(det_idx)
                iou_matrix[t_idx, :] = -1
                iou_matrix[:, d_idx] = -1

        for tid, det_idx in matches:
            det = detections[det_idx]
            self._tracks[tid].bbox = det["bbox"]
            self._tracks[tid].cls = det["cls"]
            self._tracks[tid].conf = det["conf"]
            self._tracks[tid].lost = 0

        for det_idx in unmatched_dets:
            self._register(detections[det_idx])

        for tid in list(unmatched_tracks):
            self._tracks[tid].lost += 1
            if self._tracks[tid].lost > self.max_lost:
                del self._tracks[tid]

        if not self._tracks:
            self._next_id = 1

        return [self._build_object(tid) for tid in self._tracks.keys()]

    def active_tracks(self) -> List[Dict[str, float]]:
        return [self._build_object(tid) for tid in self._tracks.keys()]

    def _build_object(self, track_id: int) -> Dict[str, float]:
        state = self._tracks[track_id]
        return {
            "id": track_id,
            "bbox": state.bbox.copy(),
            "cls": state.cls,
            "conf": state.conf,
        }
