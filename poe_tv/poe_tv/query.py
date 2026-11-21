"""Dựng bộ lọc trade từ món đồ đã phân tích. Quy tắc dựa theo mặc định của APT (dải giá trị -10%)."""
from __future__ import annotations

import math

from .data import TRADE_CATEGORY
from .parse import Item, is_armour, is_jewellery, is_weapon

RANGE = 0.10          # lấy món có chỉ số >= 90% của món đang tra (giống APT)
MIN_SCORE = 95        # dòng OCR khớp dưới mức này coi là chưa chắc, không đưa vào bộ lọc

RES = {  # mod kháng -> (số loại kháng nguyên tố, có kháng chaos)
    "+#% to all Elemental Resistances": (3, False),
    "+#% to All Resistances": (3, True),
    "+#% to Fire Resistance": (1, False), "+#% to Cold Resistance": (1, False),
    "+#% to Lightning Resistance": (1, False),
    "+#% to Fire and Lightning Resistances": (2, False), "+#% to Fire and Cold Resistances": (2, False),
    "+#% to Cold and Lightning Resistances": (2, False),
    "+#% to Chaos Resistance": (0, True),
    "+#% to Fire and Chaos Resistances": (1, True), "+#% to Cold and Chaos Resistances": (1, True),
    "+#% to Lightning and Chaos Resistances": (1, True),
}
ATTR = {
    "+# to Strength": "str", "+# to Dexterity": "dex", "+# to Intelligence": "int",
    "+# to all Attributes": "all", "+# to Strength and Dexterity": "str dex",
    "+# to Strength and Intelligence": "str int", "+# to Dexterity and Intelligence": "dex int",
}
PSEUDO = {
    "life": "pseudo.pseudo_total_life", "es": "pseudo.pseudo_total_energy_shield",
    "mana": "pseudo.pseudo_total_mana", "ele_res": "pseudo.pseudo_total_elemental_resistance",
    "chaos_res": "pseudo.pseudo_total_chaos_resistance", "str": "pseudo.pseudo_total_strength",
    "dex": "pseudo.pseudo_total_dexterity", "int": "pseudo.pseudo_total_intelligence",
}
# mod quan trọng: luôn đưa vào bộ lọc nếu món có
IMPORTANT = ("Movement Speed", "to Level of", "Critical Strike Multiplier", "increased maximum Life",
             "increased maximum Energy Shield", "Spell Damage", "Damage over Time Multiplier",
             "Physical Damage to Attacks", "increased Cast Speed", "Suppress Spell Damage")


def _min(v):
    return math.floor(v * (1 - RANGE)) if v > 0 else v


def build(it: Item, status: str = "securable"):
    """Trả về (query, ghi_chú[]) để gửi lên /api/trade/search."""
    notes = []
    q = {"query": {"status": {"option": status}, "filters": {}, "stats": [{"type": "and", "filters": []}]},
         "sort": {"price": "asc"}}
    qq = q["query"]
    flt = qq["filters"]
    stats = qq["stats"][0]["filters"]
    if it.corrupted:
        flt["misc_filters"] = {"filters": {"corrupted": {"option": "true"}}}

    unsure = [m for m in it.mods if m.score < MIN_SCORE]
    for m in unsure:
        notes.append(f"Dòng đọc chưa chắc, không dùng để lọc: {m.text}")
    mods = [m for m in it.mods if m.score >= MIN_SCORE]

    if it.rarity == "Unique":
        qq["name"], qq["type"] = it.name, it.base
        # đồ Foulborn và đồ thường cùng tên giá khác hẳn nhau: lọc đúng loại (như Awakened PoE Trade)
        mf = flt.setdefault("misc_filters", {"filters": {}})["filters"]
        mf["foulborn_item"] = {"option": "true" if it.foulborn else "false"}
        if it.foulborn:
            notes.append("Foulborn: chưa lọc theo dòng mod đột biến, giá là món Foulborn rẻ nhất cùng tên")
        return q, notes
    if it.rarity == "Gem":
        qq["type"] = it.name
        mf = flt.setdefault("misc_filters", {"filters": {}})["filters"]
        if it.props.get("level"):
            mf["gem_level"] = {"min": int(it.props["level"][0])}
        if it.props.get("quality"):
            mf["quality"] = {"min": int(it.props["quality"][0])}
        return q, notes
    if it.rarity == "Other" or not it.category:
        qq["type"] = it.base or it.name
        return q, notes

    cat = TRADE_CATEGORY.get(it.category)
    tf = {"rarity": {"option": "nonunique"}}
    if cat:
        tf["category"] = {"option": cat}
    else:
        qq["type"] = it.base
    flt["type_filters"] = {"filters": tf}

    if is_weapon(it):
        wf = {}
        pdps, edps = it.pdps, it.edps
        if pdps >= 30:
            wf["pdps"] = {"min": _min(pdps)}
        if edps >= 30:
            wf["edps"] = {"min": _min(edps)}
        if wf:
            flt["weapon_filters"] = {"filters": wf}
        else:
            notes.append("Vũ khí sát thương thấp: lọc theo mod quan trọng thay vì DPS")

    # gộp chỉ số tổng như APT
    life = sum(m.value for m in mods if m.ref == "+# to maximum Life")
    es_flat = sum(m.value for m in mods if m.ref == "+# to maximum Energy Shield")
    attrs = {"str": 0.0, "dex": 0.0, "int": 0.0}
    for m in mods:
        if m.ref in ATTR:
            for a in (("str", "dex", "int") if ATTR[m.ref] == "all" else ATTR[m.ref].split()):
                attrs[a] += m.value
    ele = sum(m.value * RES[m.ref][0] for m in mods if m.ref in RES)
    chaos = sum(m.value for m in mods if m.ref in RES and RES[m.ref][1])

    defensive = is_armour(it) or is_jewellery(it) or it.category in ("Quiver", "Jewel", "Abyss Jewel")
    if life > 0:
        stats.append({"id": PSEUDO["life"], "value": {"min": _min(life + attrs["str"] / 2)}})
    if es_flat > 0 and not is_armour(it):
        stats.append({"id": PSEUDO["es"], "value": {"min": _min(es_flat)}})
    if ele > 0:
        stats.append({"id": PSEUDO["ele_res"], "value": {"min": _min(ele)}})
    if chaos > 0:
        stats.append({"id": PSEUDO["chaos_res"], "value": {"min": _min(chaos)}})
    for a, v in attrs.items():
        if v >= 40:
            stats.append({"id": PSEUDO[a], "value": {"min": _min(v)}})
    for m in mods:
        if any(k in m.ref for k in IMPORTANT) and m.kind == "explicit":
            ids = m.trade_ids.get("explicit") or []
            if ids and m.value is not None:
                stats.append({"id": ids[0], "value": {"min": _min(m.value)}})

    if is_armour(it):
        af = {}
        for key, tkey in (("armour", "ar"), ("evasion", "ev"), ("es", "es")):
            if it.props.get(key):
                af[tkey] = {"min": _min(it.props[key][0])}
        if af:
            flt["armour_filters"] = {"filters": af}

    if defensive and life == 0 and es_flat < 40 and not (is_armour(it) and it.props.get("es")):
        notes.append("Món không có máu: giá thật nhiều khả năng THẤP hơn các món tương tự tìm được")
    if not stats and not flt.get("weapon_filters") and not flt.get("armour_filters"):
        notes.append("Không có chỉ số đáng lọc: chỉ tra theo loại món")
    return q, notes
