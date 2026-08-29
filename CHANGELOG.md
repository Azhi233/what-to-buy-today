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

### 服务器版公网部署实测（47.101.64.108，阿里云）
- **Dockerfile 支持 `PIP_INDEX_URL` 构建参数**：国内服务器拉取 pypi 官方源极慢（playwright 37MB 卡死），可 `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 加速，默认官方源不变
- 新增 `tools/clean_browser_cache.py`：仅清空 Chromium 缓存目录（Cache/Code Cache/GPUCache/Service Worker 等），**保留 Cookies/Local Storage 登录态与商品数据库**；服务器 crontab 每周一 04:00 执行（容器运行中直接清理，Chromium 自动重建）

### 服务器公网运维修复（47.101.64.108 实测发现）
- `entrypoint.sh` 启动时清理两类残留锁：`/tmp/.X99-lock`（`docker stop/start` 不重建容器时残留，会导致 Xvfb 报 "already active"）与 `browser_profile` 的 `Singleton*`/`DevToolsActivePort`（`docker down/up` 重建时卷残留，会导致 Chromium 报 "profile in use by another process"）——根治跨容器重启后监控浏览器起不来的问题
- `static/app.js` 支持 `?token=` URL 注入并写入 localStorage：远程浏览器访问 `http://host:5000/?token=xxx` 后 API 自动携带 `X-Auth-Token`，仪表盘无需手动设置 token 即可加载

### 时区修复（容器时间比北京慢 8 小时）
- 根因：playwright/jammy 基础镜像缺 `tzdata`，`TZ=Asia/Shanghai` 解析失败回退 UTC，导致日志与"上次检查"等时间戳比北京慢 8 小时（显示 10:xx 实为 18:xx 北京）
- `Dockerfile` 安装 `tzdata`；`entrypoint.sh` 显式 `export TZ=Asia/Shanghai` 并写入 `/etc/localtime`，日志与仪表盘时间统一为北京时间

### 抓取覆盖扩大（闲鱼网页端搜索单次仅渲染 30 条）
- 实测确诊：闲鱼 web 搜索为固定 30 条渲染，无限滚动不再加载更多（滚动 7 轮 DOM 链接数不变）
- `monitor.py` `_scroll_and_collect` 重写为"智能滚动"：持续滚动直到连续两轮无新商品或达到轮次上限（`MONITOR_SCROLL_ROUNDS`，默认 6），并在 DOM 无增长时立即退出
- `config.py` 新增 `scroll_rounds` 设置项；`MONITOR_MAX_ITEMS` 默认上限 60，服务器版建议 `MONITOR_MAX_ITEMS=100`
- 扩大覆盖的务实手段：**多关键词变体**（如 DGX spark / 英伟达DGX / DGX GB10 / DGX超算），同一商品按 item_id 自动去重不入重复；实测单轮原始覆盖 30→71 条（约 2.4 倍）

### 搜索结果"最新排序 + 价格区间 + 全量翻页"抓取
- 实测确认闲鱼网页搜索为**分页制**（tiny 分页 `1/N` + 箭头按钮；类名带随机 hash 后缀，需属性前缀匹配）
- `monitor.py` 搜索流程升级：点击「新发布」（最新）排序 → 在价格框填入商品价格区间并点「确定」（服务端过滤，分页总量随之缩小）→ **循环翻页直到最后一页/达到上限**（`MONITOR_MAX_SCRAPE_PAGES` 默认 30 页保护）
- 翻页 UI 兼容 tiny 箭头（右箭头 = 最后一个非禁用箭头）与桌面版「下一页」按钮两种形态
- 实测单词提取从 30 条提升至 100+ 条（价格区间内），单轮 4 个有效词合计约 269 条原始数据

### 检索流程"结果导向"收敛
- 翻页健壮性增强：点下一页后**等待页码真正前进**（最多 12s）再收集，避免收集到未刷新的旧页导致漏抓/重复；**连续 2 轮无新增商品即终止**（防空翻死循环）
- 关键词收敛为 **DGX spark 单品**（删除拓展词：英伟达DGX / DGX GB10 / GX10 / GB10 / DGX超算），同一商品按 item_id 去重
- 每词抓取上限 `MONITOR_MAX_ITEMS` 调至 180：实测单轮稳定提取 **178 条**（价格区间 25000-30000 内全部在售）

### 仪表盘价格筛选 + 推送幂等
- `market analysis` 商品列表新增**价格范围筛选**：最低价/最高价输入框 + 筛选/清除按钮，服务端 SQL 过滤（`/api/analysis` 支持 `price_min`/`price_max`），筛选结果同步更新列表、总数与分布图
- 推送去重保障：推送前显式检查 `items.notified`，**已推送过的商品永不重复推送**（只推新增）；`notified` 标记随商品入库时写入，`已推送` 状态在列表可见

### 市场分析排序 + 每日分档抓取量
- 市场分析商品列表新增**价格排序**：`价格: 默认/升序/降序` 按钮循环切换，作用于当前筛选结果
- 抓取量分档：**每日首次检查全量**（180，建当日基线），**当日后续轮次仅增量抓取前 60 条**（`MONITOR_SUBSEQUENT_MAX_ITEMS` 可配）；依据：新发布排序+价格筛选后实际核心商品约 43 条（实测），60 提供合理余量且避开二道贩子重复发布的深翻页噪音

### 商品归档 / 忽略分组 + 通知记录清除
- `items` 表新增 `disposition` 分组字段（new/tracking/ignored）；**重复搜索到的商品不影响分组位置**（upsert 只更新价格等字段）
- 市场分析页新增分组 tabs：**全部 / 待处理 / 价格监测 / 忽略**；新发现的商品带 **NEW** 标注与 `归档`/`忽略` 按钮（归档→价格监测、忽略→折叠置灰），价格监测分组可再忽略、忽略分组可恢复
- 通知页新增**清空所有通知**与**清除某时间之前通知**（`/api/notifications/clear`、`/clear-before`）；清除仅删除通知日志，**`items.notified` 已推送标记保留 → 已通知过的商品不会重复推送**
- 忽略分组增强：**已忽略的商品后续抓取直接跳过**（不更新价格/状态、不评估、不推送，`last_seen` 不再刷新）；市场分析"全部"分类**自动隐藏已忽略商品**（忽略 tab 仍可查看/恢复）

### 修复：万元缩写价格被解析成个位数（如 ¥3 实为 ¥3 万）
- **根因**：闲鱼"新发布"排序下万元商品价格 DOM 呈 `number='3'`、**无 decimal、无"万"字**（纯缩写 `¥3`）形态；原逻辑只在"number+decimal"（如 `3.20`）时做万元判定，无 decimal 分支直接把 `¥3` 解析成 3 元
- **修复**：无 decimal 分支同样启用万元判定——显式"万"、或监测区间支持万元级（max_price 量级）、或字面价远低于监测下限（min_price 量级）任一成立即按万元解释；低预算监控（配件类）不受影响
- **历史数据**：一次性修正 116 条错误价格（¥1/2/3 → ¥1万/2万/3万），市场分析最低价恢复正常
- 新增回归测试（纯缩写 ¥3 万元场景、低预算不误伤场景）

### 推送价格区间（监测区间内的推送分档）
- 监控产品新增**推送价格区间**（`push_min_price`/`push_max_price`，新建/编辑均可设置，留空=同监测区间，校验必须在监测区间内）
- 推送逻辑分档：
  - **推送区间内**（如 26666-27000）的新商品 → 推送通知
  - **监测区间内、推送区间外**（27000-28888）→ 只入库记录，市场分析可见，**不推送**
  - **降价进入推送区间** → 推送降价提醒，且降价记录模块照常显示
  - 日志区分：`匹配 X 条（推送区间外 Y）`
- 图表对应更新：市场分析分布图与走势图绘制**推送线**（虚线 markLine，红=推送上限、绿=推送下限）

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
