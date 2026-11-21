"""Tìm vùng tooltip món đồ trong một khung hình và đọc lại cho chính xác."""
from __future__ import annotations

import re
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


def needs_fine(header: list[str], data: Data) -> bool:
    """Có cần đọc lại kỹ (phóng to 2 lần) không. Unique, ngọc, currency chỉ cần tên và vài thuộc tính → bỏ qua,
    nhanh hơn 1–2 giây. Rare/magic cần đọc đúng từng dòng mod → đọc kỹ."""
    if len(header) == 2:
        name = re.sub(r"^\s*foulborn\s+", "", header[0], flags=re.I)
        u, b = data.find_unique(name), data.find_base(header[1])
        return not (u and b and letters(u["base"]) == letters(b["name"]))
    return not (data.find_gem(header[0]) or data.find_other(header[0]))


def find(img: np.ndarray, data: Data, fine: bool | None = False, fast: bool = True) -> Tooltip | None:
    """fine: True = luôn đọc kỹ, False = không, None = tự quyết theo loại món (needs_fine).
    fast: tìm chữ trên ảnh thu nhỏ rồi đọc trên ảnh gốc (ocr.read_fast)."""
    H, W = img.shape[:2]
    lines = ocr.read_fast(img) if fast else ocr.read(img, scale=min(1.0, 1600 / W))
    if not lines:
        return None

    # 1) Tìm dòng tiêu đề: dòng là base/unique/gem/vật phẩm, ưu tiên cặp (tên, base) xếp chồng
    header = None
    for i, l in enumerate(lines):
        if not data.find_base(l.text) and not data.find_gem(l.text) and not data.find_other(l.text):
            continue
        # khung chữ 2 dòng tên/loại có thể chạm nhau (khoảng cách 0) khi tìm chữ trên ảnh thu nhỏ
        above = [p for p in lines[:i] if p is not l and -0.5 * l.h < l.y0 - p.y1 < 2.0 * l.h
                 and p.y0 < l.y0 and abs(p.cx - l.cx) < 0.04 * W]
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

    names = [h.text for h in header]
    if fine is None:
        fine = needs_fine(names, data)
    if not fine:
        return Tooltip(box=box, header=names, lines=allb)

    # 3) Đọc lại vùng đã cắt, phóng to 2 lần. Cắt rộng thêm 2 bên: lượt đầu có thể sót dòng dài nhất nên khung hẹp,
    #    cắt sát thì mất chữ cuối dòng ("RATING" thành "RATINC" → không khớp mod).
    ex = int(0.03 * W)
    cx0, cx1 = max(0, box[0] - ex), min(W, box[2] + ex)
    crop = img[box[1]:box[3], cx0:cx1]
    got = ocr.read(crop, scale=2.0)
    for l in got:
        l.x0 += cx0; l.x1 += cx0; l.y0 += box[1]; l.y1 += box[1]
    return Tooltip(box=box, header=names, lines=got)
