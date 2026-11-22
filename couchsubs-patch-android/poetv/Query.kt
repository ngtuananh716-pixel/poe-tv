// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import org.json.JSONArray
import org.json.JSONObject

/** Các bộ lọc đã dùng, để hiện trên TV: bắt buộc ([req]) và nhóm đếm ([count], cần đủ [need] trong [of] dòng). */
data class FilterLabels(
    val req: List<String> = emptyList(), val count: List<String> = emptyList(), val need: Int = 0, val of: Int = 0,
)

/** Bản Kotlin của poe_tv/query.py: dựng bộ lọc trade từ món đã phân tích (mặc định như APT: dải giá trị −10%). */
object Query {
    const val RANGE = 0.10        // lấy món có chỉ số ≥ 90% món đang tra
    const val MIN_SCORE = 95.0    // dòng OCR khớp dưới mức này coi là chưa chắc, không đưa vào bộ lọc

    // Dữ liệu PoE2 ghi mod không có dấu "+" ở đầu: so khớp bỏ dấu "+".
    internal fun k(ref: String) = ref.replace("+", "")

    internal val RES: Map<String, Pair<Int, Boolean>> = mapOf(   // mod kháng → (số loại kháng nguyên tố, có kháng chaos)
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
    internal val ATTR: Map<String, String> = mapOf(
        "+# to Strength" to "str", "+# to Dexterity" to "dex", "+# to Intelligence" to "int",
        "+# to all Attributes" to "all", "+# to Strength and Dexterity" to "str dex",
        "+# to Strength and Intelligence" to "str int", "+# to Dexterity and Intelligence" to "dex int",
    ).mapKeys { k(it.key) }
    internal val PSEUDO = mapOf(
        "life" to "pseudo.pseudo_total_life", "es" to "pseudo.pseudo_total_energy_shield",
        "mana" to "pseudo.pseudo_total_mana", "ele_res" to "pseudo.pseudo_total_elemental_resistance",
        "chaos_res" to "pseudo.pseudo_total_chaos_resistance", "str" to "pseudo.pseudo_total_strength",
        "dex" to "pseudo.pseudo_total_dexterity", "int" to "pseudo.pseudo_total_intelligence",
    )

    /**
     * Mod quan trọng: (chuỗi nhận ra trong câu mẫu, nhãn ngắn hiện trên TV). Mặc định (Dòng: CHÍNH) chỉ lọc các mod này
     * cùng các tổng (máu, ES, kháng, thuộc tính); Dòng: TẤT CẢ lọc thêm mọi dòng explicit đọc được.
     */
    internal val IMPORTANT_LABEL = listOf(
        "Movement Speed" to "Tốc chạy", "to Level of" to "+Cấp skill", "Critical Strike Multiplier" to "Crit multi",
        "Critical Damage Bonus" to "Crit dmg", "increased maximum Life" to "%Máu",
        "increased maximum Energy Shield" to "%ES", "Spell Damage" to "Spell dmg",
        "Damage over Time Multiplier" to "DoT multi", "to Attacks" to "atk",
        "increased Cast Speed" to "Cast speed", "Suppress Spell Damage" to "Suppress", "to Spirit" to "Spirit",
        "increased Attack Speed" to "Attack speed", "Critical Strike Chance" to "Crit", "Critical Hit Chance" to "Crit",
        "Rarity of Items" to "Rarity", "increased Skill Speed" to "Skill speed",
    )
    private val ELEMENTS = listOf("Physical" to "Phys", "Fire" to "Fire", "Cold" to "Cold", "Lightning" to "Lightning",
        "Chaos" to "Chaos")
    // Nhãn ngắn của các bộ lọc, hiện dưới giá trên TV ("• Máu ≥ 94"); như query.labels của bản Python.
    internal val PSEUDO_LABEL = mapOf(
        "pseudo.pseudo_total_life" to "Máu", "pseudo.pseudo_total_energy_shield" to "ES", "pseudo.pseudo_total_mana" to "Mana",
        "pseudo.pseudo_total_elemental_resistance" to "K.NT", "pseudo.pseudo_total_chaos_resistance" to "K.Chaos",
        "pseudo.pseudo_total_strength" to "Str", "pseudo.pseudo_total_dexterity" to "Dex", "pseudo.pseudo_total_intelligence" to "Int",
    )
    /** Theo đúng thứ tự bản Python thêm vào bộ lọc (vũ khí: pDPS, eDPS; giáp: Giáp, Né, ES). */
    private val EQUIP_ORDER = listOf("pdps" to "pDPS", "edps" to "eDPS", "ar" to "Giáp", "ev" to "Né", "es" to "ES")
    /** Dòng đã gộp vào tổng (máu, ES cộng thẳng; kháng, thuộc tính xét riêng): Dòng: TẤT CẢ không lọc riêng lần nữa. */
    private val FOLDED = setOf("# to maximum Life", "# to maximum Energy Shield")
    /** Dòng local đã nằm trong DPS (vũ khí) hay Giáp/Né/ES (giáp): Dòng: TẤT CẢ không lọc riêng. */
    internal val LOCAL_WEAPON = Regex("^(#% increased Physical Damage|Adds # to # (Physical|Fire|Cold|Lightning|Chaos) Damage)$",
        RegexOption.IGNORE_CASE)
    internal val LOCAL_ARMOUR = Regex("^(# to (Armour|Evasion Rating|maximum Energy Shield)|" +
        "#% increased (Armour|Evasion Rating|Energy Shield)( and (Armour|Evasion|Energy Shield))?)$", RegexOption.IGNORE_CASE)
    private val SHORT_DROP = setOf("increased", "to", "of", "the", "Adds", "all", "with", "Items", "found", "damage", "Damage")

    /** Nhãn ngắn của một mod: theo bảng mod quan trọng, không có thì vài chữ chính của câu mẫu (≤ 20 ký tự). */
    fun modLabel(ref: String): String {
        for ((key, lab) in IMPORTANT_LABEL) {
            if (key in ref) {
                if (key == "to Attacks") return (ELEMENTS.firstOrNull { it.first in ref }?.second ?: "") + " atk"
                return lab
            }
        }
        val words = ref.replace(Regex("[#%+]"), " ").split(Regex("\\s+")).filter { it.isNotEmpty() && it !in SHORT_DROP }
        var out = ""
        for (w in words) {
            if (out.length + w.length + (if (out.isNotEmpty()) 1 else 0) > 20) break
            out = if (out.isNotEmpty()) "$out $w" else w
        }
        return out.ifEmpty { ref.take(20) }
    }

    private fun num(v: Any?): String = PriceBox.g((v as Number).toDouble())

    /** Các bộ lọc đã dùng (đồ không phải unique), viết ngắn để hiện trên TV. */
    fun labels(q: JSONObject, it: Item): FilterLabels {
        if (it.rarity == "Unique") return FilterLabels()
        val req = ArrayList<String>()
        val qq = q.getJSONObject("query")
        val flt = qq.optJSONObject("filters") ?: JSONObject()
        if (qq.optString("type").isNotEmpty() && it.rarity != "Gem" && it.rarity != "Other")   // gem, tiền tệ: tên đã ở dòng đầu
            req.add("Base: ${qq.getString("type")}")
        for (key in listOf("weapon_filters", "armour_filters", "equipment_filters")) {
            val f = flt.optJSONObject(key)?.optJSONObject("filters") ?: continue
            for ((k, name) in EQUIP_ORDER) {
                val o = f.optJSONObject(k) ?: continue
                if (o.has("min")) req.add("$name ≥ ${num(o.get("min"))}")
            }
        }
        val mf = flt.optJSONObject("misc_filters")?.optJSONObject("filters") ?: JSONObject()
        mf.optJSONObject("ilvl")?.let { o -> req.add("iLvl ≥ ${num(o.get("min"))}") }
        mf.optJSONObject("gem_level")?.let { o -> req.add("Cấp ≥ ${num(o.get("min"))}") }
        mf.optJSONObject("quality")?.let { o -> req.add("CL ≥ ${num(o.get("min"))}") }
        if (mf.optJSONObject("corrupted")?.optString("option") == "true") req.add("Corrupted")
        val refs = LinkedHashMap<String, String>()
        for (m in it.mods) { val sid = m.explicitId; if (sid != null && sid !in refs) refs[sid] = m.ref }

        fun lab(f: JSONObject): String {
            val id = f.getString("id")
            val ref = refs[id] ?: ""
            val name = PSEUDO_LABEL[id] ?: modLabel(ref)
            val v = f.optJSONObject("value")?.opt("min")
            if (v == null || v == JSONObject.NULL) return name
            return "$name ≥ ${num(v)}${if ("#%" in ref) "%" else ""}"
        }

        var count: List<String> = emptyList()
        var need = 0
        var of = 0
        val groups = qq.optJSONArray("stats") ?: JSONArray()
        for (i in 0 until groups.length()) {
            val g = groups.getJSONObject(i)
            val fs = g.getJSONArray("filters")
            val labs = (0 until fs.length()).map { j -> lab(fs.getJSONObject(j)) }
            if (g.getString("type") == "count") {
                count = labs; need = g.getJSONObject("value").getInt("min"); of = fs.length()
            } else req.addAll(labs)
        }
        return FilterLabels(req, count, need, of)
    }

    /** Mức tối thiểu khi lọc: rộng (mặc định) = floor(v × 0,9) như bản Python, chặt (rng = 0) = đúng giá trị; v ≤ 0 giữ nguyên. */
    fun min(v: Double, rng: Double = RANGE): Number = if (v > 0) Math.floor(v * (1 - rng)).toLong() else v

    private fun obj(vararg kv: Pair<String, Any>) = JSONObject().apply { for ((a, b) in kv) put(a, b) }
    private fun minOf(v: Number) = obj("min" to v)
    private fun miscFilters(flt: JSONObject): JSONObject {
        if (!flt.has("misc_filters")) flt.put("misc_filters", obj("filters" to JSONObject()))
        return flt.getJSONObject("misc_filters").getJSONObject("filters")
    }

    /**
     * Trả (bộ lọc gửi /api/trade/search hoặc /api/trade2/search, các ghi chú).
     * [strict]: chặt, chỉ số từ 100% (mặc định rộng: từ 90%). [byBase]: lọc đúng base (mặc định mọi base cùng loại đồ;
     * đồ trắng luôn đúng base). [allLines]: lọc mọi dòng explicit (mặc định chỉ tổng máu/kháng/thuộc tính và mod quan
     * trọng). Từ 3 dòng trở lên thì xếp vào nhóm đếm: mặc định cần đủ (số dòng − 1), allLines cần đủ hết (bên gọi tự nới
     * khi quá ít món). Giáp/Né/ES và DPS vũ khí luôn bắt buộc.
     */
    fun build(it: Item, status: String = "securable", game: String = "poe1", strict: Boolean = false,
              byBase: Boolean = false, allLines: Boolean = false): Pair<JSONObject, MutableList<String>> {
        val rng = if (strict) 0.0 else RANGE
        fun mn(v: Double) = min(v, rng)
        val poe2 = game == "poe2"
        val notes = ArrayList<String>()
        val stats = JSONArray()
        val flt = JSONObject()
        val groups = JSONArray().put(obj("type" to "and", "filters" to stats))
        val qq = obj("status" to obj("option" to status), "filters" to flt, "stats" to groups)
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
                        f.put("value", minOf(if (Math.abs(v) <= 5) v else mn(v)))
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

        if (it.rarity == "Normal" || it.rarity == "Magic") {
            // đồ làm base: item level quyết định mod có thể ra (tooltip chỉ có dòng này khi game hiện)
            it.props["ilvl"]?.firstOrNull()?.let { v -> miscFilters(flt).put("ilvl", minOf(v.toLong())) }
        }
        if ((it.rarity == "Normal" || byBase) && it.base.isNotEmpty()) qq.put("type", it.base)   // đồ trắng: giá nằm ở base

        val cat = PoeData.TRADE_CATEGORY[it.category]
        val tf = obj("rarity" to obj("option" to "nonunique"))
        if (cat != null) tf.put("category", obj("option" to cat)) else qq.put("type", it.base)
        flt.put("type_filters", obj("filters" to tf))

        if (it.isWeapon) {
            val wf = JSONObject()
            val pdps = it.pdps
            val edps = it.edps
            if (pdps >= 30) wf.put("pdps", minOf(mn(pdps)))
            if (edps >= 30) wf.put("edps", minOf(mn(edps)))
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

        val pool = ArrayList<JSONObject>()      // các dòng chỉ số (xếp vào nhóm đếm nếu từ 3 dòng)
        val defensive = it.isArmour || it.isJewellery || it.category in setOf("Quiver", "Jewel", "Abyss Jewel")
        // PoE1: 2 Str = 1 máu (như APT). PoE2 chưa kiểm chứng nên chỉ tính máu cộng thẳng
        if (life > 0) pool.add(obj("id" to PSEUDO.getValue("life"), "value" to minOf(mn(life + if (poe2) 0.0 else attrs.getValue("str") / 2))))
        if (esFlat > 0 && !it.isArmour) pool.add(obj("id" to PSEUDO.getValue("es"), "value" to minOf(mn(esFlat))))
        if (ele > 0) pool.add(obj("id" to PSEUDO.getValue("ele_res"), "value" to minOf(mn(ele))))
        if (chaos > 0) pool.add(obj("id" to PSEUDO.getValue("chaos_res"), "value" to minOf(mn(chaos))))
        for ((a, v) in attrs) if (v >= 40) pool.add(obj("id" to PSEUDO.getValue(a), "value" to minOf(mn(v))))
        val usedIds = HashSet<String>()
        for (m in mods) {
            val v = m.value
            if (m.kind != "explicit" || v == null) continue
            val important = IMPORTANT_LABEL.any { (key, _) -> key in m.ref }
            val r = k(m.ref)
            val folded = r in FOLDED || r in RES || r in ATTR ||
                (it.isWeapon && LOCAL_WEAPON.matches(r)) || (it.isArmour && LOCAL_ARMOUR.matches(r))
            if (!important && !(allLines && !folded)) continue
            val id = m.explicitId ?: continue
            if (usedIds.add(id)) pool.add(obj("id" to id, "value" to minOf(mn(v))))
        }
        if (pool.size >= 3) {
            val f = JSONArray()
            for (p in pool) f.put(p)
            groups.put(obj("type" to "count", "value" to obj("min" to if (allLines) pool.size else pool.size - 1), "filters" to f))
        } else for (p in pool) stats.put(p)

        if (it.isArmour) {
            val af = JSONObject()
            for ((key, tkey) in listOf("armour" to "ar", "evasion" to "ev", "es" to "es")) {
                it.props[key]?.firstOrNull()?.let { v -> af.put(tkey, minOf(mn(v))) }
            }
            if (af.length() > 0) {
                if (poe2) {
                    if (!flt.has("equipment_filters")) flt.put("equipment_filters", obj("filters" to JSONObject()))
                    val ef = flt.getJSONObject("equipment_filters").getJSONObject("filters")
                    for (key in listOf("ar", "ev", "es")) if (af.has(key)) ef.put(key, af.get(key))
                } else flt.put("armour_filters", obj("filters" to af))
            }
        }

        if (defensive && life == 0.0 && esFlat < 40 && !(it.isArmour && !it.props["es"].isNullOrEmpty()))
            notes.add("Món không có máu: giá thật nhiều khả năng THẤP hơn các món tương tự tìm được")
        val anyGear = listOf("weapon_filters", "armour_filters", "equipment_filters").any { key ->
            flt.optJSONObject(key)?.let { o -> o.length() > 0 } == true
        }
        if (pool.isEmpty() && !anyGear) notes.add("Không có chỉ số đáng lọc: chỉ tra theo loại món")
        return q to notes
    }
}
