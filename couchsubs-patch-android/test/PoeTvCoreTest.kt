// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * So bản Kotlin với lõi Python: fixture.json (tools/android_fixture.py) ghi dòng OCR của 3 ảnh tooltip thật cùng kết quả
 * của poe_tv (tìm tooltip, phân tích, bộ lọc trade), và kết quả khớp tên món/mod (kể cả chữ OCR sai).
 */
class PoeTvCoreTest {
    private val fixture = JSONObject(javaClass.classLoader!!.getResource("poetv/fixture.json")!!.readText())
    private val data = HashMap<String, PoeData>()
    private fun data(game: String) = data.getOrPut(game) { PoeData(File("src/main/assets/poetv/$game.json").readText()) }

    private fun str(o: JSONObject, k: String): String? = if (o.isNull(k)) null else o.getString(k)

    @Test fun fuzzMatchesRapidfuzz() {
        assertEquals(100.0, Fuzz.ratio("abc", "abc"), 1e-9)
        assertEquals(200.0 * 8 / 17, Fuzz.ratio("heavybel", "heavybelt"), 1e-9)
        // chuỗi dài hơn 64 ký tự (nhiều khối bit)
        val a = "#toleveloflallcoldspellskillgemsandsomeverylongtextthatgoesbeyondsixtyfourcharacters"
        val b = a.replace("cold", "fire").drop(3)
        val lcs = naiveLcs(a, b)
        assertEquals(lcs, Fuzz.Pattern(a).lcs(b))
        assertEquals(lcs, Fuzz.Pattern(b).lcs(a))
    }

    private fun naiveLcs(a: String, b: String): Int {
        val d = Array(a.length + 1) { IntArray(b.length + 1) }
        for (i in 1..a.length) for (j in 1..b.length)
            d[i][j] = if (a[i - 1] == b[j - 1]) d[i - 1][j - 1] + 1 else maxOf(d[i - 1][j], d[i][j - 1])
        return d[a.length][b.length]
    }

    @Test fun ritualWindowOpensGate() {
        // dòng OCR thật của cửa sổ Ritual (ảnh bản PC): đáy có dấu hiệu yếu, dải trên có tiêu đề "FAVOURS"
        for (bottom in listOf(listOf("OFFERTRIBUTETOTHEKING", "866"), listOf("CANCEL", "0 Rituals Remaining"))) {
            val l = bottom.map { Line(it, 0.0, 0.0, 10.0, 10.0) }
            assertTrue(TooltipFinder.inventoryMarks(l).isEmpty())
            assertTrue(TooltipFinder.weakHint(l))
        }
        assertEquals(listOf("FAVOURS"), TooltipFinder.titleMarks(listOf(Line("FAVOURS", 0.0, 0.0, 10.0, 10.0))))
    }

    @Test fun gameplayHintsDoNotOpenGate() {
        val l = listOf(Line("X Pick Up", 0.0, 0.0, 10.0, 10.0), Line("Toggle Chat", 0.0, 0.0, 10.0, 10.0))
        assertTrue(TooltipFinder.inventoryMarks(l).isEmpty())
    }

    @Test fun names() {
        val d = data("poe1")
        val names = fixture.getJSONArray("names")
        for (i in 0 until names.length()) {
            val n = names.getJSONObject(i)
            val t = n.getString("text")
            assertEquals(t, str(n, "base_in"), d.findBaseIn(t)?.name)
            if (n.optBoolean("only_in")) continue
            assertEquals(t, str(n, "base"), d.findBase(t)?.name)
            assertEquals(t, str(n, "unique"), d.findUnique(t)?.name)
            assertEquals(t, str(n, "other"), d.findOther(t)?.name)
            assertEquals(t, str(n, "gem"), d.findGem(t))
        }
    }

    @Test fun mods() {
        val mods = fixture.getJSONArray("mods")
        for (i in 0 until mods.length()) {
            val m = mods.getJSONObject(i)
            val line = m.getString("line")
            assertEquals(line, m.getString("clean"), Parse.cleanNumbers(line))
            val (sm, score) = data(m.getString("game")).matchMod(m.getString("clean"), str(m, "cat"))
            assertEquals(line, str(m, "ref"), sm?.ref)
            assertEquals(line, m.getDouble("score"), score, 1e-6)
            if (sm != null) {
                assertEquals(line, m.getInt("n"), sm.nValues)
                assertEquals(line, str(m, "eid"), sm.explicitId)
            }
        }
    }

    @Test fun tooltipsParseAndQuery() {
        val cases = fixture.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = c.getString("name")
            val game = c.optString("game", "poe1")
            val d = data(game)
            val ls = c.getJSONArray("lines")
            val lines = (0 until ls.length()).map { j ->
                val l = ls.getJSONObject(j)
                Line(l.getString("text"), l.getDouble("x0"), l.getDouble("y0"), l.getDouble("x1"), l.getDouble("y1"))
            }
            // cổng túi đồ: chữ gợi ý nút trong dải đáy màn hình (PoE1 và PoE2)
            val strip = lines.filter { it.y0 >= TooltipFinder.UI_STRIP * c.getInt("height") }
            assertEquals("$name marks", c.getJSONArray("marks").toList(), TooltipFinder.inventoryMarks(strip))
            assertEquals("$name weak", c.getBoolean("weak"), TooltipFinder.weakHint(strip))
            val top = lines.filter { it.y1 <= TooltipFinder.TITLE_STRIP * c.getInt("height") }
            assertEquals("$name titles", c.getJSONArray("titles").toList(), TooltipFinder.titleMarks(top))
            assertTrue("$name gate", TooltipFinder.inventoryMarks(strip).isNotEmpty() ||
                (TooltipFinder.weakHint(strip) && TooltipFinder.titleMarks(top).isNotEmpty()))
            val tt = TooltipFinder.find(lines, c.getInt("width"), c.getInt("height"), d)
            val e = c.optJSONObject("expect")
            if (e == null) { assertEquals(name, null, tt); continue }
            assertNotNull(name, tt)
            tt!!
            assertEquals(name, e.getJSONArray("box").toString(), JSONArray(tt.box.toList()).toString())
            assertEquals(name, e.getJSONArray("header").toString(), JSONArray(tt.header).toString())
            val it = Parse.parse(tt.header, tt.lines, d)
            assertEquals(name, e.getString("rarity"), it.rarity)
            assertEquals(name, e.getString("name"), it.name)
            assertEquals(name, e.getString("base"), it.base)
            assertEquals(name, str(e, "category"), it.category)
            assertEquals(name, e.getString("title"), it.title)
            val em = e.getJSONArray("mods")
            assertEquals(name, em.length(), it.mods.size)
            for (j in 0 until em.length()) {
                val m = em.getJSONObject(j)
                assertEquals("$name mod $j", m.getString("ref"), it.mods[j].ref)
                assertEquals("$name mod $j", m.getString("kind"), it.mods[j].kind)
                assertEquals("$name mod $j", m.getJSONArray("values").toList().map { (it as Number).toDouble() }, it.mods[j].values)
            }
            val props = e.getJSONObject("props")
            assertEquals(name, props.keySet(), it.props.keys)
            for (k in props.keySet())
                assertEquals("$name $k", props.getJSONArray(k).toList().map { (it as Number).toDouble() }, it.props[k])
            val (q, notes) = Query.build(it, "securable", game)
            assertTrue("$name query\n${e.getJSONObject("query")}\n$q", e.getJSONObject("query").similar(q))
            assertEquals(name, e.getJSONArray("notes").toList(), notes)
            val labels = Query.labels(q, it)
            assertEquals("$name labels", e.getJSONArray("labels").toList(), labels)
            assertEquals("$name filter lines", e.getJSONArray("filter_lines").toList(), PriceBox.filterLines(labels))
            val box = PriceBox.place(tt.box, c.getInt("width"), c.getInt("height"), 3)
            assertTrue(name, box[2] <= 1.0 && box[0] >= 0.0)
        }
    }
}
