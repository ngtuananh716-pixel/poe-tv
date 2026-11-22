// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import org.json.JSONArray
import org.json.JSONObject

/**
 * Dữ liệu món đồ và mod của một game (bản Kotlin của poe_tv/data.py). Nạp từ file JSON gọn do tools/android_data.py tạo
 * (PoE1 từ Awakened PoE Trade, PoE2 từ Exiled Exchange 2, cả hai giấy phép MIT); bước chọn mod local/global đã giải sẵn.
 */
class PoeData(json: String) {
    class Base(val name: String, val category: String)
    class Unique(val name: String, val base: String)
    class Other(val name: String, val namespace: String)

    /** Mod đã khớp: [ref] là câu mẫu ("+# to maximum Life"), [explicitId] là mã explicit trên trade (nếu có). */
    class StatMatch(
        val ref: String, val explicitId: String?, val negate: Boolean, val fixedValue: Double?, val nValues: Int,
        val better: Int,
    )

    private class Option(val test: String?, val match: StatMatch)

    val game: String
    private val bases = LinkedHashMap<String, Base>()
    private val uniques = LinkedHashMap<String, Unique>()
    private val gems = LinkedHashMap<String, String>()
    private val others = LinkedHashMap<String, Other>()
    private val matchers = HashMap<String, List<Option>>()
    private val baseKeys: Array<String>
    private val uniqueKeys: Array<String>
    private val gemKeys: Array<String>
    private val otherKeys: Array<String>
    private val matcherKeys: Array<String>

    init {
        val o = JSONObject(json)
        game = o.getString("game")
        rows(o, "bases") { bases[it.getString(0)] = Base(it.getString(1), it.getString(2)) }
        rows(o, "uniques") { uniques[it.getString(0)] = Unique(it.getString(1), it.getString(2)) }
        rows(o, "gems") { gems[it.getString(0)] = it.getString(1) }
        rows(o, "others") { others[it.getString(0)] = Other(it.getString(1), it.getString(2)) }
        val keys = ArrayList<String>()
        rows(o, "mods") { row ->
            val opts = row.getJSONArray(1)
            matchers[row.getString(0)] = (0 until opts.length()).map { i ->
                val a = opts.getJSONArray(i)
                Option(
                    a.optStringOrNull(0),
                    StatMatch(a.getString(1), a.optStringOrNull(2), a.getBoolean(3),
                        if (a.isNull(4)) null else a.getDouble(4), a.getInt(5), a.optInt(6, 1)),
                )
            }
            keys.add(row.getString(0))
        }
        baseKeys = bases.keys.toTypedArray()
        uniqueKeys = uniques.keys.toTypedArray()
        gemKeys = gems.keys.toTypedArray()
        otherKeys = others.keys.toTypedArray()
        matcherKeys = keys.toTypedArray()
    }

    // ---------- món đồ ----------
    fun findBase(text: String, cutoff: Double = 90.0): Base? = lookup(bases, baseKeys, letters(text), cutoff)

    /** Base nằm bên trong tên đồ magic (vd. "Heavy Belt of the Whale"). */
    fun findBaseIn(text: String): Base? {
        val k = letters(text)
        var best: Base? = null
        var bestLen = -1
        for ((bk, b) in bases) {
            if (bk.length >= 5 && k.contains(bk) && (best == null || bk.length > bestLen)) {
                best = b
                bestLen = letters(b.name).length
            }
        }
        return best
    }

    fun findUnique(name: String, cutoff: Double = 88.0): Unique? = lookup(uniques, uniqueKeys, letters(name), cutoff)
    fun findGem(text: String, cutoff: Double = 90.0): String? = lookup(gems, gemKeys, letters(text), cutoff)
    fun findOther(text: String, cutoff: Double = 92.0): Other? = lookup(others, otherKeys, letters(text), cutoff)

    private fun <T> lookup(map: Map<String, T>, keys: Array<String>, k: String, cutoff: Double): T? {
        map[k]?.let { return it }
        val hit = Fuzz.extractOne(k, keys, cutoff) ?: return null
        return map[hit.first]
    }

    // ---------- mod ----------
    /** Trả (mod khớp, điểm khớp) hoặc (null, 0). */
    fun matchMod(line: String, category: String?, cutoff: Double = 86.0): Pair<StatMatch?, Double> {
        val key = templateKey(line)
        if (key.replace("#", "").length < 6) return null to 0.0
        var cands = matchers[key]
        var score = 100.0
        if (cands == null) {
            val hit = Fuzz.extractOne(key, matcherKeys, cutoff) ?: return null to 0.0
            cands = matchers.getValue(hit.first)
            score = hit.second
        }
        // giống _resolve của APT: lựa chọn đầu tiên khớp loại món, không có thì lựa chọn không điều kiện, rồi lựa chọn đầu
        var idx = cands.indexOfFirst { it.test != null && categoryMatches(category, it.test) }
        if (idx == -1) idx = cands.indexOfFirst { it.test == null }.let { if (it == -1) 0 else it }
        return cands[idx].match to score
    }

    companion object {
        val WEAPON = setOf(
            "Fishing Rod", "Bow", "Wand", "One-Handed Axe", "One-Handed Mace", "One-Handed Sword",
            "Claw", "Dagger", "Rune Dagger", "Sceptre", "Two-Handed Axe", "Two-Handed Mace",
            "Two-Handed Sword", "Staff", "Warstaff",
            // PoE2
            "One Hand Axe", "Two Hand Axe", "One Hand Mace", "Two Hand Mace", "One Hand Sword", "Two Hand Sword",
            "Crossbow", "Spear", "Flail", "Talisman",
        )
        val ARMOUR = setOf("Body Armour", "Boots", "Gloves", "Helmet", "Shield", "Buckler", "Focus")
        val JEWELLERY = setOf("Amulet", "Ring", "Belt")

        /** Loại món → mã danh mục trên trade (theo pathofexile-trade.ts của APT và Exiled Exchange 2). */
        val TRADE_CATEGORY = mapOf(
            "Abyss Jewel" to "jewel.abyss", "Amulet" to "accessory.amulet", "Belt" to "accessory.belt",
            "Body Armour" to "armour.chest", "Boots" to "armour.boots", "Bow" to "weapon.bow", "Claw" to "weapon.claw",
            "Dagger" to "weapon.dagger", "Fishing Rod" to "weapon.rod", "Flask" to "flask", "Gloves" to "armour.gloves",
            "Helmet" to "armour.helmet", "Jewel" to "jewel", "One-Handed Axe" to "weapon.oneaxe",
            "One-Handed Mace" to "weapon.onemace", "One-Handed Sword" to "weapon.onesword", "Quiver" to "armour.quiver",
            "Ring" to "accessory.ring", "Rune Dagger" to "weapon.runedagger", "Sceptre" to "weapon.sceptre",
            "Shield" to "armour.shield", "Staff" to "weapon.staff", "Two-Handed Axe" to "weapon.twoaxe",
            "Two-Handed Mace" to "weapon.twomace", "Two-Handed Sword" to "weapon.twosword", "Wand" to "weapon.wand",
            "Warstaff" to "weapon.warstaff", "Cluster Jewel" to "jewel.cluster", "Trinket" to "accessory.trinket",
            "Tincture" to "tincture",
            "One Hand Axe" to "weapon.oneaxe", "Two Hand Axe" to "weapon.twoaxe", "One Hand Mace" to "weapon.onemace",
            "Two Hand Mace" to "weapon.twomace", "One Hand Sword" to "weapon.onesword", "Two Hand Sword" to "weapon.twosword",
            "Crossbow" to "weapon.crossbow", "Spear" to "weapon.spear", "Flail" to "weapon.flail", "Talisman" to "weapon.talisman",
            "Buckler" to "armour.buckler", "Focus" to "armour.focus", "Charm" to "flask.charm",
        )

        /** Chỉ giữ chữ cái thường a–z (OCR hay làm mất dấu cách). */
        fun letters(s: String): String {
            val sb = StringBuilder(s.length)
            for (c in s.lowercase()) if (c in 'a'..'z') sb.append(c)
            return sb.toString()
        }

        private val NUMBER = Regex("\\d+(?:\\.\\d+)?")

        /** Khóa so khớp mod: chữ cái thường + dấu # thay cho số. */
        fun templateKey(s: String): String {
            val t = NUMBER.replace(s.lowercase().replace("+#", "#"), "#")
            val sb = StringBuilder(t.length)
            for (c in t) if (c in 'a'..'z' || c == '#') sb.append(c)
            return sb.toString()
        }

        fun categoryMatches(category: String?, expected: String): Boolean = when {
            category == null -> false
            expected == "WEAPON" -> category in WEAPON
            expected == "ARMOUR" -> category in ARMOUR
            else -> category == expected
        }

        private inline fun rows(o: JSONObject, name: String, each: (JSONArray) -> Unit) {
            val a = o.getJSONArray(name)
            for (i in 0 until a.length()) each(a.getJSONArray(i))
        }

        private fun JSONArray.optStringOrNull(i: Int): String? = if (isNull(i)) null else getString(i)
    }
}
