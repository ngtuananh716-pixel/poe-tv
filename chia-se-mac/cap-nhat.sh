#!/bin/bash
# Cập nhật nhanh lõi tra giá PoE TV trên Mac (không tải lại app ~80 MB). Dán vào Terminal:
#   curl -fsSL https://raw.githubusercontent.com/ngtuananh716-pixel/poe-tv/main/chia-se-mac/cap-nhat.sh | bash
# Chỉ thay mã tra giá + dữ liệu trong ~/poe_tv/poe_tv (giữ logs, lựa chọn game). Khi app CouchSubs PoE TV có bản mới
# thì vẫn phải cài lại bằng gói poe-tv-mac.zip (CAI DAT.command).
set -euo pipefail
D="$HOME/poe_tv"
REPO="https://github.com/ngtuananh716-pixel/poe-tv/archive/refs/heads/main.tar.gz"
if [ ! -d "$D/poe_tv/poe_tv" ] || [ ! -x "$D/venv/bin/python" ]; then
  echo "Chưa cài PoE TV ở $D. Cài lần đầu bằng gói poe-tv-mac.zip (CAI DAT.command)."
  exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Đang tải lõi tra giá mới từ GitHub..."
curl -fsSL "$REPO" | tar -xz -C "$TMP"
SRC="$TMP/poe-tv-main/poe_tv"
[ -d "$SRC/poe_tv" ] || { echo "Gói tải về thiếu thư mục poe_tv."; exit 1; }
pkill -f "poe_tv.serve" 2>/dev/null || true
rsync -a --exclude logs --exclude state.json --exclude __pycache__ --exclude mau --exclude HUONG_DAN.md "$SRC/" "$D/poe_tv/"
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -n "$UV" ]; then
  "$UV" pip install -q --python "$D/venv/bin/python" -r "$D/poe_tv/requirements.txt"
fi
(cd "$D/poe_tv" && "$D/venv/bin/python" -c "import poe_tv.serve") || { echo "Mã mới không nạp được, báo lại Claude."; exit 1; }
echo "Xong. Bấm đúp \"PoE TV\" ngoài màn hình nền để mở lại."
