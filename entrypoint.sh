#!/bin/sh
# 容器启动脚本（以 root 进入，处理属主后降权到 pwuser 运行）：
#   1) 修正数据目录属主（pwuser 才能写入宿主卷）
#   2) 启动 Xvfb 虚拟显示（供 Playwright 有头模式使用，规避闲鱼无头拦截）
#   3) 降权为 pwuser，继承全部现有环境变量，以默认参数启动仪表盘 + 后台监控

set -e

# ── 1. 数据目录属主修正（幂等；宿主卷属主不符时用户无需手动改权限）──
chown -R pwuser:pwuser /app/data /app/browser_profile 2>/dev/null || true

# ── 2. 启动 Xvfb 虚拟显示（供 pwuser 的 Playwright 有头模式连接）──
if [ -z "$DISPLAY" ]; then
    export DISPLAY=:99
    touch /tmp/xvfb.log
    # 非 root 亦能启动 Xvfb；此处若以非 root 运行且失败，会走 ready 检查报错
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
# su-exec 保留当前全部环境变量（含 .env 注入的密钥/Token），仅切换运行用户，杜绝密钥丢失
if command -v su-exec >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    exec su-exec pwuser python app.py --no-browser --host "${HOST:-0.0.0.0}" --port "${PORT:-5000}"
else
    exec python app.py --no-browser --host "${HOST:-0.0.0.0}" --port "${PORT:-5000}"
fi