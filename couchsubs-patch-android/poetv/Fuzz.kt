// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

/**
 * So khớp mờ giống `rapidfuzz.fuzz.ratio` và `process.extractOne` của bản Python (poe_tv/data.py):
 * điểm = (1 − khoảng cách Indel / tổng độ dài) × 100, với khoảng cách Indel = tổng độ dài − 2 × LCS.
 * LCS tính kiểu song song bit (Hyyrö), đủ nhanh để quét ~12 000 câu mod trên điện thoại.
 */
object Fuzz {
    /** Mặt nạ vị trí từng ký tự của chuỗi mẫu, chia theo từng khối 64 bit. */
    class Pattern(val text: String) {
        val words = (text.length + 63) / 64
        private val masks = HashMap<Char, LongArray>()

        init {
            for ((i, c) in text.withIndex()) {
                val m = masks.getOrPut(c) { LongArray(words) }
                m[i / 64] = m[i / 64] or (1L shl (i % 64))
            }
        }

        /** Độ dài chuỗi con chung dài nhất giữa mẫu và [other]. */
        fun lcs(other: String): Int {
            if (text.isEmpty() || other.isEmpty()) return 0
            val v = LongArray(words) { -1L }
            for (c in other) {
                val pm = masks[c] ?: continue
                // V' = (V + U) | (V − U) với U = V & PM; U nằm trong V nên V − U = V & ~U (không mượn bit).
                var carry = 0L
                for (w in 0 until words) {
                    val vw = v[w]
                    val u = vw and pm[w]
                    val sum = vw + u + carry
                    // tràn khi cộng không dấu
                    carry = if (java.lang.Long.compareUnsigned(sum, vw) < 0 || (carry == 1L && sum == vw)) 1L else 0L
                    v[w] = sum or (vw and u.inv())
                }
            }
            var zeros = 0
            for (w in 0 until words) {
                val bits = if (w == words - 1 && text.length % 64 != 0) text.length % 64 else 64
                val mask = if (bits == 64) -1L else (1L shl bits) - 1
                zeros += java.lang.Long.bitCount(v[w].inv() and mask)
            }
            return zeros
        }

        /** Điểm 0–100 như fuzz.ratio. */
        fun ratio(other: String): Double {
            val total = text.length + other.length
            if (total == 0) return 100.0
            val dist = total - 2 * lcs(other)
            return (1.0 - dist.toDouble() / total) * 100.0
        }
    }

    fun ratio(a: String, b: String): Double = Pattern(a).ratio(b)

    /**
     * Như process.extractOne(query, choices, scorer=fuzz.ratio, score_cutoff=cutoff): trả (lựa chọn, điểm) tốt nhất,
     * hòa điểm thì lấy lựa chọn gặp trước; không lựa chọn nào đạt ngưỡng thì null.
     */
    fun extractOne(query: String, choices: Array<String>, cutoff: Double): Pair<String, Double>? {
        val p = Pattern(query)
        val lq = query.length
        var best: String? = null
        var bestScore = 0.0
        var need = cutoff
        for (c in choices) {
            val lc = c.length
            val total = lq + lc
            if (total == 0) continue
            // điểm cao nhất có thể (LCS = độ dài chuỗi ngắn hơn): không đạt ngưỡng thì khỏi tính
            val upper = (1.0 - (total - 2 * minOf(lq, lc)).toDouble() / total) * 100.0
            if (upper < need) continue
            val s = p.ratio(c)
            if (s >= need && (best == null || s > bestScore)) {
                best = c
                bestScore = s
                need = s
                if (s >= 100.0) break
            }
        }
        return best?.let { it to bestScore }
    }
}
