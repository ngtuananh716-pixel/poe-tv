// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import java.math.BigDecimal
import java.math.MathContext
import java.math.RoundingMode

/** Nội dung và vị trí ô giá trên TV (như các hàm price_lines, note_lines, place trong poe_tv/serve.py). */
object PriceBox {
    const val REGION_ID = "poe-price"
    const val LINE = 0.021          // chiều cao một dòng chữ trên TV (tỉ lệ chiều cao màn hình); 22/11/2026 giảm ~20% (từ 0.026)
    const val MAX_CHARS = 30        // mỗi dòng trong ô giá tối đa bấy nhiêu ký tự
    const val MAX_BULLETS = 10      // ô giá hiện tối đa bấy nhiêu dòng chỉ số

    /** Tiền nhỏ và tiền lớn để quy giá: (mã, chữ viết tắt). */
    private val MAIN_CURRENCY_SHORT = mapOf(
        "poe1" to listOf("chaos" to "c", "divine" to "div"), "poe2" to listOf("exalted" to "ex", "divine" to "div"),
    )

    private val CURRENCY = mapOf(
        "divine" to "div", "chaos" to "c", "exalted" to "ex", "mirror" to "mirror", "alch" to "alch",
        "annul" to "annul", "chance" to "chance", "vaal" to "vaal", "fusing" to "fuse", "regal" to "regal",
        "gcp" to "gcp", "chrome" to "chrome", "alt" to "alt", "jew" to "jew", "scour" to "scour",
    )

    /** Như f"{v:g}" của Python (6 chữ số có nghĩa, bỏ số 0 thừa). */
    fun g(v: Double): String {
        if (v == Math.floor(v) && Math.abs(v) < 1e6) return v.toLong().toString()
        return BigDecimal(v).round(MathContext(6)).stripTrailingZeros().toPlainString()
    }

    fun shortPrice(amount: Double, currency: String) = "${g(amount)} ${CURRENCY[currency] ?: currency}"

    /** Dòng đầu ô giá: tên món (22/11/2026 Tuấn Anh bỏ chữ "PoE2 ·"). */
    @Suppress("UNUSED_PARAMETER")
    fun head(title: String, game: String) = title.take(MAX_CHARS)

    class Listing(val amount: Double?, val currency: String?)

    /** Như fmt_each của bản Python: ≥ 10 không lẻ, ≥ 1 một số lẻ, còn lại 2 số lẻ (làm tròn như Python: nửa về số chẵn). */
    fun fmtEach(v: Double): String {
        fun fx(d: Int) = BigDecimal(v).setScale(d, RoundingMode.HALF_EVEN).toPlainString()
        if (v >= 10) return fx(0)
        return fx(if (v >= 1) 1 else 2).trimEnd('0').trimEnd('.')
    }

    /**
     * Các bộ lọc đã dùng, mỗi chỉ số một dòng: "Lọc:" + "• …" (bắt buộc), rồi "Đủ ≥N/M dòng:" + "• …" (nhóm đếm);
     * chỉ có nhóm đếm thì "Lọc (đủ ≥N/M dòng):". Quá MAX_BULLETS dòng chỉ số thì dòng cuối ghi số dòng còn lại.
     */
    fun filterLines(lab: FilterLabels?): List<String> {
        if (lab == null || (lab.req.isEmpty() && lab.count.isEmpty())) return emptyList()
        val group = if (lab.need >= lab.of) "tất cả ${lab.of} dòng" else "đủ ≥${lab.need}/${lab.of} dòng"
        var lines = ArrayList<String>()
        if (lab.req.isNotEmpty()) {
            lines.add("Lọc:")
            for (x in lab.req) lines.add("• $x")
            if (lab.count.isNotEmpty()) lines.add(group.replaceFirstChar { it.uppercaseChar() } + ":")
        } else lines.add("Lọc ($group):")
        for (x in lab.count) lines.add("• $x")
        val bullets = lines.count { it.startsWith("• ") }
        if (bullets > MAX_BULLETS) {
            var kept = 0
            val out = ArrayList<String>()
            for (l in lines) {
                if (l.startsWith("• ")) { kept++; if (kept > MAX_BULLETS - 1) continue }
                out.add(l)
            }
            out.add("• … +${bullets - (MAX_BULLETS - 1)} dòng")
            lines = out
        }
        return lines.map { it.take(MAX_CHARS) }
    }

    /**
     * Giá ước tính kiểu PoE Overlay: quy các món rẻ nhất (tối đa 10) về tiền nhỏ của game theo tỉ giá, lấy trung vị;
     * khoảng giá bỏ các giá "câu khách" thấp hơn 1/5 trung vị; độ tin cậy theo số món đang bán và độ chênh giá.
     * Không ước tính được (chưa có tỉ giá) thì trả null.
     */
    fun estimate(listings: List<Listing>, total: Int, rates: Map<String, Double>?, game: String): List<String>? {
        if (rates.isNullOrEmpty()) return null
        val vals = listings.filter { l -> l.amount != null && l.amount != 0.0 && l.currency != null && l.currency in rates }
            .map { l -> l.amount!! * rates.getValue(l.currency!!) }.sorted()
        if (vals.isEmpty()) return null
        val n = vals.size
        val med = if (n % 2 == 1) vals[n / 2] else (vals[n / 2 - 1] + vals[n / 2]) / 2
        val kept = vals.filter { v -> v >= med / 5 }.ifEmpty { vals }
        val lo = kept.first()
        val hi = kept.last()
        val spread = if (lo > 0) hi / lo else Double.POSITIVE_INFINITY
        val rel = if (total >= 20 && n >= 8 && spread <= 3) "cao" else if (total >= 5 && spread <= 10) "vừa" else "thấp"
        val (small, big) = MAIN_CURRENCY_SHORT[game] ?: MAIN_CURRENCY_SHORT.getValue("poe1")
        val unitBig = rates[big.first]?.takeIf { it != 0.0 }
        fun isBig(v: Double) = unitBig != null && v >= unitBig
        fun num(v: Double) = if (isBig(v)) fmtEach(v / unitBig!!) else fmtEach(v)
        fun full(v: Double) = "${num(v)} ${if (isBig(v)) big.second else small.second}"
        val same = isBig(lo) == isBig(med) && isBig(med) == isBig(hi)
        val rng = if (same) "${num(lo)}–${num(hi)}" else "${full(lo)}–${full(hi)}"
        return listOf("≈ ${full(med)} ($rng)".take(MAX_CHARS), "Tin cậy: $rel")
    }

    /**
     * Nội dung ô giá: tên món; Min (giá rẻ nhất); giá ước tính (khoảng) và độ tin cậy, mỗi thứ một dòng (không có tỉ giá
     * thì giá phổ biến); số món đang bán; các bộ lọc đã dùng (mỗi chỉ số một dòng); 1 lưu ý quan trọng nhất.
     */
    fun priceLines(title: String, total: Int, listings: List<Listing>, notes: List<String>,
                   labels: FilterLabels? = null, rates: Map<String, Double>? = null,
                   game: String = "poe1"): MutableList<String> {
        val lines = mutableListOf(title)
        val ls = listings.filter { it.amount != null && it.currency != null }
        if (ls.isEmpty()) lines.add("Không có món nào đang bán")
        else {
            lines.add("Min: ${shortPrice(ls[0].amount!!, ls[0].currency!!)}")
            val est = estimate(ls, total, rates, game)
            if (est != null) lines.addAll(est)
            else {
                val counts = LinkedHashMap<String, Int>()
                for (l in ls) shortPrice(l.amount!!, l.currency!!).let { counts[it] = (counts[it] ?: 0) + 1 }
                var common = ""
                var most = 0
                for ((k, n) in counts) if (n > most) { common = k; most = n }
                lines.add("Phổ biến: $common")
            }
            lines.add("$total món đang bán")
        }
        lines.addAll(filterLines(labels))
        lines.addAll(noteLines(notes).take(1))
        return lines
    }

    fun noteLines(notes: List<String>): List<String> {
        val out = ArrayList<String>()
        for (n in notes) {
            when {
                n.startsWith("Món không có máu") -> out.add("! Không máu: giá thật thấp hơn")
                n.startsWith("Dòng đọc chưa chắc") -> {}
                n.startsWith("Không có chỉ số đáng lọc") -> out.add("! Chỉ tra theo loại món")
                n.startsWith("Foulborn: có lọc") -> out.add("Đã lọc đúng dòng đột biến")
                n.startsWith("Ít món khớp") -> out.add("! " + n.take(MAX_CHARS - 2))
                n.startsWith("Foulborn: chưa ai bán") -> out.add("! Chưa ai bán đúng dòng đột biến")
                n.startsWith("Foulborn") -> out.add("! Chưa lọc dòng đột biến")
                else -> out.add("! " + n.take(MAX_CHARS - 2))
            }
        }
        val unsure = notes.count { it.startsWith("Dòng đọc chưa chắc") }
        if (unsure > 0) out.add("! $unsure dòng đọc chưa chắc, đã bỏ")
        return out
    }

    private fun r4(v: Double) = Math.round(v * 10000) / 10000.0

    /** Ô giá đặt sát cạnh phải tooltip; không đủ chỗ thì sang trái. Trả [trái, trên, phải, dưới] theo tỉ lệ 0–1. */
    fun place(box: IntArray, width: Int, height: Int, lines: Int): DoubleArray {
        val tl = box[0].toDouble() / width
        val tt = box[1].toDouble() / height
        val tr = box[2].toDouble() / width
        // rộng cố định (đủ MAX_CHARS ký tự) để ô "Đang tra giá…" và ô giá nằm đúng một chỗ, không nhảy
        val w = MAX_CHARS * 0.55 * 0.9 * LINE * (9.0 / 16) * 1.15 + 0.012
        val h = lines * LINE * 1.1 + 0.012
        val gap = 0.006
        val left = when {
            tr + gap + w <= 0.995 -> tr + gap
            tl - gap - w >= 0.005 -> tl - gap - w
            else -> maxOf(0.005, 0.995 - w)
        }
        val top = minOf(maxOf(0.005, tt), 0.995 - h)
        return doubleArrayOf(r4(left), r4(top), r4(left + w), r4(top + h))
    }

    /** Khóa nhận ra món: chữ và số thường của tiêu đề + 6 dòng đầu (mở lại đúng món thì hiện giá cũ ngay). */
    fun key(tt: Tooltip): String = (tt.header + tt.lines.take(6).map { it.text }).joinToString("|") { s ->
        buildString { for (c in s.lowercase()) if (c.isLetterOrDigit()) append(c) }
    }
}
