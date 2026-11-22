// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.net.URLEncoder
import java.util.ArrayDeque
import java.util.concurrent.TimeUnit

/**
 * Gọi trade API của pathofexile.com (bản Kotlin của poe_tv/trade.py): giới hạn tốc độ theo header X-Rate-Limit-*,
 * nhớ tạm 10 phút. PoE1: /api/trade/…/sony/{league}; PoE2: /api/trade2/…/{league}. User-Agent ghi rõ tên công cụ,
 * không giả dạng trình duyệt, không cố vượt qua khi bị từ chối.
 */
class Trade(val game: String, private val http: OkHttpClient = DEFAULT_CLIENT) {
    class RateLimited(val waitSeconds: Double) : Exception("Đang bị giới hạn tốc độ, thử lại sau ${waitSeconds.toInt()} giây")

    class Result(val total: Int, val league: String, val listings: List<PriceBox.Listing>, val url: String)

    private val realm = if (game == "poe1") "sony" else "poe2"
    private val api = if (game == "poe1") "trade" else "trade2"
    @Volatile var league: String? = null
        private set
    private val rules = HashMap<String, List<IntArray>>()          // policy → [(tối đa, cửa sổ giây, phạt)]
    private val sent = HashMap<String, ArrayDeque<Double>>()
    private val blockedUntil = HashMap<String, Double>()
    private val cache = HashMap<String, Pair<Double, Result>>()

    private fun now() = System.currentTimeMillis() / 1000.0

    @Synchronized private fun waitNeeded(policy: String): Double {
        val t = now()
        val until = blockedUntil[policy] ?: 0.0
        if (until > t) return until - t
        var need = 0.0
        val q = sent[policy] ?: return 0.0
        for (r in rules[policy] ?: emptyList()) {
            val recent = q.filter { t - it < r[1] }
            if (recent.isNotEmpty() && recent.size >= r[0] - 1)            // chừa 1 lần để an toàn
                need = maxOf(need, r[1] - (t - recent.first()) + 0.2)
        }
        return need
    }

    @Synchronized private fun learn(policy: String, resp: Response) {
        val list = ArrayList<IntArray>()
        for (key in listOf("X-Rate-Limit-Ip", "X-Rate-Limit-Account")) {
            val v = resp.header(key) ?: continue
            for (part in v.split(",")) {
                val p = part.trim().split(":").mapNotNull { it.trim().toIntOrNull() }
                if (p.size == 3) list.add(p.toIntArray())
            }
        }
        if (list.isNotEmpty()) rules[policy] = list
        if (resp.code == 429) {
            val ra = resp.header("Retry-After")?.toDoubleOrNull() ?: 60.0
            blockedUntil[policy] = now() + ra
            throw RateLimited(ra)
        }
    }

    private fun call(policy: String, request: Request): JSONObject {
        val w = waitNeeded(policy)
        if (w > MAX_WAIT) throw RateLimited(w)
        if (w > 0) Thread.sleep((w * 1000).toLong())
        synchronized(this) {
            val q = sent.getOrPut(policy) { ArrayDeque() }
            q.addLast(now())
            while (q.size > 200) q.removeFirst()
        }
        http.newCall(request).execute().use { resp ->
            learn(policy, resp)
            if (resp.code == 403) throw RuntimeException("Trade từ chối (403)")
            if (!resp.isSuccessful) throw RuntimeException("Trade lỗi HTTP ${resp.code}")
            return JSONObject(resp.body?.string() ?: "{}")
        }
    }

    private fun get(url: String) = Request.Builder().url(url).header("User-Agent", USER_AGENT)
        .header("Accept", "application/json").build()

    private fun post(url: String, body: JSONObject) = Request.Builder().url(url).header("User-Agent", USER_AGENT)
        .header("Accept", "application/json").post(body.toString().toRequestBody(JSON)).build()

    /** League đang chơi: league trade đầu tiên của realm, bỏ Hardcore/Ruthless/Standard/SSF. */
    fun pickLeague(): String {
        league?.let { return it }
        val data = call("data", get("$BASE/api/$api/data/leagues"))
        val all = data.getJSONArray("result")
        val cands = (0 until all.length()).map { all.getJSONObject(it) }
            .filter { it.optString("realm", realm) == realm }.map { it.getString("id") }
        val bad = listOf("Hardcore", "HC ", "HC)", "Ruthless", "Standard", "SSF", "Solo")
        val chosen = cands.firstOrNull { id -> bad.none { it in id } } ?: cands.firstOrNull()
            ?: throw RuntimeException("Không lấy được league")
        league = chosen
        return chosen
    }

    private fun quote(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    private fun path(kind: String, league: String) =
        if (game == "poe1") "$BASE/api/trade/$kind/$realm/${quote(league)}" else "$BASE/api/trade2/$kind/${quote(league)}"

    fun search(query: JSONObject, fetch: Int = 10): Result {
        val lg = pickLeague()
        val key = query.toString()
        synchronized(this) { cache[key]?.let { (at, r) -> if (now() - at < CACHE_TTL) return r } }
        val res = call("search", post(path("search", lg), query))
        val ids = res.optJSONArray("result")
        val qid = res.optString("id")
        val listings = ArrayList<PriceBox.Listing>()
        val n = minOf(fetch, ids?.length() ?: 0)
        if (n > 0) {
            val joined = (0 until n).joinToString(",") { ids!!.getString(it) }
            val extra = if (game == "poe1") "&realm=$realm" else ""
            val got = call("fetch", get("$BASE/api/$api/fetch/$joined?query=$qid$extra"))
            val arr = got.optJSONArray("result")
            if (arr != null) for (i in 0 until arr.length()) {
                val x = arr.optJSONObject(i) ?: continue
                val p = x.optJSONObject("listing")?.optJSONObject("price")
                val amount = p?.takeIf { it.has("amount") && !it.isNull("amount") }?.getDouble("amount")
                listings.add(PriceBox.Listing(amount, p?.optString("currency")?.takeIf { it.isNotEmpty() }))
            }
        }
        val out = Result(res.optInt("total", 0), lg, listings, "$BASE/$api/search/$realm/${quote(lg)}/$qid")
        synchronized(this) { cache[key] = now() to out }
        return out
    }

    companion object {
        const val BASE = "https://www.pathofexile.com"
        const val USER_AGENT = "poe-tv-pricecheck/0.1 (personal console price check)"
        const val CACHE_TTL = 600.0
        const val MAX_WAIT = 15.0
        private val JSON = "application/json".toMediaType()
        val DEFAULT_CLIENT: OkHttpClient by lazy {
            OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build()
        }
    }
}
