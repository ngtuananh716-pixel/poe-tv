// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import org.json.JSONArray
import org.json.JSONObject

/** Bản Kotlin của poe_tv/query.py: dựng bộ lọc trade từ món đã phân tích (mặc định như APT: dải giá trị −10%). */
object Query {
    const val RANGE = 0.10        // lấy món có chỉ số ≥ 90% món đang tra
    const val MIN_SCORE = 95.0    // dòng OCR khớp dưới mức này coi là chưa chắc, không đưa vào bộ lọc

    // Dữ liệu PoE2 ghi mod không có dấu "+" ở đầu: so khớp bỏ dấu "+".
    private fun k(ref: String) = ref.replace("+", "")

    private val RES: Map<String, Pair<Int, Boolean>> = mapOf(   // mod kháng → (số loại kháng nguyên tố, có kháng chaos)
        "+#% to all Elemental Resistances" to (3 to false),
        "+#% to All Resistances" to (3 to true),
        "+#% to Fire Resistance" to (1 to false), "+#% to Cold Resistance" to (1 to false),
        "+#% to Lightning Resistance" to (1 to false),
        "+#% to Fire and Lightning Resistances" to (2 to false), "+#% to Fire and Cold Resistances" to (2 to false),
        "+#% to Cold and Lightning Resistances" to (2 to false),
        "+#% to Chaos Resistance" to (0 to true),
        "+#% to Fire and Chaos Resistances" to (1 to true), "+#% to Cold and Chaos Resistances" to (1 to true),
        "+#% to Lightning and Chaos Resistances" to (1 to true),
    ).mapKeys { k(it.key) }
    private val ATTR: Map<String, String> = mapOf(
        "+# to Strength" to "str", "+# to Dexterity" to "dex", "+# to Intelligence" to "int",
        "+# to all Attributes" to "all", "+# to Strength and Dexterity" to "str dex",
        "+# to Strength and Intelligence" to "str int", "+# to Dexterity and Intelligence" to "dex int",
    ).mapKeys { k(it.key) }
    private val PSEUDO = mapOf(
        "life" to "pseudo.pseudo_total_life", "es" to "pseudo.pseudo_total_energy_shield",
        "mana" to "pseudo.pseudo_total_mana", "ele_res" to "pseudo.pseudo_total_elemental_resistance",
        "chaos_res" to "pseudo.pseudo_total_chaos_resistance", "str" to "pseudo.pseudo_total_strength",
        "dex" to "pseudo.pseudo_total_dexterity", "int" to "pseudo.pseudo_total_intelligence",
    )
    /** Mod quan trọng: luôn đưa vào bộ lọc nếu món có (2 mục cuối là mod PoE2). */
    private val IMPORTANT = listOf(
        "Movement Speed", "to Level of", "Critical Strike Multiplier", "increased maximum Life",
        "increased maximum Energy Shield", "Spell Damage", "Damage over Time Multiplier",
        "Physical Damage to Attacks", "increased Cast Speed", "Suppress Spell Damage",
        "Critical Damage Bonus", "to Spirit",
    )

    // Nhãn ngắn của các bộ lọc, hiện dưới giá trên TV ("Lọc: Máu≥94 · K.NT≥95"); như query.labels của bản Python.
    private val PSEUDO_LABEL = mapOf(
        "pseudo.pseudo_total_life" to "Máu", "pseudo.pseudo_total_energy_shield" to "ES", "pseudo.pseudo_total_mana" to "Mana",
        "pseudo.pseudo_total_elemental_resistance" to "K.NT", "pseudo.pseudo_total_chaos_resistance" to "K.Chaos",
        "pseudo.pseudo_total_strength" to "Str", "pseudo.pseudo_total_dexterity" to "Dex", "pseudo.pseudo_total_intelligence" to "Int",
    )
    private val IMPORTANT_LABEL = listOf(
        "Movement Speed" to "Tốc chạy", "to Level of" to "+Cấp skill", "Critical Strike Multiplier" to "Crit multi",
        "Critical Damage Bonus" to "Crit dmg", "increased maximum Life" to "%Máu", "increased maximum Energy Shield" to "%ES",
        "Spell Damage" to "Spell dmg", "Damage over Time Multiplier" to "DoT multi", "Physical Damage to Attacks" to "Phys atk",
        "increased Cast Speed" to "Cast speed", "Suppress Spell Damage" to "Suppress", "to Spirit" to "Spirit",
    )
    private val EQUIP_LABEL = mapOf("pdps" to "pDPS", "edps" to "eDPS", "ar" to "Giáp", "ev" to "Né", "es" to "ES")

    private fun num(v: Any?): String = PriceBox.g((v as Number).toDouble())

    /** Các dòng đã dùng để lọc (đồ không phải unique), viết ngắn để hiện trên TV. */
    fun labels(q: JSONObject, it: Item): List<String> {
        if (it.rarity == "Unique") return emptyList()
        val out = ArrayList<String>()
        val qq = q.getJSONObject("query")
        val flt = qq.optJSONObject("filters") ?: JSONObject()
        for (key in listOf("weapon_filters", "armour_filters", "equipment_filters")) {
            val f = flt.optJSONObject(key)?.optJSONObject("filters") ?: continue
            for (k in f.keys()) {
                val o = f.optJSONObject(k) ?: continue
                if (o.has("min")) out.add("${EQUIP_LABEL[k] ?: k}≥${num(o.get("min"))}")
            }
        }
        val mf = flt.optJSONObject("misc_filters")?.optJSONObject("filters") ?: JSONObject()
        mf.optJSONObject("gem_level")?.let { o -> out.add("Cấp≥${num(o.get("min"))}") }
        mf.optJSONObject("quality")?.let { o -> out.add("CL≥${num(o.get("min"))}") }
        if (mf.optJSONObject("corrupted")?.optString("option") == "true") out.add("Corrupted")
        val refs = LinkedHashMap<String, String>()
        for (m in it.mods) { val sid = m.explicitId; if (sid != null && sid !in refs) refs[sid] = m.ref }
        val stats = qq.getJSONArray("stats").getJSONObject(0).getJSONArray("filters")
        for (i in 0 until stats.length()) {
            val f = stats.getJSONObject(i)
            val id = f.getString("id")
            val name = PSEUDO_LABEL[id] ?: run {
                val ref = refs[id] ?: ""
                IMPORTANT_LABEL.firstOrNull { (k, _) -> k in ref }?.second ?: ref.replace("#", "").trim().take(14)
            }
            val v = f.optJSONObject("value")?.opt("min")
            out.add(if (v != null && v != JSONObject.NULL) "$name≥${num(v)}" else name)
        }
        return out
    }

    /** math.floor(v × 0,9) với v > 0 (số nguyên như bản Python), còn lại giữ nguyên v. */
    fun min(v: Double): Number = if (v > 0) Math.floor(v * (1 - RANGE)).toLong() else v

    private fun obj(vararg kv: Pair<String, Any>) = JSONObject().apply { for ((a, b) in kv) put(a, b) }
    private fun minOf(v: Number) = obj("min" to v)
    private fun miscFilters(flt: JSONObject): JSONObject {
        if (!flt.has("misc_filters")) flt.put("misc_filters", obj("filters" to JSONObject()))
        return flt.getJSONObject("misc_filters").getJSONObject("filters")
    }

    /** Trả (bộ lọc gửi /api/trade/search hoặc /api/trade2/search, các ghi chú). */
    fun build(it: Item, status: String = "securable", game: String = "poe1"): Pair<JSONObject, MutableList<String>> {
        val poe2 = game == "poe2"
        val notes = ArrayList<String>()
        val stats = JSONArray()
        val flt = JSONObject()
        val qq = obj("status" to obj("option" to status), "filters" to flt,
            "stats" to JSONArray().put(obj("type" to "and", "filters" to stats)))
        val q = obj("query" to qq, "sort" to obj("price" to "asc"))
        if (it.corrupted) flt.put("misc_filters", obj("filters" to obj("corrupted" to obj("option" to "true"))))

        for (m in it.mods) if (m.score < MIN_SCORE) notes.add("Dòng đọc chưa chắc, không dùng để lọc: ${m.text}")
        val mods = it.mods.filter { m -> m.score >= MIN_SCORE }

        if (it.rarity == "Unique") {
            qq.put("name", it.name); qq.put("type", it.base)
            if (poe2) return q to notes
            // đồ Foulborn và đồ thường cùng tên giá khác hẳn nhau: lọc đúng loại (như Awakened PoE Trade)
            miscFilters(flt).put("foulborn_item", obj("option" to if (it.foulborn) "true" else "false"))
            if (it.foulborn) {
                val used = ArrayList<String>()
                for (mu in it.mutated) {
                    val id = mu.match.explicitId ?: continue     // trade tìm dòng đột biến bằng mã explicit (như APT)
                    val f = obj("id" to id)
                    if (mu.values.isNotEmpty()) {
                        val v = mu.values.sum() / mu.values.size
                        f.put("value", minOf(if (Math.abs(v) <= 5) v else min(v)))
                    }
                    stats.put(f)
                    used.add(mu.text)
                }
                if (used.isNotEmpty()) notes.add("Foulborn: có lọc theo dòng đột biến: " + used.joinToString("; "))
                else notes.add("Foulborn: chưa lọc theo dòng mod đột biến, giá là món Foulborn rẻ nhất cùng tên")
            }
            return q to notes
        }
        if (it.rarity == "Gem") {
            qq.put("type", it.name)
            val mf = miscFilters(flt)
            it.props["level"]?.firstOrNull()?.let { v -> mf.put("gem_level", minOf(v.toLong())) }
            it.props["quality"]?.firstOrNull()?.let { v -> mf.put("quality", minOf(v.toLong())) }
            return q to notes
        }
        if (it.rarity == "Other" || it.category == null) {
            qq.put("type", it.base.ifEmpty { it.name })
            return q to notes
        }

        val cat = PoeData.TRADE_CATEGORY[it.category]
        val tf = obj("rarity" to obj("option" to "nonunique"))
        if (cat != null) tf.put("category", obj("option" to cat)) else qq.put("type", it.base)
        flt.put("type_filters", obj("filters" to tf))

        if (it.isWeapon) {
            val wf = JSONObject()
            val pdps = it.pdps
            val edps = it.edps
            if (pdps >= 30) wf.put("pdps", minOf(min(pdps)))
            if (edps >= 30) wf.put("edps", minOf(min(edps)))
            if (wf.length() > 0) flt.put(if (poe2) "equipment_filters" else "weapon_filters", obj("filters" to wf))
            else notes.add("Vũ khí sát thương thấp: lọc theo mod quan trọng thay vì DPS")
        }

        // gộp chỉ số tổng như APT
        val life = mods.filter { m -> k(m.ref) == "# to maximum Life" }.sumOf { m -> m.value ?: 0.0 }
        val esFlat = mods.filter { m -> k(m.ref) == "# to maximum Energy Shield" }.sumOf { m -> m.value ?: 0.0 }
        val attrs = linkedMapOf("str" to 0.0, "dex" to 0.0, "int" to 0.0)
        for (m in mods) {
            val kind = ATTR[k(m.ref)] ?: continue
            for (a in if (kind == "all") listOf("str", "dex", "int") else kind.split(" ")) attrs[a] = attrs.getValue(a) + (m.value ?: 0.0)
        }
        val ele = mods.filter { m -> k(m.ref) in RES }.sumOf { m -> (m.value ?: 0.0) * RES.getValue(k(m.ref)).first }
        val chaos = mods.filter { m -> RES[k(m.ref)]?.second == true }.sumOf { m -> m.value ?: 0.0 }

        val defensive = it.isArmour || it.isJewellery || it.category in setOf("Quiver", "Jewel", "Abyss Jewel")
        // PoE1: 2 Str = 1 máu (như APT). PoE2 chưa kiểm chứng nên chỉ tính máu cộng thẳng
        if (life > 0) stats.put(obj("id" to PSEUDO.getValue("life"), "value" to minOf(min(life + if (poe2) 0.0 else attrs.getValue("str") / 2))))
        if (esFlat > 0 && !it.isArmour) stats.put(obj("id" to PSEUDO.getValue("es"), "value" to minOf(min(esFlat))))
        if (ele > 0) stats.put(obj("id" to PSEUDO.getValue("ele_res"), "value" to minOf(min(ele))))
        if (chaos > 0) stats.put(obj("id" to PSEUDO.getValue("chaos_res"), "value" to minOf(min(chaos))))
        for ((a, v) in attrs) if (v >= 40) stats.put(obj("id" to PSEUDO.getValue(a), "value" to minOf(min(v))))
        for (m in mods) {
            if (IMPORTANT.any { s -> s in m.ref } && m.kind == "explicit") {
                val v = m.value
                if (m.explicitId != null && v != null) stats.put(obj("id" to m.explicitId, "value" to minOf(min(v))))
            }
        }

        if (it.isArmour) {
            val af = JSONObject()
            for ((key, tkey) in listOf("armour" to "ar", "evasion" to "ev", "es" to "es")) {
                it.props[key]?.firstOrNull()?.let { v -> af.put(tkey, minOf(min(v))) }
            }
            if (af.length() > 0) {
                if (poe2) {
                    if (!flt.has("equipment_filters")) flt.put("equipment_filters", obj("filters" to JSONObject()))
                    val ef = flt.getJSONObject("equipment_filters").getJSONObject("filters")
                    for (key in af.keys()) ef.put(key, af.get(key))
                } else flt.put("armour_filters", obj("filters" to af))
            }
        }

        if (defensive && life == 0.0 && esFlat < 40 && !(it.isArmour && !it.props["es"].isNullOrEmpty()))
            notes.add("Món không có máu: giá thật nhiều khả năng THẤP hơn các món tương tự tìm được")
        val anyGear = listOf("weapon_filters", "armour_filters", "equipment_filters").any { key ->
            flt.optJSONObject(key)?.let { o -> o.length() > 0 } == true
        }
        if (stats.length() == 0 && !anyGear) notes.add("Không có chỉ số đáng lọc: chỉ tra theo loại món")
        return q to notes
    }
}
