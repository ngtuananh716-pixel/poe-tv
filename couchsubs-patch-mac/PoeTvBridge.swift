// SPDX-License-Identifier: AGPL-3.0-only
// poe-tv (bản sửa, Mac): gửi khung hình Remote Play tới máy chủ tra giá cục bộ (python -m poe_tv.serve,
// http://127.0.0.1:8788/frame), nhận ô giá ("regions") rồi đưa sang TV bằng SubtitleServer.sendRegion. Cùng giao thức với
// bản Windows (windows/src/CouchSubs.Shell/PoeTvBridge.cs): thân là ảnh BGRA thô, kèm X-Width/X-Height/X-Stride; phản hồi
// {"regions":[{id,text,box,line}],"next_ms","price","translate","screen","game"}. Dịch đang tắt thì phần đọc phụ đề không
// chạy. 4 nút trong cửa sổ chính (PoeTvButtons) gọi set(...). POE_TV=0 thì chạy như CouchSubs gốc.

import CoreGraphics
import CouchSubsCore
import Foundation
import RemotePlay

@MainActor
final class PoeTvBridge: ObservableObject {
    nonisolated static let enabled = ProcessInfo.processInfo.environment["POE_TV"] != "0"
    nonisolated static let base: URL = {
        let raw = ProcessInfo.processInfo.environment["POE_TV_URL"] ?? "http://127.0.0.1:8788"
        return URL(string: raw) ?? URL(string: "http://127.0.0.1:8788")!
    }()
    private static let retryMs: UInt64 = 3000

    @Published private(set) var priceOn = true
    @Published private(set) var translateOn = false {
        // Tắt dịch: chỉ tra giá, 4 ảnh/giây là đủ (máy chủ hỏi khung 0,3–1,5 giây một lần); bật dịch: như gốc (20).
        didSet { PoeTvFrameRate.maxPerSecond = translateOn || !Self.enabled ? 20 : 4 }
    }
    @Published private(set) var game = "poe1"
    @Published private(set) var connected = false

    private var busy = false
    private var nextAt: UInt64 = 0
    private var screenRequests = 0
    private var updatedAt = Date.distantPast
    private var announced = false
    private weak var lastServer: SubtitleServer?
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.connectionProxyDictionary = [:]          // 127.0.0.1: không đi qua proxy hệ thống
        return URLSession(configuration: config)
    }()

    init() {
        PoeTvFrameRate.maxPerSecond = Self.enabled ? 4 : 20
    }

    /// Bao lâu (giây) chưa nhận trạng thái từ máy chủ; nút trong cửa sổ hỏi lại khi lâu không có khung hình.
    var secondsSinceUpdate: TimeInterval { Date().timeIntervalSince(updatedAt) }

    /// Máy chủ vừa yêu cầu "dịch cả màn hình" chưa (mỗi yêu cầu chỉ trả true một lần).
    func takeScreenRequest() -> Bool {
        defer { screenRequests = 0 }
        return screenRequests > 0
    }

    /// Gọi với mỗi khung hình. Trả true khi CouchSubs nên bỏ qua phần đọc phụ đề (dịch đang tắt).
    /// Không chặn luồng khung hình: mỗi lúc chỉ một yêu cầu, khung đến khi đang bận thì bỏ qua.
    func offer(_ frame: CGImage, server: SubtitleServer) -> Bool {
        guard Self.enabled else { return false }
        if !announced {
            announced = true
            SubtitleLog.write("poe-tv: chế độ tra giá PoE bật, gửi khung hình tới \(Self.base.absoluteString)/frame")
        }
        let now = DispatchTime.now().uptimeNanoseconds / 1_000_000
        if !busy, now >= nextAt, frame.width > 0, frame.height > 0 {
            busy = true
            lastServer = server
            Task { await self.send(frame, server: server) }
        }
        return !translateOn
    }

    /// Nút trong cửa sổ: đổi trạng thái trên máy chủ rồi nhận lại trạng thái mới; không kèm gì thì chỉ hỏi trạng thái.
    func set(price: Bool? = nil, translate: Bool? = nil, screen: Bool = false, game: String? = nil) async {
        var items = [URLQueryItem(name: "json", value: "1")]
        if let price { items.append(URLQueryItem(name: "price", value: price ? "1" : "0")) }
        if let translate { items.append(URLQueryItem(name: "translate", value: translate ? "1" : "0")) }
        if screen { items.append(URLQueryItem(name: "screen", value: "1")) }
        if let game, game == "poe1" || game == "poe2" { items.append(URLQueryItem(name: "game", value: game)) }
        var components = URLComponents(url: Self.base.appendingPathComponent("set"), resolvingAgainstBaseURL: false)
        components?.queryItems = items
        guard let url = components?.url else { return }
        do {
            let (data, _) = try await session.data(from: url)
            if let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] { apply(flags: root) }
            connected = true
        } catch {
            connected = false
            if price != nil || translate != nil || screen || game != nil {
                SubtitleLog.write("poe-tv: không đổi được trạng thái (\(error.localizedDescription))")
            }
        }
    }

    private func apply(flags root: [String: Any]) {
        if let price = root["price"] as? Bool { priceOn = price }
        if let translate = root["translate"] as? Bool {
            if translateOn && !translate { lastServer?.clear() }      // tắt dịch: xóa phụ đề đang hiện trên TV
            if translateOn != translate { SubtitleLog.write("poe-tv: dịch phụ đề \(translate ? "bật" : "tắt")") }
            translateOn = translate
        }
        if root["screen"] as? Bool == true { screenRequests += 1 }
        if let game = root["game"] as? String, game == "poe1" || game == "poe2" { self.game = game }
        updatedAt = Date()
    }

    private func send(_ frame: CGImage, server: SubtitleServer) async {
        defer { busy = false }
        let now = { DispatchTime.now().uptimeNanoseconds / 1_000_000 }
        guard let pixels = await Task.detached(priority: .utility, operation: { Self.bgra(frame) }).value else {
            nextAt = now() + Self.retryMs
            return
        }
        var request = URLRequest(url: Self.base.appendingPathComponent("frame"))
        request.httpMethod = "POST"
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue(String(pixels.width), forHTTPHeaderField: "X-Width")
        request.setValue(String(pixels.height), forHTTPHeaderField: "X-Height")
        request.setValue(String(pixels.stride), forHTTPHeaderField: "X-Stride")
        do {
            let (data, _) = try await session.upload(for: request, from: pixels.data)
            guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw URLError(.cannotParseResponse) }
            if !connected { SubtitleLog.write("poe-tv: đã nối máy chủ tra giá") }
            connected = true
            apply(flags: root)
            for region in root["regions"] as? [[String: Any]] ?? [] {
                guard let id = region["id"] as? String else { continue }
                let text = region["text"] as? String ?? ""
                var box: TextBox?
                if let b = region["box"] as? [Double], b.count == 4 {
                    box = TextBox(left: b[0], top: b[1], right: b[2], bottom: b[3])
                }
                server.sendRegion(id: id, text: text, box: box, lineHeight: region["line"] as? Double)
            }
            let wait = UInt64(min(10_000, max(50, (root["next_ms"] as? Double) ?? 300)))
            nextAt = now() + wait
        } catch {
            if connected { SubtitleLog.write("poe-tv: mất nối máy chủ tra giá (\(error.localizedDescription))") }
            connected = false
            nextAt = now() + Self.retryMs
        }
    }

    /// CGImage → điểm ảnh BGRA (hàng trên cùng trước), như khung hình bản Windows gửi đi.
    nonisolated private static func bgra(_ image: CGImage) -> (data: Data, width: Int, height: Int, stride: Int)? {
        let width = image.width, height = image.height, stride = width * 4
        var data = Data(count: stride * height)
        let ok = data.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: stride, space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
            else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        return ok ? (data: data, width: width, height: height, stride: stride) : nil
    }
}
