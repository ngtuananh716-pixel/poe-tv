"""Biến các dòng OCR của tooltip thành thông tin món đồ: loại, thuộc tính, mod."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from rapidfuzz import fuzz

from . import ocr
from .data import ARMOUR, JEWELLERY, WEAPON, Data, letters, unique_has_base

PROP_PATTERNS = {
    "quality": r"^QUALITY",
    "phys": r"^PHYSICAL ?DAMAGE",
    "ele": r"^ELEMENTAL ?DAMAGE",
    "chaos": r"^CHAOS ?DAMAGE",
    "crit": r"^CRITICAL ?STRIKE ?CHANCE",
    "aps": r"^ATTACKS ?PER ?SECOND",
    "range": r"^WEAPON ?RANGE",
    "armour": r"^ARMOUR",
    "evasion": r"^EVASION ?RATING",
    "es": r"^ENERGY ?SHIELD",
    "ward": r"^WARD",
    "block": r"^CHANCE ?TO ?BLOCK",
    "level": r"^LEVEL",
    "stack": r"^STACK ?SIZE",
    "requires": r"^REQUIRES",
    "ilvl": r"^ITEM ?LEVEL",
}
# PoE2: tên thuộc tính khác PoE1, sát thương nguyên tố ghi riêng từng hệ. Chỉ dùng khi tra PoE2 để không bắt nhầm
# dòng mod PoE1 bắt đầu bằng "Fire Damage…" (cả khối bị coi là thuộc tính thì mất mod).
PROP_PATTERNS_POE2 = {
    **{k: v for k, v in PROP_PATTERNS.items() if k not in ("crit", "block")},
    "crit": r"^CRITICAL ?(STRIKE|HIT) ?CHANCE",
    "block": r"^(CHANCE ?TO ?BLOCK|BLOCK ?CHANCE)",
    "fire": r"^FIRE ?DAMAGE\W*\d+-\d+",
    "cold": r"^COLD ?DAMAGE\W*\d+-\d+",
    "lightning": r"^LIGHTNING ?DAMAGE\W*\d+-\d+",
    "reload": r"^RELOAD ?TIME",
    "spirit": r"^SPIRIT\W*\d",
}


# Chế độ "Advanced Descriptions" (bấm R3 trong game): có thêm dòng mô tả mod và khoảng giá trị "7(6-9)"
META_LINE = re.compile(r"^\W*(PREFIX|SUFFIX|IMPLICIT|UNIQUE|MASTER\s*CRAFTED|CRAFTED|MONSTER|ENCHANT\w*|FRACTURED|"
                       r"CORRUPTED|FOULBORN|EATER|SEARING|SYNTHESI[SZ]ED|VEILED)\b.*\bMODIFIER\b")
RANGE = re.compile(r"\s*\(\s*[+-]?\d+(?:\.\d+)?\s*[-–~]\s*[+-]?\d+(?:\.\d+)?\s*\)")


def is_meta(s: str) -> bool:
    return bool(META_LINE.match(s.upper()))


def clean_numbers(s: str) -> str:
    """Sửa lỗi OCR hay gặp: chữ O/I lẫn vào con số, số 1/0 lẫn vào chữ. Bỏ khoảng giá trị "(6-9)" và đuôi
    "— Unscalable Value" của chế độ Advanced Descriptions."""
    s = s.upper()
    s = re.sub(r"\s*[—–-]+\s*UNSCALABLE\s*VALUE", "", s)
    s = re.sub(r"(?<=[\d.])O", "0", s)
    s = re.sub(r"(?<![A-Z])O(?=\d)", "0", s)
    s = re.sub(r"(?<![A-Z])[IL|](?=\d)", "1", s)
    s = re.sub(r"(?<=\d)[IL|](?=[\d%])", "1", s)
    s = re.sub(r"(?<=[A-Z])1(?=[A-Z])", "I", s)
    s = re.sub(r"(?<=[A-Z])0(?=[A-Z])", "O", s)
    s = RANGE.sub("", s)
    return s


def numbers(s: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]


@dataclass
class Mod:
    text: str
    ref: str
    kind: str            # implicit / explicit / enchant
    values: list
    trade_ids: dict
    score: float
    better: int = 1

    @property
    def value(self):
        """Giá trị đại diện: trung bình nếu là khoảng 'Adds # to #'."""
        if not self.values:
            return None
        return sum(self.values) / len(self.values)


@dataclass
class Item:
    rarity: str = "?"           # Unique / Rare / Magic / Normal / Gem / Other
    name: str = ""
    base: str = ""
    category: str | None = None
    props: dict = field(default_factory=dict)
    mods: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    corrupted: bool = False
    foulborn: bool = False      # unique "Foulborn …": có một dòng mod đột biến (từ 3.26)
    mutated: list = field(default_factory=list)   # dòng đột biến nhận ra qua màu hồng: [(StatMatch, values, text)]

    @property
    def title(self):
        """Tên ngắn để hiện: unique thì tên riêng, đồ khác thì loại đồ."""
        if self.rarity == "Unique" and self.name:
            return ("Foulborn " if self.foulborn else "") + self.name
        return self.base or self.name or "?"

    # ----- chỉ số tính sẵn cho vũ khí -----
    def _avg(self, key):
        v = self.props.get(key)
        if not v:
            return 0.0
        pairs = list(zip(v[0::2], v[1::2]))
        return sum((a + b) / 2 for a, b in pairs)

    @property
    def pdps(self):
        aps = (self.props.get("aps") or [0])[0]
        return self._avg("phys") * aps

    @property
    def edps(self):
        aps = (self.props.get("aps") or [0])[0]
        return self._avg("ele") * aps

    def total(self, ref_list):
        return sum(m.value or 0 for m in self.mods if m.ref in ref_list)


def _rows(lines: list[ocr.Line]):
    """Gộp các ô chữ cùng hàng (OCR có thể cắt một dòng thành nhiều ô)."""
    rows = []
    for l in sorted(lines, key=lambda l: l.cy):
        if rows and abs(rows[-1][0].cy - l.cy) < 0.45 * max(l.h, rows[-1][0].h):
            rows[-1].append(l)
        else:
            rows.append([l])
    return rows


def _sections(rows):
    """Chia tooltip thành các khối theo khoảng trống giữa các hàng (vạch ngăn cách)."""
    if len(rows) < 2:
        return [rows]
    tops = [min(l.y0 for l in r) for r in rows]
    gaps = [b - a for a, b in zip(tops, tops[1:])]
    base_gap = median(sorted(gaps)[: max(1, len(gaps) // 2 + 1)])
    secs, cur = [], [rows[0]]
    for r, g in zip(rows[1:], gaps):
        if g > 1.2 * base_gap:
            secs.append(cur)
            cur = []
        cur.append(r)
    secs.append(cur)
    return secs


def parse(header: list[str], lines: list[ocr.Line], data: Data) -> Item:
    it = Item()
    rows = _rows(lines)
    # bỏ các hàng tiêu đề (tên, base) ở đầu
    hdr_keys = [letters(h) for h in header]
    dropped = 0
    while rows and dropped < len(header) and any(
            fuzz.ratio(letters(l.text), hk) >= 80 for l in rows[0] for hk in hdr_keys):
        rows.pop(0)
        dropped += 1

    # ---- xác định loại món ----
    if len(header) == 2:
        it.name, base_txt = header[0].title(), header[1]
        b = data.find_base(base_txt)
        it.base = b["name"] if b else base_txt.title()
        it.category = b["category"] if b else None
        uname = header[0]
        m = re.match(r"^\s*foulborn\s+(.+)$", uname, re.I)       # giống FOULBORN_NAME của Awakened PoE Trade
        if m:
            uname = m.group(1)
        u = data.find_unique(uname)
        it.foulborn = bool(m and u)
        it.rarity = "Unique" if u and unique_has_base(u, it.base) else "Rare"
        if it.rarity == "Unique":
            it.name = u["name"]
    else:
        h = header[0]
        g = data.find_gem(h)
        b = data.find_base(h)
        o = data.find_other(h)
        if g:
            it.rarity, it.name, it.base = "Gem", g, g
        elif b:
            it.rarity, it.base, it.category = "Normal", b["name"], b["category"]
        elif o:
            it.rarity, it.name, it.base = "Other", o[0], o[0]
        else:
            b = data.find_base_in(h)
            it.rarity, it.name = "Magic", h.title()
            if b:
                it.base, it.category = b["name"], b["category"]

    # ---- đọc thuộc tính và mod theo từng khối ----
    secs = _sections(rows)
    patterns = PROP_PATTERNS_POE2 if getattr(data, "game", "poe1") == "poe2" else PROP_PATTERNS
    mod_secs = []
    after_req = False
    for sec in secs:
        texts = [clean_numbers(" ".join(l.text for l in sorted(r, key=lambda l: l.x0))) for r in sec]
        is_prop = False
        for t in texts:
            tt = t.replace(" ", "")
            for key, pat in patterns.items():
                if re.match(pat.replace(" ?", ""), tt):
                    if key in ("phys", "ele", "chaos", "fire", "cold", "lightning"):
                        # chỉ lấy các cặp "a-b", bỏ số rác dính vào hàng
                        pairs = [float(x) for pair in re.findall(r"(\d+)\s*-\s*(\d+)", t) for x in pair]
                        if key in ("fire", "cold", "lightning"):      # PoE2: gộp các hệ vào sát thương nguyên tố
                            it.props.setdefault("ele", []).extend(pairs)
                        else:
                            it.props[key] = pairs
                    else:
                        it.props[key] = numbers(t)
                    is_prop = True
                    if key == "requires":
                        after_req = True
                    break
            if "CORRUPTED" in tt:
                it.corrupted = True
        if is_prop:
            continue
        matched = []
        for r in sec:
            # mỗi hàng có thể gồm nhiều ô; thử cả hàng trước, rồi từng ô
            cands = [" ".join(l.text for l in sorted(r, key=lambda l: l.x0))] + ([l.text for l in r] if len(r) > 1 else [])
            if is_meta(cands[0]):
                continue            # dòng mô tả mod của chế độ Advanced Descriptions, không phải mod
            best = None
            for c in cands:
                c = clean_numbers(c)
                sm, score = data.match_mod(c, it.category)
                if sm and (best is None or score > best[1]):
                    best = (sm, score, c)
            if best:
                matched.append(best)
            else:
                it.unmatched.append(cands[0])
        if matched:
            mod_secs.append(matched)

    # khối mod đầu tiên sau yêu cầu cấp độ là implicit nếu còn khối mod khác phía sau
    for i, sec in enumerate(mod_secs):
        kind0 = "implicit" if (len(mod_secs) > 1 and i == 0 and it.rarity != "Gem") else "explicit"
        for sm, score, txt in sec:
            ids = sm.trade_ids or {}
            # dòng trong khối đầu mà trade chỉ có mã enchant (không có mã implicit): enchant, ví dụ 3 dòng đầu của
            # cluster jewel (số passive, loại passive nhỏ, số ô jewel)
            kind = "enchant" if kind0 == "implicit" and ids.get("enchant") and not ids.get("implicit") else kind0
            vals = numbers(txt)
            if sm.n_values and len(vals) > sm.n_values:
                vals = vals[-sm.n_values:]       # bỏ số rác dính phía trước
            if sm.fixed_value is not None and not vals:
                vals = [sm.fixed_value]
            if sm.negate:
                vals = [-v for v in vals]
            it.mods.append(Mod(text=txt, ref=sm.ref, kind=kind, values=vals,
                               trade_ids=sm.trade_ids, score=score, better=sm.better))
    return it


def is_weapon(it: Item):
    return it.category in WEAPON


def is_armour(it: Item):
    return it.category in ARMOUR


def is_jewellery(it: Item):
    return it.category in JEWELLERY


def add_mutated(it: Item, rows_text: list[str], data: Data, min_score: float = 90):
    """Gắn các dòng đột biến (chữ hồng của đồ Foulborn) vào món đồ để lọc trade theo đúng dòng đó."""
    for raw in rows_text:
        txt = clean_numbers(raw)
        sm, score = data.match_mod(txt, it.category)
        if not sm or score < min_score:
            continue
        vals = numbers(txt)
        if sm.n_values and len(vals) > sm.n_values:
            vals = vals[-sm.n_values:]
        if sm.fixed_value is not None and not vals:
            vals = [sm.fixed_value]
        if sm.negate:
            vals = [-v for v in vals]
        it.mutated.append((sm, vals, txt))
