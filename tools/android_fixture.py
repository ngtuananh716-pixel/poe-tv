"""Tạo dữ liệu kiểm thử cho bản Android: dòng OCR của ảnh mẫu + kết quả lõi Python (tooltip, phân tích, bộ lọc trade).
Kiểm thử JVM của bản Android chạy cùng dòng OCR qua mã Kotlin và so kết quả với file này.
Chạy: cd poe_tv && python ../tools/android_fixture.py ../couchsubs-patch-android/test/resources/poetv/fixture.json"""
import json, sys
import cv2
from poe_tv import data, ocr, parse, query, tooltip

D = data.load("poe1")
cases = []
for name in ["foe_thread", "vortex_call", "wildslash"]:
    img = cv2.imread(f"mau/{name}.jpg")
    H, W = img.shape[:2]
    lines = ocr.read_fast(img)
    tt = tooltip.find(img, D)
    case = {"name": name, "width": W, "height": H,
            "lines": [{"text": l.text, "x0": l.x0, "y0": l.y0, "x1": l.x1, "y1": l.y1} for l in lines]}
    if tt:
        it = parse.parse(tt.header, tt.lines, D)
        q, notes = query.build(it, status="securable", game="poe1")
        case["expect"] = {"box": list(tt.box), "header": tt.header, "rarity": it.rarity, "name": it.name, "base": it.base,
                          "category": it.category, "title": it.title,
                          "mods": [{"ref": m.ref, "values": m.values, "kind": m.kind} for m in it.mods],
                          "props": it.props, "query": q, "notes": notes}
    cases.append(case)
# kiểm riêng phần khớp: mod, tên món
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
json.dump({"cases": cases, "mods": checks, "names": names}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok", [ (c["name"], c.get("expect", {}).get("title")) for c in cases])
