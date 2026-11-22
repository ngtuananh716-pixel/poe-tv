// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import java.math.BigDecimal
import java.math.MathContext

/** Nội dung và vị trí ô giá trên TV (như các hàm price_lines, note_lines, place trong poe_tv/serve.py). */
object PriceBox {
    const val REGION_ID = "poe-price"
    const val LINE = 0.026          // chiều cao một dòng chữ trên TV (tỉ lệ chiều cao màn hình)
    const val MAX_CHARS = 30        // mỗi dòng trong ô giá tối đa bấy nhiêu ký tự

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

    /** Dòng đầu ô giá; chế độ PoE2 ghi rõ để lỡ quên chuyển game thì nhìn là biết. */
    fun head(title: String, game: String) = ((if (game == "poe2") "PoE2 · " else "") + title).take(MAX_CHARS)

    class Listing(val amount: Double?, val currency: String?)

    /** Tên món, giá rẻ nhất, giá phổ biến + số món, 1 lưu ý quan trọng nhất. */
    fun priceLines(title: String, total: Int, listings: List<Listing>, notes: List<String>): MutableList<String> {
        val lines = mutableListOf(title)
        val ls = listings.filter { it.amount != null && it.currency != null }
        if (ls.isEmpty()) lines.add("Không có món nào đang bán")
        else {
            lines.add("Rẻ nhất: ${shortPrice(ls[0].amount!!, ls[0].currency!!)}")
            val counts = LinkedHashMap<String, Int>()
            for (l in ls) shortPrice(l.amount!!, l.currency!!).let { counts[it] = (counts[it] ?: 0) + 1 }
            var common = ""
            var most = 0
            for ((k, n) in counts) if (n > most) { common = k; most = n }
            lines.add("Phổ biến: $common · $total món")
        }
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
