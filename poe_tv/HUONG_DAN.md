# poe_tv — Tra giá món đồ PoE1 (realm Sony) từ ảnh chụp màn hình

Bước 1 của công cụ tra giá trên TV. Hiện tại công cụ nhận **ảnh chụp màn hình** (ví dụ ảnh chụp từ PXPlay) rồi:

1. Tự tìm khung tooltip món đồ trong ảnh.
2. Đọc chữ bằng OCR (RapidOCR, chạy trên máy, không gửi ảnh đi đâu).
3. Nhận diện món đồ: loại (unique/rare/gem…), base, thuộc tính, mod implicit/explicit.
4. Dựng bộ lọc và tra trên trade **realm Sony**, league hiện tại, mặc định chỉ tính hàng **mua ngay**.

Từ 21/11/2026 có thêm chế độ **hiện giá thẳng trên TV LG** qua bản sửa CouchSubs, xem mục cuối.

## Cài đặt trên laptop (Windows 11, làm 1 lần)

1. Cài Python 3.12 hoặc 3.13 từ https://www.python.org/downloads/ . Lúc cài **tick ô "Add python.exe to PATH"**.
2. Giải nén `poe_tv.zip` vào một thư mục, ví dụ `D:\poe_tv`.
3. Mở PowerShell tại thư mục đó (trong File Explorer: gõ `powershell` vào thanh địa chỉ rồi Enter), chạy:

```
py -m pip install -r requirements.txt
```

Bộ OCR đã kèm sẵn model trong gói cài, không phải tải thêm.

## Chạy thử

Thư mục `mau\` có sẵn 3 ảnh bạn đã chụp (Wildslash, Vortex Call, Foe Thread):

```
py -m poe_tv mau\wildslash.jpg mau\vortex_call.jpg mau\foe_thread.jpg
```

Với ảnh của bạn: `py -m poe_tv "đường\dẫn\ảnh.jpg"`. Chụp lúc tooltip đang mở (game tự dừng nên ảnh rõ).

Các tùy chọn:

| Tùy chọn | Ý nghĩa |
|---|---|
| `--no-trade` | Chỉ đọc món đồ, không tra giá (để kiểm tra OCR) |
| `--status securable` | Mặc định: chỉ tính hàng **mua ngay**. Khác: `available`, `online`, `any` (tính cả hàng cũ, người bán không online) |
| `--league Covens` | Ép tên league; mặc định tự lấy league trade mới nhất của realm Sony |
| `--json` | In kết quả dạng JSON (để nối với app TV sau này) |

## Đọc kết quả

- `=> 36 món đang bán | rẻ nhất 1 alch | phổ biến 1 chaos (5/10 món rẻ nhất)`: số món tương tự, giá rẻ nhất, mức giá hay gặp nhất trong 10 món rẻ nhất.
- Dòng mod có `(?)`: OCR đọc chưa chắc (thường do chữ nền đè lên), **không** được dùng để lọc.
- `! Món không có máu…`: đồ phòng thủ/trang sức không có máu. Công cụ chỉ tìm được món "ít nhất bằng của bạn", nên giá thật thường **thấp hơn** con số tìm được.
- Đồ unique tra theo tên: chính xác nhất. Vũ khí rare tra theo DPS. Đồ rare phòng thủ chỉ để tham khảo.

## Giới hạn cần biết

- **Trade giới hạn tốc độ** (đọc từ API ngày 21/11/2026): quá 5 lần tra/10 giây bị khóa 1 phút; quá 30 lần/5 phút bị khóa **30 phút**. Công cụ tự đếm và nhớ kết quả trong 10 phút để không tra lại cùng một món. Nếu bị giới hạn, nó báo "thử lại sau N giây" chứ không treo.
- Mỗi lần tra mất khoảng 3–6 giây, chủ yếu là OCR. Sẽ tối ưu ở bước 2.
- Hình Remote Play nên để **1080p**; ở 720p chữ tooltip nhỏ, OCR dễ sai.

## Nếu tra giá báo lỗi 403

Trang trade có lớp chống bot (Cloudflare). Công cụ gọi API với tên công cụ rõ ràng, **không giả dạng trình duyệt và không cố vượt qua lớp bảo vệ**. Nếu bị từ chối, công cụ in ra một link mở trang trade với bộ lọc đã điền sẵn để bạn xem bằng trình duyệt. Báo lại cho Claude để tính cách khác.

## Ghi công

Dữ liệu mod/món đồ (`data/stats.ndjson`, `data/items.ndjson`) và cách chọn mod local/global, chỉ số tổng, dải ±10% lấy từ **Awakened PoE Trade** của SnosMe, giấy phép MIT (`data/LICENSE-awakened-poe-trade.txt`). Khi sang league mới, cập nhật 2 file này từ bản APT mới nhất.

## Chạy trên TV (bản sửa CouchSubs, từ 21/11/2026)

Thư mục trên laptop: `C:\Users\Public\poe_tv` gồm `poe_tv\` (lõi tra giá), `venv\` (Python), `couchsubs\` (bản sao
CouchSubs beta.70 đã thay `CouchSubs.Shell.dll`) và `Chay PoE TV.cmd`.

1. Bật PS5, bật app CouchSubs trên TV LG.
2. Trên laptop bấm đúp **PoE TV** ngoài màn hình nền. Nó đóng CouchSubs bản thường nếu đang mở, mở cửa sổ máy chủ tra giá
   (để nguyên, đừng tắt), rồi mở CouchSubs bản sửa. Trong CouchSubs bấm bắt đầu như mọi khi.
3. Trong game, mở tooltip món đồ và đứng yên khoảng 1 giây: cạnh tooltip hiện ô "Đang tra giá…", vài giây sau ra giá.
   Đóng tooltip hoặc chuyển món thì ô tự tắt.

- Cửa sổ máy chủ in từng lần tra. Ảnh và kết quả 30 lần tra gần nhất lưu ở `poe_tv\logs\` để Claude xem khi tra sai.
- Lần đầu chạy bản sửa, Windows/CouchSubs có thể hỏi quyền mở tường lửa cho mạng riêng (Private): bấm cho phép,
  không thì TV không nhận được chữ.
- Bản sửa **không tự cập nhật** và không đụng tới CouchSubs bản thường. Muốn dùng CouchSubs dịch phụ đề như cũ thì
  mở bản thường. Không mở cả hai cùng lúc.
- Bản sao chỉ chạy đúng với CouchSubs **beta.70**. Khi cài lại từ bản mới hơn, báo Claude build lại DLL cho khớp.
