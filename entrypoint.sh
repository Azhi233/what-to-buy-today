#!/bin/sh
# 容器启动脚本（以 root 进入，处理属主后降权到 pwuser 运行）：
#   1) 修正数据目录属主（pwuser 才能写入宿主卷）
#   2) 启动 Xvfb 虚拟显示（供 Playwright 有头模式使用，规避闲鱼无头拦截）
#   3) 降权为 pwuser，继承全部现有环境变量，以默认参数启动仪表盘 + 后台监控

set -e

RUNAS_ID=$(id -u pwuser 2>/dev/null || echo 1000)

# ── 0. 清理上次退出可能残留的锁文件（跨容器重启会遗留，导致新实例拒绝启动）──
# Xvfb 显示锁：stop/start（不重建容器）时 /tmp 保留，不清理则 Xvfb 报 "already active"
rm -f /tmp/.X99-lock 2>/dev/null || true
# Chromium profile 锁：down/up（重建容器）时卷保留，不清理则新实例报 "profile in use"
rm -f /app/browser_profile/Singleton* /app/browser_profile/DevToolsActivePort 2>/dev/null || true

# ── 1. 数据目录属主修正（幂等；宿主卷属主不符时用户无需手动改权限）──
# /app 含 data/ 、 browser_profile/ 、dashboard.log/PID 等运行时文件，一并归 pwuser；
# 仅修正需要的挂载点与可写路径，不改动镜像内只读的源码文件属主风险。
chown -R pwuser:pwuser /app/data /app/browser_profile 2>/dev/null || true
# /app 根（含 dashboard.log、.pid 等 pwuser 运行时需写入的文件）
chown pwuser:pwuser /app 2>/dev/null || true

# ── 2. 启动 Xvfb 虚拟显示（供 pwuser 的 Playwright 有头模式连接）──
if [ -z "$DISPLAY" ]; then
    export DISPLAY=:99
    touch /tmp/xvfb.log
    Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    XVFB_PID=$!
    i=0
    ready=0
    while [ $i -lt 20 ]; do
        i=$((i + 1))
        sleep 1
        if kill -0 "$XVFB_PID" 2>/dev/null; then
            ready=1
            break
        fi
    done
    if [ "$ready" -ne 1 ]; then
        echo "Xvfb 启动失败：$(cat /tmp/xvfb.log 2>/dev/null)" >&2
        exit 1
    fi
    echo "虚拟显示器 DISPLAY=:99 已启动"
fi

# ── 3. 有头模式由 Xvfb 提供虚拟显示；默认 headless=0 ──
export MONITOR_HEADLESS="${MONITOR_HEADLESS:-0}"

echo "正在启动闲鱼监控（有头模式 / 虚拟显示）..."

# 降权为 pwuser 时正确设置 HOME，否则 chromium crashpad 无法初始化（--database is required）
export HOME=/home/pwuser

if [ "$(id -u)" -eq 0 ]; then
    # setpriv 保留当前全部环境变量（含 .env 注入的密钥/Token），仅切换 uid/gid
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid="$RUNAS_ID" --regid="$RUNAS_ID" --clear-groups \
            python app.py --no-browser --host "${HOST:-0.0.0.0}" --port "${PORT:-5000}"
    else
        exec su pwuser -s /bin/sh -c 'exec "$@"' _ \
            python app.py --no-browser --host "${HOST:-0.0.0.0}" --port "${PORT:-5000}"
    fi
else
    exec python app.py --no-browser --host "${HOST:-0.0.0.0}" --port "${PORT:-5000}"
fi