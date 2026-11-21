# Cập nhật PoE TV đã cài (máy bạn hoặc máy bạn bè): chép mã tra giá + dữ liệu mới và 3 DLL CouchSubs đã sửa.
# Chạy qua "CAP NHAT.cmd". Không cài lại Python, không tải lại CouchSubs, không đụng cài đặt cá nhân.
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dllSha = @{
    'CouchSubs.Shell.dll'       = '1F28FFCC0C003D09DE5905B16F5FD24FAF88778D7FC315F96C917C8AFCCB56E7'
    'CouchSubs.Translation.dll' = '64084E4B97A8E11669BA24D15E95EE9B5E03C24E79CD69F95C8E0410934195E2'
    'CouchSubs.dll'             = 'B2B7C773C94FBB1A69A427111561ABE4372278CB72CE0CE661EF3D2C5F9E93D4'
}
function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Fail($text) { Write-Host ''; Write-Host "LỖI: $text" -ForegroundColor Red; exit 1 }

# 1. Tìm thư mục đã cài (có couchsubs\CouchSubs.exe và poe_tv\poe_tv)
$cands = @('E:\Program Files\poe_tv', "$env:PUBLIC\poe_tv", 'C:\poe_tv', 'D:\poe_tv', 'E:\poe_tv')
$found = @($cands | Where-Object { (Test-Path "$_\couchsubs\CouchSubs.exe") -and (Test-Path "$_\poe_tv\poe_tv") })
if ($found.Count -ge 1) {
    $dir = $found[0]
    if ($found.Count -gt 1) {
        Say "Thấy nhiều chỗ cài: $($found -join ' ; ')" Yellow
        $ans = Read-Host "Cập nhật chỗ nào? Enter = $dir, hoặc gõ đường dẫn"
        if ($ans) { $dir = $ans.Trim().Trim('"').TrimEnd('\') }
    }
} else {
    $dir = (Read-Host 'Không tự tìm thấy chỗ cài PoE TV. Gõ đường dẫn thư mục (ví dụ D:\poe_tv)').Trim().Trim('"').TrimEnd('\')
}
if (-not ((Test-Path "$dir\couchsubs\CouchSubs.exe") -and (Test-Path "$dir\poe_tv\poe_tv"))) {
    Fail "$dir không phải thư mục PoE TV (thiếu couchsubs\CouchSubs.exe hoặc poe_tv\poe_tv)."
}
Say "Cập nhật: $dir" Green

# 2. Kiểm gói trước khi đụng vào máy
foreach ($name in $dllSha.Keys) {
    if ((Get-FileHash "$src\couchsubs-dll\$name" -Algorithm SHA256).Hash -ne $dllSha[$name]) { Fail "$name trong gói bị hỏng. Xin gói khác." }
}

# 3. Đóng CouchSubs và cửa sổ máy chủ tra giá (chỉ python của chính thư mục này)
Get-Process CouchSubs -ErrorAction SilentlyContinue | Stop-Process -Force
# venv\Scripts\python.exe chỉ là trình mở, máy chủ thật là tiến trình python con: tìm theo dòng lệnh "poe_tv.serve"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*poe_tv.serve*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# 4. Mã tra giá + dữ liệu (giữ nguyên logs, state.json)
& robocopy "$src\poe_tv" "$dir\poe_tv" /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail 'Không chép được mã tra giá.' }
Say 'Đã chép mã tra giá và dữ liệu PoE1/PoE2.' Green

# 5. 3 DLL CouchSubs (giữ bản cũ đuôi .bak để quay lại nếu cần)
foreach ($name in $dllSha.Keys) {
    $to = Join-Path "$dir\couchsubs" $name
    if (Test-Path $to) { Copy-Item $to "$to.bak" -Force }
    Copy-Item "$src\couchsubs-dll\$name" $to -Force
    Unblock-File $to
    if ((Get-FileHash $to -Algorithm SHA256).Hash -ne $dllSha[$name]) { Fail "Chép $name không trọn vẹn." }
}
Say 'Đã thay 3 DLL CouchSubs.' Green

Write-Host ''
Say '=== CẬP NHẬT XONG ===' Yellow
Say 'Bấm đúp "PoE TV" ngoài màn hình nền như mọi khi. Trong cửa sổ CouchSubs có thêm nút "Game: PoE1 / PoE2".'
