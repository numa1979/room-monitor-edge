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


def _center_distance_ratio(box_a: np.ndarray, box_b: np.ndarray) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    acx = (ax1 + ax2) * 0.5
    acy = (ay1 + ay2) * 0.5
    bcx = (bx1 + bx2) * 0.5
    bcy = (by1 + by2) * 0.5
    aw = max(1.0, ax2 - ax1)
    ah = max(1.0, ay2 - ay1)
    bw = max(1.0, bx2 - bx1)
    bh = max(1.0, by2 - by1)
    diag_ref = max(np.hypot(aw, ah), np.hypot(bw, bh), 1.0)
    dist = np.hypot(acx - bcx, acy - bcy)
    return dist / diag_ref


@dataclass
class TrackState:
    bbox: np.ndarray
    cls: int
    conf: float
    lost: int = 0


class CentroidTracker:
    def __init__(
        self,
        max_lost: int = 45,
        match_iou: float = 0.2,
        match_center_ratio: float = 0.35,
    ) -> None:
        self.max_lost = max_lost
        self.match_iou = match_iou
        self.match_center_ratio = match_center_ratio
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
        det_indices = list(range(len(detections)))
        matches: List[Tuple[int, int]] = []
        matched_dets: set[int] = set()

        if detections:
            track_ids = list(self._tracks.keys())
            score_matrix = np.full(
                (len(track_ids), len(det_indices)), -1.0, dtype=float
            )
            for t_idx, tid in enumerate(track_ids):
                track_bbox = self._tracks[tid].bbox
                for d_idx, det_idx in enumerate(det_indices):
                    det_bbox = detections[det_idx]["bbox"]
                    iou = _compute_iou(track_bbox, det_bbox)
                    score = -1.0
                    if iou >= self.match_iou:
                        score = iou
                    elif self.match_center_ratio is not None:
                        ratio = _center_distance_ratio(track_bbox, det_bbox)
                        if ratio <= self.match_center_ratio:
                            score = max(score, 1.0 - ratio)
                    if score >= 0:
                        score_matrix[t_idx, d_idx] = score

            while score_matrix.size > 0:
                max_idx = np.unravel_index(np.argmax(score_matrix), score_matrix.shape)
                max_value = score_matrix[max_idx]
                if max_value < 0:
                    break
                t_idx, d_idx = max_idx
                tid = track_ids[t_idx]
                det_idx = det_indices[d_idx]
                if det_idx in matched_dets:
                    score_matrix[:, d_idx] = -1
                    continue
                matches.append((tid, det_idx))
                unmatched_tracks.discard(tid)
                matched_dets.add(det_idx)
                score_matrix[t_idx, :] = -1
                score_matrix[:, d_idx] = -1

        for tid, det_idx in matches:
            det = detections[det_idx]
            self._tracks[tid].bbox = det["bbox"]
            self._tracks[tid].cls = det["cls"]
            self._tracks[tid].conf = det["conf"]
            self._tracks[tid].lost = 0

        remaining_dets = [idx for idx in det_indices if idx not in matched_dets]
        for det_idx in remaining_dets:
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
