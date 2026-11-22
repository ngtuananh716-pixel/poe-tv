"""Đóng gói dữ liệu món đồ/mod cho bản Android: đọc data/ (như poe_tv.data.load) rồi ghi một file JSON gọn mỗi game,
đã giải sẵn bước chọn mod local/global (_resolve của APT) cho từng loại món, chỉ giữ các trường bản Android cần.
Chạy: cd poe_tv && PYTHONPATH=. python ../tools/android_data.py <thư mục ra>"""
import json, sys
from pathlib import Path
from poe_tv import data
from poe_tv.data import template_key, _resolve


def matcher_opts(cands):
    """Trả các lựa chọn [điều kiện loại món hoặc None, ref, mã explicit, negate, value, số #, better] theo đúng thứ tự
    thử của _resolve: lựa chọn đầu khớp loại món; không khớp thì lựa chọn điều kiện None; không có thì lựa chọn đầu."""
    groups = {}
    for entry, matcher, group in cands:
        groups.setdefault(id(group) if group else id(entry), (group, []))[1].append((entry, matcher))
    group, items = next(iter(groups.values()))

    def pack(test, entry, matcher):
        ids = (((entry.get("trade") or {}).get("ids") or {}).get("explicit")) or []
        return [test, entry["ref"], ids[0] if ids else None, bool(matcher.get("negate")), matcher.get("value"),
                matcher["string"].count("#"), entry.get("better", 1)]

    if group and group["resolve"]["strat"] == "select":
        out = []
        for idx, test in enumerate(group["resolve"]["test"]):
            chosen = group["stats"][idx]
            em = next(((e, m) for e, m in items if e is chosen), None)
            if em is None:
                m = next((m for m in chosen["matchers"] if template_key(m["string"]) == template_key(items[0][1]["string"])),
                         items[0][1])      # bản Python báo lỗi ở đây (StopIteration); Android dùng câu chữ của dòng đã khớp
                em = (chosen, m)
            out.append(pack(test, *em))
        return out
    return [pack(None, *items[0])]


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for game in data.GAMES:
        D = data.load(game)
        # kiểm: giải sẵn phải cho đúng kết quả như _resolve với mọi loại món
        cats = [None] + sorted({b["category"] for b in D.bases.values()})
        mods = []
        for key, cands in D.matchers.items():
            opts = matcher_opts(cands)
            for cat in cats:
                idx = next((i for i, o in enumerate(opts) if o[0] is not None and data._category_matches(cat, o[0])), -1)
                if idx == -1:
                    idx = next((i for i, o in enumerate(opts) if o[0] is None), 0)
                try:
                    e, m = _resolve(cands, cat)
                except StopIteration:
                    continue
                assert opts[idx][1] == e["ref"] and opts[idx][5] == m["string"].count("#"), (key, cat)
            mods.append([key, opts])
        doc = {
            "game": game,
            "bases": [[k, v["name"], v["category"]] for k, v in D.bases.items()],
            "uniques": [[k, v["name"], v["bases"]] for k, v in D.uniques.items()],
            "gems": [[k, v] for k, v in D.gems.items()],
            "others": [[k, v[0], v[1]] for k, v in D.others.items()],
            "mods": mods,
        }
        p = out / f"{game}.json"
        p.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(game, p.stat().st_size, "byte;", {k: len(v) for k, v in doc.items() if isinstance(v, list)})


if __name__ == "__main__":
    main(sys.argv[1])
