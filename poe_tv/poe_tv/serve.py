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

from . import data, parse, query, tooltip

REGION_ID = "poe-price"
GAME_NAME = {"poe1": "PoE1", "poe2": "PoE2"}
SIG_W, SIG_H = 96, 54     # ảnh thu nhỏ để so khung hình
PIX_DIFF = 20             # một điểm ảnh đổi hơn mức này (0–255) coi là "đổi"
DETECT_GAP = 0.25         # chưa có ô giá: nghỉ bấy nhiêu giây giữa 2 lần tìm tooltip
TT_CHANGED = 0.5          # bản đồ chữ vùng tooltip khác quá tỉ lệ này = tooltip đã đóng hoặc sang món khác
TT_SIG = (96, 96)         # số ô của bản đồ chữ vùng tooltip
TEXT_LEVEL = 110          # điểm ảnh sáng hơn mức này (0–255) coi là chữ
MISS_SAVE_GAP = 30        # không thấy tooltip: cứ bấy nhiêu giây lưu lại 1 ảnh để xem
RECENT_S = 600            # mở lại đúng món đã tra trong bấy nhiêu giây thì hiện giá cũ ngay, không tra lại
NEXT_MS = 300             # CouchSubs gửi khung kế tiếp sau bấy nhiêu ms
LINE = 0.026              # chiều cao một dòng chữ trên TV (tỉ lệ chiều cao màn hình)
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
    """Dòng đầu ô giá. Chế độ PoE2 ghi rõ để lỡ quên chuyển game thì nhìn là biết."""
    return (("PoE2 · " if game == "poe2" else "") + title)[:MAX_CHARS]


def price_lines(title, result, notes) -> list[str]:
    """Nội dung ô giá: tên món, giá rẻ nhất, giá phổ biến + số món, 1 lưu ý quan trọng nhất."""
    lines = [title]
    ls = [l for l in result["listings"] if l.get("amount") is not None]
    if not ls:
        lines.append("Không có món nào đang bán")
    else:
        lines.append(f"Rẻ nhất: {short_price(ls[0]['amount'], ls[0]['currency'])}")
        counts = {}
        for l in ls:
            k = short_price(l["amount"], l["currency"])
            counts[k] = counts.get(k, 0) + 1
        common = max(counts.items(), key=lambda kv: kv[1])[0]
        lines.append(f"Phổ biến: {common} · {result['total']} món")
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
                 game="poe1", loader=None, state_path=None):
        self.D, self.trade, self.status = D, trade, status
        self.game = game                                 # "poe1" / "poe2": bấm nút Game trong cửa sổ CouchSubs để đổi
        self.loader = loader                             # game -> (dữ liệu, Trade); nạp khi chuyển sang game chưa nạp
        self.games = {game: (D, trade)}
        self.state_path = state_path
        self.log_dir, self.threaded, self.clock = log_dir, threaded, clock
        self.lock = threading.Lock()
        self.prev = None          # ảnh thu nhỏ của khung trước (chỉ để báo mức động)
        self.latest = None        # khung mới nhất
        self.job = None           # lần tìm/tra đang chạy: {"stale": bool}
        self.last_done = -1e9     # lúc lần tìm gần nhất xong
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
        self.screen_pending = False
        self.recent = {}          # nội dung tooltip → (lúc tra, các dòng ô giá): mở lại món vừa xem thì hiện ngay

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
                    self.pending.append(region(""))
                    self.shown = None
                    self.changed_frames = 0
                    if self.job is not None:
                        self.job["stale"] = True
            if self.price_on and self.job is None and self.shown is None and now - self.last_done >= DETECT_GAP:
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
            job["game"] = game
            tt = tooltip.find(img, D)
            rec["ocr_s"] = round(time.time() - t0, 2)
            if not tt:
                return
            rec["found"] = True
            H, W = img.shape[:2]
            inner = inner_box(tt.box, W, H)
            it = parse.parse(tt.header, tt.lines, D)
            if it.foulborn:
                parse.add_mutated(it, tooltip.pink_rows(img, tt), D)
            q, notes = query.build(it, status=self.status, game=game)
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
                    if it.foulborn and it.mutated and res["total"] == 0:
                        q["query"]["stats"] = [{"type": "and", "filters": []}]
                        res = trade.search(q)
                        notes = [n for n in notes if not n.startswith("Foulborn")] + ["Foulborn: chưa ai bán đúng dòng đột biến"]
                    lines = price_lines(title, res, notes)
                    if rec.get("foulborn_filter_failed"):
                        lines.append("! Trade không lọc được Foulborn")
                    with self.lock:
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
                self.history.appendleft(rec)
                self.window_detects += 1
                self.window_found += 1 if rec.get("found") else 0
            if rec.get("found") or rec.get("error"):
                shown = "" if rec.get("shown") else "  [không hiện: tooltip đã đổi]"
                print(f"[{rec['at']}] {rec.get('base') or ''} {rec.get('name') or ''} → "
                      f"{' | '.join(rec.get('text', [])) or rec.get('error', '')} ({rec['seconds']}s){shown}", flush=True)
                self._save(img, rec)
            elif time.time() - self.last_miss_saved >= MISS_SAVE_GAP:
                self.last_miss_saved = time.time()      # thỉnh thoảng lưu 1 ảnh không thấy tooltip để xem lại
                self._save(img, rec)

    def flags(self) -> dict:
        """Cờ gửi kèm mỗi phản hồi cho CouchSubs: dịch bật/tắt, yêu cầu dịch cả màn hình (một lần)."""
        with self.lock:
            screen, self.screen_pending = self.screen_pending, False
            return {"price": self.price_on, "translate": self.translate_on, "screen": screen, "game": self.game}

    def set(self, price=None, translate=None, screen=False, game=None):
        if game is not None and game != self.game:
            self.switch_game(game)
        with self.lock:
            if price is not None:
                self.price_on = price
            if translate is not None:
                self.translate_on = translate
            if screen:
                self.screen_pending = True
        print(f"[{time.strftime('%H:%M:%S')}] {GAME_NAME[self.game]} · tra giá {'BẬT' if self.price_on else 'TẮT'}"
              f" · dịch {'BẬT' if self.translate_on else 'TẮT'}" + (" · dịch cả màn hình" if screen else ""), flush=True)

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


def control_page(w: Watcher) -> str:
    """Trang bật/tắt mở trên điện thoại (cùng mạng nhà)."""
    def btn(name, on, label):
        state, color, nxt = ("ĐANG BẬT", "#2e7d32", "0") if on else ("ĐANG TẮT", "#555", "1")
        return (f'<a class="b" style="background:{color}" href="/set?{name}={nxt}">{label}<br><small>{state} · bấm để '
                f'{"tắt" if on else "bật"}</small></a>')
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PoE TV</title>
<style>body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}}
h1{{font-size:20px}} .b{{display:block;color:#fff;text-decoration:none;text-align:center;font-size:22px;
padding:22px 10px;margin:12px 0;border-radius:14px}} small{{font-size:14px;opacity:.85}} p{{color:#aaa;font-size:14px}}</style>
</head><body><h1>PoE TV</h1>
{btn("price", w.price_on, "Tra giá")}
{btn("translate", w.translate_on, "Dịch phụ đề tiếng Việt")}
<a class="b" style="background:#1565c0" href="/set?screen=1">Dịch cả màn hình<br><small>bấm lần nữa để tắt</small></a>
<a class="b" style="background:#6a1b9a" href="/set?game={"poe1" if w.game == "poe2" else "poe2"}">Game: {GAME_NAME[w.game]}<br><small>bấm để chuyển sang {"PoE1" if w.game == "poe2" else "PoE2"}</small></a>
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
            if self.path != "/frame":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                w, h = int(self.headers.get("X-Width", "0")), int(self.headers.get("X-Height", "0"))
                stride = int(self.headers.get("X-Stride", str(w * 4)))
                regions = watcher.on_frame(decode(body, w, h, stride))
                self._json(200, {"regions": regions, "next_ms": NEXT_MS, **watcher.flags()})
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
                                game=q.get("game") if q.get("game") in GAME_NAME else None)
                    if q.get("json") == "1":          # nút trong cửa sổ CouchSubs bản sửa
                        return self._json(200, watcher.flags())
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = control_page(watcher).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
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
    w = Watcher(D, tr, status=a.status, log_dir=a.logs or None, game=game, loader=loader, state_path=state_path)

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
