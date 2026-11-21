"""Tìm vùng tooltip món đồ trong một khung hình và đọc lại cho chính xác."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import numpy as np

from . import ocr
from .data import Data, letters


@dataclass
class Tooltip:
    box: tuple[int, int, int, int]      # x0, y0, x1, y1 trên khung hình gốc
    header: list[str]                   # 1–2 dòng đầu (tên, base)
    lines: list[ocr.Line]               # mọi dòng trong tooltip (đọc lại ở độ phân giải cao)


def _is_item_line(text: str, data: Data) -> bool:
    k = letters(text)
    if len(k) < 3:
        return False
    return bool(data.find_base(text) or data.find_unique(text) or data.find_gem(text)
                or data.find_other(text))


def find(img: np.ndarray, data: Data, detect_scale: float | None = None) -> Tooltip | None:
    H, W = img.shape[:2]
    if detect_scale is None:
        detect_scale = min(1.0, 1600 / W)
    lines = ocr.read(img, scale=detect_scale)
    if not lines:
        return None

    # 1) Tìm dòng tiêu đề: dòng là base/unique/gem/vật phẩm, ưu tiên cặp (tên, base) xếp chồng
    header = None
    for i, l in enumerate(lines):
        if not data.find_base(l.text) and not data.find_gem(l.text) and not data.find_other(l.text):
            continue
        above = [p for p in lines[:i] if 0 < l.y0 - p.y1 < 2.0 * l.h and abs(p.cx - l.cx) < 0.04 * W]
        if above:
            header = [above[-1], l]
        else:
            header = [l]
        break
    if header is None:
        # đồ magic: tên có chứa base
        for l in lines:
            if data.find_base_in(l.text):
                header = [l]
                break
    if header is None:
        return None

    # 2) Gom các dòng căn giữa cùng cột bên dưới tiêu đề
    cx0 = sum(h.cx for h in header) / len(header)
    tol = 0.045 * W
    col = [l for l in lines if l.y0 > header[-1].y0 + 1 and abs(l.cx - cx0) < tol]
    picked = []
    last_y = header[-1].y1
    hs = [h.h for h in header]
    for l in col:
        lh = median(hs + [p.h for p in picked]) if picked else median(hs)
        if l.y0 - last_y > 4.0 * lh:
            break
        picked.append(l)
        last_y = max(last_y, l.y1)
    allb = header + picked
    x0 = min(l.x0 for l in allb)
    x1 = max(l.x1 for l in allb)
    y0 = min(l.y0 for l in allb)
    y1 = max(l.y1 for l in allb)
    mx, my = 0.02 * W, 0.01 * H
    box = (max(0, int(x0 - mx)), max(0, int(y0 - my)), min(W, int(x1 + mx)), min(H, int(y1 + my)))

    # 3) Đọc lại vùng đã cắt, phóng to 2 lần
    crop = img[box[1]:box[3], box[0]:box[2]]
    fine = ocr.read(crop, scale=2.0)
    for l in fine:
        l.x0 += box[0]; l.x1 += box[0]; l.y0 += box[1]; l.y1 += box[1]
    return Tooltip(box=box, header=[h.text for h in header], lines=fine)
