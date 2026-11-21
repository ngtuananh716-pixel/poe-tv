#!/bin/bash
# Tải bản PoE TV cho Mac mới nhất từ GitHub Releases rồi cài (lần đầu hoặc cập nhật, kể cả khi app đổi). Dán vào Terminal:
#   curl -fsSL https://raw.githubusercontent.com/ngtuananh716-pixel/poe-tv/main/chia-se-mac/cai-moi.sh | bash
set -euo pipefail
BASE="https://github.com/ngtuananh716-pixel/poe-tv/releases/latest/download"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Đang tải bản Mac mới nhất (khoảng 80 MB)..."
curl -fL --progress-bar -o "$TMP/poe-tv-mac.zip" "$BASE/poe-tv-mac.zip"
curl -fsSL -o "$TMP/poe-tv-mac.zip.sha256" "$BASE/poe-tv-mac.zip.sha256"
WANT="$(cut -d' ' -f1 "$TMP/poe-tv-mac.zip.sha256")"
GOT="$(shasum -a 256 "$TMP/poe-tv-mac.zip" | cut -d' ' -f1)"
if [ "$WANT" != "$GOT" ]; then
  echo "LỖI: file tải về bị hỏng (sai mã băm). Chạy lại lệnh."
  exit 1
fi
echo "Đang giải nén..."
ditto -x -k "$TMP/poe-tv-mac.zip" "$TMP"
bash "$TMP/poe-tv-mac/CAI DAT.command"
