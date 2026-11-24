"""Tạo dữ liệu kiểm thử cho bản Android: dòng OCR của ảnh mẫu + kết quả lõi Python (tooltip, phân tích, bộ lọc trade).
Kiểm thử JVM của bản Android chạy cùng dòng OCR qua mã Kotlin và so kết quả với file này.
Chạy: cd poe_tv && python ../tools/android_fixture.py ../couchsubs-patch-android/test/resources/poetv/fixture.json"""
import json, sys
import cv2
from poe_tv import data, ocr, parse, query, tooltip
from poe_tv.serve import filter_lines, price_lines
from poe_tv import pick


def pick_cases(it, game):
    """Bảng chọn dòng + bộ lọc dựng từ vài lựa chọn (mặc định, chọn nhanh, bỏ/sửa dòng) ở 3 chế độ."""
    out = []
    for mode in (dict(), dict(all_lines=True), dict(strict=True, by_base=True)):
        qa, _ = query.build(it, status="securable", game=game, **mode)
        pn = pick.panel(it, game, auto_q=qa, **mode)
        if pn is None:
            return out
        assert pick.build(it, pn)[0] == qa, "lựa chọn mặc định phải ra đúng bộ lọc tự tra"
        sels = [{}, pick.preset(pn, "all"), pick.preset(pn, "none"), pick.preset(pn, "main")]
        grp = next((r for r in pn["rows"] if r["groups"] and r["enabled"] and r["on"]), None)
        one = next((r for r in pn["rows"] if not r["groups"] and r["enabled"] and r["raw"] is not None
                    and r["section"] in ("explicit", "gear")), None)
        custom = {"on": {}, "edit": {}, "need": None}
        if grp:
            custom["on"][grp["key"]] = False
        if one:
            custom["on"][one["key"]] = True
            custom["edit"][one["key"]] = pick.show_min(pn, one) + 5
        sels.append(custom)
        sels.append({**custom, "need": 1})
        sels.append({"on": {"base": True}, "edit": {r["key"]: 7 for r in pn["rows"] if r["groups"] and r["on"]}})
        builds = []
        for sel in sels:
            q, summ = pick.build(it, pn, sel)
            builds.append({"sel": {"on": sel.get("on") or {}, "edit": sel.get("edit") or {}, "need": sel.get("need")},
                           "query": q, "summary": summ})
        out.append({"mode": {"strict": bool(mode.get("strict")), "by_base": bool(mode.get("by_base")),
                             "all_lines": bool(mode.get("all_lines"))},
                    "rows": [{"key": r["key"], "section": r["section"], "text": r["text"], "on": r["on"],
                              "enabled": r["enabled"], "tag": r["tag"], "show_min": pick.show_min(pn, r)} for r in pn["rows"]],
                    "slots": pn["slots"], "n_explicit": pn["n_explicit"], "need": pn["need"], "m0": pn["m0"],
                    "builds": builds})
    return out


import re
import sys as _sys
D = data.load("poe1")
cases = []
# ảnh mẫu: (tên, game, đường dẫn). Ảnh PoE2 là ảnh chụp TV của người dùng, không đưa vào kho; chỉ lưu dòng OCR (đã bỏ tên
# nhân vật). Truyền đường dẫn ảnh đó ở tham số thứ 2 (không có thì giữ nguyên ca PoE2 cũ trong fixture).
SAMPLES = [("foe_thread", "poe1", "mau/foe_thread.jpg"), ("vortex_call", "poe1", "mau/vortex_call.jpg"),
           ("wildslash", "poe1", "mau/wildslash.jpg")]
for i, nm in enumerate(["poe2_vertex", "poe2_gloves", "poe2_belt"]):
    if len(_sys.argv) > 2 + i:
        SAMPLES.append((nm, "poe2", _sys.argv[2 + i]))
PRIVATE_NAMES = {"wild", "wlld", "qimah", "qimal"}          # tên nhân vật trên ảnh TV (OCR có thể đọc sai) và dòng cấp độ
def private(text):
    k = re.sub(r"[^a-z0-9]", "", text.lower())
    return k in PRIVATE_NAMES or re.fullmatch(r"lvl\d+", k) is not None
for name, game, path in SAMPLES:
    D = data.load(game)
    img = cv2.imread(path)
    H, W = img.shape[:2]
    lines = [l for l in ocr.read_fast(img) if not private(l.text)]
    _orig = ocr.read_fast
    ocr.read_fast = lambda _img, det_max=1280, _l=lines: _l       # tìm tooltip trên đúng các dòng đã lọc
    tt = tooltip.find(img, D)
    ocr.read_fast = _orig
    key = lambda t: re.sub(r"[^A-Z]", "", t.upper())
    marks = [l.text for l in lines if l.y0 >= tooltip.UI_STRIP * H and tooltip.UI_MARKERS.search(key(l.text))]
    weak = any(l.y0 >= tooltip.UI_STRIP * H and tooltip.UI_WEAK.search(key(l.text)) for l in lines)
    titles = [l.text for l in lines if l.y1 <= tooltip.TITLE_STRIP * H and tooltip.UI_TITLE.search(key(l.text))]
    case = {"name": name, "game": game, "width": W, "height": H, "marks": marks, "weak": weak, "titles": titles,
            "lines": [{"text": l.text, "x0": l.x0, "y0": l.y0, "x1": l.x1, "y1": l.y1} for l in lines]}
    if tt:
        it = parse.parse(tt.header, tt.lines, D)
        q, notes = query.build(it, status="securable", game=game)
        case["expect"] = {"box": list(tt.box), "header": tt.header, "rarity": it.rarity, "name": it.name, "base": it.base,
                          "category": it.category, "title": it.title,
                          "mods": [{"ref": m.ref, "values": m.values, "kind": m.kind} for m in it.mods],
                          "props": it.props, "query": q, "notes": notes,
                          "labels": query.labels(q, it), "filter_lines": filter_lines(query.labels(q, it))}
        qa, _ = query.build(it, status="securable", game=game, all_lines=True)
        qs, _ = query.build(it, status="securable", game=game, strict=True, by_base=True)
        case["expect"].update({"query_all": qa, "labels_all": query.labels(qa, it),
                               "filter_lines_all": filter_lines(query.labels(qa, it)), "query_strict_base": qs})
        case["expect"]["picks"] = pick_cases(it, game)
    cases.append(case)
# kiểm riêng phần khớp: mod, tên món
D = data.load("poe1")
checks = []
for t in ["+85 to maximum Lfe", "12% increased Atack Speed", "+1 to Level of all Cold Spel Skill Gems", "+30% to Lightning Resistnce",
          "Adds 5 to 10 Physical Damage to Attack", "random words here today",
          "+85 to maximum Life", "+38% to Fire Resistance", "Adds 5 to 10 Physical Damage to Attacks", "+1 to Level of all Cold Spell Skill Gems",
          "10% increased Movement Speed", "+2O% to Cold Resistance", "12% increased Attack Speed"]:
    sm, sc = D.match_mod(parse.clean_numbers(t), None)
    checks.append({"game": "poe1", "line": t, "clean": parse.clean_numbers(t), "ref": sm.ref if sm else None, "score": sc,
                   "n": sm.n_values if sm else None, "eid": (sm.trade_ids or {}).get("explicit", [None])[0] if sm else None})
for cat in [None, "Gloves", "Bow", "Ring"]:
    for t in ["+12% to Global Critical Strike Multiplier", "Adds 10 to 20 Physical Damage", "15% increased Attack Speed", "+120 to Armour"]:
        sm, sc = D.match_mod(parse.clean_numbers(t), cat)
        checks.append({"game": "poe1", "cat": cat, "line": t, "clean": parse.clean_numbers(t), "ref": sm.ref if sm else None,
                       "score": sc, "n": sm.n_values if sm else None,
                       "eid": (sm.trade_ids or {}).get("explicit", [None])[0] if sm else None})
D2 = data.load("poe2")
for t in ["+38% to Fire Resistance", "+85 to maximum Life", "+1 to Level of all Spell Skills", "12% increased Atack Speed",
          "+30 to Spirit", "15% increased Critical Damage Bonus"]:
    sm, sc = D2.match_mod(parse.clean_numbers(t), None)
    checks.append({"game": "poe2", "line": t, "clean": parse.clean_numbers(t), "ref": sm.ref if sm else None, "score": sc,
                   "n": sm.n_values if sm else None, "eid": ((sm.trade_ids or {}).get("explicit") or [None])[0] if sm else None})
names = []
for t in ["Heavy Bel", "Vortex Cal", "Sapphire Rng", "Chaos Orh",
          "Heavy Belt", "Vortex Call", "HEAVY BELI", "Chaos Orb", "Sapphire Ring", "Vaal Regalia"]:
    b = D.find_base(t); u = D.find_unique(t); o = D.find_other(t); g = D.find_gem(t)
    bi = D.find_base_in(t)
    names.append({"text": t, "base": b["name"] if b else None, "unique": u["name"] if u else None,
                  "other": o[0] if o else None, "gem": g, "base_in": bi["name"] if bi else None})
for t in ["Heavy Belt of the Whale", "Sapphire Ring of Fire", "Chain: hello"]:
    bi = D.find_base_in(t)
    names.append({"text": t, "base": None, "unique": None, "other": None, "gem": None, "base_in": bi["name"] if bi else None, "only_in": True})

# ca dựng sẵn dòng (không cần ảnh): cluster jewel (dòng enchant, notable, dòng phụ) và jewel thường không có dòng chính
from poe_tv.serve import note_lines
def _L(text, y, x0=200.0):
    return ocr.Line(text=text, x0=x0, y0=float(y), x1=x0 + 20.0 * len(text), y1=float(y) + 34, conf=0.95)
def _synth(header, secs):
    lines, y = [], 600
    for h in header:
        lines.append(_L(h, y)); y += 40
    for sec in secs:
        y += 30
        for t in sec:
            lines.append(_L(t, y)); y += 40
    return lines
PARSE_SAMPLES = [
    ("cluster_large", "poe1", ["SPIRIT SPLINTER", "LARGE CLUSTER JEWEL"], [["REQUIRES LEVEL 54"],
     ["ADDS 9 PASSIVE SKILLS", "2 ADDED PASSIVE SKILLS ARE JEWEL SOCKETS",
      "ADDED SMALL PASSIVE SKILLS GRANT: 12% INCREASED LIGHTNING DAMAGE"],
     ["ADDED SMALL PASSIVE SKILLS ALSO GRANT: +14 TO EVASION",
      "ADDED SMALL PASSIVE SKILLS ALSO GRANT: 5% INCREASED MANA REGENERATION RATE",
      "ADDED SMALL PASSIVE SKILLS ALSO GRANT: +3 TO STRENGTH", "1 ADDED PASSIVE SKILL IS PRISMATIC HEART"],
     ["PLACE INTO AN ALLOCATED LARGE JEWEL SOCKET ON THE PASSIVE SKILL TREE."]]),
    ("jewel_no_main", "poe1", ["GLOOM SPARK", "COBALT JEWEL"], [["15% INCREASED FIRE DAMAGE", "12% INCREASED DAMAGE WITH BOWS",
      "+16 TO DEXTERITY", "6% INCREASED MANA REGENERATION RATE"], ["PLACE INTO AN ALLOCATED JEWEL SOCKET ON THE PASSIVE SKILL TREE."]]),
]
parse_cases = []
for name, game, header, secs in PARSE_SAMPLES:
    D = data.load(game)
    lines = _synth(header, secs)
    it = parse.parse(header, lines, D)
    q, notes = query.build(it, status="securable", game=game)
    qa, notes_a = query.build(it, status="securable", game=game, all_lines=True)
    parse_cases.append({"name": name, "game": game, "header": header,
                        "lines": [{"text": l.text, "x0": l.x0, "y0": l.y0, "x1": l.x1, "y1": l.y1} for l in lines],
                        "expect": {"rarity": it.rarity, "base": it.base, "category": it.category,
                                   "mods": [{"ref": m.ref, "values": m.values, "kind": m.kind} for m in it.mods],
                                   "query": q, "notes": notes, "note_lines": note_lines(notes),
                                   "labels": query.labels(q, it), "filter_lines": filter_lines(query.labels(q, it)),
                                   "query_all": qa, "filter_lines_all": filter_lines(query.labels(qa, it)),
                                   "picks": pick_cases(it, game)}})

# ô giá: giá ước tính, khoảng, độ tin cậy (tỉ giá giả định), giá phổ biến khi không có tỉ giá
L = lambda pairs: [{"amount": a, "currency": c} for a, c in pairs]
LAB = {"req": ["Giáp ≥ 530"], "count": ["K.NT ≥ 67", "Rarity ≥ 16%", "Lightning atk ≥ 21"], "need": 2, "of": 3}
price_cases = []
for title, total, ls, notes, lab, rates, game in [
    ("Utility Belt", 185, L([(3, "divine"), (4, "divine"), (5, "divine"), (6, "divine"), (6, "divine"), (6, "divine"),
                             (7, "divine"), (8, "divine"), (10, "divine"), (12, "divine")]), [],
     {"req": [], "count": ["Máu ≥ 94", "K.NT ≥ 95", "K.Chaos ≥ 16"], "need": 2, "of": 3},
     {"exalted": 1.0, "divine": 300.0}, "poe2"),
    ("Detailed Mitts", 4, L([(1, "exalted"), (80, "exalted"), (150, "exalted"), (2, "divine")]),
     ["Món không có máu: giá thật nhiều khả năng THẤP hơn các món tương tự tìm được"], LAB,
     {"exalted": 1.0, "divine": 300.0}, "poe2"),
    ("Heavy Belt", 50, L([(20, "chaos"), (25, "chaos"), (30, "chaos"), (1, "divine")]), [], None,
     {"chaos": 1.0, "divine": 180.0}, "poe1"),
    ("Heavy Belt", 50, L([(20, "chaos"), (25, "chaos"), (30, "chaos"), (1, "divine")]), [], None, None, "poe1"),
    ("Vaal Regalia", 64, L([(2.5, "divine"), (12.5, "chaos"), (0.5, "divine")]), ["Ít món khớp đủ 5/5 → tự nới"],
     {"req": ["Base: Vaal Regalia", "iLvl ≥ 86"], "count": [], "need": 0, "of": 0}, {"chaos": 1.0, "divine": 180.0}, "poe1"),
    ("Ring", 30, L([(12, "chaos"), (13, "chaos")]), [], None, {"chaos": 1.0, "divine": 180.0}, "poe1"),
    ("Foul Staff", 0, [], [], {"req": ["pDPS ≥ 197"] + [f"Mod {i} ≥ {i}" for i in range(12)], "count": [], "need": 0, "of": 0},
     None, "poe1"),
]:
    price_cases.append({"title": title, "total": total, "listings": ls, "notes": notes, "labels": lab, "rates": rates,
                        "game": game, "lines": price_lines(title, {"total": total, "listings": ls}, notes, lab, rates, game)})
json.dump({"cases": cases, "parse_cases": parse_cases, "mods": checks, "names": names, "price_cases": price_cases}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok", [ (c["name"], c.get("expect", {}).get("title")) for c in cases])
