"""Tìm vùng tooltip món đồ trong một khung hình và đọc lại cho chính xác."""
from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

import numpy as np

from . import ocr
from .data import Data, letters, unique_has_base


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
        return not (u and b and unique_has_base(u, b["name"]))
    return not (data.find_gem(header[0]) or data.find_other(header[0]))


# Dòng gợi ý nút ở đáy màn hình chỉ có khi mở túi đồ / stash / cửa sổ bán đồ (bản console): PoE1 "Navigate",
# "Compare / More Actions", "Reorder Tabs"; PoE2 (ảnh TV 22/11/2026) "Price Check", "Manage Sockets", "Toggle Weapon Set".
# Lúc đang đánh quái không có, nên đồ dưới đất không bị tra giá. Không dùng "Pick Up" (có cả khi nhặt đồ dưới đất).
UI_MARKERS = re.compile(r"NAVIGATE|COMPARE|MOREACTIONS|REORDERTABS|PRICECHECK|MANAGESOCKETS|TOGGLEWEAPONSET")
UI_STRIP = 0.70        # chỉ đọc phần màn hình từ 70% chiều cao trở xuống (dòng gợi ý nằm ở đáy)
# Dấu hiệu yếu ở dải đáy (chưa đủ để chắc chắn), thấy thì đọc thêm dải trên cùng tìm tiêu đề cửa sổ:
# - PoE2 rê vào đồ đang mặc không có sockets: dòng gợi ý chỉ còn "Pick Up"/"Drop" (có thể có cả lúc nhặt đồ dưới đất)
#   → tiêu đề "INVENTORY" (nhãn tab dưới thanh L1/R1).
# - Cửa sổ Ritual (PoE1 và PoE2, ảnh bản PC 22/11/2026): đáy có "Rituals Remaining"/"Cancel"/"Offer Tribute"
#   → tiêu đề "FAVOURS".
UI_WEAK = re.compile(r"PICKUP|DROP|TRIBUTE|RITUALS|CANCEL|DEFER|REROLL")
UI_TITLE = re.compile(r"INVENTORY|STASH|FAVOURS")
TITLE_STRIP = 0.30     # dải trên cùng để tìm nhãn tab


def _key(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def inventory_open(img: np.ndarray) -> tuple[bool, list[str]]:
    """Màn hình đang mở túi đồ/stash/cửa sổ mua bán chưa: đọc riêng dải đáy (nhẹ hơn đọc cả màn hình) và tìm chữ
    của dòng gợi ý nút; chỉ có "Pick Up"/"Drop" thì đọc thêm dải trên tìm nhãn "INVENTORY". Trả (có/không, các chữ khớp)."""
    H = img.shape[0]
    strip = ocr.read_fast(img[int(H * UI_STRIP):, :])
    marks = [l.text for l in strip if UI_MARKERS.search(_key(l.text))]
    if not marks and any(UI_WEAK.search(_key(l.text)) for l in strip):
        top = ocr.read_fast(img[:int(H * TITLE_STRIP), :])
        marks = [l.text for l in top if UI_TITLE.search(_key(l.text))]
    return bool(marks), marks


def unidentified(tt: "Tooltip") -> bool:
    """Món chưa giám định: tooltip có dòng "Unidentified"."""
    return any("UNIDENTIFIED" in re.sub(r"[^A-Z]", "", l.text.upper()) for l in tt.lines)


def find(img: np.ndarray, data: Data, fine: bool | None = False, fast: bool = True) -> Tooltip | None:
    """fine: True = luôn đọc kỹ, False = không, None = tự quyết theo loại món (needs_fine).
    fast: tìm chữ trên ảnh thu nhỏ rồi đọc trên ảnh gốc (ocr.read_fast)."""
    H, W = img.shape[:2]
    lines = ocr.read_fast(img) if fast else ocr.read(img, scale=min(1.0, 1600 / W))
    if not lines:
        return None

    # 1) Mọi dòng có thể là tiêu đề: base/unique/gem/vật phẩm (ghép với dòng tên ngay phía trên nếu có),
    #    hoặc đồ magic có tên chứa base. Khung chat, bảng đồ… cũng có tên món (link đồ trong chat) nên không lấy dòng
    #    đầu tiên tìm thấy, mà chấm điểm từng ứng viên theo cột chữ căn giữa bên dưới (tooltip thật có nhiều dòng).
    cands = []
    for i, l in enumerate(lines):
        if data.find_base(l.text) or data.find_gem(l.text) or data.find_other(l.text):
            # khung chữ 2 dòng tên/loại có thể chạm nhau (khoảng cách 0) khi tìm chữ trên ảnh thu nhỏ
            above = [p for p in lines[:i] if p is not l and -0.5 * l.h < l.y0 - p.y1 < 2.0 * l.h
                     and p.y0 < l.y0 and abs(p.cx - l.cx) < 0.04 * W]
            cands.append([above[-1], l] if above else [l])
        elif ":" not in l.text and data.find_base_in(l.text):      # dòng chat dạng "tên: lời nhắn" thì bỏ
            cands.append([l])
    if not cands:
        return None

    def column(header):
        """Các dòng căn giữa cùng cột bên dưới tiêu đề, tới khi gặp khoảng trống lớn."""
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
        return picked

    best = None
    for header in cands:
        picked = column(header)
        # chữ trong tooltip căn giữa nên tâm từng dòng gần như trùng tâm tiêu đề; chữ chat căn trái thì tâm lệch nhau
        cx0 = sum(h.cx for h in header) / len(header)
        centered = sum(1 for l in picked if abs(l.cx - cx0) < 0.015 * W)
        score = centered + (2 if len(header) == 2 else 0)
        if best is None or score > best[0]:
            best = (score, header, picked)
    score, header, picked = best
    if score < 2:
        return None          # chỉ là chữ lẻ (chat, tên bảng…), không phải tooltip
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


def pink_rows(img: np.ndarray, tt: Tooltip) -> list[str]:
    """Các hàng chữ màu hồng trong tooltip (dòng mod đột biến của đồ Foulborn).
    Đo 21/11/2026 trên ảnh TV: dòng hồng R199 G99 B150; tên unique R205 G113 B95; chữ mô tả R209 G141 B128;
    mod thường xanh tím (B > R). Hồng = G/R < 0,58 và B/R > 0,62."""
    from .parse import _rows
    out = []
    body = [l for l in tt.lines if l.text not in tt.header]
    for row in _rows(body):
        px = []
        for l in row:
            c = img[max(0, int(l.y0)):int(l.y1), max(0, int(l.x0)):int(l.x1)].reshape(-1, 3)
            px.append(c[c.max(1) > 120])
        px = np.concatenate(px) if px else np.zeros((0, 3))
        if len(px) < 30:
            continue
        b, g, r = px.astype(float).mean(0)
        if r > 140 and g / r < 0.58 and b / r > 0.62:
            out.append(" ".join(l.text for l in sorted(row, key=lambda l: l.x0)))
    return out
