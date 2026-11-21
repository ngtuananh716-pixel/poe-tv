// SPDX-License-Identifier: AGPL-3.0-only
// poe-tv (bản sửa, Mac): 4 nút dưới nút "Start translating": Tra giá bật/tắt, Dịch tiếng Việt bật/tắt,
// Dịch cả màn hình (một lần), Game PoE1/PoE2. Trạng thái nằm ở máy chủ poe_tv (cửa sổ Terminal "PoE TV"); nút chỉ gửi
// lệnh rồi hiện trạng thái máy chủ trả về.

import SwiftUI

struct PoeTvButtons: View {
    @ObservedObject var bridge: PoeTvBridge
    @State private var polling = false
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: Theme.space("s")) {
            HStack(spacing: Theme.space("s")) {
                Button(bridge.priceOn ? "Tra giá: BẬT" : "Tra giá: TẮT") {
                    Task { await bridge.set(price: !bridge.priceOn) }
                }
                .buttonStyle(CSButtonStyle(kind: .secondary, fullWidth: true))
                Button(bridge.translateOn ? "Dịch: BẬT" : "Dịch: TẮT") {
                    Task { await bridge.set(translate: !bridge.translateOn) }
                }
                .buttonStyle(CSButtonStyle(kind: .secondary, fullWidth: true))
            }
            HStack(spacing: Theme.space("s")) {
                Button("Dịch cả màn hình") {
                    Task { await bridge.set(screen: true) }
                }
                .buttonStyle(CSButtonStyle(kind: .secondary, fullWidth: true))
                Button(bridge.game == "poe2" ? "Game: PoE2" : "Game: PoE1") {
                    Task { await bridge.set(game: bridge.game == "poe2" ? "poe1" : "poe2") }
                }
                .buttonStyle(CSButtonStyle(kind: .secondary, fullWidth: true))
            }
            if !bridge.connected {
                HintText(text: "Chưa nối được máy chủ tra giá: mở PoE TV (cửa sổ Terminal) trước.")
            }
        }
        // Chưa bấm bắt đầu thì không có khung hình nào mang trạng thái về: hỏi máy chủ (không đổi gì) mỗi ~3 giây.
        .onReceive(timer) { _ in
            guard !polling, bridge.secondsSinceUpdate > 3 else { return }
            polling = true
            Task {
                await bridge.set()
                polling = false
            }
        }
    }
}
