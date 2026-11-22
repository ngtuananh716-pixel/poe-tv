"""Chế độ máy chủ cho bản sửa CouchSubs (poe-tv).

Chạy:  python -m poe_tv.serve            (mặc định cổng 127.0.0.1:8788)

Bản sửa CouchSubs gửi khung hình Remote Play tới  POST /frame  khoảng 3 lần/giây:
  - thân: ảnh BGRA thô;  header X-Width, X-Height, X-Stride
  - trả về JSON  {"regions": [{"id", "text", "box": [trái, trên, phải, dưới], "line"}], "next_ms": n}
    (tọa độ là tỉ lệ 0–1 của khung hình; text rỗng = xóa ô)
CouchSubs chỉ chuyển nguyên các ô này sang app TV (SubtitleServer.SendRegion). Mọi quyết định
(khi nào tra, đặt ô ở đâu, viết gì) nằm ở đây, để sửa mà không phải build lại CouchSubs.

Cách tra: chưa có ô giá thì cứ khoảng DETECT_GAP giây tìm tooltip một lần trên khung mới nhất (game vẫn chuyển
động sau bảng đồ nên không chờ cả màn hình đứng yên) → đọc món → tra giá realm Sony → hiện ô giá cạnh tooltip.
Sau đó chỉ theo dõi riêng vùng tooltip: vùng đó đổi (đóng tooltip, chuyển món) thì xóa ô và tìm lại.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

from . import data, parse, pick, query, tooltip

REGION_ID = "poe-price"
GAME_NAME = {"poe1": "PoE1", "poe2": "PoE2"}
SIG_W, SIG_H = 96, 54     # ảnh thu nhỏ để so khung hình
PIX_DIFF = 20             # một điểm ảnh đổi hơn mức này (0–255) coi là "đổi"
DETECT_GAP = 0.25         # chưa có ô giá: nghỉ bấy nhiêu giây giữa 2 lần tìm tooltip
UI_IDLE_GAP = 0.6         # không mở túi đồ (đang chơi): nghỉ lâu hơn giữa 2 lần xem, đỡ tốn CPU
TT_CHANGED = 0.5          # bản đồ chữ vùng tooltip khác quá tỉ lệ này = tooltip đã đóng hoặc sang món khác
TT_SIG = (96, 96)         # số ô của bản đồ chữ vùng tooltip
TEXT_LEVEL = 110          # điểm ảnh sáng hơn mức này (0–255) coi là chữ
MISS_SAVE_GAP = 30        # không thấy tooltip: cứ bấy nhiêu giây lưu lại 1 ảnh để xem
RECENT_S = 600            # mở lại đúng món đã tra trong bấy nhiêu giây thì hiện giá cũ ngay, không tra lại
NEXT_MS = 300             # CouchSubs gửi khung kế tiếp sau bấy nhiêu ms (đang mở túi đồ / đang hiện ô giá)
IDLE_NEXT_MS = 1000       # không mở túi đồ: 1 khung/giây là đủ để biết lúc mở (đỡ nóng máy)
OFF_NEXT_MS = 1500        # tắt tra giá: chỉ cần giữ liên lạc để nhận nút bật/tắt
LINE = 0.021              # chiều cao một dòng chữ trên TV (tỉ lệ chiều cao màn hình); 22/11/2026 giảm ~20% (từ 0.026)
RELAX_MIN = 5             # Dòng: TẤT CẢ ra ít hơn bấy nhiêu món thì tự nới (bớt 1 dòng, rồi 2 dòng)
MAX_BULLETS = 10          # ô giá hiện tối đa bấy nhiêu dòng chỉ số
MAX_SAVED = 30            # số ảnh tra gần nhất giữ lại trong thư mục logs
MAX_CHARS = 30            # mỗi dòng trong ô giá tối đa bấy nhiêu ký tự

CURRENCY = {"divine": "div", "chaos": "c", "exalted": "ex", "mirror": "mirror", "alch": "alch",
            "annul": "annul", "chance": "chance", "vaal": "vaal", "fusing": "fuse", "regal": "regal",
            "gcp": "gcp", "chrome": "chrome", "alt": "alt", "jew": "jew", "scour": "scour"}


def signature(img: np.ndarray) -> np.ndarray:
    small = cv2.resize(img, (SIG_W, SIG_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)


def frac(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.count_nonzero(np.abs(a - b) > PIX_DIFF)) / a.size


def crop_sig(img: np.ndarray, box) -> np.ndarray:
    """Bản đồ chữ của vùng tooltip: ô nào có chữ sáng. Nền tooltip trong suốt một phần nên game động phía sau vẫn
    lộ ra, nhưng chỉ lờ mờ (tối), không vượt ngưỡng chữ sáng → so bản đồ chữ không bị quái/pet cử động làm nhiễu."""
    x0, y0, x1, y1 = box
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return np.zeros(TT_SIG[::-1], bool)
    text = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) > TEXT_LEVEL).astype(np.uint8) * 255
    density = cv2.resize(text, TT_SIG, interpolation=cv2.INTER_AREA)
    return density > 25


def sig_change(a: np.ndarray, b: np.ndarray) -> float:
    """Tỉ lệ ô chữ khác nhau giữa 2 bản đồ chữ (0 = y hệt)."""
    return np.count_nonzero(a ^ b) / max(int(np.count_nonzero(a)), int(np.count_nonzero(b)), 50)


def inner_box(box, W, H):
    """Bỏ phần lề tooltip.find thêm vào (0,02 rộng, 0,01 cao): chỉ so phần chữ, nền game quanh tooltip có động cũng không sao."""
    x0, y0, x1, y1 = box
    mx, my = int(0.02 * W), int(0.01 * H)
    if x1 - x0 > 3 * mx and y1 - y0 > 3 * my:
        return (x0 + mx, y0 + my, x1 - mx, y1 - my)
    return box


def letters_key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def short_price(amount, currency) -> str:
    return f"{amount:g} {CURRENCY.get(currency, currency)}"


def head(title: str, game: str = "poe1") -> str:
    """Dòng đầu ô giá: tên món (22/11/2026 Tuấn Anh bỏ chữ "PoE2 ·")."""
    return title[:MAX_CHARS]


def filter_lines(lab) -> list[str]:
    """Các bộ lọc đã dùng, mỗi chỉ số một dòng:
    "Lọc:" + "• …" (bắt buộc), rồi "Đủ ≥N/M dòng:" + "• …" (nhóm đếm); chỉ có nhóm đếm thì "Lọc (đủ ≥N/M dòng):"."""
    if not lab or not (lab.get("req") or lab.get("count")):
        return []
    req, cnt, need, of = lab.get("req") or [], lab.get("count") or [], lab.get("need", 0), lab.get("of", 0)
    group = f"tất cả {of} dòng" if need >= of else f"đủ ≥{need}/{of} dòng"
    lines = []
    if req:
        lines.append("Lọc:")
        lines += ["• " + x for x in req]
        if cnt:
            lines.append(group[0].upper() + group[1:] + ":")
    else:
        lines.append(f"Lọc ({group}):")
    lines += ["• " + x for x in cnt]
    bullets = sum(1 for l in lines if l.startswith("• "))
    if bullets > MAX_BULLETS:                       # quá dài: giữ MAX_BULLETS − 1 dòng đầu, dòng cuối ghi số còn lại
        kept, out, left = 0, [], bullets - (MAX_BULLETS - 1)
        for l in lines:
            if l.startswith("• "):
                kept += 1
                if kept > MAX_BULLETS - 1:
                    continue
            out.append(l)
        lines = out + [f"• … +{left} dòng"]
    return [l[:MAX_CHARS] for l in lines]


def estimate(listings, total, rates, game="poe1"):
    """Giá ước tính kiểu PoE Overlay: quy các món rẻ nhất (tối đa 10) về tiền nhỏ của game theo tỉ giá, lấy trung vị;
    khoảng giá bỏ các giá "câu khách" thấp hơn 1/5 trung vị; độ tin cậy theo số món đang bán và độ chênh giá.
    Trả (các dòng, có ước tính được không)."""
    if not rates:
        return [], False
    vals = sorted(l["amount"] * rates[l["currency"]] for l in listings
                  if l.get("amount") and l.get("currency") in rates)
    if not vals:
        return [], False
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    kept = [v for v in vals if v >= med / 5] or vals
    lo, hi = kept[0], kept[-1]
    spread = hi / lo if lo > 0 else float("inf")
    rel = "cao" if total >= 20 and n >= 8 and spread <= 3 else "vừa" if total >= 5 and spread <= 10 else "thấp"
    (small, s_short), (big, b_short) = MAIN_CURRENCY_SHORT[game]
    unit_big = rates.get(big)

    def unit(v):
        return "big" if unit_big and v >= unit_big else "small"

    def num(v):
        return fmt_each(v / unit_big) if unit(v) == "big" else fmt_each(v)

    def full(v):
        return f"{num(v)} {b_short if unit(v) == 'big' else s_short}"

    same = unit(lo) == unit(med) == unit(hi)
    rng = f"{num(lo)}–{num(hi)}" if same else f"{full(lo)}–{full(hi)}"
    return [f"≈ {full(med)} ({rng})"[:MAX_CHARS], f"Tin cậy: {rel}"], True


# tiền nhỏ và tiền lớn để quy giá: (mã, chữ viết tắt)
MAIN_CURRENCY_SHORT = {"poe1": (("chaos", "c"), ("divine", "div")), "poe2": (("exalted", "ex"), ("divine", "div"))}


def price_lines(title, result, notes, labels=None, rates=None, game="poe1") -> list[str]:
    """Nội dung ô giá: tên món; Min (giá rẻ nhất); giá ước tính (khoảng) và độ tin cậy, mỗi thứ một dòng (không có tỉ
    giá thì giá phổ biến); số món đang bán; các bộ lọc đã dùng (mỗi chỉ số một dòng); 1 lưu ý quan trọng nhất."""
    lines = [title]
    ls = [l for l in result["listings"] if l.get("amount") is not None]
    if not ls:
        lines.append("Không có món nào đang bán")
    else:
        lines.append(f"Min: {short_price(ls[0]['amount'], ls[0]['currency'])}")
        est, ok = estimate(ls, result["total"], rates, game)
        if ok:
            lines.extend(est)
        else:
            counts = {}
            for l in ls:
                k = short_price(l["amount"], l["currency"])
                counts[k] = counts.get(k, 0) + 1
            common = max(counts.items(), key=lambda kv: kv[1])[0]
            lines.append(f"Phổ biến: {common}")
        lines.append(f"{result['total']} món đang bán")
    lines.extend(filter_lines(labels))
    lines.extend(note_lines(notes)[:1])
    return lines


def fmt_each(v: float) -> str:
    if v >= 10:
        return f"{v:.0f}"
    if v >= 1:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


def market_lines(title, mk) -> list[str]:
    """Ô giá theo giao dịch đã khớp trên Currency Exchange (Faustus)."""
    lines = [title]
    if "chaos" in mk:
        lines.append(f"≈ {fmt_each(mk['chaos'])} c/món")
    if "divine" in mk and ("chaos" not in mk or mk["divine"] >= 0.1):
        if mk["divine"] >= 0.1:
            lines.append(f"≈ {fmt_each(mk['divine'])} div/món")
        else:                                   # chaos orb và đồ rẻ: ghi ngược cho dễ đọc
            lines.append(f"1 div ≈ {fmt_each(1 / mk['divine'])} món")
    lines.append(f"Faustus: {mk['volume']} món đã khớp, {mk['hours']} giờ qua")
    return lines


def bulk_lines(title, res, currencies=(("chaos", "c"), ("divine", "div"))) -> list[str]:
    """Ô giá cho đồ bán theo lô: giá mỗi món rẻ nhất tính bằng tiền chính của game, số người bán đang online."""
    lines = [title]
    best = res.get("best") or {}
    if not best:
        return lines + ["Sàn trao đổi: không ai bán"]
    first = True
    for cur, short in currencies:
        if cur in best:
            lines.append(f"{'Rẻ nhất' if first else 'Hoặc'}: {fmt_each(best[cur]['each'])} {short}/món")
            first = False
    lines.append(f"{res.get('sellers', 0)} người bán" + (" (cả offline)" if res.get("offline") else " online"))
    return lines


def note_lines(notes) -> list[str]:
    out = []
    for n in notes:
        if n.startswith("Món không có máu"):
            out.append("! Không máu: giá thật thấp hơn")
        elif n.startswith("Dòng đọc chưa chắc"):
            continue
        elif n.startswith("Không có chỉ số đáng lọc"):
            out.append("! Chỉ tra theo loại món")
        elif n.startswith("Foulborn: có lọc"):
            out.append("Đã lọc đúng dòng đột biến")
        elif n.startswith("Ít món khớp"):
            out.append("! " + n[:MAX_CHARS - 2])
        elif n.startswith("Foulborn: chưa ai bán"):
            out.append("! Chưa ai bán đúng dòng đột biến")
        elif n.startswith("Foulborn"):
            out.append("! Chưa lọc dòng đột biến")
        else:
            out.append("! " + n[:MAX_CHARS - 2])
    unsure = sum(1 for n in notes if n.startswith("Dòng đọc chưa chắc"))
    if unsure:
        out.append(f"! {unsure} dòng đọc chưa chắc, đã bỏ")
    return out


def place(tt_box, img_w, img_h, lines) -> list[float]:
    """Ô giá đặt sát cạnh phải tooltip; không đủ chỗ thì sang cạnh trái. Trả [trái, trên, phải, dưới] 0–1."""
    x0, y0, x1, y1 = tt_box
    tl, tt, tr = x0 / img_w, y0 / img_h, x1 / img_w
    # Rộng cố định (đủ cho MAX_CHARS ký tự): ô "Đang tra giá…" và ô giá nằm đúng một chỗ, không nhảy.
    # cỡ chữ TV ≈ 0.9 × LINE × cao màn hình; chữ rộng ≈ 0.55 cỡ chữ; màn hình 16:9
    w = MAX_CHARS * 0.55 * 0.9 * LINE * (9 / 16) * 1.15 + 0.012
    h = len(lines) * LINE * 1.1 + 0.012
    gap = 0.006
    if tr + gap + w <= 0.995:
        left = tr + gap
    elif tl - gap - w >= 0.005:
        left = tl - gap - w
    else:
        left = max(0.005, 0.995 - w)
    top = min(max(0.005, tt), 0.995 - h)
    return [round(left, 4), round(top, 4), round(left + w, 4), round(top + h, 4)]


def region(text: str, box=None) -> dict:
    r = {"id": REGION_ID, "text": text}
    if box is not None:
        r["box"] = box
        r["line"] = LINE
    return r


class Watcher:
    """Nhận khung hình, quyết định khi nào tìm tooltip, khi nào tra giá và gửi lại ô nào.

    Game vẫn chuyển động phía sau bảng đồ (21/11: màn hình động 4–15% khi đang mở tooltip), nên không chờ cả màn hình
    đứng yên. Thay vào đó: chưa có ô giá thì cứ cách DETECT_GAP giây tìm tooltip một lần trên khung mới nhất; thấy
    tooltip thì hiện ô và chỉ theo dõi riêng vùng tooltip đó. Vùng đó đổi (đóng tooltip, chuyển món) thì xóa ô và tìm lại.
    """

    def __init__(self, D, trade=None, status="securable", log_dir=None, threaded=True, clock=time.monotonic,
                 game="poe1", loader=None, state_path=None, inventory_only=True):
        self.D, self.trade, self.status = D, trade, status
        self.game = game                                 # "poe1" / "poe2": bấm nút Game trong cửa sổ CouchSubs để đổi
        self.loader = loader                             # game -> (dữ liệu, Trade); nạp khi chuyển sang game chưa nạp
        self.games = {game: (D, trade)}
        self.state_path = state_path
        self.inventory_only = inventory_only           # chỉ tra khi đang mở túi đồ/stash/cửa sổ mua bán
        self.log_dir, self.threaded, self.clock = log_dir, threaded, clock
        self.lock = threading.Lock()
        self.prev = None          # ảnh thu nhỏ của khung trước (chỉ để báo mức động)
        self.latest = None        # khung mới nhất
        self.job = None           # lần tìm/tra đang chạy: {"stale": bool}
        self.last_done = -1e9     # lúc lần tìm gần nhất xong
        self.gap = DETECT_GAP     # nghỉ trước lần tìm kế tiếp (dài hơn khi không mở túi đồ)
        self.shown = None         # ô đang hiện: {"box": vùng tooltip, "sig": ảnh vùng đó, "key": nội dung}
        self.pending = []         # ô chờ gửi
        self.history = deque(maxlen=20)
        self.frames = 0
        self.motion = deque(maxlen=200)
        self.window_frames = 0
        self.window_detects = 0   # số lần tìm tooltip trong chu kỳ báo trạng thái
        self.window_found = 0
        self.last_miss_saved = -1e9
        self.changed_frames = 0   # số khung liên tiếp vùng tooltip khác đi (cần 2 khung mới xóa, đỡ nháy)
        self.price_on = True      # bật/tắt trên trang điện thoại
        self.translate_on = False # dịch phụ đề tiếng Việt (CouchSubs bản sửa đọc cờ này)
        self.strict = False       # chế độ lọc chặt (chỉ số từ 100%); mặc định rộng (từ 90%)
        self.by_base = False      # lọc đúng base; mặc định mọi base cùng loại đồ (đồ trắng luôn đúng base)
        self.all_lines = False    # lọc mọi dòng; mặc định chỉ dòng chính (tổng máu/kháng, mod quan trọng)
        self.screen_pending = False
        self.recent = {}          # nội dung tooltip → (lúc tra, các dòng ô giá): mở lại món vừa xem thì hiện ngay
        self.picks = deque(maxlen=10)   # các món vừa tra (khung "Món vừa tra" trên trang để tự chọn dòng)
        self.pick_seq = 0

    # ---- gọi từ máy chủ HTTP ----
    def on_frame(self, img: np.ndarray) -> list[dict]:
        now = self.clock()
        self.frames += 1
        if self.frames == 1:
            print(f"Đã nhận khung hình đầu tiên từ CouchSubs: {img.shape[1]}x{img.shape[0]}", flush=True)
        sig = signature(img)
        start = None
        with self.lock:
            self.latest = img
            self.window_frames += 1
            self.motion.append(1.0 if self.prev is None else frac(sig, self.prev))
            self.prev = sig
            if not self.price_on:
                if self.shown is not None:
                    if not self.shown.get("silent"):
                        self.pending.append(region(""))
                    self.shown = None
                if self.job is not None:
                    self.job["stale"] = True
            elif self.shown is not None:
                if sig_change(crop_sig(img, self.shown["box"]), self.shown["sig"]) > TT_CHANGED:
                    self.changed_frames += 1
                else:
                    self.changed_frames = 0
                if self.changed_frames >= 2:
                    if not self.shown.get("silent"):
                        self.pending.append(region(""))
                    self.shown = None
                    self.changed_frames = 0
                    if self.job is not None:
                        self.job["stale"] = True
            if self.price_on and self.job is None and self.shown is None and now - self.last_done >= self.gap:
                self.job = start = {"stale": False}
        if start is not None:
            if self.threaded:
                threading.Thread(target=self._run, args=(img.copy(), start), daemon=True).start()
            else:
                self._run(img, start)
        with self.lock:
            out, self.pending = self.pending, []
        return out

    # ---- tìm tooltip và tra giá (chạy nền) ----
    def _run(self, img, job):
        t0 = time.time()
        rec = {"at": time.strftime("%H:%M:%S"), "found": False}
        try:
            with self.lock:
                D, trade, game = self.D, self.trade, self.game
                strict, by_base, all_lines = self.strict, self.by_base, self.all_lines
            job["game"] = game
            if self.inventory_only:
                is_open, marks = tooltip.inventory_open(img)
                rec["ui"] = marks
                if not is_open:
                    rec["ui_closed"] = True
                    return          # đang chơi, không mở túi đồ: không tra (đồ dưới đất, chat…)
            tt = tooltip.find(img, D)
            rec["ocr_s"] = round(time.time() - t0, 2)
            if not tt:
                return
            rec["found"] = True
            H, W = img.shape[:2]
            inner = inner_box(tt.box, W, H)
            skip = "chưa giám định" if tooltip.unidentified(tt) else None
            it = None if skip else parse.parse(tt.header, tt.lines, D)
            if it is not None and it.rarity == "Other":
                skip = "đồ bán theo lô"       # currency, fragment, offering, scarab, essence, thẻ bài…: người dùng bỏ
            if skip:
                rec.update(skip=skip, name=it.name if it else " ".join(tt.header))
                # theo dõi im lặng vùng tooltip này (không hiện ô): khỏi đọc lại mỗi 0,25 giây khi món vẫn đang mở
                sig = crop_sig(img, inner)
                with self.lock:
                    if not job["stale"] and self.latest is not None and sig_change(crop_sig(self.latest, inner), sig) <= TT_CHANGED:
                        self.shown = {"box": inner, "sig": sig, "key": "", "silent": True}
                        self.changed_frames = 0
                return
            if it.foulborn:
                parse.add_mutated(it, tooltip.pink_rows(img, tt), D)
            q, notes = query.build(it, status=self.status, game=game, strict=strict, by_base=by_base,
                                   all_lines=all_lines)
            key = "|".join(letters_key(x) for x in tt.header + [l.text for l in tt.lines[:6]])
            rec.update(name=it.name, base=it.base, rarity=it.rarity, notes=notes, game=game)
            title = head(it.title, game)
            wait = [title, "Đang tra giá…"]
            sig = crop_sig(img, inner)
            with self.lock:
                # OCR mất 1–2 giây: tooltip đã đóng hoặc đổi trong lúc đó thì thôi
                if job["stale"] or self.latest is None or sig_change(crop_sig(self.latest, inner), sig) > TT_CHANGED:
                    rec["shown"] = False
                    return
                self.shown = {"box": inner, "sig": sig, "key": key}
                self.changed_frames = 0
                cached = self.recent.get(key)
                if cached and time.time() - cached[0] < RECENT_S:
                    self.pending.append(region("\n".join(cached[1]), place(tt.box, W, H, cached[1])))
                    rec.update(text=cached[1], shown=True, cached=True)
                    return
                self.pending.append(region("\n".join(wait), place(tt.box, W, H, wait)))
            tag = D.trade_tags.get(letters_key(it.name)) if it.rarity == "Other" else None
            if trade is None:
                lines = [title, "(chưa bật tra giá)"] + note_lines(notes)[:1]
            elif tag:
                # đồ bán theo lô (currency, fragment, offering, scarab…): tra trên sàn trao đổi
                from .trade import RateLimited
                try:
                    from .trade import MAIN_CURRENCY
                    stem = D.icon_stems.get(letters_key(it.name))
                    mk = trade.market(stem) if stem else None
                    if mk:
                        lines = market_lines(title, mk)
                        rec["summary"] = {"market": stem, **mk}
                    else:
                        res = trade.exchange_any(tag)
                        lines = bulk_lines(title, res, MAIN_CURRENCY[game])
                        rec["summary"] = {"exchange": tag, "url": res["url"], "cached": res.get("cached")}
                    with self.lock:
                        self.recent[key] = (time.time(), lines)
                except RateLimited as e:
                    lines = [title, f"Trade API bận, chờ {int(getattr(e, 'wait', 0)) + 1} giây"]
                except Exception as e:
                    lines = [title, "Lỗi tra giá", str(e)[:MAX_CHARS]]
                    rec["error"] = str(e)
            else:
                from .trade import RateLimited
                try:
                    try:
                        res = trade.search(q)
                    except RateLimited:
                        raise
                    except Exception:
                        # phòng khi trade realm Sony không nhận bộ lọc foulborn_item: bỏ bộ lọc đó, tra lại 1 lần
                        mf = q["query"].get("filters", {}).get("misc_filters", {}).get("filters", {})
                        if mf.pop("foulborn_item", None) is None:
                            raise
                        rec["foulborn_filter_failed"] = True
                        res = trade.search(q)
                    # Dòng: TẤT CẢ ra quá ít món: nới nhóm đếm, bớt 1 dòng rồi 2 dòng
                    cg = next((g for g in q["query"]["stats"] if g["type"] == "count"), None)
                    if all_lines and cg and res["total"] < RELAX_MIN:
                        n_all = len(cg["filters"])
                        for need in (n_all - 1, n_all - 2):
                            if need < 1:
                                break
                            cg["value"]["min"] = need
                            res = trade.search(q)
                            if res["total"] >= RELAX_MIN:
                                break
                        notes = [f"Ít món khớp đủ {n_all}/{n_all} → tự nới"] + notes
                    if it.foulborn and it.mutated and res["total"] == 0:
                        q["query"]["stats"] = [{"type": "and", "filters": []}]
                        res = trade.search(q)
                        notes = [n for n in notes if not n.startswith("Foulborn")] + ["Foulborn: chưa ai bán đúng dòng đột biến"]
                    try:
                        from .trade import get_rates
                        rates = get_rates(trade) if res["listings"] else None
                    except Exception:
                        rates = None             # không có tỉ giá: ô giá hiện giá phổ biến như cũ
                    lines = price_lines(title, res, notes, query.labels(q, it), rates, game)
                    try:
                        pn = pick.panel(it, game, strict, by_base, all_lines, auto_q=q)
                    except Exception:
                        pn = None                # bảng chọn dòng lỗi không được làm hỏng ô giá trên TV
                        traceback.print_exc()
                    if pn is not None:
                        with self.lock:
                            self.pick_seq += 1
                            self.picks.append({"id": self.pick_seq, "at": time.strftime("%H:%M"), "item": it,
                                               "panel": pn, "auto": price_lines(title, res, [], None, rates, game)[1:],
                                               "auto_url": res.get("url"), "sel": None, "result": None, "url": None})
                    if rec.get("foulborn_filter_failed"):
                        lines.append("! Trade không lọc được Foulborn")
                    with self.lock:
                        # đã đổi chế độ lọc / game trong lúc tra: giá này của chế độ cũ, không nhớ
                        if (game, strict, by_base, all_lines) == (self.game, self.strict, self.by_base, self.all_lines):
                            self.recent[key] = (time.time(), lines)
                    rec["summary"] = {"total": res["total"], "url": res["url"], "cached": res.get("cached")}
                except RateLimited as e:
                    lines = [title, f"Trade API bận, chờ {int(getattr(e, 'wait', 0)) + 1} giây"]
                except Exception as e:  # mạng, 403…
                    lines = [title, "Lỗi tra giá", str(e)[:MAX_CHARS]]
                    rec["error"] = str(e)
            rec["text"] = lines
            with self.lock:
                ok = (not job["stale"] and self.shown is not None and self.shown["key"] == key
                      and job.get("game") == self.game)
                if ok:
                    self.pending.append(region("\n".join(lines), place(tt.box, W, H, lines)))
            rec["shown"] = ok
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            rec["seconds"] = round(time.time() - t0, 2)
            with self.lock:
                self.job = None
                self.last_done = self.clock()
                self.gap = UI_IDLE_GAP if rec.get("ui_closed") else DETECT_GAP
                self.history.appendleft(rec)
                self.window_detects += 1
                self.window_found += 1 if rec.get("found") else 0
            if rec.get("skip"):
                print(f"[{rec['at']}] {rec.get('name') or ''} → bỏ qua ({rec['skip']})", flush=True)
            elif rec.get("found") or rec.get("error"):
                shown = "" if rec.get("shown") else "  [không hiện: tooltip đã đổi]"
                print(f"[{rec['at']}] {rec.get('base') or ''} {rec.get('name') or ''} → "
                      f"{' | '.join(rec.get('text', [])) or rec.get('error', '')} ({rec['seconds']}s){shown}", flush=True)
                self._save(img, rec)
            elif time.time() - self.last_miss_saved >= MISS_SAVE_GAP:
                self.last_miss_saved = time.time()      # thỉnh thoảng lưu 1 ảnh không thấy tooltip để xem lại
                self._save(img, rec)

    def next_ms(self) -> int:
        """Bao lâu nữa CouchSubs gửi khung kế tiếp: thưa khi không cần, dày khi đang mở túi đồ hoặc đang hiện ô giá."""
        with self.lock:
            if not self.price_on:
                return OFF_NEXT_MS
            if self.shown is None and self.job is None and self.gap == UI_IDLE_GAP:
                return IDLE_NEXT_MS
            return NEXT_MS

    def flags(self) -> dict:
        """Cờ gửi kèm mỗi phản hồi cho CouchSubs: dịch bật/tắt, yêu cầu dịch cả màn hình (một lần)."""
        with self.lock:
            screen, self.screen_pending = self.screen_pending, False
            return {"price": self.price_on, "translate": self.translate_on, "screen": screen, "game": self.game}

    def set(self, price=None, translate=None, screen=False, game=None, strict=None, base=None, lines=None):
        """Nút trong CouchSubs gọi /set; không kèm thay đổi nào thì chỉ là hỏi trạng thái (mỗi vài giây), không in gì."""
        if game is not None and game != self.game:
            self.switch_game(game)
        with self.lock:
            before = (self.price_on, self.translate_on)
            if price is not None:
                self.price_on = price
            if translate is not None:
                self.translate_on = translate
            if screen:
                self.screen_pending = True
            if strict is not None and strict != self.strict:
                self.strict = strict
                self._mode_changed()
                print(f"[{time.strftime('%H:%M:%S')}] Chế độ lọc: {'CHẶT (100%)' if strict else 'RỘNG (90%)'}", flush=True)
            if base is not None and base != self.by_base:
                self.by_base = base
                self._mode_changed()
                print(f"[{time.strftime('%H:%M:%S')}] Base: {'ĐÚNG BASE' if base else 'MỌI BASE'}", flush=True)
            if lines is not None and lines != self.all_lines:
                self.all_lines = lines
                self._mode_changed()
                print(f"[{time.strftime('%H:%M:%S')}] Dòng: {'TẤT CẢ' if lines else 'CHÍNH'}", flush=True)
            changed = before != (self.price_on, self.translate_on)
        if not (changed or screen):
            return
        print(f"[{time.strftime('%H:%M:%S')}] {GAME_NAME[self.game]} · tra giá {'BẬT' if self.price_on else 'TẮT'}"
              f" · dịch {'BẬT' if self.translate_on else 'TẮT'}" + (" · dịch cả màn hình" if screen else ""), flush=True)

    # ---- tự chọn dòng (khung "Món vừa tra" trên trang) ----
    def pick_get(self, pid=None):
        with self.lock:
            if not self.picks:
                return None
            if pid is None:
                return self.picks[-1]
            return next((p for p in self.picks if p["id"] == pid), None)

    def pick_latest_id(self):
        with self.lock:
            return self.picks[-1]["id"] if self.picks else 0

    def pick_apply(self, pid, sel, act):
        """Nút trên khung "Món vừa tra": chọn nhanh (main / all / none) hoặc TRA LẠI. Kết quả chỉ hiện trên trang:
        không lên TV, không ghi vào giá đã nhớ của TV."""
        rec = self.pick_get(pid)
        if rec is None:
            return None
        if act in ("main", "all", "none"):
            rec["sel"] = pick.preset(rec["panel"], act, sel)
            return rec
        rec["sel"] = sel
        game = rec["panel"]["game"]
        trade = (self.games.get(game) or (None, None))[1]
        if trade is None:
            rec["result"], rec["url"] = ["Chưa bật tra giá"], None
            return rec
        from .trade import RateLimited, get_rates
        q, _ = pick.build(rec["item"], rec["panel"], sel, status=self.status)
        try:
            res = trade.search(q)
            try:
                rates = get_rates(trade) if res["listings"] else None
            except Exception:
                rates = None
            rec["result"] = price_lines(rec["item"].title, res, [], None, rates, game)[1:]
            rec["url"] = res.get("url")
        except RateLimited as e:
            rec["result"], rec["url"] = [f"Trade API bận, chờ {int(getattr(e, 'wait', 0)) + 1} giây rồi bấm lại"], None
        except Exception as e:
            rec["result"], rec["url"] = ["Lỗi tra giá", str(e)[:120]], None
        print(f"[{time.strftime('%H:%M:%S')}] Tra lại {rec['item'].title}: {' | '.join(rec['result'])}", flush=True)
        return rec

    def _mode_changed(self):
        """Đổi chế độ lọc (gọi khi đang giữ lock): quên giá đã nhớ (của chế độ cũ), xóa ô đang hiện để món đang mở được
        tra lại ngay theo chế độ mới."""
        if self.shown is not None:
            self.pending.append(region(""))
            self.shown = None
        if self.job is not None:
            self.job["stale"] = True
        self.recent.clear()

    def switch_game(self, game):
        """Đổi PoE1 ⇄ PoE2: nạp dữ liệu game đó nếu chưa có (vài giây, lần đầu), xóa ô giá đang hiện, nhớ lựa chọn."""
        if game not in GAME_NAME:
            return
        if game not in self.games:
            if self.loader is None:
                print(f"Không chuyển được sang {GAME_NAME[game]}: chưa có bộ nạp dữ liệu", flush=True)
                return
            print(f"Đang nạp dữ liệu {GAME_NAME[game]}…", flush=True)
            try:
                self.games[game] = self.loader(game)
            except Exception as e:
                print(f"Không nạp được dữ liệu {GAME_NAME[game]}: {e}", flush=True)
                return
        with self.lock:
            self.game = game
            self.D, self.trade = self.games[game]
            if self.shown is not None:
                self.pending.append(region(""))
                self.shown = None
            if self.job is not None:
                self.job["stale"] = True
            self.recent.clear()
        if self.state_path:
            try:
                with open(self.state_path, "w", encoding="utf-8") as f:
                    json.dump({"game": game}, f)
            except OSError:
                pass
        tr = self.trade
        try:
            league = tr.pick_league() if tr else "-"
        except Exception as e:
            league = f"chưa lấy được ({e})"
        print(f"[{time.strftime('%H:%M:%S')}] Đã chuyển sang {GAME_NAME[game]} · league {league}", flush=True)

    def status_line(self):
        """Một dòng tóm tắt 10 giây vừa qua."""
        with self.lock:
            n, self.window_frames = self.window_frames, 0
            d, self.window_detects = self.window_detects, 0
            f, self.window_found = self.window_found, 0
            motion = sorted(self.motion)
            self.motion.clear()
            img = self.latest
            showing = self.shown is not None
        if not n or img is None:
            return None
        med = motion[len(motion) // 2] * 100 if motion else 0
        return (f"[{time.strftime('%H:%M:%S')}] {n} khung/10 giây ({img.shape[1]}x{img.shape[0]}, động {med:.0f}%), "
                f"tìm tooltip {d} lần, thấy {f}" + (" · đang hiện ô giá" if showing else ""))

    def snap(self):
        with self.lock:
            img = self.latest
        if img is None or not self.log_dir:
            return None
        os.makedirs(self.log_dir, exist_ok=True)
        path = os.path.join(self.log_dir, f"snap-{time.strftime('%Y%m%d-%H%M%S')}.png")
        ok, buf = cv2.imencode(".png", img)
        if ok:
            buf.tofile(path)
            return path
        return None

    def _save(self, img, rec):
        if not self.log_dir:
            return
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                buf.tofile(os.path.join(self.log_dir, f"{stamp}.jpg"))
            with open(os.path.join(self.log_dir, "lookups.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"file": f"{stamp}.jpg", **rec}, ensure_ascii=False) + "\n")
            jpgs = sorted(p for p in os.listdir(self.log_dir) if p.endswith(".jpg"))
            for old in jpgs[:-MAX_SAVED]:
                os.remove(os.path.join(self.log_dir, old))
        except OSError:
            pass


def decode(body: bytes, w: int, h: int, stride: int) -> np.ndarray:
    if w <= 0 or h <= 0 or stride < w * 4 or len(body) < (h - 1) * stride + w * 4:
        raise ValueError(f"khung hình sai kích thước: {w}x{h}, stride {stride}, {len(body)} byte")
    buf = np.frombuffer(body, dtype=np.uint8)
    if len(buf) < h * stride:
        buf = np.concatenate([buf, np.zeros(h * stride - len(buf), np.uint8)])
    rows = buf[: h * stride].reshape(h, stride)[:, : w * 4].reshape(h, w, 4)
    return np.ascontiguousarray(rows[:, :, :3])     # BGRA → BGR


def pick_section(w: Watcher, pid=None) -> str:
    """Khung "Món vừa tra": chọn dòng, sửa mức tối thiểu, TRA LẠI (kết quả chỉ hiện ở đây, TV giữ giá tự tra)."""
    from html import escape as esc
    rec = w.pick_get(pid)
    if rec is None:
        return ""
    it, pn, sel = rec["item"], rec["panel"], rec["sel"] or {}
    on = pick._sel_on(pn, sel)
    edit = sel.get("edit") or {}
    _, summ = pick.build(it, pn, sel)
    rows_html = {"base": [], "gear": [], "implicit": [], "explicit": []}
    for r in pn["rows"]:
        key = r["key"]
        box = (f'<input type="checkbox" name="on_{key}" value="1"{" checked" if on[key] else ""}'
               f'{"" if r["enabled"] else " disabled"}>')
        num = ""
        shown = pick.show_min(pn, r)
        if r["section"] != "base" or key == "ilvl":
            if shown is not None and r["enabled"]:
                val = edit.get(key, shown)
                num = (f'≥ <input type="number" step="any" name="min_{key}" value="{query._num(val)}">'
                       f'<input type="hidden" name="min0_{key}" value="{query._num(shown)}">')
        tag = f'<td class="t">{esc(r["tag"])}</td>' if r["tag"] else '<td class="t"></td>'
        cls = "" if (on[key] and r["enabled"]) else ' class="off"'
        rows_html[r["section"]].append(f'<tr><td class="c">{box}</td><td{cls}>{esc(r["text"])}</td>{tag}'
                                       f'<td class="v">{num}</td></tr>')
    for _ in range(pn["slots"] - pn["n_explicit"]):
        rows_html["explicit"].append('<tr class="none"><td class="c"></td><td>none</td><td></td><td></td></tr>')
    keys = ",".join(r["key"] for r in pn["rows"] if r["enabled"])

    def sec(title, name):
        return f'<div class="sec">{title}</div><table>{"".join(rows_html[name])}</table>' if rows_html[name] else ""

    expl_title = f"DÒNG TRÊN MÓN · {pn['n_explicit']}/{pn['slots']}" if pn["slots"] else "DÒNG TRÊN MÓN"
    bullets = "".join(f"• {esc(x)}<br>" for x in summ["req"] + summ["stats"])
    need = (f'Cần đủ <input type="number" name="need" min="1" max="{summ["of"]}" value="{summ["need"]}" '
            f'style="width:52px"><input type="hidden" name="need0" value="{summ["need"]}"> / {summ["of"]} dòng'
            if summ["of"] else "Chưa chọn dòng chỉ số nào")
    res, url = (rec["result"], rec["url"]) if rec["result"] else (rec["auto"], rec["auto_url"])
    res_title = "KẾT QUẢ TRA LẠI (chỉ hiện ở đây, TV giữ giá tự tra)" if rec["result"] else "GIÁ TỰ TRA"
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener">Mở trên trang trade ›</a>' if url else ""
    latest = w.pick_latest_id()
    return f"""<div class="card" id="pick"><form method="post" action="/pick">
<input type="hidden" name="id" value="{rec['id']}"><input type="hidden" name="keys" value="{keys}">
<div class="hd"><b>Món vừa tra: {esc(it.title)} ({esc(it.rarity)} · {GAME_NAME.get(pn['game'], '')})</b>
<span>tra lúc {rec['at']}</span></div>
<a id="newer" class="newer" href="/"{"" if latest != rec["id"] else ' style="display:none"'}>Có món mới · Xem món mới ›</a>
{sec("BASE", "base")}{sec("CHỈ SỐ MÓN", "gear")}{sec("IMPLICIT", "implicit")}{sec(expl_title, "explicit")}
<div class="bottom"><div><div class="box"><div class="s">KHI TRA (ĐÃ GỘP)</div>{bullets}{need}</div>
<div class="btns"><button class="g" name="act" value="main">Dòng chính</button><button class="g" name="act" value="all">Tất cả</button>
<button class="g" name="act" value="none">Bỏ hết</button></div>
<button class="go" name="act" value="retry">TRA LẠI</button></div>
<div class="box"><div class="s">{res_title}</div>{"<br>".join(esc(x) for x in res)}<br>{link}</div></div>
</form></div>
<script>
var cur={rec['id']};
setInterval(function(){{fetch('/pick_latest').then(function(r){{return r.json()}}).then(function(j){{
 if(j.id&&j.id!==cur)document.getElementById('newer').style.display='block';}}).catch(function(){{}});}},3000);
</script>"""


def parse_pick_form(w: Watcher, body: str):
    """Đọc form khung "Món vừa tra" → (id, lựa chọn, nút đã bấm)."""
    from urllib.parse import parse_qs
    f = {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}
    pid = int(f.get("id", "0") or 0)
    keys = [k for k in f.get("keys", "").split(",") if k]
    sel = {"on": {k: f.get(f"on_{k}") == "1" for k in keys}, "edit": {}, "need": None}
    for k in keys:
        a, b = f.get(f"min_{k}"), f.get(f"min0_{k}")
        if a is None or a.strip() == "" or a == b:
            continue
        try:
            v = float(a.replace(",", "."))
        except ValueError:
            continue
        sel["edit"][k] = int(v) if v.is_integer() else v
    if f.get("need") not in (None, "", f.get("need0")):
        try:
            sel["need"] = int(float(f["need"]))
        except ValueError:
            pass
    act = f.get("act", "retry")
    return pid, sel, act if act in ("main", "all", "none", "retry") else "retry"


def control_page(w: Watcher, pid=None) -> str:
    """Trang bật/tắt mở trên điện thoại (cùng mạng nhà)."""
    def btn(name, on, label):
        state, color, nxt = ("ĐANG BẬT", "#2e7d32", "0") if on else ("ĐANG TẮT", "#555", "1")
        return (f'<a class="b" style="background:{color}" href="/set?{name}={nxt}">{label}<br><small>{state} · bấm để '
                f'{"tắt" if on else "bật"}</small></a>')
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PoE TV</title>
<style>body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}}
h1{{font-size:20px}} .b{{display:block;color:#fff;text-decoration:none;text-align:center;font-size:22px;
padding:22px 10px;margin:12px 0;border-radius:14px}} small{{font-size:14px;opacity:.85}} p{{color:#aaa;font-size:14px}}
.card{{background:#1c1e23;border:1.5px solid #f5b83d;border-radius:14px;padding:12px 14px;margin:16px 0;max-width:900px}}
.hd{{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}} .hd b{{font-size:17px}}
.hd span{{color:#a9a59c;font-size:13px}} .newer{{display:block;background:#3a3020;color:#f5b83d;border-radius:10px;
padding:8px 10px;margin-top:8px;text-decoration:none}} .sec{{color:#a9a59c;font-size:11px;font-weight:700;letter-spacing:.06em;
margin:12px 0 4px}} table{{width:100%;border-collapse:collapse}} td{{padding:6px 4px;border-bottom:1px solid #2a2d33;font-size:15px}}
td.c{{width:26px}} td.v{{width:104px;text-align:right;white-space:nowrap}} td.t{{color:#a9a59c;font-size:12px;width:200px}}
.off{{color:#8d8a83}} .none td{{color:#5f5d59;font-style:italic}}
input[type=checkbox]{{width:18px;height:18px;accent-color:#f5b83d}}
input[type=number]{{width:64px;background:#25282f;color:#f3eee4;border:1px solid #3a3d44;border-radius:8px;padding:5px;font-size:15px;text-align:right}}
.bottom{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:12px}}
.box{{background:#25282f;border-radius:12px;padding:10px 12px;font-size:15px;line-height:1.55}} .box a{{color:#a9a59c}}
.box .s{{color:#a9a59c;font-size:11px;font-weight:700;letter-spacing:.06em}} .btns{{display:flex;gap:8px;margin-top:10px}}
.g{{flex:1;background:#25282f;border:1px solid #3a3d44;color:#eee;border-radius:10px;padding:9px;font-size:14px}}
.go{{display:block;width:100%;background:#f5b83d;color:#17181b;font-weight:700;border:0;border-radius:12px;padding:13px;
font-size:17px;margin-top:10px}}</style>
</head><body><h1>PoE TV</h1>
{btn("price", w.price_on, "Tra giá")}
{btn("translate", w.translate_on, "Dịch phụ đề tiếng Việt")}
<a class="b" style="background:#1565c0" href="/set?screen=1">Dịch cả màn hình<br><small>bấm lần nữa để tắt</small></a>
<a class="b" style="background:#ef6c00" href="/set?strict={"0" if w.strict else "1"}">Lọc: {"CHẶT (100%)" if w.strict else "RỘNG (90%)"}<br><small>bấm để chuyển sang {"rộng" if w.strict else "chặt"}</small></a>
<a class="b" style="background:#ef6c00" href="/set?base={"0" if w.by_base else "1"}">Base: {"ĐÚNG BASE" if w.by_base else "MỌI BASE"}<br><small>bấm để chuyển sang {"mọi base" if w.by_base else "đúng base"}</small></a>
<a class="b" style="background:#ef6c00" href="/set?lines={"0" if w.all_lines else "1"}">Dòng: {"TẤT CẢ" if w.all_lines else "CHÍNH"}<br><small>bấm để chuyển sang {"chính" if w.all_lines else "tất cả"}</small></a>
<a class="b" style="background:#6a1b9a" href="/set?game={"poe1" if w.game == "poe2" else "poe2"}">Game: {GAME_NAME[w.game]}<br><small>bấm để chuyển sang {"PoE1" if w.game == "poe2" else "PoE2"}</small></a>
{pick_section(w, pid)}
<p>Dịch cần khóa AI (Gemini miễn phí hoặc Claude) nhập trong CouchSubs › Settings › Translation. Chưa có khóa thì phụ đề giữ tiếng Anh.</p>
</body></html>"""


def lan_ip() -> str | None:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.1.1", 9))      # không gửi gì, chỉ để biết địa chỉ trong mạng nhà
            return s.getsockname()[0]
    except OSError:
        return None


def make_handler(watcher: Watcher):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/pick":
                n = int(self.headers.get("Content-Length", "0"))
                pid, sel, act = parse_pick_form(watcher, self.rfile.read(n).decode("utf-8", "replace"))
                rec = watcher.pick_apply(pid, sel, act)
                self.send_response(303)
                self.send_header("Location", f"/?id={rec['id']}#pick" if rec else "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path != "/frame":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                w, h = int(self.headers.get("X-Width", "0")), int(self.headers.get("X-Height", "0"))
                stride = int(self.headers.get("X-Stride", str(w * 4)))
                regions = watcher.on_frame(decode(body, w, h, stride))
                self._json(200, {"regions": regions, "next_ms": watcher.next_ms(), **watcher.flags()})
            except Exception as e:
                self._json(400, {"error": str(e), "next_ms": 2000})

        def do_GET(self):
            path, _, qs = self.path.partition("?")
            if path in ("/", "/set"):
                if path == "/set":
                    from urllib.parse import parse_qs
                    q = {k: v[-1] for k, v in parse_qs(qs).items()}
                    onoff = lambda k: None if k not in q else q[k] == "1"
                    watcher.set(price=onoff("price"), translate=onoff("translate"), screen=q.get("screen") == "1",
                                game=q.get("game") if q.get("game") in GAME_NAME else None, strict=onoff("strict"),
                                base=onoff("base"), lines=onoff("lines"))
                    if q.get("json") == "1":          # nút trong cửa sổ CouchSubs bản sửa
                        return self._json(200, watcher.flags())
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                from urllib.parse import parse_qs
                pq = {k: v[-1] for k, v in parse_qs(qs).items()}
                pid = int(pq["id"]) if pq.get("id", "").isdigit() else None
                body = control_page(watcher, pid).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/pick_latest":
                return self._json(200, {"id": watcher.pick_latest_id()})
            if self.path == "/health":
                return self._json(200, {"ok": True, "frames": watcher.frames})
            if self.path == "/snap":
                path = watcher.snap()
                return self._json(200 if path else 404, {"saved": path} if path else {"error": "chưa có khung hình nào"})
            if self.path == "/last":
                with watcher.lock:
                    return self._json(200, list(watcher.history))
            self._json(404, {"error": "not found"})

        def log_message(self, fmt, *args):   # không in mỗi khung hình
            pass

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(prog="poe_tv.serve", description="Máy chủ tra giá cho bản sửa CouchSubs")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = chỉ máy này (mặc định; bật/tắt bằng nút trong CouchSubs); 0.0.0.0 = mở thêm trang bật/tắt trên điện thoại")
    ap.add_argument("--no-trade", action="store_true", help="chỉ đọc món, không tra giá")
    ap.add_argument("--everywhere", action="store_true",
                    help="tra cả khi không mở túi đồ (mặc định chỉ tra khi màn hình có dòng gợi ý Navigate/Compare ở đáy)")
    ap.add_argument("--status", default="securable", help="securable = mua ngay (mặc định) | available | online | any")
    ap.add_argument("--league", default=None, help="league PoE1 (mặc định tự lấy league mới nhất của realm)")
    ap.add_argument("--league2", default=None, help="league PoE2 (mặc định tự lấy league trade2 mới nhất)")
    ap.add_argument("--realm", default="sony", help="realm PoE1")
    ap.add_argument("--game", choices=list(GAME_NAME), default=None,
                    help="game lúc mở; mặc định là game chọn lần trước (nút Game trong CouchSubs), lần đầu là poe1")
    ap.add_argument("--logs", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"),
                    help="thư mục lưu ảnh và kết quả các lần tra gần nhất ('' để tắt)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_path = os.path.join(root, "state.json")
    game = a.game
    if game is None:
        try:
            with open(state_path, encoding="utf-8") as f:
                game = json.load(f).get("game")
        except (OSError, ValueError):
            game = None
    if game not in GAME_NAME:
        game = "poe1"

    def loader(g):
        """Nạp dữ liệu món đồ/mod và tạo Trade cho một game."""
        D = data.load(g)
        tr = None
        if not a.no_trade:
            from .trade import Trade
            tr = Trade(realm=a.realm, league=a.league if g == "poe1" else a.league2, game=g)
        return D, tr

    print(f"Đang nạp dữ liệu {GAME_NAME[game]} và mô hình OCR…", flush=True)
    D, tr = loader(game)
    from . import ocr
    ocr.read(np.full((64, 256, 3), 255, np.uint8))     # nạp sẵn mô hình, lần tra đầu không bị chậm
    if tr is not None:
        try:
            print(f"{GAME_NAME[game]} · league: {tr.pick_league()}" + (f" (realm {a.realm})" if game == "poe1" else ""),
                  flush=True)
        except Exception as e:
            print(f"Chưa lấy được league ({e}); sẽ thử lại khi tra", flush=True)
    w = Watcher(D, tr, status=a.status, log_dir=a.logs or None, game=game, loader=loader, state_path=state_path,
                inventory_only=not a.everywhere)

    def report():
        waited = 0
        while True:
            time.sleep(10)
            line = w.status_line()
            if line:
                print(line, flush=True)
                waited = 0
            else:
                waited += 10
                if waited in (30, 120) or (waited and waited % 600 == 0):
                    print(f"[{time.strftime('%H:%M:%S')}] chưa nhận khung hình nào từ CouchSubs "
                          f"(đã bấm Start translating chưa?)", flush=True)
    threading.Thread(target=report, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(w))
    print(f"Sẵn sàng: http://127.0.0.1:{a.port}  (Ctrl+C để dừng). Mở CouchSubs bản poe-tv rồi bấm bắt đầu.",
          flush=True)
    ip = lan_ip() if a.host == "0.0.0.0" else None
    if ip:
        print(f"Bật/tắt tra giá và dịch trên điện thoại (cùng Wi-Fi): http://{ip}:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
