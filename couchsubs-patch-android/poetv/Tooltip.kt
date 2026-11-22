// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

/** Một dòng chữ OCR, tọa độ theo điểm ảnh của khung hình gốc. */
class Line(var text: String, var x0: Double, var y0: Double, var x1: Double, var y1: Double) {
    val cx get() = (x0 + x1) / 2
    val cy get() = (y0 + y1) / 2
    val h get() = y1 - y0
    override fun toString() = "Line($text, $x0, $y0, $x1, $y1)"
}

/** Vùng tooltip: [box] = x0, y0, x1, y1 trên khung hình gốc; [header] = 1–2 dòng đầu (tên, base). */
class Tooltip(val box: IntArray, val header: List<String>, val lines: List<Line>)

/** Bản Kotlin của poe_tv/tooltip.py: tìm tooltip món đồ trong các dòng OCR của cả khung hình. */
object TooltipFinder {
    /**
     * Dòng gợi ý nút ở đáy màn hình chỉ có khi mở túi đồ / stash / cửa sổ bán đồ (bản console): PoE1 "Navigate",
     * "Compare / More Actions", "Reorder Tabs"; PoE2 "Price Check", "Manage Sockets", "Toggle Weapon Set".
     * Không dùng "Pick Up" (có cả khi nhặt đồ dưới đất).
     */
    val UI_MARKERS = Regex("NAVIGATE|COMPARE|MOREACTIONS|REORDERTABS|PRICECHECK|MANAGESOCKETS|TOGGLEWEAPONSET")
    /** Chỉ đọc phần màn hình từ 70% chiều cao trở xuống để tìm dòng gợi ý đó. */
    const val UI_STRIP = 0.70

    fun upperLetters(s: String): String {
        val sb = StringBuilder(s.length)
        for (c in s.uppercase()) if (c in 'A'..'Z') sb.append(c)
        return sb.toString()
    }

    /** Các chữ của dòng gợi ý nút tìm thấy trong dải đáy (rỗng = đang chơi, không mở túi đồ). */
    fun inventoryMarks(stripLines: List<Line>): List<String> =
        stripLines.map { it.text }.filter { UI_MARKERS.containsMatchIn(upperLetters(it)) }

    fun unidentified(tt: Tooltip): Boolean = tt.lines.any { "UNIDENTIFIED" in upperLetters(it.text) }

    private val FOULBORN = Regex("^\\s*foulborn\\s+", RegexOption.IGNORE_CASE)

    /** Có cần đọc lại kỹ (phóng to 2 lần) không: unique, ngọc, vật phẩm chỉ cần tên → không; rare/magic → có. */
    fun needsFine(header: List<String>, data: PoeData): Boolean {
        if (header.size == 2) {
            val name = FOULBORN.replaceFirst(header[0], "")
            val u = data.findUnique(name)
            val b = data.findBase(header[1])
            return !(u != null && b != null && u.hasBase(b.name))
        }
        return !(data.findGem(header[0]) != null || data.findOther(header[0]) != null)
    }

    fun median(v: List<Double>): Double {
        val s = v.sorted()
        val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2
    }

    /** [lines] đã xếp theo (y0, x0). Trả tooltip hoặc null; chưa đọc kỹ (bên gọi đọc lại vùng [Tooltip.box] nếu cần). */
    fun find(lines: List<Line>, width: Int, height: Int, data: PoeData): Tooltip? {
        if (lines.isEmpty()) return null
        val W = width.toDouble()
        val H = height.toDouble()
        // 1) Ứng viên tiêu đề: base/ngọc/vật phẩm (ghép dòng tên ngay phía trên nếu có) hoặc tên đồ magic chứa base.
        val cands = ArrayList<List<Line>>()
        for ((i, l) in lines.withIndex()) {
            if (data.findBase(l.text) != null || data.findGem(l.text) != null || data.findOther(l.text) != null) {
                val above = lines.subList(0, i).filter { p ->
                    p !== l && -0.5 * l.h < l.y0 - p.y1 && l.y0 - p.y1 < 2.0 * l.h && p.y0 < l.y0 &&
                        Math.abs(p.cx - l.cx) < 0.04 * W
                }
                cands.add(if (above.isNotEmpty()) listOf(above.last(), l) else listOf(l))
            } else if (':' !in l.text && data.findBaseIn(l.text) != null) {
                cands.add(listOf(l))          // dòng chat "tên: lời nhắn" thì bỏ
            }
        }
        if (cands.isEmpty()) return null

        fun column(header: List<Line>): List<Line> {
            val cx0 = header.sumOf { it.cx } / header.size
            val tol = 0.045 * W
            val col = lines.filter { it.y0 > header.last().y0 + 1 && Math.abs(it.cx - cx0) < tol }
            val picked = ArrayList<Line>()
            var lastY = header.last().y1
            val hs = header.map { it.h }
            for (l in col) {
                val lh = if (picked.isNotEmpty()) median(hs + picked.map { it.h }) else median(hs)
                if (l.y0 - lastY > 4.0 * lh) break
                picked.add(l)
                lastY = maxOf(lastY, l.y1)
            }
            return picked
        }

        var bestScore = -1
        var bestHeader: List<Line> = emptyList()
        var bestPicked: List<Line> = emptyList()
        for (header in cands) {
            val picked = column(header)
            val cx0 = header.sumOf { it.cx } / header.size
            val centered = picked.count { Math.abs(it.cx - cx0) < 0.015 * W }
            val score = centered + if (header.size == 2) 2 else 0
            if (bestScore < 0 || score > bestScore) {
                bestScore = score
                bestHeader = header
                bestPicked = picked
            }
        }
        if (bestScore < 2) return null       // chữ lẻ (chat, tên bảng…), không phải tooltip
        val all = bestHeader + bestPicked
        val x0 = all.minOf { it.x0 }
        val x1 = all.maxOf { it.x1 }
        val y0 = all.minOf { it.y0 }
        val y1 = all.maxOf { it.y1 }
        val mx = 0.02 * W
        val my = 0.01 * H
        val box = intArrayOf(
            maxOf(0, (x0 - mx).toInt()), maxOf(0, (y0 - my).toInt()),
            minOf(width, (x1 + mx).toInt()), minOf(height, (y1 + my).toInt()),
        )
        return Tooltip(box, bestHeader.map { it.text }, all)
    }

    /** Vùng đọc lại kỹ: cắt rộng thêm 3% bề ngang mỗi bên (lượt đầu có thể sót đuôi dòng dài nhất). */
    fun fineCrop(box: IntArray, width: Int): IntArray {
        val ex = (0.03 * width).toInt()
        return intArrayOf(maxOf(0, box[0] - ex), box[1], minOf(width, box[2] + ex), box[3])
    }

    /**
     * Các hàng chữ màu hồng (dòng mod đột biến của đồ Foulborn). [rgb] (x, y) → 0xRRGGBB trên khung gốc.
     * Ngưỡng như bản Python: hồng = G/R < 0,58 và B/R > 0,62 (đo trên ảnh TV 21/11/2026).
     */
    fun pinkRows(tt: Tooltip, rgb: (Int, Int) -> Int, width: Int, height: Int): List<String> {
        val out = ArrayList<String>()
        val body = tt.lines.filter { it.text !in tt.header }
        for (row in Parse.rows(body)) {
            var n = 0
            var r = 0.0
            var g = 0.0
            var b = 0.0
            for (l in row) {
                val ys = maxOf(0, l.y0.toInt())
                val ye = minOf(height, l.y1.toInt())
                val xs = maxOf(0, l.x0.toInt())
                val xe = minOf(width, l.x1.toInt())
                for (y in ys until ye) for (x in xs until xe) {
                    val c = rgb(x, y)
                    val cr = (c shr 16) and 0xff
                    val cg = (c shr 8) and 0xff
                    val cb = c and 0xff
                    if (maxOf(cr, cg, cb) > 120) {
                        n++; r += cr; g += cg; b += cb
                    }
                }
            }
            if (n < 30) continue
            r /= n; g /= n; b /= n
            if (r > 140 && g / r < 0.58 && b / r > 0.62)
                out.add(row.sortedBy { it.x0 }.joinToString(" ") { it.text })
        }
        return out
    }
}
