// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.couchsubs.mobile.core.CropRegion
import com.couchsubs.mobile.core.TextBox
import com.couchsubs.mobile.ocr.RegionPixels
import com.couchsubs.mobile.ocr.SubtitleReader
import com.couchsubs.mobile.remoteplay.VideoFrame
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Tra giá món đồ PoE ngay trên điện thoại (bản Kotlin của Watcher trong poe_tv/serve.py, thay cho laptop):
 * nhận khung hình Remote Play, chỉ khi đang mở túi đồ/stash/cửa sổ mua bán mới tìm tooltip, đọc món, tra trade rồi đặt
 * ô giá cạnh tooltip trên TV (qua máy chủ phụ đề của CouchSubs, như ô "poe-price" của bản laptop).
 *
 * Luồng: [offer] chạy trên luồng giải mã hình (phải nhanh): theo dõi vùng tooltip đang hiện giá, hoặc chép khung hình
 * để luồng nền tìm tooltip. Đồ dưới đất, đồ bán theo lô và đồ chưa giám định được bỏ qua như bản laptop.
 */
object PoeTv {
    private const val TAG = "PoeTv"
    const val DETECT_GAP_MS = 250L       // chưa có ô giá: nghỉ bấy nhiêu giữa 2 lần tìm tooltip
    const val UI_IDLE_GAP_MS = 600L      // không mở túi đồ (đang chơi): nghỉ lâu hơn, đỡ tốn pin
    const val CHECK_MS = 300L            // đang hiện ô giá: so vùng tooltip mỗi bấy nhiêu ms
    const val TT_CHANGED = 0.5           // bản đồ chữ vùng tooltip khác quá tỉ lệ này = đã đóng hoặc sang món khác
    const val RECENT_MS = 600_000L       // mở lại đúng món đã tra trong 10 phút thì hiện giá cũ ngay
    const val RELAX_MIN = 5              // Dòng: TẤT CẢ ra ít hơn bấy nhiêu món thì tự nới (bớt 1 dòng, rồi 2 dòng)
    private const val SIG = 96
    private const val TEXT_LEVEL = 110
    val GAME_NAME = mapOf("poe1" to "PoE1", "poe2" to "PoE2")
    private val FULL = CropRegion(top = 0.0, bottom = 1.0, left = 0.0, right = 1.0)

    /**
     * [strict]: Lọc CHẶT (chỉ số từ 100%) hay RỘNG (từ 90%); [byBase]: ĐÚNG BASE hay MỌI BASE;
     * [allLines]: Dòng TẤT CẢ (mọi dòng explicit, tự nới khi ít món) hay CHÍNH (tổng máu/kháng + mod quan trọng).
     */
    data class Status(
        val price: Boolean = true, val game: String = "poe1", val note: String = "", val last: String = "",
        val strict: Boolean = false, val byBase: Boolean = false, val allLines: Boolean = false,
    )

    private val _status = MutableStateFlow(Status())
    val status: StateFlow<Status> = _status

    /** Món vừa tra (đồ không phải unique) cho màn "Chọn dòng để tra": bảng dòng, giá tự tra, link trang trade. */
    class PickRecord(
        val id: Int, val at: String, val item: Item, val panel: PickPanel, val auto: List<String>, val autoUrl: String?,
    )

    /** Kết quả TRA LẠI của món [id]: chỉ hiện trên điện thoại, không lên TV, không ghi vào giá đã nhớ của TV. */
    class PickResult(val id: Int, val busy: Boolean, val lines: List<String>, val url: String?)

    private val _pick = MutableStateFlow<PickRecord?>(null)
    val pick: StateFlow<PickRecord?> = _pick
    private val _pickResult = MutableStateFlow<PickResult?>(null)
    val pickResult: StateFlow<PickResult?> = _pickResult
    private var pickSeq = 0
    private var pickWorker: ExecutorService? = null

    /** Bản gọn: không tải quảng cáo, không hiện chữ ký/logo của nhà phát triển. */
    val NO_PROMO = true
    /** Bản gọn: không gom và gửi câu phụ đề đọc được lên máy chủ chung của nhà phát triển. */
    val NO_POOL = true

    /** Đọc phụ đề tiếng Thổ của CouchSubs: tắt khi tra giá (đỡ tốn pin; dịch tiếng Việt trên điện thoại làm sau). */
    @Volatile var translateOn = false
        private set

    private class Job(val game: String, val strict: Boolean, val byBase: Boolean, val allLines: Boolean) {
        @Volatile var stale = false
    }

    private class Shown(
        val ttBox: IntArray, val box: IntArray, val sig: BooleanArray, val key: String, val silent: Boolean,
        val game: String, val width: Int, val height: Int,
    ) {
        var verified = false      // đã thấy tooltip vẫn mở ở khung kế tiếp (OCR mất 1–2 giây)
        var text: List<String>? = null
        var sent: String? = null
    }

    private val lock = Any()
    private var appContext: Context? = null
    private var send: ((String, TextBox?) -> Unit)? = null
    private var worker: ExecutorService? = null
    private var ocr: PoeOcr? = null
    private val games = HashMap<String, Pair<PoeData, Trade>>()
    private var job: Job? = null
    private var shown: Shown? = null
    private var lastDone = 0L
    private var lastCheck = 0L
    private var gap = DETECT_GAP_MS
    private var changedFrames = 0
    private val recent = HashMap<String, Pair<Long, List<String>>>()
    private var loaded = false

    /** Nạp lựa chọn đã lưu (Tra giá bật/tắt, PoE1/PoE2). Gọi được nhiều lần. */
    fun init(context: Context) {
        synchronized(lock) {
            if (loaded) return
            loaded = true
            appContext = context.applicationContext
        }
        val p = context.getSharedPreferences("poetv", Context.MODE_PRIVATE)
        val game = p.getString("game", "poe1").takeIf { it in GAME_NAME } ?: "poe1"
        _status.value = _status.value.copy(price = p.getBoolean("price", true), game = game,
            strict = p.getBoolean("strict", false), byBase = p.getBoolean("by_base", false),
            allLines = p.getBoolean("all_lines", false))
    }

    private fun save(context: Context?) {
        val s = _status.value
        context?.getSharedPreferences("poetv", Context.MODE_PRIVATE)?.edit()
            ?.putBoolean("price", s.price)?.putString("game", s.game)?.putBoolean("strict", s.strict)
            ?.putBoolean("by_base", s.byBase)?.putBoolean("all_lines", s.allLines)?.apply()
    }

    private fun note(text: String) { _status.value = _status.value.copy(note = text) }

    /** Phiên Remote Play bắt đầu: [sender] gửi ô giá lên TV (chữ rỗng + null = xóa ô). */
    fun attach(context: Context, sender: (String, TextBox?) -> Unit) {
        init(context)
        synchronized(lock) {
            send = sender
            if (worker == null) worker = Executors.newSingleThreadExecutor { r -> Thread(r, "poetv").apply { isDaemon = true } }
            shown = null; job = null; changedFrames = 0; lastDone = 0L
        }
        note(if (_status.value.price) "Chờ mở túi đồ / stash" else "Tra giá đang TẮT")
    }

    fun detach() {
        val w: ExecutorService?
        val o: PoeOcr?
        synchronized(lock) {
            job?.stale = true
            send = null
            shown = null
            w = worker
            worker = null
            o = ocr
            ocr = null
        }
        // đóng sau việc đang chạy trên cùng luồng nền (luồng đơn), không đóng giữa chừng
        try { w?.execute { o?.close() } } catch (e: Exception) { o?.close() }
        w?.shutdown()
        note("")
    }

    fun setPrice(context: Context, on: Boolean) {
        init(context)
        synchronized(lock) {
            if (!on) {
                shown?.let { if (it.sent != null) send?.invoke("", null) }
                shown = null
                job?.stale = true
            }
        }
        _status.value = _status.value.copy(price = on)
        save(context)
        note(if (on) "Chờ mở túi đồ / stash" else "Tra giá đang TẮT")
    }

    /** Đổi PoE1 ⇄ PoE2: xóa ô giá đang hiện, quên giá đã nhớ, lưu lựa chọn. Dữ liệu game mới nạp ở lần tra đầu. */
    fun setGame(context: Context, game: String) {
        if (game !in GAME_NAME) return
        init(context)
        synchronized(lock) {
            shown?.let { if (it.sent != null) send?.invoke("", null) }
            shown = null
            job?.stale = true
            recent.clear()
            _status.value = _status.value.copy(game = game)
        }
        save(context)
    }

    /**
     * Đổi chế độ lọc (Lọc RỘNG/CHẶT, Base MỌI/ĐÚNG, Dòng CHÍNH/TẤT CẢ): quên giá đã nhớ (là của chế độ cũ), xóa ô đang
     * hiện để món đang mở được tra lại ngay theo chế độ mới, lưu lựa chọn.
     */
    fun setFilter(context: Context, strict: Boolean? = null, byBase: Boolean? = null, allLines: Boolean? = null) {
        init(context)
        val s = _status.value
        val ns = strict ?: s.strict
        val nb = byBase ?: s.byBase
        val nl = allLines ?: s.allLines
        if (ns == s.strict && nb == s.byBase && nl == s.allLines) return
        synchronized(lock) {
            shown?.let { clear(it) }
            job?.stale = true
            recent.clear()
            _status.value = _status.value.copy(strict = ns, byBase = nb, allLines = nl)
        }
        save(context)
    }

    /**
     * TRA LẠI theo các dòng đã chọn trên màn "Chọn dòng để tra" (luồng riêng, chạy được cả khi chưa kết nối PS5).
     * Kết quả chỉ ở [pickResult]: không gửi lên TV, không ghi vào giá đã nhớ.
     */
    fun retry(rec: PickRecord, sel: PickSel) {
        val ex = synchronized(lock) {
            pickWorker ?: Executors.newSingleThreadExecutor { r -> Thread(r, "poetv-pick").apply { isDaemon = true } }
                .also { pickWorker = it }
        }
        _pickResult.value = PickResult(rec.id, true, listOf("Đang tra…"), null)
        ex.execute {
            val game = rec.panel.game
            val out = try {
                val trade = synchronized(lock) { games[game]?.second } ?: throw IllegalStateException("Chưa nạp dữ liệu ${GAME_NAME[game]}")
                val (q, _) = Pick.build(rec.item, rec.panel, sel)
                val res = trade.search(q)
                val rates = if (res.listings.isEmpty()) null else try { trade.rates() } catch (e: Exception) { null }
                PickResult(rec.id, false,
                    PriceBox.priceLines(rec.item.title, res.total, res.listings, emptyList(), null, rates, game).drop(1), res.url)
            } catch (e: Trade.RateLimited) {
                PickResult(rec.id, false, listOf("Trade API bận, chờ ${e.waitSeconds.toInt() + 1} giây rồi bấm lại"), null)
            } catch (e: Exception) {
                Log.w(TAG, "retry", e)
                PickResult(rec.id, false, listOf("Lỗi tra giá", (e.message ?: e.javaClass.simpleName).take(120)), null)
            }
            _pickResult.value = out
        }
    }

    // ---------------- khung hình (luồng giải mã) ----------------
    fun offer(frame: VideoFrame) {
        val st = _status.value
        var start: Job? = null
        synchronized(lock) {
            if (send == null) return
            if (!st.price) return
            val now = SystemClock.elapsedRealtime()
            val s = shown
            if (s != null) {
                if (now - lastCheck < CHECK_MS) return
                lastCheck = now
                if (Math.abs(frame.width - s.width) > 1 || Math.abs(frame.height - s.height) > 1) {     // đổi độ phân giải
                    clear(s); return
                }
                val changed = sigChange(frameSig(frame, s.box), s.sig) > TT_CHANGED
                if (!s.verified) {
                    if (changed) { shown = null; job?.stale = true }      // đóng trong lúc đang đọc: không hiện gì
                    else { s.verified = true; flush(s) }
                } else {
                    changedFrames = if (changed) changedFrames + 1 else 0
                    if (changedFrames >= 2) clear(s)                      // 2 lần liền mới xóa, đỡ nháy
                }
                return
            }
            if (job != null || now - lastDone < gap || worker == null) return
            start = Job(st.game, st.strict, st.byBase, st.allLines).also { job = it }
        }
        val j = start ?: return
        val pixels = SubtitleReader.copyRegion(frame, FULL)
        if (pixels == null) {
            synchronized(lock) { if (job === j) job = null }
            return
        }
        try {
            worker!!.execute { run(pixels, j) }
        } catch (e: Exception) {
            synchronized(lock) { if (job === j) job = null }
        }
    }

    /** Gọi khi đang giữ [lock]. */
    private fun clear(s: Shown) {
        if (s.sent != null) send?.invoke("", null)
        shown = null
        changedFrames = 0
        job?.stale = true
    }

    /** Gửi nội dung mới nhất của ô (gọi khi đang giữ [lock]). */
    private fun flush(s: Shown) {
        val lines = s.text ?: return
        if (s.silent || !s.verified) return
        val text = lines.joinToString("\n")
        if (text == s.sent) return
        val b = PriceBox.place(s.ttBox, s.width, s.height, lines.size)
        send?.invoke(text, TextBox(b[0], b[1], b[2], b[3]))
        s.sent = text
    }

    // ---------------- tìm tooltip, tra giá (luồng nền) ----------------
    private fun dataFor(game: String): Pair<PoeData, Trade> {
        synchronized(lock) { games[game]?.let { return it } }
        note("Đang nạp dữ liệu ${GAME_NAME[game]}…")
        val ctx = appContext ?: throw IllegalStateException("chưa gắn app")
        val json = ctx.assets.open("poetv/$game.json").bufferedReader().use { it.readText() }
        val pair = PoeData(json) to Trade(game)
        synchronized(lock) { games[game] = pair }
        return pair
    }

    private fun run(px: RegionPixels, j: Job) {
        var uiClosed = false
        val t0 = SystemClock.elapsedRealtime()
        try {
            val (data, trade) = dataFor(j.game)
            val o = synchronized(lock) { ocr ?: PoeOcr().also { ocr = it } }
            val W = px.width
            val H = px.height
            // 1) Cổng túi đồ: đọc riêng dải đáy, tìm dòng gợi ý nút "Navigate / Compare / Reorder Tabs".
            val y0 = (H * TooltipFinder.UI_STRIP).toInt() and 1.inv()
            val strip = px.nv21.copyOfRange(y0 * W, H * W)
            val stripLines = o.read(strip, W, H - y0, 0, y0)
            var marks = TooltipFinder.inventoryMarks(stripLines)
            if (marks.isEmpty() && TooltipFinder.weakHint(stripLines)) {
                // PoE2, đồ đang mặc không có sockets: chỉ có "Pick Up" → xem nhãn "INVENTORY" ở dải trên cùng
                val th = (H * TooltipFinder.TITLE_STRIP).toInt() and 1.inv()
                marks = TooltipFinder.titleMarks(o.read(px.nv21.copyOfRange(0, th * W), W, th))
            }
            if (marks.isEmpty()) {
                uiClosed = true
                note("Chưa mở túi đồ / stash (đang chơi)")
                return
            }
            // 2) Tìm tooltip trên cả khung hình.
            val tt0 = TooltipFinder.find(o.read(px.nv21, W, H), W, H, data)
            if (tt0 == null) {
                note("Đã mở túi đồ · chưa thấy tooltip món")
                return
            }
            // 3) Rare/magic: đọc lại vùng tooltip phóng to 2 lần cho đúng từng dòng mod.
            val tt = if (!TooltipFinder.needsFine(tt0.header, data)) tt0 else {
                val c = TooltipFinder.fineCrop(tt0.box, W)
                val factor = minOf(2.0, 2400.0 / maxOf(c[2] - c[0], c[3] - c[1]).coerceAtLeast(1))
                val (img, w, h) = PoeOcr.cropScaled(px.nv21, W, c[0], c[1], c[2], c[3], factor)
                Tooltip(tt0.box, tt0.header, o.read(img, w, h, c[0], c[1], factor))
            }
            val inner = innerBox(tt.box, W, H)
            val sig = bytesSig(px.nv21, W, inner)
            val skip = if (TooltipFinder.unidentified(tt)) "chưa giám định" else null
            val item = if (skip != null) null else Parse.parse(tt.header, tt.lines, data)
            val skipWhy = skip ?: if (item?.rarity == "Other") "đồ bán theo lô" else null
            if (skipWhy != null) {
                // theo dõi im lặng vùng này (không hiện ô): khỏi đọc lại liên tục khi món vẫn đang mở
                synchronized(lock) {
                    if (!j.stale) { shown = Shown(tt.box, inner, sig, "", true, j.game, W, H); changedFrames = 0 }
                }
                note("${item?.name?.ifEmpty { null } ?: tt.header.joinToString(" ")} → bỏ qua ($skipWhy)")
                return
            }
            item!!
            if (item.foulborn) px.color?.let { rgb -> Parse.addMutated(item, TooltipFinder.pinkRows(tt, rgb, W, H), data) }
            val (q, notes0) = Query.build(item, "securable", j.game, j.strict, j.byBase, j.allLines)
            var notes: List<String> = notes0
            val key = PriceBox.key(tt)
            val title = PriceBox.head(item.title, j.game)
            val s = Shown(tt.box, inner, sig, key, false, j.game, W, H)
            synchronized(lock) {
                if (j.stale) return
                shown = s
                changedFrames = 0
                val cached = recent[key]
                if (cached != null && SystemClock.elapsedRealtime() - cached.first < RECENT_MS) {
                    s.text = cached.second
                    note("${item.title} → ${cached.second.drop(1).joinToString(" · ")} (đã tra)")
                    return
                }
                s.text = listOf(title, "Đang tra giá…")
            }
            note("${item.title} → đang tra giá…")
            val lines: List<String> = try {
                var res = trade.search(q)
                // Dòng: TẤT CẢ ra quá ít món: nới nhóm đếm, bớt 1 dòng rồi 2 dòng
                val groups = q.getJSONObject("query").getJSONArray("stats")
                val cg = (0 until groups.length()).map { i -> groups.getJSONObject(i) }.firstOrNull { g -> g.getString("type") == "count" }
                if (j.allLines && cg != null && res.total < RELAX_MIN) {
                    val nAll = cg.getJSONArray("filters").length()
                    for (need in listOf(nAll - 1, nAll - 2)) {
                        if (need < 1) break
                        cg.getJSONObject("value").put("min", need)
                        res = trade.search(q)
                        if (res.total >= RELAX_MIN) break
                    }
                    notes = listOf("Ít món khớp đủ $nAll/$nAll → tự nới") + notes
                }
                if (item.foulborn && item.mutated.isNotEmpty() && res.total == 0) {
                    // chưa ai bán đúng dòng đột biến: tra lại món Foulborn cùng tên
                    q.getJSONObject("query").put("stats", org.json.JSONArray().put(
                        org.json.JSONObject().put("type", "and").put("filters", org.json.JSONArray())))
                    res = trade.search(q)
                    notes = notes.filter { n -> !n.startsWith("Foulborn") } + "Foulborn: chưa ai bán đúng dòng đột biến"
                }
                val rates = if (res.listings.isEmpty()) null else try { trade.rates() } catch (e: Exception) {
                    null        // không có tỉ giá: ô giá hiện giá phổ biến như cũ
                }
                try {
                    Pick.panel(item, j.game, j.strict, j.byBase, j.allLines, q)?.let { pn ->
                        val auto = PriceBox.priceLines(item.title, res.total, res.listings, emptyList(), null, rates, j.game).drop(1)
                        val at = java.text.SimpleDateFormat("HH:mm", java.util.Locale.ROOT).format(java.util.Date())
                        val id = synchronized(lock) { ++pickSeq }
                        _pick.value = PickRecord(id, at, item, pn, auto, res.url)
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "pick", e)       // bảng chọn dòng lỗi không được làm hỏng ô giá trên TV
                }
                PriceBox.priceLines(title, res.total, res.listings, notes, Query.labels(q, item), rates, j.game).also { l ->
                    // đã đổi chế độ lọc / game trong lúc tra: giá này của chế độ cũ, không nhớ
                    synchronized(lock) {
                        val st = _status.value
                        if (j.game == st.game && j.strict == st.strict && j.byBase == st.byBase && j.allLines == st.allLines)
                            recent[key] = SystemClock.elapsedRealtime() to l
                    }
                }
            } catch (e: Trade.RateLimited) {
                listOf(title, "Trade API bận, chờ ${e.waitSeconds.toInt() + 1} giây")
            } catch (e: Exception) {
                Log.w(TAG, "trade", e)
                listOf(title, "Lỗi tra giá", (e.message ?: e.javaClass.simpleName).take(PriceBox.MAX_CHARS))
            }
            synchronized(lock) {
                if (!j.stale && shown === s && j.game == _status.value.game) {
                    s.text = lines
                    flush(s)
                }
            }
            val secs = (SystemClock.elapsedRealtime() - t0) / 1000.0
            note("${item.title} → ${lines.drop(1).joinToString(" · ")} (${"%.1f".format(secs)} giây)")
            _status.value = _status.value.copy(last = lines.joinToString(" · "))
        } catch (e: Throwable) {
            Log.w(TAG, "run", e)
            note("Lỗi: ${e.message ?: e.javaClass.simpleName}")
        } finally {
            synchronized(lock) {
                if (job === j) job = null
                lastDone = SystemClock.elapsedRealtime()
                gap = if (uiClosed) UI_IDLE_GAP_MS else DETECT_GAP_MS
            }
        }
    }

    // ---------------- bản đồ chữ vùng tooltip ----------------
    /** Bỏ phần lề tooltip được thêm (2% rộng, 1% cao): chỉ so phần chữ. */
    fun innerBox(box: IntArray, W: Int, H: Int): IntArray {
        val mx = (0.02 * W).toInt()
        val my = (0.01 * H).toInt()
        return if (box[2] - box[0] > 3 * mx && box[3] - box[1] > 3 * my)
            intArrayOf(box[0] + mx, box[1] + my, box[2] - mx, box[3] - my) else box
    }

    /**
     * Bản đồ chữ SIG×SIG ô: ô nào có đủ điểm sáng (chữ). Nền tooltip trong suốt một phần nên game động phía sau chỉ lờ
     * mờ, không vượt ngưỡng chữ sáng → quái/pet cử động không làm nhiễu. Lấy mẫu cách 1 điểm ảnh cho nhẹ.
     */
    private fun sigOf(box: IntArray, row: (Int, ByteArray) -> Unit): BooleanArray {
        val bw = box[2] - box[0]
        val bh = box[3] - box[1]
        val out = BooleanArray(SIG * SIG)
        if (bw <= 0 || bh <= 0) return out
        val total = IntArray(SIG * SIG)
        val bright = IntArray(SIG * SIG)
        val buf = ByteArray(bw)
        var y = 0
        while (y < bh) {
            row(box[1] + y, buf)
            val cy = y * SIG / bh
            var x = 0
            while (x < bw) {
                val c = cy * SIG + x * SIG / bw
                total[c]++
                if ((buf[x].toInt() and 0xff) > TEXT_LEVEL) bright[c]++
                x += 2
            }
            y += 2
        }
        for (i in out.indices) out[i] = total[i] > 0 && bright[i] * 255 > 25 * total[i]
        return out
    }

    private fun bytesSig(y: ByteArray, width: Int, box: IntArray) =
        sigOf(box) { r, buf -> System.arraycopy(y, r * width + box[0], buf, 0, buf.size) }

    private fun frameSig(frame: VideoFrame, box: IntArray): BooleanArray {
        val b = frame.buffer.duplicate()
        val stride = frame.rowStride
        return sigOf(box) { r, buf ->
            val at = r * stride + box[0]
            if (at + buf.size <= b.capacity()) { b.position(at); b.get(buf, 0, buf.size) } else java.util.Arrays.fill(buf, 0.toByte())
        }
    }

    private fun sigChange(a: BooleanArray, b: BooleanArray): Double {
        var diff = 0
        var na = 0
        var nb = 0
        for (i in a.indices) {
            if (a[i] != b[i]) diff++
            if (a[i]) na++
            if (b[i]) nb++
        }
        return diff.toDouble() / maxOf(na, nb, 50)
    }
}
