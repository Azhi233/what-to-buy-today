# 闲鱼价格监控 🕵️

自动化监控闲鱼特定商品价格，发现合适价格后通过多渠道推送通知，你手动下单。

## 特性

- 🔍 **真实浏览器监控**：使用 Playwright 模拟真人浏览，行为与手动使用无异
- 🔐 **只读不写**：不评论、不购买、不发布，只搜索浏览，封号风险极低
- 💰 **价格区间过滤**：只推送你接受价格范围内的商品
- ⭐ **卖家信用过滤**：自动识别并过滤"初出茅庐"（新号）、"信用较差/极差"卖家
- 🛡️ **引流识别**：识别站外导流（加微信/QQ）、先款诈骗、仿品文案并直接过滤
- ⚠️ **风险标记**：夸张承诺、压力话术、价格异常（明显低于市场价）推送时标注提醒
- 🚫 **关键词排除**：自动跳过换屏、拆机、模型机等垃圾信息
- 🔔 **多渠道通知**：Bark / PushPlus / 邮件 / Telegram，总有一个能收到
- 💾 **防重复推送**：已推送过的商品自动记录，不重复打扰
- 📊 **Web 仪表盘**：可视化查看商品、市场分析、降价记录（`python app.py`）
- 🔄 **自动恢复**：浏览器意外关闭/崩溃时自动重启，无需人工干预

## 入口说明

- `python app.py`：**唯一常驻入口**，启动仪表盘 + 后台监控（推荐）。
- `python run.py`：**运维入口**，仅用于 `run.py --login` 扫码登录、`--once` 单轮检查、`--stats`/`--check` 状态检查。

两者共用同一套 SQLite 数据与浏览器登录态（`data/`、`browser_profile/`），**不要同时常驻两个入口**（`app.py` 已有单实例保护）。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
# 安装 Playwright 浏览器（首次需要）
playwright install chromium
```

> 💡 如果浏览器下载太慢，可以设置国内镜像：
> ```bash
> set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
> playwright install chromium
> ```

### 2. 登录闲鱼（只需一次）

> ⚠️ 闲鱼网页版搜索**需要登录**才能看到搜索结果。
> 本工具不自动登录，会打开浏览器窗口让你手动扫码。

```bash
python run.py --login
```

在打开的浏览器窗口中，用**闲鱼 App 扫码登录**。登录成功后状态自动保存，之后无需再登录。

> 💡 **安全建议**：如果担心风险，建议用一个**专门的小号**（手机号注册的新账号）来监控，完全不影响主账号。

### 3. 配置

**本地开发**：直接编辑仓库中的 `config.py` 的 `MONITOR_ITEMS`（已为占位模板）；密钥通过环境变量注入（推荐容器/服务部署）。

```bash
# 复制 .env.example -> .env 配合 docker compose / systemd 使用
cp .env.example .env
```

```python
# config.py 示例
MONITOR_ITEMS = [
    {
        "keyword": "iPhone 15 Pro Max",
        "max_price": 5000,
        "min_price": 500,
        "exclude_keywords": ["换屏", "拆机", "模型机"],
        "must_include": ["国行", "256G"],
    },
]
```

敏感配置支持环境变量覆盖（见 `config.py` / `.env.example`），例如 `DASHBOARD_TOKEN`、`BARK_KEY`、`SMTP_PASSWORD` 等。

### 4. 配置通知渠道（任选一个）

| 渠道 | 说明 | 获取方式 |
|------|------|---------|
| **Bark** (推荐) | iPhone 推送 | App Store 下载 Bark，复制 Key |
| **PushPlus** | 微信推送 | 访问 pushplus.plus 获取 Token |
| **SMTP** | 邮件推送 | 邮箱开通 SMTP 获取授权码 |
| **Telegram** | 电报推送 | BotFather 创建 Bot 获取 Token |

### 5. 启动监控

```bash
python app.py                          # 常驻：仪表盘 + 后台监控（自动开浏览器）
python app.py --no-browser --port 5000 # 常驻（容器/服务推荐）：不弹浏览器
python run.py --login                  # 运维：扫码登录
python run.py --once                   # 运维：只检查一轮后退出
python run.py --stats                  # 运维：查看 SQLite 统计
python run.py --check                  # 运维：配置与依赖检查
```

浏览器窗口会保持打开（可最小化），这是**故意的**——闲鱼风控会拦截无头浏览器。部署方式见 `deploy/README.md`。

## 卖家信用与防骗过滤

闲鱼搜索结果卡片会显示卖家信用等级，本工具自动提取并过滤高风险卖家：

| 信用等级 | 含义 | 处理 |
|---------|------|------|
| 信用极好 / 信用优秀 / 百分百好评 | 优质卖家 | ✅ 正常推送 |
| 信用较好 / 信用一般 | 普通卖家 | ✅ 正常推送 |
| **初出茅庐** | **新注册小号（引流/诈骗高发）** | ❌ 默认过滤 |
| **信用较差 / 信用极差** | **差评卖家** | ❌ 默认过滤 |

可在 `config.py` 中调整 `MIN_SELLER_CREDIT` 门槛。

### 引流小号常见文案特点

| 类型 | 典型文案 | 处理方式 |
|------|---------|---------|
| **站外导流** | "加微信看视频"、"vx聊"、"加qq" | 直接过滤（脱离平台保障） |
| **先款诈骗** | "先款秒发"、"交定金"、"直款" | 直接过滤 |
| **仿品假货** | "高仿/精仿/复刻"、"原单尾货"、"a货" | 直接过滤 |
| **夸张承诺** | "支持专柜验货"、"假一赔十"、"内部渠道" | 推送时标记 ⚠️ |
| **压力话术** | "仅此一台"、"手慢无"、"秒出" | 推送时标记 ⚠️ |
| **规避用语** | "懂的都懂"、"懂的来" | 推送时标记 ⚠️ |
| **价格异常** | 价格低于市场价一半（真捡漏 or 引流） | 推送时标记 ⚠️ |

触发词表在 `config.py` 的 `SCAM_RULES` 中维护，可按需增删。

## 反封号注意事项

1. **登录小号监控**：用专门的小号，风险与主账号完全隔离
2. **间隔时间**：默认 30 分钟检查一次，不要低于 15 分钟
3. **不要改无头模式**：`headless: True` 很容易触发闲鱼"非法访问"拦截
4. **不买不聊**：看到合适价格用手机 App 手动下单
5. **看到合适价格及时下单**：闲鱼好货抢手

## 项目结构

```
buy/
├── app.py                 # 唯一常驻入口：Flask 仪表盘 + 后台监控
├── run.py                 # 运维入口：--login / --once / --stats / --check
├── config.py              # 可提交的占位配置（MONITOR_ITEMS 在此编辑，密钥走环境变量）
├── .env.example           # 环境变量模板（容器/systemd 推荐）
├── monitor.py             # 闲鱼搜索核心（含信用提取、引流识别）
├── monitor_service.py     # 后台监控服务（浏览器自动恢复）
├── database.py            # SQLite 数据层
├── notifier.py            # 多渠道通知系统
├── requirements.txt       # 运行依赖
├── requirements-dev.txt   # 开发依赖（pytest/ruff）
├── pyproject.toml         # pytest/ruff 配置
├── Makefile               # make check / test / lint
├── CHANGELOG.md           # 变更记录（语义化版本）
├── deploy/                # Docker/systemd 部署说明
├── templates/             # 仪表盘页面模板
├── static/                # 仪表盘前端资源
├── tests/                 # pytest 用例
├── browser_profile/       # 浏览器登录态（自动生成，已忽略）
└── data/                  # SQLite 数据（data/monitor.db，已忽略）
```

## 开发与质量检查

```bash
pip install -r requirements-dev.txt
make check        # compileall + ruff + pytest
make lint         # ruff check .
make test         # pytest
```

CI 见 `.github/workflows/ci.yml`。变更记录见 `CHANGELOG.md`，历史问题/修复追溯见 `OPTIMIZATION_CHECKLIST.md`。

## 常见问题

**Q: 会封号吗？**
A: 本工具只搜索不进行任何写操作，行为与真人浏览一致。建议使用小号监控，彻底隔离风险。

**Q: 为什么必须登录？**
A: 闲鱼网页版搜索 API 要求登录。登录后我们只做搜索（最普通的用户行为），风险很低。

**Q: 推送没收到怎么办？**
A: 先用 `python run.py --once` 或 `python run.py --check` 观察输出；再检查通知渠道配置与 `DASHBOARD_TOKEN`。

**Q: 找不到商品？**
A: 确认已登录（`python run.py --once` 会提示）。若仍找不到，可能是搜索无结果或页面结构变化。

**Q: 电脑关机了监控还运行吗？**
A: 不运行。需要电脑保持开机。如果想 24 小时监控，可以考虑把项目部署到云服务器（见 `deploy/README.md`）。
