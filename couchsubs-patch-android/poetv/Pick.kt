// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import org.json.JSONArray
import org.json.JSONObject

/** Một dòng trong bảng "Chọn dòng để tra" (như dict của pick._row bên Python). */
class PickRow(
    val key: String, val section: String, val text: String, val on: Boolean, val enabled: Boolean,
    val raw: Double?, val exact: Boolean, val groups: List<Pair<String, Double>>, val id: String?, val pct: Boolean,
    val tag: String?, val ref: String, val unsure: Boolean,
)

class PickPanel(
    val game: String, val title: String, val rarity: String, val base: String, val strict: Boolean,
    val allLines: Boolean, val rng: Double, val rows: List<PickRow>, val slots: Int, val nExplicit: Int,
    val corrupted: Boolean, val category: String?,
) {
    var m0 = 0
        internal set
    var need = 0
        internal set
}

/** Lựa chọn của người dùng: dòng chọn/bỏ, mức đã sửa, số dòng cần đủ đã chỉnh (null = theo mặc định). */
data class PickSel(val on: Map<String, Boolean> = emptyMap(), val edit: Map<String, Double> = emptyMap(), val need: Int? = null)

/** Khung "Khi tra": điều kiện bắt buộc, các dòng chỉ số đã gộp, cần đủ [need] trong [of] dòng. */
class PickSummary(val req: List<String>, val stats: List<String>, val need: Int, val of: Int)

/**
 * Bản Kotlin của poe_tv/pick.py: tự chọn dòng để tra (màn "Chọn dòng để tra"). Lựa chọn mặc định cho ra ĐÚNG bộ lọc tự
 * tra (Query.build) để bấm TRA LẠI mà không đổi gì thì ra cùng kết quả. Gộp như tự tra: máu (PoE1 cộng ½ Str), ES cộng
 * thẳng (trừ giáp), kháng, thuộc tính (chỉ lọc khi tổng ≥ 40). Mức tổng = floor(tổng dòng chưa sửa × 90%) + mức đã sửa.
 */
object Pick {
    val GROUP_ORDER = listOf("life", "es", "ele", "chaos", "str", "dex", "int")
    private val GROUP_ID = mapOf(
        "life" to Query.PSEUDO.getValue("life"), "es" to Query.PSEUDO.getValue("es"),
        "ele" to Query.PSEUDO.getValue("ele_res"), "chaos" to Query.PSEUDO.getValue("chaos_res"),
        "str" to Query.PSEUDO.getValue("str"), "dex" to Query.PSEUDO.getValue("dex"), "int" to Query.PSEUDO.getValue("int"),
    )
    val GROUP_LABEL: Map<String, String> = GROUP_ORDER.associateWith { Query.PSEUDO_LABEL.getValue(GROUP_ID.getValue(it)) + " tổng" }
    private val ATTRS = listOf("str", "dex", "int")
    private const val ATTR_MIN = 40.0
    private val SLOTS = mapOf("Rare" to 6, "Magic" to 2)

    /** Câu mẫu + giá trị: "+# to Strength", [49] → "+49 to Strength". */
    fun fill(ref: String, values: List<Double>): String {
        val parts = ref.split("#")
        val sb = StringBuilder(parts[0])
        for (j in 1 until parts.size) sb.append(if (j - 1 < values.size) PriceBox.g(values[j - 1]) else "#").append(parts[j])
        return sb.toString()
    }

    fun defaultNeed(m: Int, allLines: Boolean) = if (allLines || m < 3) m else m - 1

    private fun row(key: String, section: String, text: String, on: Boolean, enabled: Boolean = true, raw: Double? = null,
                    exact: Boolean = false, groups: List<Pair<String, Double>> = emptyList(), id: String? = null,
                    pct: Boolean = false, tag: String? = null, ref: String = "", unsure: Boolean = false) =
        PickRow(key, section, text, on, enabled, raw, exact, groups, id, pct, tag, ref, unsure)

    /** Bảng chọn dòng của món (null với unique, gem, đồ bán theo lô: những món này tra theo tên). */
    fun panel(it: Item, game: String = "poe1", strict: Boolean = false, byBase: Boolean = false, allLines: Boolean = false,
              autoQ: JSONObject? = null): PickPanel? {
        if (it.rarity in setOf("Unique", "Gem", "Other") || it.category == null) return null
        val poe2 = game == "poe2"
        val rng = if (strict) 0.0 else Query.RANGE
        val weapon = it.isWeapon
        val armour = it.isArmour
        val rows = ArrayList<PickRow>()
        val baseForced = PoeData.TRADE_CATEGORY[it.category] == null
        rows.add(row("base", "base", "Đúng base: ${it.base.ifEmpty { "?" }}",
            baseForced || ((it.rarity == "Normal" || byBase) && it.base.isNotEmpty()), enabled = !baseForced && it.base.isNotEmpty()))
        it.props["ilvl"]?.firstOrNull()?.let { v0 ->
            val v = v0.toLong().toDouble()
            rows.add(row("ilvl", "base", "Item level ${PriceBox.g(v)}", it.rarity == "Normal" || it.rarity == "Magic", raw = v, exact = true))
        }
        if (weapon) {
            for ((k, name, v) in listOf(Triple("g_pdps", "pDPS", it.pdps), Triple("g_edps", "eDPS", it.edps)))
                if (v > 0) rows.add(row(k, "gear", "$name ${Math.floor(v).toLong()}", v >= 30, raw = v))
        }
        if (armour) {
            for ((prop, k, name) in listOf(Triple("armour", "g_ar", "Giáp"), Triple("evasion", "g_ev", "Né"), Triple("es", "g_es", "ES"))) {
                val v = it.props[prop]?.firstOrNull() ?: continue
                rows.add(row(k, "gear", "$name ${PriceBox.g(v)}", true, raw = v))
            }
        }

        // tổng từng thuộc tính (dòng đọc chắc) để biết thuộc tính nào đủ 40 mà chọn sẵn
        val attrTotal = linkedMapOf("str" to 0.0, "dex" to 0.0, "int" to 0.0)
        val modGroups = ArrayList<List<Pair<String, Double>>>()
        for (m in it.mods) {
            val r = Query.k(m.ref)
            val g = ArrayList<Pair<String, Double>>()
            val res = Query.RES[r]
            val attr = Query.ATTR[r]
            if (res != null) {
                if (res.first != 0) g.add("ele" to res.first.toDouble())
                if (res.second) g.add("chaos" to 1.0)
            } else if (attr != null) {
                for (a in if (attr == "all") ATTRS else attr.split(" ")) g.add(a to 1.0)
            } else if (r == "# to maximum Life") g.add("life" to 1.0)
            else if (r == "# to maximum Energy Shield" && !armour) g.add("es" to 1.0)
            modGroups.add(g)
            if (m.score >= Query.MIN_SCORE) for ((a, w) in g) if (a in attrTotal) attrTotal[a] = attrTotal.getValue(a) + (m.value ?: 0.0) * w
        }
        val hasLife = modGroups.any { g -> g.any { it.first == "life" } }
        val used = HashSet<String>()
        var nExpl = 0
        for ((i, m) in it.mods.withIndex()) {
            val g = modGroups[i]
            val r = Query.k(m.ref)
            val unsure = m.score < Query.MIN_SCORE
            val sid = m.explicitId
            val local = (weapon && Query.LOCAL_WEAPON.matches(r)) || (armour && Query.LOCAL_ARMOUR.matches(r))
            val section: String
            if (m.kind == "implicit") {
                if (g.isEmpty()) continue                 // implicit không gộp vào tổng: chưa lọc riêng được
                section = "implicit"
            } else {
                section = "explicit"
                nExpl++
            }
            var text = fill(m.ref, m.values)
            if (m.text.trimStart().startsWith("+") && !text.startsWith("+")) text = "+$text"   // dữ liệu PoE2 không có "+"
            val pct = "#%" in m.ref
            if (g.isNotEmpty()) {
                var tag = "→ gộp vào " + g.joinToString(" + ") { GROUP_LABEL.getValue(it.first) }
                if (!poe2 && hasLife && g.any { it.first == "str" }) tag += " · ½ vào Máu tổng"
                val allAttr = g.all { it.first in ATTRS }
                val on = !unsure && (!allAttr || g.any { (attrTotal[it.first] ?: 0.0) >= ATTR_MIN })
                rows.add(row("m$i", section, text, on, raw = m.value, groups = g, pct = pct,
                    tag = tag + if (unsure) " · đọc chưa chắc" else "", ref = m.ref, unsure = unsure))
                continue
            }
            if (local) {
                rows.add(row("m$i", section, text, false, enabled = false, raw = m.value,
                    tag = if (weapon) "đã tính trong DPS" else "đã tính trong Giáp/Né/ES", ref = m.ref, unsure = unsure))
                continue
            }
            if (sid == null) {
                rows.add(row("m$i", section, text, false, enabled = false, raw = m.value, tag = "không lọc được", ref = m.ref, unsure = unsure))
                continue
            }
            val important = Query.IMPORTANT_LABEL.any { (k, _) -> k in m.ref }
            val on = !unsure && m.value != null && sid !in used && (important || allLines)
            if (on) used.add(sid)
            rows.add(row("m$i", section, text, on, raw = m.value, id = sid, pct = pct,
                tag = if (unsure) "đọc chưa chắc" else null, ref = m.ref, unsure = unsure))
        }
        val pn = PickPanel(game, it.title, it.rarity, it.base, strict, allLines, rng, rows,
            maxOf(SLOTS[it.rarity] ?: 0, nExpl), nExpl, it.corrupted, it.category)
        val m0 = pool(pn, selOn(pn, null), emptyMap()).first.size
        pn.m0 = m0
        var need = defaultNeed(m0, allLines)
        val stats = autoQ?.optJSONObject("query")?.optJSONArray("stats")
        if (stats != null) for (i in 0 until stats.length()) {
            val g = stats.getJSONObject(i)
            if (g.optString("type") == "count") need = g.getJSONObject("value").getInt("min")   // Dòng TẤT CẢ đã tự nới
        }
        pn.need = need
        return pn
    }

    /** Dòng nào đang chọn: dòng không chọn được thì giữ mặc định. */
    fun selOn(pn: PickPanel, sel: PickSel?): Map<String, Boolean> =
        pn.rows.associate { r -> r.key to (if (r.enabled) sel?.on?.get(r.key) ?: r.on else r.on) }

    private fun minOf(pn: PickPanel, r: PickRow, edit: Map<String, Double>): Number {
        edit[r.key]?.let { return whole(it) }
        if (r.exact) return r.raw!!.toLong()
        return Query.min(r.raw!!, pn.rng)
    }

    /** Số hiện sẵn trong ô "≥" của một dòng (chưa sửa). */
    fun showMin(pn: PickPanel, r: PickRow): Number? {
        val v = r.raw ?: return null
        return if (r.exact) v.toLong() else Query.min(v, pn.rng)
    }

    private fun whole(v: Double): Number = if (v == Math.floor(v) && Math.abs(v) < 1e15) v.toLong() else v
    private fun num(v: Number) = PriceBox.g(v.toDouble())

    /** Các bộ lọc chỉ số theo thứ tự tự tra: các tổng, rồi từng dòng. Trả (pool, nhãn). */
    private fun pool(pn: PickPanel, on: Map<String, Boolean>, edit: Map<String, Double>): Pair<List<JSONObject>, List<String>> {
        val poe2 = pn.game == "poe2"
        val pool = ArrayList<JSONObject>()
        val labs = ArrayList<String>()
        val rows = pn.rows.filter { it.section == "implicit" || it.section == "explicit" }
        for (grp in GROUP_ORDER) {
            val ticked = rows.filter { r -> on[r.key] == true && r.groups.any { it.first == grp } }
            if (ticked.isEmpty()) continue
            fun w(r: PickRow) = r.groups.first { it.first == grp }.second
            var raw = ticked.filter { it.key !in edit }.sumOf { r -> (r.raw ?: 0.0) * w(r) }
            var ed = ticked.filter { it.key in edit }.sumOf { r -> edit.getValue(r.key) * w(r) }
            if (grp in ATTRS && ticked.sumOf { r -> (r.raw ?: 0.0) * w(r) } < ATTR_MIN) continue
            if (grp == "life" && !poe2) {
                // 2 Str = 1 máu (như APT): Str của món tính vào máu dù có chọn dòng Str hay không (trade cũng tính vậy)
                for (r in rows) {
                    val sw = r.groups.firstOrNull { it.first == "str" }?.second ?: continue
                    if (on[r.key] != true && r.unsure) continue
                    if (r.key in edit) ed += edit.getValue(r.key) * sw / 2 else raw += (r.raw ?: 0.0) * sw / 2
                }
            }
            val rawPart = if (raw > 0) Math.floor(raw * (1 - pn.rng)) else raw
            val total = whole(rawPart + ed)
            pool.add(JSONObject().put("id", GROUP_ID.getValue(grp)).put("value", JSONObject().put("min", total)))
            labs.add("${GROUP_LABEL.getValue(grp)} ≥ ${num(total)}")
        }
        val used = HashSet<String>()
        for (r in rows) {
            val id = r.id
            if (r.groups.isNotEmpty() || on[r.key] != true || id == null || !used.add(id)) continue
            if (r.raw == null && r.key !in edit) {
                pool.add(JSONObject().put("id", id))
                labs.add(Query.modLabel(r.ref))
                continue
            }
            val v = minOf(pn, r, edit)
            pool.add(JSONObject().put("id", id).put("value", JSONObject().put("min", v)))
            labs.add("${Query.modLabel(r.ref)} ≥ ${num(v)}${if (r.pct) "%" else ""}")
        }
        return pool to labs
    }

    /** Số dòng cần đủ: đã chỉnh thì giữ (1..m); chưa chỉnh mà chọn như mặc định thì như tự tra; còn lại theo quy tắc. */
    fun needFor(pn: PickPanel, sel: PickSel?, m: Int): Int {
        sel?.need?.let { return if (m > 0) it.coerceIn(1, m) else 0 }
        if (selOn(pn, sel) == selOn(pn, null) && sel?.edit.isNullOrEmpty()) return pn.need
        return defaultNeed(m, pn.allLines)
    }

    /** Bộ lọc trade từ lựa chọn; kèm tóm tắt để hiện khung "Khi tra". */
    fun build(it: Item, pn: PickPanel, sel: PickSel? = null, status: String = "securable"): Pair<JSONObject, PickSummary> {
        val on = selOn(pn, sel)
        val edit = (sel?.edit ?: emptyMap()).filterKeys { it in on }
        val poe2 = pn.game == "poe2"
        val rows = pn.rows.associateBy { it.key }
        val stats = JSONArray()
        val flt = JSONObject()
        val groups = JSONArray().put(JSONObject().put("type", "and").put("filters", stats))
        val qq = JSONObject().put("status", JSONObject().put("option", status)).put("filters", flt).put("stats", groups)
        val q = JSONObject().put("query", qq).put("sort", JSONObject().put("price", "asc"))
        val req = ArrayList<String>()
        fun misc(): JSONObject {
            if (!flt.has("misc_filters")) flt.put("misc_filters", JSONObject().put("filters", JSONObject()))
            return flt.getJSONObject("misc_filters").getJSONObject("filters")
        }
        if (pn.corrupted) misc().put("corrupted", JSONObject().put("option", "true"))
        var ilvl: Number? = null
        rows["ilvl"]?.let { r -> if (on["ilvl"] == true) { ilvl = minOf(pn, r, edit); misc().put("ilvl", JSONObject().put("min", ilvl)) } }
        if (on["base"] == true && it.base.isNotEmpty()) qq.put("type", it.base)
        val cat = PoeData.TRADE_CATEGORY[it.category]
        val tf = JSONObject().put("rarity", JSONObject().put("option", "nonunique"))
        if (cat != null) tf.put("category", JSONObject().put("option", cat)) else qq.put("type", it.base)
        flt.put("type_filters", JSONObject().put("filters", tf))
        if (qq.optString("type").isNotEmpty()) req.add("Base: ${qq.getString("type")}")
        val wf = JSONObject()
        for ((k, tk, name) in listOf(Triple("g_pdps", "pdps", "pDPS"), Triple("g_edps", "edps", "eDPS"))) {
            val r = rows[k] ?: continue
            if (on[k] != true) continue
            val v = minOf(pn, r, edit)
            wf.put(tk, JSONObject().put("min", v))
            req.add("$name ≥ ${num(v)}")
        }
        if (wf.length() > 0) flt.put(if (poe2) "equipment_filters" else "weapon_filters", JSONObject().put("filters", wf))
        ilvl?.let { v -> req.add("iLvl ≥ ${num(v)}") }
        if (pn.corrupted) req.add("Corrupted")

        val (pool, labs) = pool(pn, on, edit)
        val m = pool.size
        val need = needFor(pn, sel, m)
        if (m > 0 && (m >= 3 || need < m)) {
            val f = JSONArray()
            for (p in pool) f.put(p)
            groups.put(JSONObject().put("type", "count").put("value", JSONObject().put("min", need)).put("filters", f))
        } else for (p in pool) stats.put(p)

        val af = JSONObject()
        for ((k, tk, name) in listOf(Triple("g_ar", "ar", "Giáp"), Triple("g_ev", "ev", "Né"), Triple("g_es", "es", "ES"))) {
            val r = rows[k] ?: continue
            if (on[k] != true) continue
            val v = minOf(pn, r, edit)
            af.put(tk, JSONObject().put("min", v))
            req.add("$name ≥ ${num(v)}")
        }
        if (af.length() > 0) {
            if (poe2) {
                if (!flt.has("equipment_filters")) flt.put("equipment_filters", JSONObject().put("filters", JSONObject()))
                val ef = flt.getJSONObject("equipment_filters").getJSONObject("filters")
                for (key in listOf("ar", "ev", "es")) if (af.has(key)) ef.put(key, af.get(key))
            } else flt.put("armour_filters", JSONObject().put("filters", af))
        }
        return q to PickSummary(req, labs, if (m > 0) need else 0, m)
    }

    /**
     * Chọn nhanh các dòng chỉ số (implicit + dòng của món): "main" = như tự tra, "all" = mọi dòng chọn được, "none" = bỏ
     * hết. Giữ base, chỉ số món, các mức đã sửa; số dòng cần đủ về mặc định.
     */
    fun preset(pn: PickPanel, which: String, sel: PickSel? = null): PickSel {
        val on = HashMap(selOn(pn, sel))
        for (r in pn.rows) if ((r.section == "implicit" || r.section == "explicit") && r.enabled)
            on[r.key] = if (which == "main") r.on else which == "all"
        return PickSel(on, sel?.edit ?: emptyMap(), null)
    }
}
