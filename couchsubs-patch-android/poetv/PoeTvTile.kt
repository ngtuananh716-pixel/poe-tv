// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.couchsubs.mobile.ui.SecondaryButton
import com.couchsubs.mobile.ui.SectionTitle
import com.couchsubs.mobile.ui.Tile
import com.couchsubs.mobile.ui.TileLabel
import com.couchsubs.mobile.ui.Tokens

/**
 * Ô điều khiển tra giá PoE trên màn hình chính: bật/tắt tra giá, chọn PoE1/PoE2; chế độ lọc (RỘNG/CHẶT, MỌI/ĐÚNG BASE,
 * Dòng CHÍNH/TẤT CẢ); dòng trạng thái gần nhất.
 */
@Composable
fun PoeTvTile(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    remember { PoeTv.init(context); true }
    val s by PoeTv.status.collectAsState()
    Tile(modifier.fillMaxWidth()) {
        SectionTitle("Tra giá PoE (PoE TV)")
        Row(horizontalArrangement = Arrangement.spacedBy(Tokens.Space.gap)) {
            SecondaryButton(if (s.price) "Tra giá: BẬT" else "Tra giá: TẮT", Modifier.weight(1f)) {
                PoeTv.setPrice(context, !s.price)
            }
            SecondaryButton("Game: ${PoeTv.GAME_NAME[s.game]}", Modifier.weight(1f)) {
                PoeTv.setGame(context, if (s.game == "poe1") "poe2" else "poe1")
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Tokens.Space.gap)) {
            ModeButton("Lọc", if (s.strict) "CHẶT 100%" else "RỘNG 90%", Modifier.weight(1f)) {
                PoeTv.setFilter(context, strict = !s.strict)
            }
            ModeButton("Base", if (s.byBase) "ĐÚNG BASE" else "MỌI BASE", Modifier.weight(1f)) {
                PoeTv.setFilter(context, byBase = !s.byBase)
            }
            ModeButton("Dòng", if (s.allLines) "TẤT CẢ" else "CHÍNH", Modifier.weight(1f)) {
                PoeTv.setFilter(context, allLines = !s.allLines)
            }
        }
        val rec by PoeTv.pick.collectAsState()
        var open by remember { mutableStateOf(false) }
        rec?.let { r -> PickCard(r) { open = true } }
        if (open) rec?.let { r -> PickDialog(r) { open = false } }
        if (s.note.isNotEmpty()) Text(s.note, style = Tokens.Type.caption, color = Tokens.Color.text)
        else TileLabel("Chỉ tra khi mở túi đồ, stash hoặc cửa sổ mua bán. Bỏ qua đồ bán theo lô và đồ chưa giám định.")
    }
}

/**
 * Nút chế độ lọc: chữ nhỏ (tên chế độ) ở trên, giá trị ở dưới. Cùng màu và khung với SecondaryButton của CouchSubs
 * nhưng lề trong hẹp (6 dp thay cho 24 dp): 3 nút một hàng trên điện thoại vẫn đủ chỗ, không cắt chữ.
 * Giá trị không vừa thì thu nhỏ dần tới 75% như nút của CouchSubs.
 */
@Composable
private fun ModeButton(caption: String, value: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Button(onClick, shape = RoundedCornerShape(Tokens.Radius.control), border = BorderStroke(1.dp, Tokens.Color.borderStrong),
        colors = ButtonDefaults.buttonColors(containerColor = Tokens.Color.surface2, contentColor = Tokens.Color.text),
        elevation = null, contentPadding = PaddingValues(horizontal = 6.dp, vertical = 6.dp),
        modifier = modifier.fillMaxWidth().heightIn(min = Tokens.Control.button)) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(caption, style = Tokens.Type.caption.copy(fontSize = 12.sp), color = Tokens.Color.secondary,
                textAlign = TextAlign.Center, maxLines = 1)
            val style = Tokens.Type.headline.copy(fontSize = 15.sp)
            var scale by remember(value) { mutableFloatStateOf(1f) }
            Text(value, style = style.copy(fontSize = style.fontSize * scale), textAlign = TextAlign.Center,
                maxLines = 1, softWrap = false, overflow = TextOverflow.Clip,
                onTextLayout = { if (it.didOverflowWidth && scale > 0.75f) scale = (scale - 0.05f).coerceAtLeast(0.75f) })
        }
    }
}
