# Changelog

本项目的显著变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 与语义化版本（SemVer）。

## [Unreleased]

### 修复
- 修复 `PRICE_ANOMALY_RATIO` 死配置：`evaluate_item` 两处调用均传入该配置，用户修改立即生效
- 修复老商品重新评估漏传 `strict_unknown_credit`，与新商品路径配置保持一致
- 修复清空商品池后降价记录残留（`/api/clear-items` 现一并清理 `item_price_changes`）
- 修复 `cleanup_expired` 不清理降价记录表导致无限增长的问题
- 登录异常告警、响应解析等静默异常改为记录日志，不再吞掉关键错误
- 移除监控服务中无对应代码的死注释

### 工程化
- `run.py` 无参数不再启动常驻监控，仅保留运维子命令（`--login/--once/--stats/--check`），单一常驻入口收敛到 `app.py`（P-02/K-02）
- `PRICE_ANOMALY_RATIO`、降价阈值（20元/5%）、万元判定系数、IQR 系数、页面超时、监控循环调优参数全部收敛为具名常量（C-05）
- 路由层不再直接执行 SQL：`db.clear_items()` / `db.get_all_price_history()` / `db.get_last_price_change()` 封装
- `run.py` 配置校验补充价格类型、`max_price>0`、`min≤max` 检查（F-05）
- `config.py` 环境变量非法值时回退默认并告警到 stderr（F-04）
- CI 编译检查改为 `compileall` 全量覆盖，不再硬编码文件清单（Q-03）
- 容器默认以非 root 用户 `pwuser` 运行（S-08）
- `--host`/`--port` 支持 `HOST`/`PORT` 环境变量覆盖（F-07）
- 核心纯函数与 API 回归单测补齐：价格解析/信用评分/风险识别/IQR/万元判定、鉴权脱敏、校验分支、运行控制

### 清理与一致性
- 移除死代码：`filter_items`、`SEARCH_API_NAMES`、`_search_api_data`、`NotifierManager.report_channels/get_bark_targets`
- `monitor_service` 轮询间隔默认值与 `config`/仪表盘一致（`MONITOR_INTERVAL_MINUTES` 生效）
- mtop AppKey、启动重试/冷却、关键词间隔、兜底价格上限等剩余魔法数字收敛为具名常量

### 容器封装（最终用户零配置）
- 新增 `entrypoint.sh`：以 root 修正数据卷属主后降权非 root 运行、启动 Xvfb 虚拟显示、以有头模式运行（规避闲鱼无头拦截）
- 新增 `start.bat`：Windows 用户双击一键构建并启动容器（需 Docker Desktop）
- Dockerfile 改为 root 进入 + 降权，兼容宿主卷挂载无需手动 chown
- compose 移除强制 `MONITOR_HEADLESS=1`，改由 entrypoint 默认有头

## [1.0.0] - 2026-08-28

### 新增
- 闲鱼价格监控首版：Playwright 真实浏览器搜索、卖家信用与引流文案过滤、多渠道通知（Bark/PushPlus/SMTP/Telegram）、Web 仪表盘
- 单一常驻入口 `app.py` + 运维薄壳 `run.py`，共用同一 SQLite 与浏览器登录态
- 仪表盘 API 鉴权（`X-Auth-Token`，`hmac.compare_digest`，默认仅本机回环可访问）
- 健康探针 `/api/healthz`（免鉴权），支持 Docker healthcheck / Uptime Kuma
- 浏览器崩溃自动重启（指数退避 5→120s）、定时回收、单实例 PID 保护
- 保留策略清理（商品池/历史/检查日志/通知记录）+ 手动清理 + VACUUM
- CSV 导出（文件名消毒、UTF-8 BOM 兼容 Excel）
- CI（`pytest` + `ruff` + `compileall`）、`Makefile` 一键自检、`pyproject.toml` 统一配置

### 修复（继承自 OPTIMIZATION_CHECKLIST 前四轮）
- 降价提醒重新执行风险校验，不再推送已过滤的诈骗/低信用商品
- 万元缩写误判修正（显式"万"或价格量级证据才按万元换算）
- 关键词型号匹配强制命中数字与 Pro/Max 等型号词
- 浏览器崩溃异常上抛触发自动重启，不再静默返回空
- 登录失败状态不被覆盖为"运行中"；`login_ok` 三态并前端提示
- CLI 与仪表盘收敛到同一套 MonitorService 管线，移除 `seen_items.json` 双去重
- 通知记录区分真实渠道与仅控制台；`test-notify` 返回布尔语义
- Bark Key 脱敏返回，编辑留空保持原值
- 单轮超量推送合并摘要（>10 条），Bark 限流保护
- 排除词/必含词仅按逗号分隔，保留词组内部空格
- 清理策略统一本地时间口径，避免 8 小时偏差

### 部署
- Docker Compose / systemd 部署文档与单元文件，环境变量注入密钥，数据卷持久化 DB 与浏览器配置
- `config.py` 明确为可提交占位模板，真实密钥一律环境变量注入
