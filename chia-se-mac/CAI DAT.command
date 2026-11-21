#!/bin/bash
# PoE TV cho Mac: cài (hoặc cập nhật) vào ~/poe_tv. Chạy trong Terminal:  bash "<kéo file này vào>"
# Làm: chép app CouchSubs bản sửa + lõi tra giá, cài Python 3.13 riêng (bằng uv, không cần mật khẩu máy),
# tạo môi trường Python và lối tắt "PoE TV" ngoài màn hình nền. Chạy lại với gói mới để cập nhật.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
D="$HOME/poe_tv"
APP_NAME="CouchSubs PoE TV.app"
say() { printf '\033[%sm%s\033[0m\n' "${2:-0}" "$1"; }
fail() { say "LỖI: $1" 31; exit 1; }

say "PoE TV - cài đặt cho Mac" 33
MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
[ "$MAJOR" -ge 13 ] || fail "Cần macOS 13 (Ventura) trở lên, máy đang là $(sw_vers -productVersion)."
[ -d "$SRC/$APP_NAME" ] || fail "Không thấy \"$APP_NAME\" cạnh file này. Giải nén đủ gói rồi chạy lại."

say "== 1/5 Đóng PoE TV đang chạy" 36
pkill -f "poe_tv.serve" 2>/dev/null || true
pkill -x CouchSubs 2>/dev/null || true
sleep 1

say "== 2/5 Chép app và lõi tra giá vào $D" 36
mkdir -p "$D"
rm -rf "$D/$APP_NAME.moi"
ditto "$SRC/$APP_NAME" "$D/$APP_NAME.moi"
# app tải từ mạng bị macOS đánh dấu "cách ly"; bỏ dấu để mở được app chưa có chữ ký của Apple
xattr -dr com.apple.quarantine "$D/$APP_NAME.moi" 2>/dev/null || true
if [ -d "$D/$APP_NAME" ]; then mv "$D/$APP_NAME" "$D/$APP_NAME.cu"; fi
mv "$D/$APP_NAME.moi" "$D/$APP_NAME"
rm -rf "$D/$APP_NAME.cu"
mkdir -p "$D/poe_tv"
rsync -a --exclude logs --exclude state.json --exclude __pycache__ "$SRC/tep/poe_tv/" "$D/poe_tv/"

say "== 3/5 Python 3.13 (uv)" 36
UV="$(command -v uv || true)"
[ -n "$UV" ] || { [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"; } || true
if [ -z "$UV" ]; then
  say "Chưa có uv, đang cài từ astral.sh (công cụ quản lý Python, không cần mật khẩu máy)..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh || fail "Không cài được uv. Kiểm tra mạng rồi chạy lại."
  UV="$HOME/.local/bin/uv"
fi
[ -x "$D/venv/bin/python" ] || "$UV" venv --python 3.13 "$D/venv" || fail "Không tạo được môi trường Python 3.13."

say "== 4/5 Thư viện OCR (vài phút lần đầu)" 36
"$UV" pip install --python "$D/venv/bin/python" -r "$D/poe_tv/requirements.txt" || fail "Cài thư viện Python thất bại."
"$D/venv/bin/python" -c "import rapidocr_onnxruntime, cv2, numpy, rapidfuzz, requests" || fail "Thư viện cài xong nhưng không nạp được."

say "== 5/5 Lối tắt \"PoE TV\" ngoài màn hình nền" 36
cat > "$D/Chay PoE TV.command" <<'EOF'
#!/bin/bash
# PoE TV: máy chủ tra giá (cửa sổ này, đừng đóng) + CouchSubs bản sửa
D="$HOME/poe_tv"
pkill -f "poe_tv.serve" 2>/dev/null
pkill -x CouchSubs 2>/dev/null
sleep 2
cd "$D/poe_tv" || exit 1
"$D/venv/bin/python" -m poe_tv.serve &
SERVER=$!
sleep 5
open "$D/CouchSubs PoE TV.app"
wait $SERVER
EOF
chmod +x "$D/Chay PoE TV.command"
cp "$D/Chay PoE TV.command" "$HOME/Desktop/PoE TV.command"
chmod +x "$HOME/Desktop/PoE TV.command"

echo
say "=== CÀI XONG ===" 33
say "Tiếp theo (xem thêm HUONG DAN.txt):"
say " 1. Bấm đúp \"PoE TV\" ngoài màn hình nền. Cửa sổ Terminal là máy chủ tra giá: để nguyên, đừng đóng."
say " 2. macOS hỏi cho CouchSubs tìm thiết bị trong mạng nội bộ (Local Network): bấm Allow."
say " 3. Làm theo trợ lý thiết lập trong CouchSubs: đăng nhập PSN PHỤ, ghép PS5, cài app lên TV LG."
say " 4. Muốn dịch: CouchSubs > Settings > Translation, chọn Gemini, dán khóa Gemini của chính bạn."
