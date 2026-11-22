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

`chia-se/`: bộ cài cho máy khác (gói `poe-tv-cho-ban.zip`). Bộ cài tải CouchSubs gốc beta.70 từ GitHub (kiểm SHA-256),
thay 3 DLL, cài Python 3.13 + thư viện, tạo lối tắt. Cấu trúc gói: `CAI DAT.cmd`, `HUONG DAN.txt`,
`tep/{cai-dat.ps1, Chay PoE TV.cmd, couchsubs-dll/, poe_tv/}`, `nguon-ma/`.

**Bản Mac** (thử nghiệm): `couchsubs-patch-mac/` (cầu nối Swift, 4 nút, dịch tiếng Việt, tắt tự cập nhật) +
`.github/workflows/build-mac.yml` (macOS runner, ký ad-hoc, ghép gói TV/trợ giúp từ bản Mac chính thức cùng phiên bản,
đăng Release `poe-tv-mac.zip`). Bộ cài Mac: `chia-se-mac/` (Python 3.13 qua uv, cài vào `~/poe_tv`).

**Bản Android** (thử nghiệm, chỉ tra giá): `couchsubs-patch-android/` (lõi tra giá viết lại bằng Kotlin, OCR ML Kit,
chạy ngay trên điện thoại, không cần laptop) + `.github/workflows/build-android.yml` (đăng Release tag `android`).
Cài: mở https://github.com/ngtuananh716-pixel/poe-tv/releases/download/android/poe-tv-android.apk trên điện thoại;
hướng dẫn ở `chia-se-android/HUONG DAN.txt`.
