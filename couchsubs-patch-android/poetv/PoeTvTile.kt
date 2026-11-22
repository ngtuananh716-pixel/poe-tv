// SPDX-License-Identifier: AGPL-3.0-only
package com.couchsubs.mobile.poetv

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
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
            SecondaryButton(if (s.strict) "Lọc: CHẶT (100%)" else "Lọc: RỘNG (90%)", Modifier.weight(1f)) {
                PoeTv.setFilter(context, strict = !s.strict)
            }
            SecondaryButton(if (s.byBase) "Base: ĐÚNG BASE" else "Base: MỌI BASE", Modifier.weight(1f)) {
                PoeTv.setFilter(context, byBase = !s.byBase)
            }
            SecondaryButton(if (s.allLines) "Dòng: TẤT CẢ" else "Dòng: CHÍNH", Modifier.weight(1f)) {
                PoeTv.setFilter(context, allLines = !s.allLines)
            }
        }
        if (s.note.isNotEmpty()) Text(s.note, style = Tokens.Type.caption, color = Tokens.Color.text)
        else TileLabel("Chỉ tra khi mở túi đồ, stash hoặc cửa sổ mua bán. Bỏ qua đồ bán theo lô và đồ chưa giám định.")
    }
}
