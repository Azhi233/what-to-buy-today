# 工程治理修复计划 — 可信任 / 可部署 / 可维护

> 针对朋友总体评价中“双入口双存储、无鉴权暴露、Docker 数据卷设计错误、一批死代码和假数据字段”的治理债。
> 本文件是需求/验收清单，修复后按项勾选。

## 已有基础
- 抓取过滤链路（Playwright 持久化 profile、随机 UA/视口、人类延迟、崩溃指数退避、定时回收、IQR 核心价、万元修正、信用/引流过滤）已验证，无需再动。
- P0~P1-9、P2-10~P2-18、R1~R14 已在 OPTIMIZATION_CHECKLIST.md 中闭环。

---

## GOV-1 可信任 — 无鉴权暴露

- [x] **GOV-1.1 Flask `/api/*` 裸奔**
> ✅ 修复说明：增加基于环境变量 DASHBOARD_TOKEN 的 API 鉴权，缺失或错误 Token 时返回 401；健康探针免鉴权，前端自动附加已保存的 Token。
  - 现状：`app.py` 监听 `127.0.0.1:5000` 但容器里等同 `0.0.0.0`，所有 `/api/*` 无鉴权，局域网可改配置/清空库
  - 修复：引入 `DASHBOARD_TOKEN`（环境变量，空值=本地开发不启用），`@app.before_request` 对 `/api/*` 校验 `X-Auth-Token` / `Authorization: Bearer` / `?token=`；`/api/healthz` 豁免；`401` 时返回 `{"ok":false,"error":"unauthorized"}`
  - 文件：`config.py` 新增 `DASHBOARD_TOKEN` 从环境变量读取；`app.py` 增加鉴权中间件；`static/app.js` 的 `api()` 自动附加 `localStorage.dashboard_token`
  - 验收：`DASHBOARD_TOKEN=secret python app.py` 后，无 Token 请求 `/api/status` 返回 401，带正确 Token 返回 200；`curl /api/healthz` 始终 200

## GOV-2 可部署 — Docker 数据卷设计错误

- [x] **GOV-2.1 DB 不在 VOLUME 内 + 单文件绑定导致 WAL 不同步**
> ✅ 修复说明：数据库迁移到 data 目录内，Docker compose 移除了单文件绑定，WAL 文件与 DB 同目录持久化。
  - 现状：`DB_PATH = BASE_DIR/monitor.db` 不在 `VOLUME ["/app/browser_profile","/app/data"]` 内；`docker-compose.yml` 用 ` - ./monitor.db:/app/monitor.db` 单文件绑定，WAL 模式下 `monitor.db-shm/wal` 不同步，Windows 会锁文件
  - 修复：`DB_PATH` 改为 `BASE_DIR / data / monitor.db`（`data` 来自 `MONITOR_SETTINGS.data_dir`），启动时若旧 `BASE_DIR/monitor.db` 存在且新路径不存在则自动迁移；`Dockerfile` 保持 `VOLUME ["/app/data","/app/browser_profile"]`；`docker-compose.yml` 删除单文件绑定，仅保留 `./data:/app/data` 与 `./browser_profile:/app/browser_profile`
  - 文件：`app.py`、`run.py`、`database.py`（确保目录创建）、`Dockerfile`、`docker-compose.yml`
  - 验收：`docker compose config` 无 `monitor.db` 单文件挂载；宿主机 `data/monitor.db` 存在且容器内 `/app/data/monitor.db` 可读写；`WAL` 文件在同一目录下

- [x] **GOV-2.2 镜像与 compose 健康检查一致性**
> ✅ 修复说明：健康检查继续命中免鉴权的 /api/healthz，端口与仪表盘一致。
  - 修复：`docker-compose.yml` healthcheck 命中 `/api/healthz` 时若启用 Token 需豁免（已豁免则无需改），`Dockerfile` `EXPOSE` 与 `app.py --port` 一致
  - 验收：`docker compose up` 后 healthcheck 为 healthy

## GOV-3 可维护 — 死代码 / 双存储 / 假字段

- [x] **GOV-3.1 删除双存储遗留**
> ✅ 修复说明：删除 storage.py 及对应的旧文件存储文件，CLI 已仅保留 SQLite 统计。
  - 现状：`storage.py`（`SeenStorage`/`StatsCollector`）已无调用但文件仍在；`data/seen_items.json`、`data/stats.json` 为陈旧文件存储，与 SQLite 双轨
  - 修复：删除 `storage.py`；删除 `data/seen_items.json`、`data/stats.json`（若存在）；确认 `run.py` 不再导入
  - 验收：仓库中无 `storage.py`；`grep -r SeenStorage` 零命中；`python -m py_compile` 通过

- [x] **GOV-3.2 修正 .gitignore 与 DB 忽略策略**
> ✅ 修复说明：忽略规则改为仅忽略 data/monitor.db 及其 WAL 文件，不再忽略整个 data 目录。
  - 现状：`.gitignore` 有 `data/` 与 `*.db`，但 `DB_PATH` 迁移到 `data/monitor.db` 后需确保 `data/.gitkeep` 可提交而 DB 文件仍忽略；`config.py` 被忽略导致模板无法提交
  - 修复：`.gitignore` 保留 `*.db`、`*.db-shm`、`*.db-wal`、`data/monitor.db`、`browser_profile/`、`*.log`、`dashboard.pid`；移除 `data/` 目录级忽略改为 `data/monitor.db`；将 `config.py` 改为提供 `config.example.py` 模板（或保留 `config.py` 但要求提交时脱敏）
  - 验收：`git status` 不再提示 `data/monitor.db`；`config.example.py` 存在或 `config.py` 可被追踪

- [x] **GOV-3.3 清理假数据字段与文档唯一入口**
> ✅ 修复说明：文档明确主入口为 app.py，run.py 仅用于登录和运维；移除已删除存储的过时说明。
  - 修复：`README.md`/`PROJECT_SUMMARY.md` 明确唯一入口为 `python app.py`（仪表盘+后台），`python run.py --login/--once` 仅为运维薄壳；移除 `storage` 相关文档描述
  - 验收：文档中无 `SeenStorage`/`stats.json` 描述

---

## 执行顺序
1. GOV-1.1 → 2. GOV-2.1/2.2 → 3. GOV-3.1/3.2/3.3
2. 每项完成后 `python -m py_compile` 验证
3. 提交信息前缀 `governance:`
