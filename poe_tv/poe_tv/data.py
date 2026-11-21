"""Nạp dữ liệu món đồ và mod: PoE1 lấy từ Awakened PoE Trade (data/, MIT), PoE2 lấy từ Exiled Exchange 2 (data/poe2/, MIT)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GAMES = ("poe1", "poe2")

# Nhóm loại món đồ, theo renderer/src/parser/meta.ts của APT
WEAPON = {
    "Fishing Rod", "Bow", "Wand", "One-Handed Axe", "One-Handed Mace", "One-Handed Sword",
    "Claw", "Dagger", "Rune Dagger", "Sceptre", "Two-Handed Axe", "Two-Handed Mace",
    "Two-Handed Sword", "Staff", "Warstaff",
}
ARMOUR = {"Body Armour", "Boots", "Gloves", "Helmet", "Shield"}
JEWELLERY = {"Amulet", "Ring", "Belt"}
# PoE2 (tên loại theo dữ liệu Exiled Exchange 2, khác PoE1 nên gộp chung không lẫn)
WEAPON |= {"One Hand Axe", "Two Hand Axe", "One Hand Mace", "Two Hand Mace", "One Hand Sword", "Two Hand Sword",
           "Crossbow", "Spear", "Flail", "Talisman"}
ARMOUR |= {"Buckler", "Focus"}
# PoE2: những loại có "craftable" nhưng không phải trang bị (tiền tệ, soul core, omen…) → xếp vào nhóm vật phẩm khác
POE2_NON_EQUIP = {"Currency", "SoulCore", "Omen", "Map", "MapFragment", "MiscMapItem", "PinnacleKey", "VaultKey",
                  "TowerAugment", "BrequelFruit", "Breachstone", "ExpeditionLogbook", "Incubator", "QuestItem",
                  "UncutSkillGem"}

# Loại món -> mã danh mục trên trade (theo pathofexile-trade.ts của APT)
TRADE_CATEGORY = {
    "Abyss Jewel": "jewel.abyss", "Amulet": "accessory.amulet", "Belt": "accessory.belt",
    "Body Armour": "armour.chest", "Boots": "armour.boots", "Bow": "weapon.bow", "Claw": "weapon.claw",
    "Dagger": "weapon.dagger", "Fishing Rod": "weapon.rod", "Flask": "flask", "Gloves": "armour.gloves",
    "Helmet": "armour.helmet", "Jewel": "jewel", "One-Handed Axe": "weapon.oneaxe",
    "One-Handed Mace": "weapon.onemace", "One-Handed Sword": "weapon.onesword", "Quiver": "armour.quiver",
    "Ring": "accessory.ring", "Rune Dagger": "weapon.runedagger", "Sceptre": "weapon.sceptre",
    "Shield": "armour.shield", "Staff": "weapon.staff", "Two-Handed Axe": "weapon.twoaxe",
    "Two-Handed Mace": "weapon.twomace", "Two-Handed Sword": "weapon.twosword", "Wand": "weapon.wand",
    "Warstaff": "weapon.warstaff", "Cluster Jewel": "jewel.cluster", "Trinket": "accessory.trinket",
    "Tincture": "tincture",
    # PoE2 (theo pathofexile-trade.ts của Exiled Exchange 2)
    "One Hand Axe": "weapon.oneaxe", "Two Hand Axe": "weapon.twoaxe", "One Hand Mace": "weapon.onemace",
    "Two Hand Mace": "weapon.twomace", "One Hand Sword": "weapon.onesword", "Two Hand Sword": "weapon.twosword",
    "Crossbow": "weapon.crossbow", "Spear": "weapon.spear", "Flail": "weapon.flail", "Talisman": "weapon.talisman",
    "Buckler": "armour.buckler", "Focus": "armour.focus", "Charm": "flask.charm",
}


def letters(s: str) -> str:
    """Chỉ giữ chữ cái thường, dùng để so khớp khi OCR làm mất dấu cách."""
    return re.sub(r"[^a-z]", "", s.lower())


def template_key(s: str) -> str:
    """Khóa so khớp mod: chữ cái thường + dấu # thay cho số."""
    s = s.lower().replace("+#", "#")
    s = re.sub(r"\d+(?:\.\d+)?", "#", s)
    return re.sub(r"[^a-z#]", "", s)


@dataclass
class StatMatch:
    ref: str
    trade_ids: dict
    negate: bool
    fixed_value: float | None
    n_values: int
    better: int = 1


@dataclass
class Data:
    game: str = "poe1"
    bases: dict = field(default_factory=dict)       # letters(base) -> {"name", "category"}
    uniques: dict = field(default_factory=dict)     # letters(name) -> {"name", "base"}
    gems: dict = field(default_factory=dict)        # letters(name) -> name
    others: dict = field(default_factory=dict)      # tiền tệ, thẻ bài... letters(name) -> (name, namespace)
    trade_tags: dict = field(default_factory=dict)  # letters(name) -> mã trên sàn trao đổi theo lô (vd "offer", "chaos")
    icon_stems: dict = field(default_factory=dict)  # letters(name) -> tên ảnh icon (vd Divine Orb -> CurrencyModValues)
    matchers: dict = field(default_factory=dict)    # template_key -> list[(entry, matcher)]
    _matcher_keys: list = field(default_factory=list)

    # ---------- món đồ ----------
    def find_base(self, text: str, cutoff: int = 90):
        k = letters(text)
        if k in self.bases:
            return self.bases[k]
        hit = process.extractOne(k, self.bases.keys(), scorer=fuzz.ratio, score_cutoff=cutoff)
        return self.bases[hit[0]] if hit else None

    def find_base_in(self, text: str):
        """Tìm base nằm bên trong tên đồ magic (vd. 'Heavy Belt of the Whale')."""
        k = letters(text)
        best = None
        for bk, b in self.bases.items():
            if len(bk) >= 5 and bk in k and (best is None or len(bk) > len(letters(best["name"]))):
                best = b
        return best

    def find_unique(self, name: str, cutoff: int = 88):
        k = letters(name)
        if k in self.uniques:
            return self.uniques[k]
        hit = process.extractOne(k, self.uniques.keys(), scorer=fuzz.ratio, score_cutoff=cutoff)
        return self.uniques[hit[0]] if hit else None

    def find_gem(self, text: str, cutoff: int = 90):
        k = letters(text)
        if k in self.gems:
            return self.gems[k]
        hit = process.extractOne(k, self.gems.keys(), scorer=fuzz.ratio, score_cutoff=cutoff)
        return self.gems[hit[0]] if hit else None

    def find_other(self, text: str, cutoff: int = 92):
        k = letters(text)
        if k in self.others:
            return self.others[k]
        hit = process.extractOne(k, self.others.keys(), scorer=fuzz.ratio, score_cutoff=cutoff)
        return self.others[hit[0]] if hit else None

    # ---------- mod ----------
    def match_mod(self, line: str, category: str | None, cutoff: int = 86):
        """Trả về (StatMatch, điểm khớp) hoặc (None, điểm)."""
        key = template_key(line)
        if len(key.replace("#", "")) < 6:
            return None, 0
        cands = self.matchers.get(key)
        score = 100.0
        if not cands:
            hit = process.extractOne(key, self._matcher_keys, scorer=fuzz.ratio, score_cutoff=cutoff)
            if not hit:
                return None, 0
            cands = self.matchers[hit[0]]
            score = hit[1]
        entry, matcher = _resolve(cands, category)
        sm = StatMatch(
            ref=entry["ref"],
            trade_ids=entry.get("trade", {}).get("ids", {}),
            negate=bool(matcher.get("negate")),
            fixed_value=matcher.get("value"),
            n_values=matcher["string"].count("#"),
            better=entry.get("better", 1),
        )
        return sm, score


def _category_matches(category: str | None, expected: str) -> bool:
    if category is None:
        return False
    if expected == "WEAPON":
        return category in WEAPON
    if expected == "ARMOUR":
        return category in ARMOUR
    return category == expected


def _resolve(cands, category):
    """Chọn mod đúng khi cùng một câu chữ có nhiều mã (mod local/global), theo cách của APT."""
    groups = {}
    for entry, matcher, group in cands:
        groups.setdefault(id(group) if group else id(entry), (group, []))[1].append((entry, matcher))
    group, items = next(iter(groups.values()))
    if group and group["resolve"]["strat"] == "select":
        test = group["resolve"]["test"]
        idx = next((i for i, exp in enumerate(test) if exp is not None and _category_matches(category, exp)), -1)
        if idx == -1:
            idx = test.index(None) if None in test else 0
        chosen = group["stats"][idx]
        for entry, matcher in items:
            if entry is chosen:
                return entry, matcher
        m = next(m for m in chosen["matchers"] if template_key(m["string"]) == template_key(items[0][1]["string"]))
        return chosen, m
    return items[0]


_DATA: dict = {}


def load(game: str = "poe1") -> Data:
    if game not in GAMES:
        raise ValueError(f"game phải là một trong {GAMES}")
    if game in _DATA:
        return _DATA[game]
    folder = DATA_DIR if game == "poe1" else DATA_DIR / game
    d = Data(game=game)
    for line in open(folder / "items.ndjson", encoding="utf-8"):
        o = json.loads(line)
        ns, name = o.get("namespace"), o.get("name")
        if not name:            # dữ liệu PoE2 có vài dòng không tên
            continue
        if o.get("tradeTag") and name:
            d.trade_tags[letters(name)] = o["tradeTag"]
            icon = (o.get("icon") or "").rsplit("/", 1)[-1]
            if icon.endswith(".png"):
                d.icon_stems[letters(name)] = icon[:-4]
        if ns == "ITEM" and o.get("craftable") and not (game == "poe2" and o["craftable"]["category"] in POE2_NON_EQUIP):
            d.bases[letters(name)] = {"name": name, "category": o["craftable"]["category"]}
        elif ns == "UNIQUE" and o.get("unique"):
            d.uniques[letters(name)] = {"name": name, "base": o["unique"]["base"]}
        elif ns == "GEM":
            d.gems[letters(name)] = name
        elif ns in ("ITEM", "DIVINATION_CARD", "CAPTURED_BEAST"):
            d.others[letters(name)] = (name, ns)
    for line in open(folder / "stats.ndjson", encoding="utf-8"):
        o = json.loads(line)
        group = o if "resolve" in o else None
        for entry in (o["stats"] if group else [o]):
            for m in entry.get("matchers", []):
                if "string" not in m:
                    continue
                d.matchers.setdefault(template_key(m["string"]), []).append((entry, m, group))
    d._matcher_keys = list(d.matchers.keys())
    _DATA[game] = d
    return d
