# 部署说明

项目只有一个常驻进程入口：`python app.py --no-browser`。`run.py` 只用于登录、单轮检查和运维查询，不要与 `app.py` 同时作为常驻服务启动。

## 共同准备

```bash
# 安装运行依赖
python -m pip install -r requirements.txt
playwright install chromium

# 编辑仓库中 config.py 的 MONITOR_ITEMS；敏感配置放到 .env
cp .env.example .env
```

`config.py`、`.env` 和 `browser_profile/` 可能包含本地敏感数据，不要提交。数据库统一位于 `data/monitor.db`，SQLite 的 `-wal` / `-shm` 文件也必须与它在同一目录。

首次登录：

```bash
python run.py --login
```

## Docker Compose

```bash
# Windows 最终用户：直接双击 start.bat 即可一键构建并启动（需已装 Docker Desktop）
git clone https://github.com/Azhi233/what-to-buy-today.git
cd what-to-buy-today
cp .env.example .env
# 编辑 config.py 的 MONITOR_ITEMS 和 .env 的密钥/Token
# 首次使用可在宿主机执行 python run.py --login，再将 browser_profile/同步到服务器

docker compose up -d --build
curl http://127.0.0.1:5000/api/healthz | python -m json.tool
```

compose 持久化 `./data:/app/data` 与 `./browser_profile:/app/browser_profile`，不绑定单个 SQLite 文件；这样数据库、WAL 和共享内存文件始终同步。配置通过 `.env` 注入。

> **有头模式（规避闲鱼无头拦截）**：容器内通过 `Xvfb` 虚拟显示器提供有头浏览器环境，`MONITOR_HEADLESS` 默认 `0`（有头）。请不要把该变量设为 `1`——闲鱼风控会拦截无头浏览器。日志中会出现 `虚拟显示器 DISPLAY=:99 已启动`，代表有头浏览器已就绪。

> **权限（S-08 非 root，自动处理）**：镜像内以 `pwuser`（UID 1000）运行，`entrypoint.sh` 会以 root 自动修正 `data/`、`browser_profile/` 的宿主卷属主后降权，首次部署**无需手动 `chown`**。仅当宿主机目录本身不可写时才需手动设置。

> **安全要求**：容器/远程部署**必须设置 `DASHBOARD_TOKEN`**。未设置时，除 `/api/healthz` 外的所有 API 都会对非 localhost 客户端返回 401，仪表盘将无法加载数据。`.env` 缺失时 compose 会跳过（`required: false`），请用 `.env.example` 创建并填写 Token。

更新：

```bash
git pull
docker compose up -d --build
```

容器入口 `entrypoint.sh` 会修正属主、启动 Xvfb 虚拟显示，再以 `python app.py --no-browser --port 5000`（默认 `0.0.0.0:5000`，可用 `HOST`/`PORT` 环境变量覆盖）启动。

## systemd（Ubuntu/Debian）

在服务器准备代码和配置：

```bash
sudo mkdir -p /opt/what-to-buy-today
sudo chown -R ubuntu:ubuntu /opt/what-to-buy-today
cp .env.example /opt/what-to-buy-today/.env
# 编辑 config.py / .env；建议将 .env 权限设为 600
chmod 600 /opt/what-to-buy-today/.env
python -m pip install -r requirements.txt
playwright install chromium
```

安装并启动服务：

```bash
sudo cp deploy/what-to-buy-today.service /etc/systemd/system/what-to-buy-today.service
sudo systemctl daemon-reload
sudo systemctl enable --now what-to-buy-today
sudo systemctl status what-to-buy-today
journalctl -u what-to-buy-today -f
curl http://127.0.0.1:5000/api/healthz
```

service 使用 `/opt/what-to-buy-today/.env` 和 `app.py --no-browser --port 5000`，数据目录为 `/opt/what-to-buy-today/data`。

> **安全要求**：远程访问必须设置 `DASHBOARD_TOKEN`（放在 `.env` 中，权限设为 600）。未设置时非 localhost 客户端访问管理 API 会被拒绝。建议在反向代理层启用 HTTPS，不要把管理 API 直接暴露到公网。

## Windows 常驻（NSSM）

```powershell
nssm install WhatToBuyToday "C:\Python312\python.exe" "E:\buy\app.py --no-browser --port 5000"
nssm set WhatToBuyToday AppDirectory "E:\buy"
nssm set WhatToBuyToday AppRestartDelay 10000
nssm start WhatToBuyToday
```

任务计划程序的操作同样使用 `python E:\buy\app.py --no-browser --port 5000`，工作目录设为 `E:\buy`，并配置 `.env` 或系统环境变量。

## 健康探针

`GET /api/healthz` 始终免鉴权，返回 `ok`、`status`、`uptime_seconds`、`last_check_at`、`rss_mb`、`db_mb` 等字段，可供 Docker healthcheck、Uptime Kuma 或 k8s livenessProbe 使用。
