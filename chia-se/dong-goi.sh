#!/usr/bin/env bash
# Đóng 2 gói từ kho này + 3 DLL trên nhánh dist:
#   poe-tv-cho-ban.zip  : cài mới trên máy khác (CAI DAT.cmd)
#   poe-tv-capnhat.zip  : cập nhật máy đã cài (CAP NHAT.cmd)
# Dùng: chia-se/dong-goi.sh <thư mục ra>
set -euo pipefail
OUT="${1:?thư mục ra}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git -C "$ROOT" fetch -q origin +refs/heads/dist:refs/remotes/origin/dist

stage() {   # $1 = thư mục gói
  local B="$1"
  mkdir -p "$B/tep/couchsubs-dll" "$B/tep/poe_tv" "$B/nguon-ma"
  for f in CouchSubs.Shell.dll CouchSubs.Translation.dll CouchSubs.dll; do
    git -C "$ROOT" show "origin/dist:$f" > "$B/tep/couchsubs-dll/$f"
  done
  cp -r "$ROOT/poe_tv/poe_tv" "$ROOT/poe_tv/data" "$ROOT/poe_tv/requirements.txt" "$B/tep/poe_tv/"
  find "$B" -name __pycache__ -prune -exec rm -rf {} +
  cp -r "$ROOT/couchsubs-patch" "$B/nguon-ma/"
  cp "$ROOT/.github/workflows/build.yml" "$B/nguon-ma/build.yml"
  cp "$ROOT/LICENSE" "$B/nguon-ma/LICENSE"
  cp "$ROOT/chia-se/NGUON-MA.txt" "$B/nguon-ma/README.txt"
  cp "$ROOT/chia-se/HUONG DAN.txt" "$B/"
}

B="$WORK/poe-tv-ban"; stage "$B"
cp "$ROOT/chia-se/CAI DAT.cmd" "$B/"
cp "$ROOT/chia-se/cai-dat.ps1" "$ROOT/chia-se/Chay PoE TV.cmd" "$B/tep/"

C="$WORK/poe-tv-capnhat"; stage "$C"
cp "$ROOT/chia-se/CAP NHAT.cmd" "$C/"
cp "$ROOT/chia-se/cap-nhat.ps1" "$C/tep/"

# mã băm DLL ghi trong 2 script phải khớp DLL thật
for f in CouchSubs.Shell.dll CouchSubs.Translation.dll CouchSubs.dll; do
  h="$(sha256sum "$B/tep/couchsubs-dll/$f" | cut -c1-64 | tr a-f A-F)"
  for s in "$ROOT/chia-se/cai-dat.ps1" "$ROOT/chia-se/cap-nhat.ps1"; do
    grep -q "$h" "$s" || { echo "Mã băm $f không khớp trong $(basename "$s")" >&2; exit 1; }
  done
done

mkdir -p "$OUT"
(cd "$WORK" && python3 - "$OUT" <<'EOF'
import os, sys, zipfile
out = sys.argv[1]
for name in ("poe-tv-ban", "poe-tv-capnhat"):
    zname = os.path.join(out, "poe-tv-cho-ban.zip" if name == "poe-tv-ban" else "poe-tv-capnhat.zip")
    with zipfile.ZipFile(zname, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(name):
            dirs.sort()
            for f in sorted(files):
                z.write(os.path.join(root, f))
    print(zname, os.path.getsize(zname))
EOF
)
