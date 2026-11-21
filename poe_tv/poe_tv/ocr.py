"""Bọc engine OCR (RapidOCR chạy bằng ONNX, không cần mạng)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def h(self):
        return self.y1 - self.y0


def read(img: np.ndarray, scale: float = 1.0) -> list[Line]:
    """OCR một ảnh; trả về các dòng với tọa độ theo ảnh gốc."""
    src = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    res, _ = engine()(src)
    out = []
    for box, text, conf in res or []:
        xs = [p[0] / scale for p in box]
        ys = [p[1] / scale for p in box]
        out.append(Line(text.strip(), min(xs), min(ys), max(xs), max(ys), float(conf)))
    out.sort(key=lambda l: (l.y0, l.x0))
    return out


def read_fast(img: np.ndarray, det_max: int = 1280) -> list[Line]:
    """Tìm vị trí chữ trên ảnh thu nhỏ (cạnh dài ≤ det_max, nhanh hơn), rồi đọc chữ trên các ô cắt từ ảnh GỐC
    (không mất nét như đọc trên ảnh thu nhỏ). Bỏ bước xoay chữ vì chữ trong game luôn nằm ngang."""
    e = engine()
    H, W = img.shape[:2]
    s = min(1.0, det_max / max(H, W))
    small = img if s == 1.0 else cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    boxes, _ = e.text_detector(small)
    if boxes is None or len(boxes) == 0:
        return []
    boxes = e.sorted_boxes(boxes)
    full = [np.array(b, dtype=np.float32) / s for b in boxes]
    crops = e.get_crop_img_list(img, full)
    rec, _ = e.text_recognizer(crops)
    out = []
    for b, (text, conf) in zip(full, rec):
        if float(conf) < e.text_score or not text.strip():
            continue
        xs, ys = b[:, 0], b[:, 1]
        out.append(Line(text.strip(), float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()), float(conf)))
    out.sort(key=lambda l: (l.y0, l.x0))
    return out
