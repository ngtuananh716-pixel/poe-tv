# Cập nhật PoE TV đã cài (máy bạn hoặc máy bạn bè): chép mã tra giá + dữ liệu mới và 3 DLL CouchSubs đã sửa.
# Chạy qua "CAP NHAT.cmd". Không cài lại Python, không tải lại CouchSubs, không đụng cài đặt cá nhân.
# Thư mục cài không ghi được (vd E:\Program Files) thì tự xin quyền quản trị một lần và cấp quyền sửa thư mục đó cho tài
# khoản đang dùng, để lần sau cập nhật và máy chủ tra giá (ghi logs, state.json) chạy được bằng quyền thường.
param([string]$Dir = '', [string]$GrantTo = '')
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dllSha = @{
    'CouchSubs.Shell.dll'       = '1F28FFCC0C003D09DE5905B16F5FD24FAF88778D7FC315F96C917C8AFCCB56E7'
    'CouchSubs.Translation.dll' = '64084E4B97A8E11669BA24D15E95EE9B5E03C24E79CD69F95C8E0410934195E2'
    'CouchSubs.dll'             = 'B2B7C773C94FBB1A69A427111561ABE4372278CB72CE0CE661EF3D2C5F9E93D4'
}
function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Fail($text) { Write-Host ''; Write-Host "LỖI: $text" -ForegroundColor Red; Done-Pause; exit 1 }

function Done-Pause { if ($GrantTo) { Write-Host ''; Read-Host 'Nhấn Enter để đóng cửa sổ này' | Out-Null } }

# 1. Tìm thư mục đã cài (có couchsubs\CouchSubs.exe và poe_tv\poe_tv)
if ($Dir) { $dir = $Dir } else {
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
}
if (-not ((Test-Path "$dir\couchsubs\CouchSubs.exe") -and (Test-Path "$dir\poe_tv\poe_tv"))) {
    Fail "$dir không phải thư mục PoE TV (thiếu couchsubs\CouchSubs.exe hoặc poe_tv\poe_tv)."
}
Say "Cập nhật: $dir" Green

# 2. Kiểm gói và quyền ghi trước khi đụng vào máy
Say 'Đang kiểm gói và quyền ghi...'
if ($GrantTo) {
    Say "Cấp quyền sửa thư mục $dir cho tài khoản $GrantTo..."
    & icacls "$dir" /grant "${GrantTo}:(OI)(CI)M" /T /C /Q | Out-Null
}
$probe = Join-Path "$dir\poe_tv" ".ghi-thu-$PID.tmp"
try { Set-Content -Path $probe -Value 'x' -ErrorAction Stop; Remove-Item $probe -ErrorAction SilentlyContinue }
catch {
    if ($GrantTo) { Fail "Đã chạy quyền quản trị mà vẫn không ghi được vào $dir." }
    Say "Thư mục $dir cần quyền quản trị. Windows sẽ hỏi cho phép (UAC): bấm Yes." Yellow
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Dir', "`"$dir`"", '-GrantTo', "`"$me`"")
    try { Start-Process powershell -Verb RunAs -Wait -ArgumentList $args2 }
    catch { Fail 'Chưa được cho phép quyền quản trị nên chưa cập nhật.' }
    Say 'Cửa sổ quản trị đã chạy xong (kết quả hiện trong cửa sổ đó).' Green
    exit 0
}
foreach ($name in $dllSha.Keys) {
    if ((Get-FileHash "$src\couchsubs-dll\$name" -Algorithm SHA256).Hash -ne $dllSha[$name]) { Fail "$name trong gói bị hỏng. Xin gói khác." }
}

# 3. Đóng CouchSubs và cửa sổ máy chủ tra giá
Say 'Đang đóng CouchSubs và máy chủ tra giá...'
Get-Process CouchSubs -ErrorAction SilentlyContinue | Stop-Process -Force
# venv\Scripts\python.exe chỉ là trình mở, máy chủ thật là tiến trình python con: tìm theo dòng lệnh "poe_tv.serve"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -OperationTimeoutSec 15 -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*poe_tv.serve*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# 4. Mã tra giá + dữ liệu (giữ nguyên logs, state.json). /R:2 /W:1: file bị khóa thì thử 2 lần rồi báo, không treo.
Say 'Đang chép mã tra giá và dữ liệu...'
$log = & robocopy "$src\poe_tv" "$dir\poe_tv" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) {
    $log | Select-Object -Last 15 | ForEach-Object { Say $_ Yellow }
    Fail 'Không chép được mã tra giá (xem dòng vàng ở trên). Thử chạy lại bằng Run as administrator.'
}
Say 'Đã chép mã tra giá và dữ liệu PoE1/PoE2.' Green

# 5. 3 DLL CouchSubs (giữ bản cũ đuôi .bak để quay lại nếu cần)
Say 'Đang thay 3 DLL CouchSubs...'
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
Done-Pause
