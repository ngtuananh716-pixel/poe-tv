"""Chạy thử: python -m poe_tv anh1.jpg [anh2.jpg ...] [--no-trade] [--json] [--status any] [--league Covens]"""
from __future__ import annotations

import argparse
import json
import sys
import time

import cv2
import numpy as np

from . import data, parse, query, tooltip


def imread(path):
    # đọc được cả đường dẫn có dấu tiếng Việt trên Windows
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="poe_tv", description="Tra giá món đồ PoE (realm Sony) từ ảnh chụp màn hình")
    ap.add_argument("images", nargs="+")
    ap.add_argument("--no-trade", action="store_true", help="chỉ đọc món đồ, không tra giá")
    ap.add_argument("--json", action="store_true", help="in kết quả dạng JSON")
    ap.add_argument("--status", default="securable", help="securable = mua ngay (mặc định) | available | online | any")
    ap.add_argument("--league", default=None, help="tên league, mặc định tự chọn league trade mới nhất")
    ap.add_argument("--realm", default="sony")
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    D = data.load()
    tr = None
    if not a.no_trade:
        from .trade import RateLimited, Trade, summarize
        tr = Trade(realm=a.realm, league=a.league)

    out = []
    for path in a.images:
        t0 = time.time()
        img = imread(path)
        if img is None:
            print(f"Không đọc được ảnh: {path}")
            continue
        tt = tooltip.find(img, D)
        if not tt:
            print(f"[{path}] Không tìm thấy tooltip món đồ")
            continue
        it = parse.parse(tt.header, tt.lines, D)
        q, notes = query.build(it, status=a.status)
        rec = {"file": path, "rarity": it.rarity, "name": it.name, "base": it.base, "category": it.category,
               "mods": [{"kind": m.kind, "ref": m.ref, "values": m.values, "score": m.score} for m in it.mods],
               "notes": notes, "query": q, "ocr_seconds": round(time.time() - t0, 2)}
        if it.category and parse.is_weapon(it):
            rec["pdps"], rec["edps"] = round(it.pdps, 1), round(it.edps, 1)
        if tr:
            try:
                res = tr.search(q)
                rec["result"] = res
                rec["summary"] = summarize(res)
            except RateLimited as e:
                rec["error"] = str(e)
            except Exception as e:  # mạng, 403...
                from .trade import browser_url
                rec["error"] = f"Lỗi tra giá: {e}"
                rec["browser_url"] = browser_url(q, a.realm, a.league or tr.league)
        rec["seconds"] = round(time.time() - t0, 2)
        out.append(rec)
        if not a.json:
            _print(rec)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))


def _print(r):
    title = f"{r['name']} {r['base']}".strip() if r["rarity"] in ("Unique", "Rare", "Magic") else r["base"]
    print(f"\n=== {title}  [{r['rarity']}]  ({r['seconds']}s)")
    if "pdps" in r:
        print(f"    DPS vật lý {r['pdps']} | DPS nguyên tố {r['edps']}")
    for m in r["mods"]:
        flag = "" if m["score"] >= query.MIN_SCORE else "  (?)"
        print(f"    {m['kind'][:4]}: {_fmt(m['ref'], m['values'])}{flag}")
    for n in r["notes"]:
        print(f"    ! {n}")
    if "summary" in r:
        print(f"    => {r['summary']['text']}")
        print(f"    Link: {r['result']['url']}")
    if "error" in r:
        print(f"    !! {r['error']}")
    if "browser_url" in r:
        print(f"    Mở trình duyệt để tra tay: {r['browser_url']}")


def _fmt(ref, values):
    """In mod cho dễ đọc: thay # bằng số; giá trị âm của 'increased' thì in thành 'reduced'."""
    if not values or ref.count("#") != len(values):
        return ref
    if values[0] < 0 and "increased" in ref:
        ref, values = ref.replace("increased", "reduced"), [abs(v) for v in values]
    return ref.replace("#", "{}").format(*[f"{v:g}" for v in values]).replace("++", "+")


if __name__ == "__main__":
    main()
