// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

/** Mod đã đọc: [ref] câu mẫu, [kind] implicit/explicit, [values] các số trên dòng. */
class Mod(
    val text: String, val ref: String, val kind: String, val values: List<Double>, val explicitId: String?,
    val score: Double, val better: Int = 1,
) {
    /** Giá trị đại diện: trung bình nếu là khoảng "Adds # to #". */
    val value: Double? get() = if (values.isEmpty()) null else values.sum() / values.size
}

class Mutated(val match: PoeData.StatMatch, val values: List<Double>, val text: String)

class Item {
    var rarity = "?"             // Unique / Rare / Magic / Normal / Gem / Other
    var name = ""
    var base = ""
    var category: String? = null
    val props = LinkedHashMap<String, MutableList<Double>>()
    val mods = ArrayList<Mod>()
    val unmatched = ArrayList<String>()
    var corrupted = false
    var foulborn = false
    val mutated = ArrayList<Mutated>()

    /** Tên ngắn để hiện: unique thì tên riêng, đồ khác thì loại đồ. */
    val title: String
        get() = if (rarity == "Unique" && name.isNotEmpty()) (if (foulborn) "Foulborn " else "") + name
        else base.ifEmpty { name.ifEmpty { "?" } }

    private fun avg(key: String): Double {
        val v = props[key] ?: return 0.0
        if (v.isEmpty()) return 0.0
        var s = 0.0
        var i = 0
        while (i + 1 < v.size) {
            s += (v[i] + v[i + 1]) / 2; i += 2
        }
        return s
    }

    private val aps get() = props["aps"]?.firstOrNull() ?: 0.0
    val pdps get() = avg("phys") * aps
    val edps get() = avg("ele") * aps

    val isWeapon get() = category in PoeData.WEAPON
    val isArmour get() = category in PoeData.ARMOUR
    val isJewellery get() = category in PoeData.JEWELLERY
}

/** Bản Kotlin của poe_tv/parse.py: biến các dòng OCR của tooltip thành thông tin món đồ. */
object Parse {
    private val PROP_POE1 = linkedMapOf(
        "quality" to "^QUALITY", "phys" to "^PHYSICALDAMAGE", "ele" to "^ELEMENTALDAMAGE", "chaos" to "^CHAOSDAMAGE",
        "crit" to "^CRITICALSTRIKECHANCE", "aps" to "^ATTACKSPERSECOND", "range" to "^WEAPONRANGE",
        "armour" to "^ARMOUR", "evasion" to "^EVASIONRATING", "es" to "^ENERGYSHIELD", "ward" to "^WARD",
        "block" to "^CHANCETOBLOCK", "level" to "^LEVEL", "stack" to "^STACKSIZE", "requires" to "^REQUIRES",
        "ilvl" to "^ITEMLEVEL",
    ).mapValues { Regex(it.value) }

    // PoE2: tên thuộc tính khác PoE1, sát thương nguyên tố ghi riêng từng hệ (gộp vào "ele")
    private val PROP_POE2 = LinkedHashMap(PROP_POE1.filterKeys { it != "crit" && it != "block" }).apply {
        put("crit", Regex("^CRITICAL(STRIKE|HIT)CHANCE"))
        put("block", Regex("^(CHANCETOBLOCK|BLOCKCHANCE)"))
        put("fire", Regex("^FIREDAMAGE\\W*\\d+-\\d+"))
        put("cold", Regex("^COLDDAMAGE\\W*\\d+-\\d+"))
        put("lightning", Regex("^LIGHTNINGDAMAGE\\W*\\d+-\\d+"))
        put("reload", Regex("^RELOADTIME"))
        put("spirit", Regex("^SPIRIT\\W*\\d"))
    }

    /** Dòng mô tả mod của chế độ "Advanced Descriptions" (R3 trong game), không phải mod. */
    private val META_LINE = Regex("^\\W*(PREFIX|SUFFIX|IMPLICIT|UNIQUE|MASTER\\s*CRAFTED|CRAFTED|MONSTER|ENCHANT\\w*|FRACTURED|" +
        "CORRUPTED|FOULBORN|EATER|SEARING|SYNTHESI[SZ]ED|VEILED)\\b.*\\bMODIFIER\\b")
    private val RANGE = Regex("\\s*\\(\\s*[+-]?\\d+(?:\\.\\d+)?\\s*[-–~]\\s*[+-]?\\d+(?:\\.\\d+)?\\s*\\)")
    private val NUM = Regex("\\d+(?:\\.\\d+)?")
    private val PAIR = Regex("(\\d+)\\s*-\\s*(\\d+)")
    private val FOULBORN_NAME = Regex("^\\s*foulborn\\s+(.+)$", RegexOption.IGNORE_CASE)

    private val CLEAN = listOf(
        Regex("\\s*[—–-]+\\s*UNSCALABLE\\s*VALUE") to "",
        Regex("(?<=[\\d.])O") to "0",
        Regex("(?<![A-Z])O(?=\\d)") to "0",
        Regex("(?<![A-Z])[IL|](?=\\d)") to "1",
        Regex("(?<=\\d)[IL|](?=[\\d%])") to "1",
        Regex("(?<=[A-Z])1(?=[A-Z])") to "I",
        Regex("(?<=[A-Z])0(?=[A-Z])") to "O",
    )

    fun isMeta(s: String) = META_LINE.containsMatchIn(s.uppercase())

    /** Sửa lỗi OCR hay gặp (O/I lẫn vào số, 1/0 lẫn vào chữ), bỏ khoảng giá trị "(6-9)" và đuôi "Unscalable Value". */
    fun cleanNumbers(s: String): String {
        var t = s.uppercase()
        for ((re, rep) in CLEAN) t = re.replace(t, rep)
        return RANGE.replace(t, "")
    }

    fun numbers(s: String): List<Double> = NUM.findAll(s).map { it.value.toDouble() }.toList()

    /** Như str.title() của Python: viết hoa chữ đầu mỗi cụm chữ cái. */
    fun title(s: String): String {
        val sb = StringBuilder(s.length)
        var prev = false
        for (c in s) {
            if (c.isLetter()) {
                sb.append(if (prev) c.lowercaseChar() else c.titlecaseChar()); prev = true
            } else {
                sb.append(c); prev = false
            }
        }
        return sb.toString()
    }

    /** Gộp các ô chữ cùng hàng (OCR có thể cắt một dòng thành nhiều ô). */
    fun rows(lines: List<Line>): List<MutableList<Line>> {
        val rows = ArrayList<MutableList<Line>>()
        for (l in lines.sortedBy { it.cy }) {
            val last = rows.lastOrNull()
            if (last != null && Math.abs(last[0].cy - l.cy) < 0.45 * maxOf(l.h, last[0].h)) last.add(l)
            else rows.add(mutableListOf(l))
        }
        return rows
    }

    /** Chia tooltip thành các khối theo khoảng trống giữa các hàng (vạch ngăn cách). */
    fun sections(rows: List<List<Line>>): List<List<List<Line>>> {
        if (rows.size < 2) return listOf(rows)
        val tops = rows.map { r -> r.minOf { it.y0 } }
        val gaps = tops.zipWithNext { a, b -> b - a }
        val baseGap = TooltipFinder.median(gaps.sorted().take(maxOf(1, gaps.size / 2 + 1)))
        val secs = ArrayList<List<List<Line>>>()
        var cur = ArrayList<List<Line>>().apply { add(rows[0]) }
        for ((r, g) in rows.drop(1).zip(gaps)) {
            if (g > 1.2 * baseGap) {
                secs.add(cur); cur = ArrayList()
            }
            cur.add(r)
        }
        secs.add(cur)
        return secs
    }

    private fun joined(r: List<Line>) = r.sortedBy { it.x0 }.joinToString(" ") { it.text }

    private fun values(sm: PoeData.StatMatch, txt: String): List<Double> {
        var vals = numbers(txt)
        if (sm.nValues > 0 && vals.size > sm.nValues) vals = vals.takeLast(sm.nValues)     // bỏ số rác phía trước
        if (sm.fixedValue != null && vals.isEmpty()) vals = listOf(sm.fixedValue)
        if (sm.negate) vals = vals.map { -it }
        return vals
    }

    fun parse(header: List<String>, lines: List<Line>, data: PoeData): Item {
        val it = Item()
        val rows = rows(lines).toMutableList()
        // bỏ các hàng tiêu đề (tên, base) ở đầu
        val hdrKeys = header.map { PoeData.letters(it) }
        var dropped = 0
        while (rows.isNotEmpty() && dropped < header.size &&
            rows[0].any { l -> hdrKeys.any { hk -> Fuzz.ratio(PoeData.letters(l.text), hk) >= 80 } }) {
            rows.removeAt(0); dropped++
        }

        // ---- loại món ----
        if (header.size == 2) {
            it.name = title(header[0])
            val b = data.findBase(header[1])
            it.base = b?.name ?: title(header[1])
            it.category = b?.category
            val m = FOULBORN_NAME.find(header[0])
            val uname = m?.groupValues?.get(1) ?: header[0]
            val u = data.findUnique(uname)
            it.foulborn = m != null && u != null
            it.rarity = if (u != null && u.hasBase(it.base)) "Unique" else "Rare"
            if (it.rarity == "Unique") it.name = u!!.name
        } else {
            val h = header[0]
            val g = data.findGem(h)
            val b = data.findBase(h)
            val o = data.findOther(h)
            when {
                g != null -> { it.rarity = "Gem"; it.name = g; it.base = g }
                b != null -> { it.rarity = "Normal"; it.base = b.name; it.category = b.category }
                o != null -> { it.rarity = "Other"; it.name = o.name; it.base = o.name }
                else -> {
                    val bi = data.findBaseIn(h)
                    it.rarity = "Magic"; it.name = title(h)
                    if (bi != null) { it.base = bi.name; it.category = bi.category }
                }
            }
        }

        // ---- thuộc tính và mod theo từng khối ----
        val patterns = if (data.game == "poe2") PROP_POE2 else PROP_POE1
        val modSecs = ArrayList<List<Triple<PoeData.StatMatch, Double, String>>>()
        for (sec in sections(rows)) {
            val texts = sec.map { cleanNumbers(joined(it)) }
            var isProp = false
            for (t in texts) {
                val tt = t.replace(" ", "")
                for ((key, pat) in patterns) {
                    if (pat.containsMatchIn(tt)) {
                        if (key in setOf("phys", "ele", "chaos", "fire", "cold", "lightning")) {
                            // chỉ lấy các cặp "a-b", bỏ số rác dính vào hàng
                            val pairs = PAIR.findAll(t).flatMap { m -> m.groupValues.drop(1).map { v -> v.toDouble() } }.toMutableList()
                            if (key == "fire" || key == "cold" || key == "lightning") it.props.getOrPut("ele") { ArrayList() }.addAll(pairs)
                            else it.props[key] = pairs
                        } else {
                            it.props[key] = numbers(t).toMutableList()
                        }
                        isProp = true
                        break
                    }
                }
                if ("CORRUPTED" in tt) it.corrupted = true
            }
            if (isProp) continue
            val matched = ArrayList<Triple<PoeData.StatMatch, Double, String>>()
            for (r in sec) {
                // mỗi hàng có thể gồm nhiều ô: thử cả hàng trước, rồi từng ô
                val cands = listOf(joined(r)) + (if (r.size > 1) r.map { l -> l.text } else emptyList())
                if (isMeta(cands[0])) continue
                var best: Triple<PoeData.StatMatch, Double, String>? = null
                for (c0 in cands) {
                    val c = cleanNumbers(c0)
                    val (sm, score) = data.matchMod(c, it.category)
                    if (sm != null && (best == null || score > best.second)) best = Triple(sm, score, c)
                }
                if (best != null) matched.add(best) else it.unmatched.add(cands[0])
            }
            if (matched.isNotEmpty()) modSecs.add(matched)
        }

        // khối mod đầu tiên là implicit nếu còn khối mod khác phía sau
        for ((i, sec) in modSecs.withIndex()) {
            val kind = if (modSecs.size > 1 && i == 0 && it.rarity != "Gem") "implicit" else "explicit"
            for ((sm, score, txt) in sec)
                it.mods.add(Mod(txt, sm.ref, kind, values(sm, txt), sm.explicitId, score, sm.better))
        }
        return it
    }

    /** Gắn các dòng đột biến (chữ hồng của đồ Foulborn) để lọc trade đúng dòng đó. */
    fun addMutated(it: Item, rowsText: List<String>, data: PoeData, minScore: Double = 90.0) {
        for (raw in rowsText) {
            val txt = cleanNumbers(raw)
            val (sm, score) = data.matchMod(txt, it.category)
            if (sm == null || score < minScore) continue
            it.mutated.add(Mutated(sm, values(sm, txt), txt))
        }
    }
}
