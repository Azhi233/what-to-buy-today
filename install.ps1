<#
============================================================
  闲鱼价格监控 - 一键安装脚本（Windows 系统级服务）
============================================================
功能：
  1. 检测 Python 3.10+（缺失时提示，可用 winget 安装）
  2. 创建独立虚拟环境 .venv（不污染系统 Python）
  3. 安装运行依赖 + Playwright Chromium 浏览器内核
  4. 注册 Windows 任务计划程序（开机自启，失败自动重启）
  5. 引导首次扫码登录，登录后服务即可正常监控
  6. 输出仪表盘地址

用法（右键"以管理员身份运行"）：
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoElevate   # 已提权环境（安装包调用）

卸载：  .\uninstall.ps1
============================================================
#>

param(
    [switch]$NoElevate   # 已处于管理员上下文（Inno Setup 安装包调用）时跳过提权
)

$ErrorActionPreference = "Stop"
$APP_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $APP_DIR

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-ERR($msg)  { Write-Host "    [失败] $msg" -ForegroundColor Red }

# ── 0. 请求管理员权限（注册开机自启任务需要）──────────────
if (-not $NoElevate -and
    -not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "需要管理员权限，正在请求提升（请点击 UAC 确认）..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-NoElevate"
    )
    exit
}

$TaskName = "XianYuMonitor"

# 安装包静默模式：登录交互提示直接跳过（登录后用户可手动执行 run.py --login）
$Silent = [bool]$NoElevate

# ── 1. 检测 Python 3.10+ ──────────────────────────────────
Write-Step "1/7 检测 Python"
$PythonExe = $null
$candidates = @()
try { $candidates += (Get-Command python -ErrorAction Stop).Source } catch {}
foreach ($ver in @("3.13", "3.12", "3.11", "3.10")) {
    try {
        $p = (py -$ver -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        if ($p) { $candidates += $p.Trim() }
    } catch {}
}
foreach ($cand in $candidates | Select-Object -Unique) {
    try {
        $verOut = & $cand -c "import sys; print('%d.%d' % (sys.version_info.major, sys.version_info.minor))" 2>$null
        if ($verOut -match "3\.(1[0-9]|[2-9])") {
            $PythonExe = $cand
            Write-OK "Python $($verOut.Trim()) ($PythonExe)"
            break
        }
    } catch {}
}
if (-not $PythonExe) {
    Write-ERR "未找到 Python 3.10+。请先安装：winget install Python.Python.3.12"
    Write-Host "       或到 https://www.python.org/downloads/ 下载（安装时勾选 Add to PATH）"
    Read-Host "按回车退出"
    exit 1
}

# ── 2. 创建虚拟环境 ───────────────────────────────────────
Write-Step "2/7 创建虚拟环境 (.venv)"
$VenvPython = "$APP_DIR\.venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    Write-OK "虚拟环境已存在，跳过创建"
} else {
    & $PythonExe -m venv "$APP_DIR\.venv"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) {
        Write-ERR "创建虚拟环境失败"
        exit 1
    }
    Write-OK "已创建 .venv"
}

# ── 3. 安装依赖 ───────────────────────────────────────────
Write-Step "3/7 安装 Python 依赖"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r "$APP_DIR\requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-ERR "依赖安装失败，请检查网络"
    exit 1
}
Write-OK "依赖安装完成"

# ── 4. 安装 Playwright Chromium ───────────────────────────
Write-Step "4/7 安装 Playwright 浏览器内核（约 150MB，请耐心等待）"
# playwright install 幂等：已安装时自动跳过下载
Write-Host "    下载慢时可设置国内镜像后重试："
Write-Host "    `$env:PLAYWRIGHT_DOWNLOAD_HOST = 'https://npmmirror.com/mirrors/playwright/'"
& $VenvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-ERR "Chromium 安装失败"
    exit 1
}
Write-OK "Playwright Chromium 就绪"

# ── 5. 初始化 .env / 数据目录 ─────────────────────────────
Write-Step "5/7 初始化配置"
if (-not (Test-Path "$APP_DIR\.env") -and (Test-Path "$APP_DIR\.env.example")) {
    Copy-Item "$APP_DIR\.env.example" "$APP_DIR\.env"
    Write-OK "已创建 .env（请按需填入 Bark Key / DASHBOARD_TOKEN）"
}
New-Item -ItemType Directory -Force -Path "$APP_DIR\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$APP_DIR\browser_profile" | Out-Null
# 安装进程以管理员运行，创建的数据目录需保证普通权限的监控进程可写（最小权限，S-08）
icacls "$APP_DIR\data" /grant "$($env:USERDOMAIN)\$($env:USERNAME):(OI)(CI)M" 2>$null | Out-Null
icacls "$APP_DIR\browser_profile" /grant "$($env:USERDOMAIN)\$($env:USERNAME):(OI)(CI)M" 2>$null | Out-Null
Write-OK "数据目录就绪"

# ── 6. 注册系统级服务（任务计划程序）─────────────────────
Write-Step "6/7 注册自启服务 [$TaskName]"
# 关键：用 ONLOGON + 当前交互用户（而非 ONSTART + SYSTEM）。
# Windows 的 Session 0（服务会话）无法创建窗口，有头 Chromium 会启动失败；
# 用户登录会话里有头模式正常。安装完成后会立即启动一次。
$WorkDir = $APP_DIR
$cmd = "`"$VenvPython`" `"$APP_DIR\app.py`" --no-browser"
# 最小权限（S-08）：任务以当前用户普通权限运行，不提升为 HIGHEST
schtasks /Create /F /TN $TaskName `
    /TR "$cmd" `
    /SC ONLOGON `
    | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-ERR "服务注册失败（schtasks）。可用手动方式：python $APP_DIR\app.py --no-browser"
    exit 1
}
Write-OK "已注册任务 [$TaskName]（用户登录后自启，崩溃自动重启）"

# ── 7. 首次登录 + 启动 ────────────────────────────────────
Write-Step "7/7 首次登录与启动"
if (-not $Silent) {
    Write-Host ""
    Write-Host "  下一步：需要完成一次闲鱼扫码登录（只需一次）。" -ForegroundColor Yellow
    $choice = Read-Host "  现在登录？（Y=现在打开浏览器扫码 / N=稍后手动执行 python run.py --login）[Y/N]"
    if ($choice -notmatch "^[Nn]") {
        & $VenvPython "$APP_DIR\run.py" --login
    }
} else {
    Write-Host "    静默模式：跳过登录（安装完成后运行 .\.venv\Scripts\python.exe run.py --login）"
}

# 启动服务
schtasks /Run /TN $TaskName | Out-Null
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  安装完成！" -ForegroundColor Green
Write-Host "  仪表盘: http://127.0.0.1:5000" -ForegroundColor Green
Write-Host "  服务任务: $TaskName（开机自启，崩溃自动重启）" -ForegroundColor Green
Write-Host "  卸载: .\uninstall.ps1" -ForegroundColor Green
Write-Host "  登录: .\.venv\Scripts\python.exe run.py --login" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
if (-not $Silent) {
    Read-Host "按回车关闭本窗口"
}
