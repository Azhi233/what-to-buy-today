<#
============================================================
  闲鱼价格监控 - 卸载脚本
============================================================
功能：
  1. 停止并删除开机自启任务 XianYuMonitor
  2. 可选删除虚拟环境 / 数据库 / 浏览器登录态
用法（右键"以管理员身份运行"）：
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -NoElevate   # 安装包卸载调用（静默，保留用户数据）
============================================================
#>

param(
    [switch]$NoElevate
)

$ErrorActionPreference = "Continue"
$APP_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "XianYuMonitor"

if (-not $NoElevate -and
    -not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "需要管理员权限，正在请求提升..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"", "-NoElevate"
    )
    exit
}

Write-Host "==> 停止并删除服务任务 [$TaskName]" -ForegroundColor Cyan
schtasks /End /TN $TaskName 2>$null | Out-Null
schtasks /Delete /TN $TaskName /F 2>$null | Out-Null
Write-Host "    已删除开机自启任务" -ForegroundColor Green

if ($NoElevate) {
    # 静默卸载：保留用户数据（data/browser_profile/.venv），避免误删
    Write-Host "    静默模式：保留数据与虚拟环境（如需清理请手动删除）" -ForegroundColor Yellow
    Write-Host "卸载完成。" -ForegroundColor Green
    exit 0
}

Write-Host ""
$keepData = Read-Host "是否保留数据（data/ 数据库、browser_profile/ 登录态）？保留则下次安装可无缝续用。[Y/N]"
if ($keepData -match "^[Nn]") {
    Remove-Item "$APP_DIR\data" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$APP_DIR\browser_profile" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$APP_DIR\dashboard.pid" -Force -ErrorAction SilentlyContinue
    Remove-Item "$APP_DIR\*.log" -Force -ErrorAction SilentlyContinue
    Write-Host "    已删除数据与登录态" -ForegroundColor Green
}

$keepVenv = Read-Host "是否删除虚拟环境 .venv（约 300MB）？删除后重装需重新安装依赖。[Y/N]"
if ($keepVenv -match "^[Yy]") {
    Remove-Item "$APP_DIR\.venv" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    已删除虚拟环境" -ForegroundColor Green
}

Write-Host ""
Write-Host "卸载完成。如有残留进程：taskkill /F /IM python.exe 后重试。" -ForegroundColor Green
Read-Host "按回车关闭本窗口"
