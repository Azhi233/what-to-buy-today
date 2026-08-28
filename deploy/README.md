# 部署说明

## Docker（推荐，Linux/云服务器）

```bash
git clone https://github.com/Azhi233/what-to-buy-today.git
cd what-to-buy-today
# 若为全新环境，需先在本地 python run.py --login 扫码并把 browser_profile/ 同步到服务器
# 或进入容器后执行：docker compose exec xianyu-monitor python run.py --login（需有头，可改用 VNC）

docker compose up -d
# 查看健康探针
curl http://127.0.0.1:5000/api/healthz | python -m json.tool
# Uptime Kuma 外部心跳：监控 http://服务器IP:5000/api/healthz
```

更新：

```bash
git pull
docker compose build && docker compose up -d
```

## systemd（原生 Python，Ubuntu/Debian）

```bash
sudo cp deploy/what-to-buy-today.service /etc/systemd/system/what-to-buy-today.service
sudo systemctl daemon-reload
sudo systemctl enable --now what-to-buy-today
sudo systemctl status what-to-buy-today
journalctl -u what-to-buy-today -f
curl http://127.0.0.1:5000/api/healthz
```

## Windows 常驻（nssm 或 任务计划程序）

### nssm

```powershell
nssm install WhatToBuyToday "C:\Python312\python.exe" "E:\buy\app.py --no-browser"
nssm set WhatToBuyToday AppDirectory "E:\buy"
nssm set WhatToBuyToday AppRestartDelay 10000
nssm start WhatToBuyToday
```

### 任务计划程序

- 触发器：系统启动时 + 每天 04:00
- 操作：`python E:\buy\app.py --no-browser`
- 条件：取消“仅在交流电源时启动”，勾选“不管用户是否登录都要运行”

## 健康探针

`GET /api/healthz` 返回 `{ ok, status, uptime_seconds, last_check_at, rss_mb, db_mb }`，供 Uptime Kuma / k8s livenessProbe 使用。
