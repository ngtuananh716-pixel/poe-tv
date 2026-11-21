# poe-tv

Tra giá món đồ Path of Exile (PS5, realm Sony) và hiện ô giá thẳng trên TV LG, cạnh tooltip.

Chuỗi chạy: PS5 → HDMI → TV LG. Laptop chạy CouchSubs (bản sửa) nối Remote Play với PS5 bằng tài khoản PSN phụ,
gửi khung hình sang `poe_tv` (Python, chạy trên chính laptop). `poe_tv` tìm tooltip, đọc món, tra giá trên trade
realm Sony rồi trả ô giá; CouchSubs chuyển ô đó sang app CouchSubs trên TV.

| Thư mục | Nội dung |
|---|---|
| `poe_tv/` | Lõi tra giá (OCR RapidOCR, dữ liệu mod của Awakened PoE Trade, trade API). Máy chủ: `python -m poe_tv.serve` |
| `couchsubs-patch/` | Bản vá cho CouchSubs v0.1.0-beta.70: `PoeTvBridge.cs` + chỗ móc trong `AppController.cs` |
| `.github/workflows/build.yml` | GitHub Actions build `CouchSubs.Shell.dll` đã vá |

Giấy phép: AGPL-3.0 (theo CouchSubs). Dữ liệu `poe_tv/data` lấy từ Awakened PoE Trade (MIT).
