@echo off
rem ============================================
rem  闲鱼价格监控 — 一键启动（Windows / Docker）
rem  用户无需安装 Python / Playwright，只需装好 Docker Desktop。
rem ============================================
cd /d "%~dp0"

rem 检查 Docker 是否可用
where docker >nul 2>&1
if errorlevel 1 (
    echo [X] 未检测到 Docker。请先安装 Docker Desktop：
    echo     https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

rem 检查 Docker 服务是否在运行
docker info >nul 2>&1
if errorlevel 1 (
    echo [X] Docker 未启动。请先启动 Docker Desktop 后重试。
    pause
    exit /b 1
)

echo 正在构建并启动闲鱼监控容器（首次构建需几分钟，请耐心等待）...
docker compose up -d --build

if errorlevel 1 (
    echo [X] 启动失败，请查看上方错误信息。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   闲鱼监控已启动！
echo   打开浏览器访问: http://127.0.0.1:5000
echo.
echo   首次使用请先在容器内完成闲鱼登录：
echo   本脚本依赖 compose 卷已挂载 browser_profile，
echo   （登录操作请按部署文档在宿主机执行 run.py --login，
echo    或将已登录的 browser_profile 同步到 ./browser_profile 目录）
echo ============================================
echo.
pause