"""Tự chọn dòng để tra (màn "Chọn dòng để tra" trên điện thoại, khung "Món vừa tra" trên trang laptop).

panel(): bảng các dòng của món vừa tra: base, chỉ số món (DPS / Giáp, Né, ES), item level, implicit có gộp vào tổng,
các dòng của món (đồ rare 6 ô, magic 2 ô; thiếu thì "none"). Mỗi dòng có dấu chọn và mức tối thiểu.
build(): dựng bộ lọc trade từ lựa chọn. Lựa chọn mặc định cho ra ĐÚNG bộ lọc của query.build (cùng chế độ lọc), để bấm
TRA LẠI mà không đổi gì thì ra cùng kết quả với giá tự tra.

Gộp như tự tra: dòng máu → Máu tổng (PoE1 cộng ½ Str của món như APT), ES cộng thẳng (trừ giáp) → ES tổng, dòng kháng →
K.NT tổng / K.Chaos tổng, thuộc tính → Str/Dex/Int tổng (chỉ lọc khi tổng các dòng đã chọn ≥ 40, như tự tra).
Mức tối thiểu của tổng = floor(tổng giá trị dòng chưa sửa × 90%) + các mức đã sửa. Dòng local (đã tính trong DPS vũ khí
hay Giáp/Né/ES của giáp) không chọn được; implicit không gộp vào tổng thì chưa lọc riêng được nên không hiện.
"""
from __future__ import annotations

import math

from . import query as Q
from .data import TRADE_CATEGORY
from .parse import Item, is_armour, is_weapon

GROUP_ORDER = ("life", "es", "ele", "chaos", "str", "dex", "int")
GROUP_ID = {"life": Q.PSEUDO["life"], "es": Q.PSEUDO["es"], "ele": Q.PSEUDO["ele_res"],
            "chaos": Q.PSEUDO["chaos_res"], "str": Q.PSEUDO["str"], "dex": Q.PSEUDO["dex"], "int": Q.PSEUDO["int"]}
GROUP_LABEL = {g: Q.PSEUDO_LABEL[GROUP_ID[g]] + " tổng" for g in GROUP_ORDER}
ATTRS = ("str", "dex", "int")
ATTR_MIN = 40               # thuộc tính chỉ lọc khi tổng ≥ bấy nhiêu (như tự tra)
SLOTS = {"Rare": 6, "Magic": 2}
JEWEL_SLOTS = {"Rare": 4, "Magic": 2}       # jewel (kể cả cluster): tối đa 2 prefix + 2 suffix
JEWELS = ("Jewel", "Abyss Jewel", "Cluster Jewel")


def fill(ref: str, values) -> str:
    """Câu mẫu + giá trị: "+# to Strength", [49] → "+49 to Strength"."""
    parts = ref.split("#")
    out = parts[0]
    for j, p in enumerate(parts[1:]):
        out += (Q._num(values[j]) if j < len(values) else "#") + p
    return out


def default_need(m: int, all_lines: bool) -> int:
    """Số dòng cần đủ mặc định khi có m dòng chỉ số (như tự tra)."""
    if all_lines or m < 3:
        return m
    return m - 1


def _row(key, section, text, on, enabled=True, raw=None, exact=False, groups=None, sid=None, pct=False, tag=None,
         ref="", unsure=False, req=False, option=None, eq=False):
    """req: bộ lọc bắt buộc riêng (enchant, notable), không vào nhóm đếm; option: lọc theo lựa chọn (loại passive nhỏ);
    eq: lọc đúng số (số passive)."""
    return {"key": key, "section": section, "text": text, "on": bool(on), "enabled": bool(enabled), "raw": raw,
            "exact": exact, "groups": groups or [], "id": sid, "pct": pct, "tag": tag, "ref": ref, "unsure": unsure,
            "req": req, "option": option, "eq": eq}


def panel(it: Item, game: str = "poe1", strict: bool = False, by_base: bool = False, all_lines: bool = False,
          auto_q: dict | None = None) -> dict | None:
    """Bảng chọn dòng của món (None với unique, gem, đồ bán theo lô: những món này tra theo tên)."""
    if it.rarity in ("Unique", "Gem", "Other") or not it.category:
        return None
    poe2 = game == "poe2"
    rng = 0.0 if strict else Q.RANGE
    weapon, armour = is_weapon(it), is_armour(it)
    rows = []
    base_forced = not TRADE_CATEGORY.get(it.category)
    cluster = it.category == "Cluster Jewel"
    rows.append(_row("base", "base", f"Đúng base: {it.base or '?'}", base_forced or ((it.rarity == "Normal" or by_base
                     or cluster) and bool(it.base)), enabled=not base_forced and bool(it.base),
                     tag="cluster: luôn lọc đúng cỡ" if cluster else None))
    if it.props.get("ilvl"):
        v = float(int(it.props["ilvl"][0]))
        rows.append(_row("ilvl", "base", f"Item level {Q._num(v)}", it.rarity in ("Normal", "Magic"), raw=v, exact=True))
    if weapon:
        for k, name, v in (("g_pdps", "pDPS", it.pdps), ("g_edps", "eDPS", it.edps)):
            if v > 0:
                rows.append(_row(k, "gear", f"{name} {math.floor(v)}", v >= 30, raw=v))
    if armour:
        for prop, k, name in (("armour", "g_ar", "Giáp"), ("evasion", "g_ev", "Né"), ("es", "g_es", "ES")):
            if it.props.get(prop):
                v = it.props[prop][0]
                rows.append(_row(k, "gear", f"{name} {Q._num(v)}", True, raw=v))

    # tổng từng thuộc tính (dòng đọc chắc) để biết thuộc tính nào đủ 40 mà chọn sẵn
    attr_total = {a: 0.0 for a in ATTRS}
    mod_groups = []
    for m in it.mods:
        r = Q._k(m.ref)
        g = []
        if r in Q.RES:
            n, ch = Q.RES[r]
            if n:
                g.append(["ele", n])
            if ch:
                g.append(["chaos", 1])
        elif r in Q.ATTR:
            kind = Q.ATTR[r]
            g = [[a, 1] for a in (ATTRS if kind == "all" else kind.split())]
        elif r == "# to maximum Life":
            g = [["life", 1]]
        elif r == "# to maximum Energy Shield" and not armour:
            g = [["es", 1]]
        mod_groups.append(g)
        if m.score >= Q.MIN_SCORE:
            for a, w in g:
                if a in attr_total:
                    attr_total[a] += (m.value or 0) * w
    has_life = any(gr == "life" for g in mod_groups for gr, _ in g)
    used = set()
    n_expl = 0
    for i, m in enumerate(it.mods):
        g = mod_groups[i]
        r = Q._k(m.ref)
        unsure = m.score < Q.MIN_SCORE
        sid = ((m.trade_ids or {}).get("explicit") or [None])[0]
        local = bool((weapon and Q.LOCAL_WEAPON.match(r)) or (armour and Q.LOCAL_ARMOUR.match(r)))
        if m.kind == "enchant":
            eid, opt = Q.enchant_id(m)
            text = fill(m.ref, m.values)
            if opt is not None and m.ref.startswith(Q.SMALL_TYPE_PREFIX):
                rows.append(_row(f"m{i}", "enchant", text, not unsure, sid=eid, tag="loại passive nhỏ", ref=m.ref,
                                 unsure=unsure, req=True, option=opt))
            elif m.ref == Q.PASSIVES_REF and m.value is not None:
                rows.append(_row(f"m{i}", "enchant", text, not unsure, raw=float(int(m.value)), sid=eid,
                                 tag=f"số passive: lọc đúng {int(m.value)}", ref=m.ref, unsure=unsure, req=True, eq=True))
            else:
                rows.append(_row(f"m{i}", "enchant", text, False, enabled=bool(eid), raw=m.value, sid=eid,
                                 pct="#%" in m.ref, tag=None if eid else "không lọc được", ref=m.ref, unsure=unsure,
                                 req=True))
            continue
        if m.kind == "implicit":
            if not g:
                continue                    # implicit không gộp vào tổng: chưa lọc riêng được
            section = "implicit"
        else:
            section = "explicit"
            n_expl += 1
        text = fill(m.ref, m.values)
        if m.text.lstrip().startswith("+") and not text.startswith("+"):
            text = "+" + text               # dữ liệu PoE2 ghi câu mẫu không có dấu "+"
        if g:
            names = " + ".join(GROUP_LABEL[gr] for gr, _ in g)
            tag = f"→ gộp vào {names}"
            if not poe2 and has_life and any(gr == "str" for gr, _ in g):
                tag += " · ½ vào Máu tổng"
            on = not unsure and (not all(gr in ATTRS for gr, _ in g) or any(attr_total[gr] >= ATTR_MIN for gr, _ in g))
            rows.append(_row(f"m{i}", section, text, on, raw=m.value, groups=g, pct="#%" in m.ref,
                             tag=tag + (" · đọc chưa chắc" if unsure else ""), ref=m.ref, unsure=unsure))
            continue
        if local:
            rows.append(_row(f"m{i}", section, text, False, enabled=False, raw=m.value,
                             tag="đã tính trong DPS" if weapon else "đã tính trong Giáp/Né/ES", ref=m.ref, unsure=unsure))
            continue
        if not sid:
            rows.append(_row(f"m{i}", section, text, False, enabled=False, raw=m.value, tag="không lọc được",
                             ref=m.ref, unsure=unsure))
            continue
        if m.ref.startswith(Q.NOTABLE_PREFIX):
            rows.append(_row(f"m{i}", section, text, not unsure, sid=sid, tag="notable" + (" · đọc chưa chắc" if unsure
                             else ""), ref=m.ref, unsure=unsure, req=True))
            continue
        important = any(k in m.ref for k in Q.IMPORTANT)
        on = (not unsure and m.value is not None and sid not in used and (important or all_lines))
        if on:
            used.add(sid)
        tag = "đọc chưa chắc" if unsure else ("dòng phụ" if m.ref.startswith(Q.FILLER_PREFIX) else None)
        rows.append(_row(f"m{i}", section, text, on, raw=m.value, sid=sid, pct="#%" in m.ref, tag=tag, ref=m.ref,
                         unsure=unsure))
    # không có dòng chính nào (jewel thường, flask…): như tự tra, chọn sẵn mọi dòng có số, cần đủ (tổng − 1)
    fallback = False
    if not all_lines and not any(r["on"] for r in rows if r["section"] in ("gear", "implicit", "explicit", "enchant")):
        for r in rows:
            if (r["section"] == "explicit" and not r["groups"] and r["enabled"] and not r["req"] and r["raw"] is not None
                    and not r["unsure"] and r["id"] and r["id"] not in used):
                used.add(r["id"])
                r["on"] = True
                fallback = True
    slots = (JEWEL_SLOTS if it.category in JEWELS else SLOTS).get(it.rarity, 0)
    pn = {"game": game, "title": it.title, "rarity": it.rarity, "base": it.base, "strict": strict,
          "all_lines": all_lines, "rng": rng, "rows": rows, "slots": max(slots, n_expl),
          "n_explicit": n_expl, "corrupted": it.corrupted, "fallback": fallback}
    m0 = len(_pool(it, pn, _sel_on(pn, None), {})[0])
    pn["m0"] = m0
    need = default_need(m0, all_lines)
    for g in (auto_q or {}).get("query", {}).get("stats", []):
        if g.get("type") == "count":
            need = g["value"]["min"]        # Dòng TẤT CẢ đã tự nới: lấy đúng số dòng tự tra đã dùng
    pn["need"] = need
    return pn


def _sel_on(pn, sel):
    """Dòng nào đang chọn: dòng không chọn được thì giữ mặc định."""
    want = (sel or {}).get("on") or {}
    return {r["key"]: (want.get(r["key"], r["on"]) if r["enabled"] else r["on"]) for r in pn["rows"]}


def _min_of(pn, r, edit):
    """Mức tối thiểu một dòng không gộp: đã sửa thì lấy số đã sửa, không thì 90% (hoặc 100% khi chặt)."""
    if r["key"] in edit:
        return edit[r["key"]]
    if r["exact"]:
        return int(r["raw"])
    return Q._min(r["raw"], pn["rng"])


def show_min(pn, r):
    """Số hiện sẵn trong ô "≥" của một dòng (chưa sửa)."""
    if r["raw"] is None:
        return None
    if r["exact"] or r["eq"]:
        return int(r["raw"])
    return Q._min(r["raw"], pn["rng"])


def _pool(it, pn, on, edit):
    """Các bộ lọc chỉ số theo thứ tự tự tra: các tổng, rồi từng dòng. Trả (pool, nhãn)."""
    poe2 = pn["game"] == "poe2"
    pool, labs = [], []
    rows = [r for r in pn["rows"] if r["section"] in ("implicit", "explicit")]
    for grp in GROUP_ORDER:
        ticked = [r for r in rows if on[r["key"]] and any(g == grp for g, _ in r["groups"])]
        if not ticked:
            continue
        w = lambda r: next(x for g, x in r["groups"] if g == grp)
        raw = sum((r["raw"] or 0) * w(r) for r in ticked if r["key"] not in edit)
        ed = sum(edit[r["key"]] * w(r) for r in ticked if r["key"] in edit)
        if grp in ATTRS and sum((r["raw"] or 0) * w(r) for r in ticked) < ATTR_MIN:
            continue
        if grp == "life" and not poe2:
            # 2 Str = 1 máu (như APT): Str của món tính vào máu dù có chọn dòng Str hay không (trade cũng tính vậy)
            for r in rows:
                if any(g == "str" for g, _ in r["groups"]) and (on[r["key"]] or not r["unsure"]):
                    sw = next(x for g, x in r["groups"] if g == "str")
                    if r["key"] in edit:
                        ed += edit[r["key"]] * sw / 2
                    else:
                        raw += (r["raw"] or 0) * sw / 2
        total = (math.floor(raw * (1 - pn["rng"])) if raw > 0 else raw) + ed
        if isinstance(total, float) and total.is_integer():
            total = int(total)
        pool.append({"id": GROUP_ID[grp], "value": {"min": total}})
        labs.append(f"{GROUP_LABEL[grp]} ≥ {Q._num(total)}")
    used = set()
    for r in rows:
        if r["groups"] or r["req"] or not on[r["key"]] or not r["id"] or r["id"] in used:
            continue
        used.add(r["id"])
        if r["raw"] is None and r["key"] not in edit:
            pool.append({"id": r["id"]})
            labs.append(Q.mod_label(r["ref"]))
            continue
        v = _min_of(pn, r, edit)
        pool.append({"id": r["id"], "value": {"min": v}})
        labs.append(f"{Q.mod_label(r['ref'])} ≥ {Q._num(v)}{'%' if r['pct'] else ''}")
    return pool, labs


def need_for(pn, sel, m):
    """Số dòng cần đủ: người dùng đã chỉnh thì giữ (trong 1..m); chưa chỉnh mà dòng chọn như mặc định thì như tự tra;
    còn lại theo quy tắc tự tra."""
    sel = sel or {}
    if sel.get("need") is not None:
        return max(1, min(int(sel["need"]), m)) if m else 0
    if _sel_on(pn, sel) == _sel_on(pn, None) and not sel.get("edit"):
        return pn["need"]
    return default_need(m, pn["all_lines"])


def build(it: Item, pn: dict, sel: dict | None = None, status: str = "securable"):
    """Bộ lọc trade từ lựa chọn sel = {"on": {khóa: bool}, "edit": {khóa: số}, "need": số|None}.
    Trả (query, tóm tắt {"req": [...], "stats": [...], "need": n, "of": m}) để hiện khung "Khi tra"."""
    sel = sel or {}
    on = _sel_on(pn, sel)
    edit = {k: v for k, v in (sel.get("edit") or {}).items() if k in on}
    poe2 = pn["game"] == "poe2"
    rows = {r["key"]: r for r in pn["rows"]}
    q = {"query": {"status": {"option": status}, "filters": {}, "stats": [{"type": "and", "filters": []}]},
         "sort": {"price": "asc"}}
    qq = q["query"]
    flt = qq["filters"]
    stats = qq["stats"][0]["filters"]
    req = []
    if pn["corrupted"]:
        flt["misc_filters"] = {"filters": {"corrupted": {"option": "true"}}}
    if "ilvl" in rows and on["ilvl"]:
        v = _min_of(pn, rows["ilvl"], edit)
        flt.setdefault("misc_filters", {"filters": {}})["filters"]["ilvl"] = {"min": v}
    if on["base"] and it.base:
        qq["type"] = it.base
    cat = TRADE_CATEGORY.get(it.category)
    tf = {"rarity": {"option": "nonunique"}}
    if cat:
        tf["category"] = {"option": cat}
    else:
        qq["type"] = it.base
    flt["type_filters"] = {"filters": tf}
    if qq.get("type"):
        req.append(f"Base: {qq['type']}")
    wf = {}
    for k, tk, name in (("g_pdps", "pdps", "pDPS"), ("g_edps", "edps", "eDPS")):
        if k in rows and on[k]:
            wf[tk] = {"min": _min_of(pn, rows[k], edit)}
            req.append(f"{name} ≥ {Q._num(wf[tk]['min'])}")
    if wf:
        flt["equipment_filters" if poe2 else "weapon_filters"] = {"filters": wf}
    if "ilvl" in rows and on["ilvl"]:
        req.append(f"iLvl ≥ {Q._num(flt['misc_filters']['filters']['ilvl']['min'])}")
    if pn["corrupted"]:
        req.append("Corrupted")

    for r in pn["rows"]:                       # enchant, notable: bắt buộc, theo thứ tự dòng
        if not (r["req"] and on[r["key"]] and r["id"]):
            continue
        if r["option"] is not None:
            f = {"id": r["id"], "value": {"option": r["option"]}}
        elif r["eq"]:
            v = edit.get(r["key"], int(r["raw"]))
            f = {"id": r["id"], "value": {"min": v, "max": v}}
        elif r["raw"] is None and r["key"] not in edit:
            f = {"id": r["id"]}
        else:
            f = {"id": r["id"], "value": {"min": _min_of(pn, r, edit)}}
        stats.append(f)
        req.append(Q.stat_label(f, r["ref"]))
    pool, labs = _pool(it, pn, on, edit)
    m = len(pool)
    need = need_for(pn, sel, m)
    if m and (m >= 3 or need < m):
        qq["stats"].append({"type": "count", "value": {"min": need}, "filters": pool})
    else:
        stats.extend(pool)

    af = {}
    for k, tk, name in (("g_ar", "ar", "Giáp"), ("g_ev", "ev", "Né"), ("g_es", "es", "ES")):
        if k in rows and on[k]:
            af[tk] = {"min": _min_of(pn, rows[k], edit)}
            req.append(f"{name} ≥ {Q._num(af[tk]['min'])}")
    if af:
        if poe2:
            flt.setdefault("equipment_filters", {"filters": {}})["filters"].update(af)
        else:
            flt["armour_filters"] = {"filters": af}
    return q, {"req": req, "stats": labs, "need": need if m else 0, "of": m}


def preset(pn: dict, which: str, sel: dict | None = None) -> dict:
    """Chọn nhanh các dòng chỉ số (implicit + dòng của món): "main" = như tự tra, "all" = mọi dòng chọn được,
    "none" = bỏ hết. Giữ base, chỉ số món, các mức đã sửa; số dòng cần đủ về mặc định."""
    sel = dict(sel or {})
    on = dict(_sel_on(pn, sel))
    for r in pn["rows"]:
        if r["section"] in ("implicit", "explicit", "enchant") and r["enabled"]:
            on[r["key"]] = r["on"] if which == "main" else (which == "all")
    sel["on"] = on
    sel["need"] = None
    return sel


def result_lines(lines: list[str]) -> list[str]:
    """Phần giá của ô giá (bỏ tên món): Min, ước tính, tin cậy, số món."""
    return lines[1:]
