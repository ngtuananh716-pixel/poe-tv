"""Chế độ máy chủ cho bản sửa CouchSubs (poe-tv).

Chạy:  python -m poe_tv.serve            (mặc định cổng 127.0.0.1:8788)

Bản sửa CouchSubs gửi khung hình Remote Play tới  POST /frame  khoảng 3 lần/giây:
  - thân: ảnh BGRA thô;  header X-Width, X-Height, X-Stride
  - trả về JSON  {"regions": [{"id", "text", "box": [trái, trên, phải, dưới], "line"}], "next_ms": n}
    (tọa độ là tỉ lệ 0–1 của khung hình; text rỗng = xóa ô)
CouchSubs chỉ chuyển nguyên các ô này sang app TV (SubtitleServer.SendRegion). Mọi quyết định
(khi nào tra, đặt ô ở đâu, viết gì) nằm ở đây, để sửa mà không phải build lại CouchSubs.

Cách tra: khi màn hình đứng yên khoảng STABLE_S giây (mở tooltip thì game dừng) và khác chỗ đã
tra lần trước → tìm tooltip → đọc món → tra giá realm Sony → hiện ô giá cạnh tooltip.
Màn hình đổi (đóng tooltip, chuyển món) → xóa ô.
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
SIG_W, SIG_H = 96, 54     # ảnh thu nhỏ để so khung hình
PIX_DIFF = 20             # một điểm ảnh đổi hơn mức này (0–255) coi là "đổi"
MOVE = 0.01               # tỉ lệ điểm đổi giữa 2 khung liên tiếp: trên mức này = màn hình đang động
CHANGED = 0.02            # so với khung đã tra: trên mức này = đã sang màn hình khác
STABLE_S = 0.6            # đứng yên bao lâu thì tra
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


def short_price(amount, currency) -> str:
    return f"{amount:g} {CURRENCY.get(currency, currency)}"


def price_lines(it, result, notes) -> list[str]:
    """Nội dung ô giá: tên món, giá rẻ nhất, giá phổ biến + số món, 1 lưu ý quan trọng nhất."""
    title = it.name if it.rarity == "Unique" and it.name else (it.base or it.name or "?")
    lines = [title[:MAX_CHARS]]
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


def note_lines(notes) -> list[str]:
    out = []
    for n in notes:
        if n.startswith("Món không có máu"):
            out.append("! Không máu: giá thật thấp hơn")
        elif n.startswith("Dòng đọc chưa chắc"):
            continue
        elif n.startswith("Không có chỉ số đáng lọc"):
            out.append("! Chỉ tra theo loại món")
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
    """Nhận khung hình, quyết định khi nào tra và gửi lại ô nào."""

    def __init__(self, D, trade=None, status="securable", log_dir=None, threaded=True, clock=time.monotonic):
        self.D, self.trade, self.status = D, trade, status
        self.log_dir, self.threaded, self.clock = log_dir, threaded, clock
        self.lock = threading.Lock()
        self.prev = None          # khung trước
        self.stable_since = 0.0
        self.checked = None       # khung đã tra gần nhất
        self.shown = None         # khung ứng với ô đang hiện trên TV
        self.job = None           # {"sig":…, "stale": bool}
        self.pending = []         # ô chờ gửi
        self.history = deque(maxlen=20)

    # ---- gọi từ máy chủ HTTP ----
    def on_frame(self, img: np.ndarray) -> list[dict]:
        now = self.clock()
        sig = signature(img)
        start = None
        with self.lock:
            if self.prev is None or frac(sig, self.prev) > MOVE:
                self.stable_since = now
            self.prev = sig
            if self.shown is not None and frac(sig, self.shown) > CHANGED:
                self.pending.append(region(""))
                self.shown = None
            if self.job is not None:
                if frac(sig, self.job["sig"]) > CHANGED:
                    self.job["stale"] = True
            elif now - self.stable_since >= STABLE_S and (self.checked is None or frac(sig, self.checked) > CHANGED):
                self.checked = sig
                self.job = start = {"sig": sig, "stale": False}
        if start is not None:
            if self.threaded:
                threading.Thread(target=self._run, args=(img.copy(), start), daemon=True).start()
            else:
                self._run(img, start)
        with self.lock:
            out, self.pending = self.pending, []
        return out

    # ---- tra một món (chạy nền) ----
    def _emit(self, job, r, shown=True):
        with self.lock:
            if job["stale"]:
                return False
            self.pending.append(r)
            if shown:
                self.shown = job["sig"]
            return True

    def _run(self, img, job):
        t0 = time.time()
        rec = {"at": time.strftime("%H:%M:%S"), "found": False}
        try:
            tt = tooltip.find(img, self.D)
            rec["ocr_s"] = round(time.time() - t0, 2)
            if not tt:
                return
            rec["found"] = True
            H, W = img.shape[:2]
            it = parse.parse(tt.header, tt.lines, self.D)
            q, notes = query.build(it, status=self.status)
            rec.update(name=it.name, base=it.base, rarity=it.rarity, notes=notes)
            title = it.name if it.rarity == "Unique" and it.name else (it.base or it.name or "?")
            wait = [title[:MAX_CHARS], "Đang tra giá…"]
            self._emit(job, region("\n".join(wait), place(tt.box, W, H, wait)))
            if self.trade is None:
                lines = [title[:MAX_CHARS], "(chưa bật tra giá)"] + note_lines(notes)[:1]
            else:
                from .trade import RateLimited
                try:
                    res = self.trade.search(q)
                    lines = price_lines(it, res, notes)
                    rec["summary"] = {"total": res["total"], "url": res["url"], "cached": res.get("cached")}
                except RateLimited as e:
                    lines = [title[:MAX_CHARS], f"Trade API bận, chờ {int(getattr(e, 'wait', 0)) + 1} giây"]
                except Exception as e:  # mạng, 403…
                    lines = [title[:MAX_CHARS], "Lỗi tra giá", str(e)[:MAX_CHARS]]
                    rec["error"] = str(e)
            ok = self._emit(job, region("\n".join(lines), place(tt.box, W, H, lines)))
            rec["text"] = lines
            rec["shown"] = ok
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            rec["seconds"] = round(time.time() - t0, 2)
            with self.lock:
                self.job = None
                self.history.appendleft(rec)
            if rec.get("found") or rec.get("error"):
                print(f"[{rec['at']}] {rec.get('base') or ''} {rec.get('name') or ''} → "
                      f"{' | '.join(rec.get('text', [])) or rec.get('error', '')} ({rec['seconds']}s)", flush=True)
                self._save(img, rec)

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
                self._json(200, {"regions": regions, "next_ms": NEXT_MS})
            except Exception as e:
                self._json(400, {"error": str(e), "next_ms": 2000})

        def do_GET(self):
            if self.path == "/health":
                return self._json(200, {"ok": True})
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
    ap.add_argument("--no-trade", action="store_true", help="chỉ đọc món, không tra giá")
    ap.add_argument("--status", default="securable", help="securable = mua ngay (mặc định) | available | online | any")
    ap.add_argument("--league", default=None)
    ap.add_argument("--realm", default="sony")
    ap.add_argument("--logs", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"),
                    help="thư mục lưu ảnh và kết quả các lần tra gần nhất ('' để tắt)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("Đang nạp dữ liệu và mô hình OCR…", flush=True)
    D = data.load()
    from . import ocr
    ocr.read(np.full((64, 256, 3), 255, np.uint8))     # nạp sẵn mô hình, lần tra đầu không bị chậm
    tr = None
    if not a.no_trade:
        from .trade import Trade
        tr = Trade(realm=a.realm, league=a.league)
        try:
            print(f"League: {tr.pick_league()} (realm {a.realm})", flush=True)
        except Exception as e:
            print(f"Chưa lấy được league ({e}); sẽ thử lại khi tra", flush=True)
    w = Watcher(D, tr, status=a.status, log_dir=a.logs or None)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(w))
    print(f"Sẵn sàng: http://127.0.0.1:{a.port}  (Ctrl+C để dừng). Mở CouchSubs bản poe-tv rồi bấm bắt đầu.",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
