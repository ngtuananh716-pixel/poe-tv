# Cài PoE TV (tra giá PoE1 + dịch tiếng Việt hiện trên TV) cho máy mới.
# Chạy qua "CAI DAT.cmd". Chạy lại với gói mới để cập nhật (chọn đúng thư mục cũ).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$src = $PSScriptRoot
$csVersion = '0.1.0-beta.70'
$csUrl = "https://github.com/yorulmazsinan/couchsubs/releases/download/v$csVersion/CouchSubsApp-$csVersion-full.nupkg"
$csSha = '1CB67BC41CBD323ECAA4E4CABF2A0287D1D1725E88043860964273A7E4B21CAC'
$dllSha = @{
    'CouchSubs.Shell.dll'       = '3F49623BB1236E35F951D4CC1FEBCEB60D5A3BBCF8E9E7124B7CCA5D1BBA1C04'
    'CouchSubs.Translation.dll' = '64084E4B97A8E11669BA24D15E95EE9B5E03C24E79CD69F95C8E0410934195E2'
    'CouchSubs.dll'             = 'E3A3D554DC3D177CDCE25060C0F8D7C9EAAD75470D79B8827B3891517A842981'
}

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Step($text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ''; Write-Host "LỖI: $text" -ForegroundColor Red; exit 1 }

Say 'PoE TV - cài đặt' Yellow
Say 'Cần khoảng 1,5 GB trống và mạng ổn định (tải khoảng 450 MB).'

# 1. Thư mục cài -----------------------------------------------------------
Step '1/6 Chọn thư mục cài'
$default = 'C:\poe_tv'
$dir = Read-Host "Cài vào đâu? Nhấn Enter để dùng $default, hoặc gõ đường dẫn khác (không dấu)"
if (-not $dir) { $dir = $default }
$dir = $dir.Trim().Trim('"').TrimEnd('\')
if ($dir -notmatch '^[A-Za-z]:\\') { Fail "Đường dẫn phải dạng ổ đĩa, ví dụ D:\poe_tv" }
if ($dir -match '[^\x20-\x7E]') { Fail 'Đường dẫn có dấu tiếng Việt hoặc ký tự lạ. Chọn thư mục không dấu, ví dụ D:\poe_tv' }
New-Item -ItemType Directory -Force $dir | Out-Null
$csDir = Join-Path $dir 'couchsubs'
$marker = Join-Path $csDir 'poe-tv-goc.txt'
if ((Test-Path $csDir) -and -not (Test-Path $marker)) {
    Fail "$csDir đã có sẵn và không phải do bộ cài này tạo. Chọn thư mục khác."
}
Say "Thư mục: $dir" Green

# 2. Python 3.13 ------------------------------------------------------------
Step '2/6 Python'
function Find-Python {
    foreach ($v in '3.13', '3.12') {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { return ([string]$p).Trim() }
        } catch { }
    }
    foreach ($p in "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                   "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                   "$env:ProgramFiles\Python313\python.exe",
                   "$env:ProgramFiles\Python312\python.exe") {
        if (Test-Path $p) { return $p }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say 'Chưa có Python 3.13, đang cài bằng winget...'
        & winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
        $py = Find-Python
    }
    if (-not $py) {
        Fail 'Không cài được Python. Tải bản 3.13 ở https://www.python.org/downloads/ , lúc cài tick "Add python.exe to PATH", rồi chạy lại CAI DAT.'
    }
}
Say "Python: $py" Green

# 3. Lõi tra giá + thư viện -------------------------------------------------
Step '3/6 Lõi tra giá và thư viện OCR (vài phút)'
& robocopy "$src\poe_tv" "$dir\poe_tv" /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail 'Không chép được thư mục poe_tv.' }
$venvPy = Join-Path $dir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    & $py -m venv (Join-Path $dir 'venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail 'Không tạo được môi trường Python (venv).' }
}
& $venvPy -m pip install --disable-pip-version-check -r "$dir\poe_tv\requirements.txt"
if ($LASTEXITCODE -ne 0) { Fail 'Cài thư viện Python thất bại (xem dòng lỗi ở trên).' }
& $venvPy -c "import rapidocr_onnxruntime, cv2, numpy, rapidfuzz, requests"
if ($LASTEXITCODE -ne 0) { Fail 'Thư viện Python cài xong nhưng không nạp được.' }
Say 'Xong lõi tra giá.' Green

# 4. CouchSubs beta.70 (gốc) + 3 DLL đã sửa ---------------------------------
Step "4/6 CouchSubs $csVersion"
Get-Process CouchSubs -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800
if (-not (Test-Path $marker)) {
    $pkg = Join-Path $dir "CouchSubsApp-$csVersion-full.nupkg"
    $ok = (Test-Path $pkg) -and ((Get-FileHash $pkg -Algorithm SHA256).Hash -eq $csSha)
    if (-not $ok) {
        Say 'Đang tải CouchSubs gốc từ GitHub (khoảng 220 MB)...'
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe -L --fail --retry 3 -o $pkg $csUrl
            if ($LASTEXITCODE -ne 0) { Fail 'Tải CouchSubs thất bại. Kiểm tra mạng rồi chạy lại.' }
        } else {
            Invoke-WebRequest -UseBasicParsing -Uri $csUrl -OutFile $pkg
        }
        if ((Get-FileHash $pkg -Algorithm SHA256).Hash -ne $csSha) { Fail 'File CouchSubs tải về bị hỏng (sai mã băm). Chạy lại CAI DAT.' }
    }
    Say 'Đang giải nén...'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $tmpDir = "$csDir.tmp"
    if (Test-Path $tmpDir) { Fail "Còn thư mục dở dang $tmpDir từ lần cài trước. Xóa nó rồi chạy lại." }
    $zip = [IO.Compression.ZipFile]::OpenRead($pkg)
    try {
        foreach ($e in $zip.Entries) {
            if (-not $e.FullName.StartsWith('lib/app/') -or $e.FullName.EndsWith('/')) { continue }
            $rel = [Uri]::UnescapeDataString($e.FullName.Substring(8)).Replace('/', '\')
            if ($rel -match '(^|\\)\.\.(\\|$)') { continue }
            $out = Join-Path $tmpDir $rel
            New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $out, $true)
        }
    } finally { $zip.Dispose() }
    if (-not (Test-Path "$tmpDir\CouchSubs.exe")) { Fail 'Giải nén xong nhưng không thấy CouchSubs.exe.' }
    Set-Content -Path "$tmpDir\poe-tv-goc.txt" -Value "CouchSubs $csVersion (goc tu GitHub) + 3 DLL poe-tv" -Encoding ASCII
    Rename-Item $tmpDir 'couchsubs'
    Remove-Item $pkg
}
foreach ($name in $dllSha.Keys) {
    $from = Join-Path "$src\couchsubs-dll" $name
    if ((Get-FileHash $from -Algorithm SHA256).Hash -ne $dllSha[$name]) { Fail "$name trong gói bị hỏng. Xin gói khác." }
    Copy-Item $from (Join-Path $csDir $name) -Force
    Unblock-File (Join-Path $csDir $name)
}
Say 'Xong CouchSubs (bản sửa: tra giá + dịch tiếng Việt).' Green

# 5. WebView2 (cửa sổ đăng nhập PlayStation cần) -----------------------------
Step '5/6 WebView2'
function Has-WebView2 {
    $id = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($k in "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$id",
                   "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id",
                   "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id") {
        $pv = (Get-ItemProperty $k -ErrorAction SilentlyContinue).pv
        if ($pv -and $pv -ne '0.0.0.0') { return $true }
    }
    return $false
}
if (Has-WebView2) {
    Say 'Đã có sẵn.' Green
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    & winget install -e --id Microsoft.EdgeWebView2Runtime --silent --accept-package-agreements --accept-source-agreements
    if (Has-WebView2) { Say 'Đã cài.' Green } else { Say 'Chưa cài được WebView2. Nếu cửa sổ đăng nhập PlayStation trắng trơn, tải ở https://go.microsoft.com/fwlink/p/?LinkId=2124703' Yellow }
} else {
    Say 'Thiếu WebView2. Tải ở https://go.microsoft.com/fwlink/p/?LinkId=2124703 rồi cài.' Yellow
}

# 6. Lệnh chạy + lối tắt ngoài màn hình nền ---------------------------------
Step '6/6 Lối tắt "PoE TV" ngoài màn hình nền'
Copy-Item "$src\Chay PoE TV.cmd" $dir -Force
# Tạo lối tắt trong thư mục không dấu rồi mới chuyển ra màn hình nền: WScript.Shell lỗi với đường dẫn có dấu.
$lnkTmp = Join-Path $dir 'PoE TV.lnk'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkTmp)
$lnk.TargetPath = Join-Path $dir 'Chay PoE TV.cmd'
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = "$csDir\CouchSubs.exe,0"
$lnk.Save()
$desktop = [Environment]::GetFolderPath('Desktop')
Move-Item $lnkTmp (Join-Path $desktop 'PoE TV.lnk') -Force
Say 'Đã tạo.' Green

Write-Host ''
Say '=== CÀI XONG ===' Yellow
Say 'Tiếp theo (xem thêm HUONG DAN.txt):'
Say ' 1. Bấm đúp "PoE TV" ngoài màn hình nền. Windows hỏi tường lửa: cho phép mạng Private.'
Say ' 2. Làm theo hướng dẫn trong CouchSubs: đăng nhập tài khoản PSN phụ, ghép PS5, cài app lên TV LG.'
Say ' 3. Muốn dịch: Settings > Translation > Gemini, dán khóa Gemini của chính bạn.'
