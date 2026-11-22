// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.concurrent.TimeUnit

/**
 * Đọc chữ bằng Google ML Kit (chạy trên máy, mô hình Latin có sẵn trong app như bản CouchSubs) trên ảnh xám (mặt phẳng
 * độ sáng Y). Trả các dòng với tọa độ điểm ảnh của khung hình gốc, xếp theo (y0, x0) như bản Python.
 */
class PoeOcr : AutoCloseable {
    private val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)

    /** [y]: độ sáng [width]×[height] (hàng liền nhau). Tọa độ trả về = tọa độ trong ảnh / [scale] + ([dx], [dy]). */
    fun read(y: ByteArray, width: Int, height: Int, dx: Int = 0, dy: Int = 0, scale: Double = 1.0): List<Line> {
        val w = width and 1.inv()
        val h = height and 1.inv()
        if (w < 16 || h < 16) return emptyList()
        val nv21 = ByteArray(w * h * 3 / 2)
        if (w == width) System.arraycopy(y, 0, nv21, 0, w * h)
        else for (r in 0 until h) System.arraycopy(y, r * width, nv21, r * w, w)
        java.util.Arrays.fill(nv21, w * h, nv21.size, 128.toByte())
        val input = InputImage.fromByteArray(nv21, w, h, 0, InputImage.IMAGE_FORMAT_NV21)
        val text = Tasks.await(recognizer.process(input), 8, TimeUnit.SECONDS)
        val out = ArrayList<Line>()
        for (block in text.textBlocks) for (line in block.lines) {
            val b = line.boundingBox ?: continue
            val t = line.text.trim()
            if (t.isEmpty()) continue
            out.add(Line(t, b.left / scale + dx, b.top / scale + dy, b.right / scale + dx, b.bottom / scale + dy))
        }
        return out.sortedWith(compareBy({ it.y0 }, { it.x0 }))
    }

    override fun close() = recognizer.close()

    companion object {
        /** Cắt vùng [x0, y0, x1, y1] của ảnh độ sáng [src] (rộng [srcWidth]) và phóng to [factor] lần (song tuyến). */
        fun cropScaled(src: ByteArray, srcWidth: Int, x0: Int, y0: Int, x1: Int, y1: Int, factor: Double): Triple<ByteArray, Int, Int> {
            val cw = x1 - x0
            val ch = y1 - y0
            val w = (cw * factor).toInt()
            val h = (ch * factor).toInt()
            val out = ByteArray(w * h)
            for (oy in 0 until h) {
                val fy = (oy + 0.5) / factor - 0.5
                val iy = fy.toInt().coerceIn(0, ch - 1)
                val iy2 = (iy + 1).coerceAtMost(ch - 1)
                val wy = (fy - iy).coerceIn(0.0, 1.0)
                val r1 = (y0 + iy) * srcWidth + x0
                val r2 = (y0 + iy2) * srcWidth + x0
                for (ox in 0 until w) {
                    val fx = (ox + 0.5) / factor - 0.5
                    val ix = fx.toInt().coerceIn(0, cw - 1)
                    val ix2 = (ix + 1).coerceAtMost(cw - 1)
                    val wx = (fx - ix).coerceIn(0.0, 1.0)
                    val a = (src[r1 + ix].toInt() and 0xff) * (1 - wx) + (src[r1 + ix2].toInt() and 0xff) * wx
                    val b = (src[r2 + ix].toInt() and 0xff) * (1 - wx) + (src[r2 + ix2].toInt() and 0xff) * wx
                    out[oy * w + ox] = (a * (1 - wy) + b * wy).toInt().toByte()
                }
            }
            return Triple(out, w, h)
        }
    }
}
