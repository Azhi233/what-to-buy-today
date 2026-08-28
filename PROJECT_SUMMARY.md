# 闲鱼价格监控 — 项目总结

> 基于 Playwright 真实浏览器自动化 + Flask 仪表盘的闲鱼二手商品价格监控系统。只读不写（仅搜索浏览），多维度风险过滤与多渠道推送。

## 1. 系统架构

```
闲鱼网页版 ⇄ GoofishMonitor（Playwright） → MonitorService（后台线程/asyncio） → SQLite
                                                     ├→ NotifierManager（多渠道推送）
                                                     └→ Flask 仪表盘（app.py）
```

**主入口**：`python app.py` 启动仪表盘与后台监控；`run.py` 是运维薄壳，仅提供 `--login`、`--once`、`--stats`、`--check`。两者共用 `data/monitor.db` 和 `browser_profile/`，不可同时启动常驻监控。

## 2. 已实现功能

| 模块 | 功能 |
|------|------|
| 搜索与提取 | Playwright 持久化浏览器、DOM + mtop API 双路提取、懒加载、崩溃自动恢复 |
| 过滤与风控 | 关键词/型号/必含/排除词、卖家信用、站外导流与仿品识别、价格异常标记 |
| 价格分析 | 万元缩写修正、IQR 离群过滤、历史趋势和价格分布 |
| 推送 | Bark 多目标、PushPlus、SMTP、Telegram、控制台日志 |
| 仪表盘 | 商品 CRUD、市场分析、降价与通知记录、运行设置、保留策略、CSV 导出 |
| 运维 | SQLite 数据、WAL、健康探针、自动清理、配置/登录状态持久化 |

## 3. 配置与安全

- `config.py` 是仓库中的占位配置模板，`MONITOR_ITEMS` 在此编辑；密钥走环境变量，不含真实密钥。
- `.env.example` 是 Docker Compose/systemd 的环境变量模板；`.env` 已被 Git 忽略。
- `DASHBOARD_TOKEN` 设置后，除 `/api/healthz` 外的 API 需要 `X-Auth-Token` 或 `Authorization: Bearer`（Token 仅走 Header，不通过 URL 查询串）。
- Bark/PushPlus/SMTP/Telegram 密钥支持环境变量覆盖，详见 `config.py` / `.env.example`。
- `data/monitor.db`、`browser_profile/`、日志和 PID 文件均为运行时文件，不提交。

## 4. 目录结构

```
buy/
├── app.py                 # 唯一常驻入口：Flask + 后台监控
├── run.py                 # 运维入口：登录/单轮/统计/检查
├── config.py              # 可提交的占位配置（密钥走环境变量）
├── .env.example           # 环境变量模板
├── monitor.py             # 闲鱼搜索与过滤核心
├── monitor_service.py     # 后台监控服务
├── database.py            # SQLite 数据层
├── notifier.py            # 通知抽象
├── requirements.txt       # 运行依赖
├── requirements-dev.txt   # pytest/ruff 开发依赖
├── pyproject.toml         # 测试与 lint 配置
├── Makefile               # 本地质量门禁
├── CHANGELOG.md           # 变更记录（语义化版本）
├── tests/                 # pytest 单元测试
├── templates/             # 仪表盘页面
├── static/                # 前端资源
└── deploy/                # Docker/systemd 部署文件
```

## 5. 质量与部署

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .
python run.py --check
```

CI 入口为 `.github/workflows/ci.yml`。部署细节、端口、卷和环境变量见 `deploy/README.md`；所有常驻部署统一执行 `app.py --no-browser --port 5000`。

## 6. 已知取舍

- 闲鱼搜索 API 不提供真实成交价，价格趋势使用搜索样本的 IQR 核心带作为参考。
- 信用字段可能因页面结构变化而缺失，默认标记“信用未知”但不硬过滤，可通过 `STRICT_UNKNOWN_CREDIT` 调整。
- 远程部署应使用专用闲鱼小号，并通过 Token + HTTPS 保护仪表盘管理 API。

## 7. 许可证

仅供学习与个人使用，请遵守闲鱼平台服务条款。
