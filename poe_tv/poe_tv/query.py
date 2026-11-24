"""Dựng bộ lọc trade từ món đồ đã phân tích. Quy tắc dựa theo mặc định của APT (dải giá trị -10%)."""
from __future__ import annotations

import math
import re

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
# Dữ liệu PoE2 (Exiled Exchange 2) ghi mod không có dấu "+" ở đầu ("#% to Fire Resistance"): so khớp bỏ dấu "+".
def _k(ref: str) -> str:
    return ref.replace("+", "")


RES = {_k(k): v for k, v in RES.items()}
ATTR = {_k(k): v for k, v in ATTR.items()}

# Mod quan trọng: (chuỗi nhận ra trong câu mẫu, nhãn ngắn hiện trên TV). Mặc định (Dòng: CHÍNH) chỉ lọc các mod này
# cùng các tổng (máu, ES, kháng, thuộc tính); Dòng: TẤT CẢ lọc thêm mọi dòng explicit đọc được.
IMPORTANT_LABEL = (
    ("Movement Speed", "Tốc chạy"), ("to Level of", "+Cấp skill"), ("Critical Strike Multiplier", "Crit multi"),
    ("Critical Damage Bonus", "Crit dmg"), ("increased maximum Life", "%Máu"),
    ("increased maximum Energy Shield", "%ES"), ("Spell Damage", "Spell dmg"),
    ("Damage over Time Multiplier", "DoT multi"), ("to Attacks", "atk"),
    ("increased Cast Speed", "Cast speed"), ("Suppress Spell Damage", "Suppress"), ("to Spirit", "Spirit"),
    ("increased Attack Speed", "Attack speed"), ("Critical Strike Chance", "Crit"), ("Critical Hit Chance", "Crit"),
    ("Rarity of Items", "Rarity"), ("increased Skill Speed", "Skill speed"),
)
IMPORTANT = tuple(k for k, _ in IMPORTANT_LABEL)
ELEMENTS = (("Physical", "Phys"), ("Fire", "Fire"), ("Cold", "Cold"), ("Lightning", "Lightning"), ("Chaos", "Chaos"))
# Nhãn ngắn của các bộ lọc, hiện dưới giá trên TV ("• Máu ≥ 94")
PSEUDO_LABEL = {v: k for k, v in {
    "Máu": "pseudo.pseudo_total_life", "ES": "pseudo.pseudo_total_energy_shield", "Mana": "pseudo.pseudo_total_mana",
    "K.NT": "pseudo.pseudo_total_elemental_resistance", "K.Chaos": "pseudo.pseudo_total_chaos_resistance",
    "Str": "pseudo.pseudo_total_strength", "Dex": "pseudo.pseudo_total_dexterity", "Int": "pseudo.pseudo_total_intelligence",
}.items()}
EQUIP_LABEL = {"pdps": "pDPS", "edps": "eDPS", "ar": "Giáp", "ev": "Né", "es": "ES"}
# dòng đã gộp vào tổng (máu, ES cộng thẳng, kháng, thuộc tính): Dòng: TẤT CẢ không lọc riêng lần nữa
FOLDED = {"# to maximum Life", "# to maximum Energy Shield"}
# Dòng: TẤT CẢ không lọc riêng các dòng local đã nằm trong DPS (vũ khí) hay Giáp/Né/ES (giáp)
LOCAL_WEAPON = re.compile(r"^(#% increased Physical Damage|Adds # to # (Physical|Fire|Cold|Lightning|Chaos) Damage)$", re.I)
LOCAL_ARMOUR = re.compile(r"^(# to (Armour|Evasion Rating|maximum Energy Shield)|"
                          r"#% increased (Armour|Evasion Rating|Energy Shield)( and (Armour|Evasion|Energy Shield))?)$", re.I)
# Cluster jewel: số passive (enchant), loại passive nhỏ (enchant, lọc theo lựa chọn), notable (dòng không có số)
PASSIVES_REF = "Adds # Passive Skills"
FALLBACK_NOTE = "Lọc theo mọi dòng có số"
SMALL_TYPE_PREFIX = "Added Small Passive Skills grant: "
NOTABLE_PREFIX = "1 Added Passive Skill is "
FILLER_PREFIX = "Added Small Passive Skills also grant: "
_SHORT_DROP = {"increased", "to", "of", "the", "Adds", "all", "with", "Items", "found", "damage", "Damage"}


def mod_label(ref: str) -> str:
    """Nhãn ngắn của một mod: theo bảng mod quan trọng, không có thì lấy vài chữ chính của câu mẫu (≤ 20 ký tự);
    notable của cluster jewel thì lấy tên notable; dòng sát thương bỏ chữ "Damage" thì ghi thêm "dmg"."""
    if ref.startswith(NOTABLE_PREFIX):
        return ref[len(NOTABLE_PREFIX):]
    if ref.startswith(FILLER_PREFIX):
        ref = ref[len(FILLER_PREFIX):]          # dòng phụ của cluster jewel: chỉ lấy phần chỉ số
    for key, lab in IMPORTANT_LABEL:
        if key in ref:
            if key == "to Attacks":
                return next((short for word, short in ELEMENTS if word in ref), "") + " atk"
            return lab
    words = [w for w in re.sub(r"[#%+]", " ", ref).split() if w not in _SHORT_DROP]
    out = ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > 20:
            break
        out = f"{out} {w}" if out else w
    if out and "Damage" in ref.split() and len(out) + 4 <= 20:
        out += " dmg"
    return out or ref[:20]


def small_type(ref: str) -> str:
    """Loại passive nhỏ của cluster jewel: "Added Small Passive Skills grant: 12% increased Lightning Damage"
    → "Lightning Damage"."""
    s = ref[len(SMALL_TYPE_PREFIX):] if ref.startswith(SMALL_TYPE_PREFIX) else ref
    return re.sub(r"^[+-]?\d+(\.\d+)?%?\s*(increased\s+)?", "", s)


def enchant_id(m) -> tuple[str | None, int | None]:
    """Mã enchant trên trade của một dòng và lựa chọn (loại passive nhỏ: "enchant.stat_3948993189|14" → (mã, 14))."""
    ids = (m.trade_ids or {}).get("enchant") or []
    if not ids:
        return None, None
    sid, _, opt = ids[0].partition("|")
    return sid, (int(opt) if opt else None)


def stat_label(f: dict, ref: str, name: str | None = None) -> str:
    """Nhãn của một bộ lọc chỉ số: "Máu ≥ 94", "Loại: Lightning Damage", "Số passive = 9", "Prismatic Heart"."""
    v = f.get("value") or {}
    if "option" in v:
        return f"Loại: {small_type(ref)}"
    if ref == PASSIVES_REF and v.get("min") is not None and v.get("min") == v.get("max"):
        return f"Số passive = {_num(v['min'])}"
    name = name or mod_label(ref)
    if v.get("min") is None:
        return name
    return f"{name} ≥ {_num(v['min'])}{'%' if '#%' in ref else ''}"


def cluster_filters(it: Item, mods) -> list:
    """Bộ lọc bắt buộc theo dòng enchant và notable: số passive (đúng số), loại passive nhỏ, từng notable.
    Trả [(bộ lọc, mod)] theo thứ tự dòng."""
    out = []
    for m in mods:
        if m.kind == "enchant":
            sid, opt = enchant_id(m)
            if not sid:
                continue
            if opt is not None and m.ref.startswith(SMALL_TYPE_PREFIX):
                out.append(({"id": sid, "value": {"option": opt}}, m))
            elif m.ref == PASSIVES_REF and m.value is not None:
                out.append(({"id": sid, "value": {"min": int(m.value), "max": int(m.value)}}, m))
        elif m.kind == "explicit" and m.ref.startswith(NOTABLE_PREFIX):
            sid = ((m.trade_ids or {}).get("explicit") or [None])[0]
            if sid:
                out.append(({"id": sid}, m))
    return out


def _num(v) -> str:
    return f"{v:g}"


def labels(q: dict, it: Item, totals: bool = False) -> dict:
    """Các bộ lọc đã dùng (đồ không phải unique), viết ngắn để hiện trên TV:
    {"req": [bắt buộc], "count": [nhóm đếm], "need": số dòng cần đủ, "of": số dòng trong nhóm}.
    totals: ghi "Máu tổng", "K.NT tổng"… (bảng chọn dòng) thay cho "Máu", "K.NT" (ô giá trên TV)."""
    out = {"req": [], "count": [], "need": 0, "of": 0}
    if it.rarity == "Unique":
        return out
    qq = q["query"]
    flt = qq.get("filters", {})
    req = out["req"]
    if qq.get("type") and it.rarity not in ("Gem", "Other"):      # gem, tiền tệ...: tên đã ở dòng đầu
        req.append(f"Base: {qq['type']}")
    for key in ("weapon_filters", "armour_filters", "equipment_filters"):
        for k, f in ((flt.get(key) or {}).get("filters") or {}).items():
            if "min" in f:
                req.append(f"{EQUIP_LABEL.get(k, k)} ≥ {_num(f['min'])}")
    mf = (flt.get("misc_filters") or {}).get("filters") or {}
    if "ilvl" in mf:
        req.append(f"iLvl ≥ {_num(mf['ilvl']['min'])}")
    if "gem_level" in mf:
        req.append(f"Cấp ≥ {_num(mf['gem_level']['min'])}")
    if "quality" in mf:
        req.append(f"CL ≥ {_num(mf['quality']['min'])}")
    if (mf.get("corrupted") or {}).get("option") == "true":
        req.append("Corrupted")
    refs = {}
    for m in it.mods:
        for sid in (((m.trade_ids or {}).get("explicit") or [None])[0], enchant_id(m)[0]):
            if sid and sid not in refs:
                refs[sid] = m.ref

    def lab(f):
        name = PSEUDO_LABEL.get(f["id"])
        ref = refs.get(f["id"], "")
        if name is not None and totals:
            name += " tổng"
        return stat_label(f, ref, name)

    for g in qq.get("stats", []):
        if g["type"] == "count":
            out["count"] = [lab(f) for f in g["filters"]]
            out["need"] = g["value"]["min"]
            out["of"] = len(g["filters"])
        else:
            req.extend(lab(f) for f in g["filters"])
    return out


def _min(v, rng=RANGE):
    """Mức tối thiểu khi lọc: rộng (mặc định) = 90% giá trị, chặt (rng=0) = đúng giá trị."""
    return math.floor(v * (1 - rng)) if v > 0 else v


def build(it: Item, status: str = "securable", game: str = "poe1", strict: bool = False, by_base: bool = False,
          all_lines: bool = False):
    """Trả về (query, ghi_chú[]) để gửi lên /api/trade/search (PoE1) hoặc /api/trade2/search (PoE2).
    PoE2 gộp bộ lọc vũ khí và giáp vào equipment_filters; không có Foulborn.
    strict: chặt, lấy chỉ số từ 100% (mặc định rộng: từ 90%, như Awakened PoE Trade).
    by_base: lọc đúng base (mặc định mọi base cùng loại đồ; đồ trắng luôn lọc đúng base).
    all_lines: lọc mọi dòng explicit đọc được (mặc định chỉ tổng máu/kháng/thuộc tính và mod quan trọng).
    Từ 3 dòng trở lên thì xếp vào nhóm đếm: mặc định cần đủ (số dòng − 1), all_lines cần đủ hết (bên gọi tự nới
    khi quá ít món). Giáp/Né/ES và DPS vũ khí luôn bắt buộc."""
    rng = 0.0 if strict else RANGE

    def mn(v):
        return _min(v, rng)

    poe2 = game == "poe2"
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
        if poe2:
            return q, notes
        # đồ Foulborn và đồ thường cùng tên giá khác hẳn nhau: lọc đúng loại (như Awakened PoE Trade)
        mf = flt.setdefault("misc_filters", {"filters": {}})["filters"]
        mf["foulborn_item"] = {"option": "true" if it.foulborn else "false"}
        if it.foulborn:
            used = []
            for sm, vals, txt in it.mutated:
                ids = (sm.trade_ids or {}).get("explicit") or []     # trade tìm dòng đột biến bằng mã explicit (như APT)
                if not ids:
                    continue
                f = {"id": ids[0]}
                if vals:
                    v = sum(vals) / len(vals)
                    f["value"] = {"min": v if abs(v) <= 5 else mn(v)}
                stats.append(f)
                used.append(txt)
            if used:
                notes.append("Foulborn: có lọc theo dòng đột biến: " + "; ".join(used))
            else:
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

    if it.rarity in ("Normal", "Magic") and it.props.get("ilvl"):
        # đồ làm base: item level quyết định mod có thể ra (tooltip chỉ có dòng này khi game hiện)
        flt.setdefault("misc_filters", {"filters": {}})["filters"]["ilvl"] = {"min": int(it.props["ilvl"][0])}
    if (it.rarity == "Normal" or by_base or it.category == "Cluster Jewel") and it.base:
        qq["type"] = it.base            # đồ trắng: giá nằm ở chính base; cluster jewel: Large/Medium/Small giá khác hẳn

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
            wf["pdps"] = {"min": mn(pdps)}
        if edps >= 30:
            wf["edps"] = {"min": mn(edps)}
        if wf:
            flt["equipment_filters" if poe2 else "weapon_filters"] = {"filters": wf}
        else:
            notes.append("Vũ khí sát thương thấp: lọc theo mod quan trọng thay vì DPS")

    # gộp chỉ số tổng như APT
    life = sum(m.value for m in mods if _k(m.ref) == "# to maximum Life")
    es_flat = sum(m.value for m in mods if _k(m.ref) == "# to maximum Energy Shield")
    attrs = {"str": 0.0, "dex": 0.0, "int": 0.0}
    for m in mods:
        if _k(m.ref) in ATTR:
            a_kind = ATTR[_k(m.ref)]
            for a in (("str", "dex", "int") if a_kind == "all" else a_kind.split()):
                attrs[a] += m.value
    ele = sum(m.value * RES[_k(m.ref)][0] for m in mods if _k(m.ref) in RES)
    chaos = sum(m.value for m in mods if _k(m.ref) in RES and RES[_k(m.ref)][1])

    pool = []          # các dòng chỉ số (xếp vào nhóm đếm nếu từ 3 dòng)
    defensive = is_armour(it) or is_jewellery(it) or it.category in ("Quiver", "Jewel", "Abyss Jewel")
    if life > 0:
        # PoE1: 2 Str = 1 máu (như APT). PoE2 chưa kiểm chứng nên chỉ tính máu cộng thẳng
        pool.append({"id": PSEUDO["life"], "value": {"min": mn(life + (0 if poe2 else attrs["str"] / 2))}})
    if es_flat > 0 and not is_armour(it):
        pool.append({"id": PSEUDO["es"], "value": {"min": mn(es_flat)}})
    if ele > 0:
        pool.append({"id": PSEUDO["ele_res"], "value": {"min": mn(ele)}})
    if chaos > 0:
        pool.append({"id": PSEUDO["chaos_res"], "value": {"min": mn(chaos)}})
    for a, v in attrs.items():
        if v >= 40:
            pool.append({"id": PSEUDO[a], "value": {"min": mn(v)}})
    def explicit_lines(every):
        used_ids = set()
        for m in mods:
            if m.kind != "explicit" or m.value is None or m.ref.startswith(NOTABLE_PREFIX):
                continue                    # notable đã lọc bắt buộc riêng
            important = any(k in m.ref for k in IMPORTANT)
            folded = (_k(m.ref) in FOLDED or _k(m.ref) in RES or _k(m.ref) in ATTR
                      or (is_weapon(it) and LOCAL_WEAPON.match(_k(m.ref)))
                      or (is_armour(it) and LOCAL_ARMOUR.match(_k(m.ref))))
            if not important and not (every and not folded):
                continue
            ids = (m.trade_ids or {}).get("explicit") or []
            if ids and ids[0] not in used_ids:
                used_ids.add(ids[0])
                pool.append({"id": ids[0], "value": {"min": mn(m.value)}})

    explicit_lines(all_lines)
    required = [f for f, _ in cluster_filters(it, mods)]   # số passive, loại passive nhỏ, notable: bắt buộc
    stats.extend(required)

    def place_pool():
        if len(pool) >= 3:
            qq["stats"].append({"type": "count", "value": {"min": len(pool) if all_lines else len(pool) - 1},
                                "filters": pool})
        else:
            stats.extend(pool)

    if is_armour(it):
        af = {}
        for key, tkey in (("armour", "ar"), ("evasion", "ev"), ("es", "es")):
            if it.props.get(key):
                af[tkey] = {"min": mn(it.props[key][0])}
        if af:
            if poe2:
                flt.setdefault("equipment_filters", {"filters": {}})["filters"].update(af)
            else:
                flt["armour_filters"] = {"filters": af}

    if defensive and life == 0 and es_flat < 40 and not (is_armour(it) and it.props.get("es")):
        notes.append("Món không có máu: giá thật nhiều khả năng THẤP hơn các món tương tự tìm được")
    gear = any(flt.get(k) for k in ("weapon_filters", "armour_filters", "equipment_filters"))
    if not pool and not gear and not required and not all_lines:
        # không có dòng chính nào (jewel thường, flask…): lọc theo mọi dòng có số, cần đủ (tổng − 1)
        explicit_lines(True)
        if pool:
            notes.insert(0, FALLBACK_NOTE)
    place_pool()
    if not pool and not gear and not required:
        notes.append("Không có chỉ số đáng lọc: chỉ tra theo loại món")
    return q, notes
