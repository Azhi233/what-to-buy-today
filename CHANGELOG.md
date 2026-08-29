# Changelog

本项目的显著变更记录。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 与语义化版本（SemVer）。

## [Unreleased]

### 服务器版（本地扫码模式）
- `config.py`：新增 `MONITOR_KEYWORD` 单品环境变量覆盖——设置后仅监控该单商品（服务器场景），未设置保持默认列表；配套 `MONITOR_MAX_PRICE`/`MONITOR_MIN_PRICE`/`MONITOR_EXCLUDE_KEYWORDS`/`MONITOR_MUST_INCLUDE`
- 新增 `tools/check_login.py`：本地扫码登录态自检脚本（解析 Chromium `unb`/`tracknick` cookie），上传服务器前确认已有效登录
- 新增 `.env.server.example`：服务器版配置模板（DGX Spark 25000-30000，屏蔽询价/私聊，Xvfb 有头）
- 新增 `docker-compose.server.yml`：服务器编排（本地扫码上传 `browser_profile`/`data` 后即可启动）
- 新增 `deploy/server-deploy.md`：完整部署手册（本地扫码→上传→启动→运维）
- `.gitignore`：追加 `.env.server`/`.env.local` 防泄漏

### 服务器版实测修复（容器全链路验证）
- **跨平台 cookie 加密问题**：Windows 新版 Chromium 的 `unb`/`tracknick` cookie 使用 app-bound 加密（绑定 Windows 系统密钥），Linux 容器无法解密被丢弃 → 新增 `tools/export_cookies.py` 本地导出明文 cookie，`monitor.py` 启动时经 `MONITOR_COOKIES_FILE` 自动注入（注入后以 Linux 格式持久化，无需重复）
- `tools/check_login.py` 修复 Cookies 路径：Chromium 89+ 的 cookie 库在 `Default/Network/Cookies`（原查 `Default/Cookies` 恒未命中）
- `docker-compose.server.yml` 挂载 `cookies.json`；`.dockerignore` 排除 `cookies.json`（防明文凭证进镜像）
- `deploy/server-deploy.md` 更新：明确 cookie 导出流程与 `DASHBOARD_TOKEN` 远程访问强制要求

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
- 修复容器内 chromium 崩溃：降权后正确设置 `HOME`（crashpad 数据库路径依赖 HOME）
- 修复 `_ensure_browser` 误判浏览器失效：`BrowserContext` 无 `is_closed()`，改用 `pages` 属性探测连接
- 修复 `_is_browser_error` 不识别 `BrowserDeadError`，浏览器失效时无法触发重启

### Windows 系统级安装包
- 新增 `install.ps1`：一键安装（检测 Python → 创建 `.venv` → 安装依赖与 Playwright Chromium → 注册任务计划程序 `XianYuMonitor` 登录自启 → 引导扫码登录），支持 `-NoElevate` 供安装包静默调用
- 新增 `uninstall.ps1`：停止并删除自启任务，可选清理数据/虚拟环境（含静默卸载模式，保留用户数据）
- 新增 `xianyu-monitor.iss`：Inno Setup 6 安装包定义，编译产出 `.exe` 安装程序（默认安装到用户目录，普通权限可写）
- 安装采用任务计划程序 ONLOGON（交互会话）而非 Windows 服务：Session 0 无法创建窗口，有头 Chromium 无法在服务会话运行
- 修复 CSV 导出公式注入（S-06）：`=`/`+`/`-`/`@` 开头单元格加单引号前缀 + 回归测试
- 修复 `/api/status` 轮询间隔默认值与 `config` 不一致
- 修复计划任务/服务启动（工作目录非项目根）时数据落到 `System32`：`config`/`app`/`run` 全部默认路径改为基于项目根 `BASE_DIR` 的绝对路径
- 修复安装包快捷方式名称含非法字符 `:`/`/` 导致安装失败（改为"打开监控仪表盘"）

### 后台抓取与安装体验
- 新增 `MONITOR_HIDE_WINDOW`（默认开启）：监控抓取时浏览器窗口移出屏幕（`--window-position=-32000,-32000`），保持有头模式规避风控但用户无感知；`run.py --login` 强制显示窗口供扫码
- Windows 下后台抓取进一步隐藏浏览器窗口（`SW_HIDE`，任务栏/Alt+Tab 均不可见），实测不抢占前台焦点，不影响前台工作
- 安装包启用目录选择页（`DisableDirPage=no`），安装时可选安装位置

### 系统托盘（推荐入口）
- 新增 `tray.py`：以系统托盘图标常驻后台，自动拉起/复用监控进程（`app.py`）
- 托盘右键菜单：**显示仪表盘**（浏览器打开面板）/ **重新登录闲鱼**（扫码窗口）/ **退出**（停止监控并退出托盘）
- 双击托盘图标直达仪表盘；安装脚本与快捷方式入口改为托盘启动

### 新用户引导（防呆）
- 首次打开仪表盘自动弹出两步引导：①配置推送通知（Bark 或邮件 SMTP，任选其一）→ ②添加第一个监控商品
- 引导基于实际配置状态判定：通知渠道就绪 + 有启用商品后不再弹出（第二次进入直接使用）
- 每步均可跳过；SMTP 配置存入数据库（`channel_config`），`notifier` 运行时读取覆盖 config
- 新增 `GET/POST /api/channel-config`（密码脱敏）、`GET /api/onboarding/status`

### 修复：重新登录空白页
- 根因：监控（隐藏 Chromium）持有 `browser_profile` 的 SingletonLock，"重新登录"再用同一 profile 启动浏览器时，窗口被隐藏的监控实例接管 → 空白页
- 修复：托盘"重新登录"先暂停监控释放 profile → 登录窗口正常显示 → 登录完成自动恢复监控
- `run.py --login` 增加兜底：检测到监控在运行自动暂停（手动执行登录时同样有效）

### 代码审查加固（R8）
- `config.py` 新增 `_load_dotenv()`：本地运行从项目根 `.env` 加载环境变量（不覆盖已有环境变量，Docker/systemd 由编排层注入）
- `tray.py` 增加**单实例保护**（Windows 命名 Mutex）：重复启动托盘静默退出，防止双托盘/双监控抢占端口与浏览器配置
- `tray.py` 通过 `import config` 触发 `.env` 加载，使 HOST/PORT 与 `app.py` 读取来源一致
- `install.ps1`/`uninstall.ps1` 恢复 UTF-8 BOM：无 BOM 的 UTF-8 脚本在 Windows PowerShell 5.1 下中文按 ANSI 解码会破坏语法（修复了 `<#` 块注释头被误读的问题）
- `monitor.py` 登录检测加固：仅以登录标识 cookie（`unb`/`tracknick`）为准，移除匿名会话也存在的 `last_u_xianyu_web`；API 检测要求返回体带用户数据，避免未登录误判"已登录"秒关扫码窗口
- 新增 `clear_login_state()`：重新登录前清除历史登录态，保证二维码稳定显示
- 可见模式（登录）显式指定 `--window-position=120,120`，覆盖 profile 中可能记住的屏外坐标
- 托盘 URL 支持环境变量 `HOST`/`PORT` 覆盖；`.iss` 不再打包 `requirements-dev.txt`

### 修复：价格识别与屏蔽词
- **万元缩写误判**：监控上限接近万元时（如 max=29999），`2.99万`（闲鱼显示 `¥2.99`）曾按字面价解析为 2.99 元。移除过严的"同量级（≤90%×max）"检查，改为"万元解释 ≤ max×3"单一量级门槛；同时修正 `suspicious_literal` 条件方向（`>`→`<=`），高 min_price 监控下万元商品也能正确识别
- **屏蔽词漏检**：搜索卡片标题改用 `title` 属性（完整标题）优先提取——页面显示文本可能被 CSS 省略，截断标题会导致排除词/必须包含词漏检
- 万元价格结果 `round` 到分，消除浮点误差（2.99+0.99 累积为 29900.000000000004）

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
