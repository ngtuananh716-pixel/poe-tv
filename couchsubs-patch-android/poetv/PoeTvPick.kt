// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.couchsubs.mobile.ui.PrimaryButton
import com.couchsubs.mobile.ui.Tokens

/** Khung "Món vừa tra" trong ô Tra giá: tên món, giá tự tra, nút mở màn "Chọn dòng để tra". */
@Composable
fun PickCard(rec: PoeTv.PickRecord, onOpen: () -> Unit) {
    val shape = RoundedCornerShape(Tokens.Radius.control)
    Column(
        Modifier.fillMaxWidth().background(Tokens.Color.surface2, shape).border(1.dp, Tokens.Color.accent, shape).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text("MÓN VỪA TRA · ${rec.at}", style = Tokens.Type.caption, color = Tokens.Color.secondary)
        Text("${rec.item.title} (${rec.item.rarity})", style = Tokens.Type.headline, color = Tokens.Color.text)
        Text(rec.auto.joinToString(" · "), style = Tokens.Type.body, color = Tokens.Color.text)
        PrimaryButton("Chọn dòng để tra  ›") { onOpen() }
    }
}

/**
 * Màn "Chọn dòng để tra" (hộp thoại toàn màn hình): base, chỉ số món, implicit gộp vào tổng, các dòng của món (thiếu
 * thì "none"), mỗi dòng có dấu chọn và mức tối thiểu; khung "Khi tra" (đã gộp) với số dòng cần đủ; chọn nhanh; TRA LẠI.
 * Kết quả TRA LẠI chỉ hiện ở đây (TV giữ giá tự tra). Có món mới được tra thì giữ bảng đang chỉnh, hiện nút "Xem món mới".
 */
@Composable
fun PickDialog(initial: PoeTv.PickRecord, onClose: () -> Unit) {
    val latest by PoeTv.pick.collectAsState()
    val result by PoeTv.pickResult.collectAsState()
    var viewing by remember { mutableStateOf(initial) }
    var sel by remember(viewing.id) { mutableStateOf(PickSel()) }
    val texts = remember(viewing.id) { mutableStateMapOf<String, String>() }
    val pn = viewing.panel
    val on = Pick.selOn(pn, sel)
    val summ = remember(viewing.id, sel) { Pick.build(viewing.item, pn, sel).second }
    val uri = LocalUriHandler.current
    val box = RoundedCornerShape(Tokens.Radius.control)

    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(
            Modifier.fillMaxSize().background(Tokens.Color.bg).imePadding().verticalScroll(rememberScrollState())
                .padding(horizontal = Tokens.Space.screen, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(Tokens.Space.inline),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("‹", style = Tokens.Type.title, color = Tokens.Color.text,
                    modifier = Modifier.clickable(onClick = onClose).padding(end = 14.dp))
                Text("Chọn dòng để tra", style = Tokens.Type.title, color = Tokens.Color.text)
            }
            Text("${pn.title} · ${pn.rarity} · ${PoeTv.GAME_NAME[pn.game]} · tra lúc ${viewing.at}",
                style = Tokens.Type.caption, color = Tokens.Color.secondary)
            latest?.let { l ->
                if (l.id != viewing.id) Text("Có món mới: ${l.item.title} · Xem món mới  ›", style = Tokens.Type.body,
                    color = Tokens.Color.accentText,
                    modifier = Modifier.fillMaxWidth().background(Tokens.Color.accentSoft, box).clickable { viewing = l }.padding(12.dp))
            }

            val explTitle = if (pn.slots > 0) "DÒNG TRÊN MÓN · ${pn.nExplicit}/${pn.slots}" else "DÒNG TRÊN MÓN"
            for ((title, key) in listOf("BASE" to "base", "CHỈ SỐ MÓN" to "gear", "IMPLICIT" to "implicit", explTitle to "explicit")) {
                val rows = pn.rows.filter { it.section == key }
                val nones = if (key == "explicit") pn.slots - pn.nExplicit else 0
                if (rows.isNotEmpty() || nones > 0) {
                    Text(title, style = Tokens.Type.caption, color = Tokens.Color.secondary, modifier = Modifier.padding(top = 6.dp))
                    for (r in rows) {
                        val shown = if (r.section == "base" && r.key != "ilvl") null else Pick.showMin(pn, r)
                        val txt = if (shown == null || !r.enabled) null else texts[r.key] ?: PriceBox.g(shown.toDouble())
                        PickRowView(r, on[r.key] == true, txt,
                            onCheck = { v -> sel = sel.copy(on = sel.on + (r.key to v)) },
                            onMin = { s ->
                                texts[r.key] = s
                                val v = s.replace(',', '.').toDoubleOrNull()
                                val e = sel.edit.toMutableMap()
                                if (v == null || (shown != null && v == shown.toDouble())) e.remove(r.key) else e[r.key] = v
                                sel = sel.copy(edit = e)
                            })
                    }
                    repeat(maxOf(0, nones)) {
                        Text("none", style = Tokens.Type.body, color = Tokens.Color.secondary,
                            modifier = Modifier.fillMaxWidth().border(1.dp, Tokens.Color.border, RoundedCornerShape(10.dp))
                                .padding(horizontal = 44.dp, vertical = 10.dp))
                    }
                }
            }

            Column(Modifier.fillMaxWidth().padding(top = 6.dp).background(Tokens.Color.surface, box).border(1.dp, Tokens.Color.border, box)
                .padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("KHI TRA (ĐÃ GỘP)", style = Tokens.Type.caption, color = Tokens.Color.secondary)
                for (x in summ.req + summ.stats) Text("• $x", style = Tokens.Type.body, color = Tokens.Color.text)
                if (summ.of > 0) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("Cần đủ", style = Tokens.Type.body, color = Tokens.Color.text)
                        SmallButton("−", Modifier.width(48.dp)) { sel = sel.copy(need = (summ.need - 1).coerceAtLeast(1)) }
                        Text("${summ.need}", style = Tokens.Type.headline, color = Tokens.Color.text)
                        SmallButton("+", Modifier.width(48.dp)) { sel = sel.copy(need = (summ.need + 1).coerceAtMost(summ.of)) }
                        Text("/ ${summ.of} dòng", style = Tokens.Type.body, color = Tokens.Color.text)
                    }
                } else Text("Chưa chọn dòng chỉ số nào", style = Tokens.Type.body, color = Tokens.Color.secondary)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(Tokens.Space.inline)) {
                SmallButton("Dòng chính", Modifier.weight(1f)) { sel = Pick.preset(pn, "main", sel); texts.clear() }
                SmallButton("Tất cả", Modifier.weight(1f)) { sel = Pick.preset(pn, "all", sel); texts.clear() }
                SmallButton("Bỏ hết", Modifier.weight(1f)) { sel = Pick.preset(pn, "none", sel); texts.clear() }
            }
            val mine = result?.takeIf { it.id == viewing.id }
            PrimaryButton("TRA LẠI", enabled = mine?.busy != true) { PoeTv.retry(viewing, sel) }
            Column(Modifier.fillMaxWidth().background(Tokens.Color.surface, box).border(1.dp, Tokens.Color.border, box).padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(if (mine != null) "KẾT QUẢ TRA LẠI (chỉ hiện ở đây, TV giữ giá tự tra)" else "GIÁ TỰ TRA",
                    style = Tokens.Type.caption, color = Tokens.Color.secondary)
                for (x in mine?.lines ?: viewing.auto) Text(x, style = Tokens.Type.body, color = Tokens.Color.text)
                val url = if (mine != null) mine.url else viewing.autoUrl
                if (url != null) Text("Mở trên trang trade  ›", style = Tokens.Type.body, color = Tokens.Color.accentText,
                    modifier = Modifier.clickable { try { uri.openUri(url) } catch (e: Exception) { } }.padding(vertical = 6.dp))
            }
        }
    }
}

/** Một dòng: dấu chọn, câu (và ghi chú gộp vào tổng nào), ô "≥ mức tối thiểu". */
@Composable
private fun PickRowView(r: PickRow, checked: Boolean, minText: String?, onCheck: (Boolean) -> Unit, onMin: (String) -> Unit) {
    val shape = RoundedCornerShape(10.dp)
    val active = checked && r.enabled
    Row(
        Modifier.fillMaxWidth().background(Tokens.Color.surface, shape).border(1.dp, Tokens.Color.border, shape)
            .padding(start = 2.dp, end = 10.dp, top = 4.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked, onCheckedChange = if (r.enabled) onCheck else null, enabled = r.enabled,
            colors = CheckboxDefaults.colors(checkedColor = Tokens.Color.accent, checkmarkColor = Tokens.Color.onAccent,
                uncheckedColor = Tokens.Color.secondary, disabledUncheckedColor = Tokens.Color.borderStrong,
                disabledCheckedColor = Tokens.Color.borderStrong))
        Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
            Text(r.text, style = Tokens.Type.body, color = if (active) Tokens.Color.text else Tokens.Color.secondary)
            r.tag?.let { Text(it, style = Tokens.Type.caption, color = Tokens.Color.secondary) }
        }
        if (minText != null) {
            Text("≥", style = Tokens.Type.body, color = Tokens.Color.secondary, modifier = Modifier.padding(start = 6.dp, end = 4.dp))
            BasicTextField(
                value = minText, onValueChange = onMin, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                textStyle = Tokens.Type.headline.copy(color = if (active) Tokens.Color.text else Tokens.Color.secondary, textAlign = TextAlign.End),
                cursorBrush = SolidColor(Tokens.Color.accent),
                modifier = Modifier.width(68.dp).background(Tokens.Color.surface2, RoundedCornerShape(9.dp))
                    .border(1.dp, Tokens.Color.borderStrong, RoundedCornerShape(9.dp)).padding(horizontal = 10.dp, vertical = 9.dp),
            )
        }
    }
}

/** Nút nhỏ cùng kiểu nút phụ của CouchSubs nhưng lề trong hẹp (vừa 3 nút một hàng). */
@Composable
private fun SmallButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Button(onClick, shape = RoundedCornerShape(Tokens.Radius.control), border = BorderStroke(1.dp, Tokens.Color.borderStrong),
        colors = ButtonDefaults.buttonColors(containerColor = Tokens.Color.surface2, contentColor = Tokens.Color.text),
        elevation = null, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 6.dp),
        modifier = modifier.heightIn(min = Tokens.Control.buttonSmall)) {
        Text(text, style = Tokens.Type.body, color = Tokens.Color.text, maxLines = 1)
    }
}
