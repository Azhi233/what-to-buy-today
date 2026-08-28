# 闲鱼价格监控 — 项目总结

> 基于 Playwright 真实浏览器自动化 + Flask 仪表盘的闲鱼二手商品价格监控系统。只读不写（仅搜索浏览），多维度风险过滤与多渠道推送。

---

## 1. 背景与目标

闲鱼商品价格波动频繁，优质捡漏往往昙花一现。手动搜索效率低、响应不及时。本项目通过自动化搜索、智能过滤与多渠道推送，实现 7×24 小时无人值守监控，用户只需在手机上接收通知后手动下单。

---

## 2. 系统架构

```
┌─────────────────┐     ┌─────────────────────────────────┐     ┌──────────────┐
│  闲鱼网页版      │────▶│  GoofishMonitor（Playwright）    │────▶│  SQLite 数据层│
│  goofish.com    │◀────│  - 真实浏览器（可持久化 profile） │     │  - 商品池     │
│  (搜索API+DOM)  │     │  - DOM + API 双路提取            │     │  - 价格历史   │
│                 │     │  - 卖家信用 / 价格万元修正       │     │  - 通知日志   │
└─────────────────┘     └──────────┬──────────────────────┘     └──┬───────────┘
                                  │                               │
                     ┌────────────▼────────────────────┐  ┌────────▼─────────┐
                     │  MonitorService（后台线程）      │  │  Flask 仪表盘   │
                     │  - 独立 Asyncio 事件循环        │  │  app.py         │
                     │  - 浏览器自动恢复（指数退避）   │  │  - 6 个视图     │
                     │  - Bark 多地址推送              │  │  - Bark 控制盘  │
                     │  - IQR 核心价/风险评估          │  │  - ECharts 图表 │
                     └─────────────────────────────────┘  └──────────────────┘
```

**数据流**：`浏览器搜索 → DOM + API 提取 → 关键词/信用/引流综合评估 → 入库 → 推送（Bark/微信/邮件/Telegram/控制台）→ 仪表盘可视化`

---

## 3. 已实现功能清单

| 模块 | 功能 | 关键点 |
|------|------|--------|
| **搜索与提取** | Playwright 真实浏览器搜索 | 有头模式（降低风控），滚动懒加载，DOM + 拦截 API 双路合并 |
| **价格修正** | 万元缩写修正 | 闲鱼 DOM 把 3.2万显示为 `3/.20` 两段 span，已自动识别为 32000 |
| **关键词过滤** | 排除/必含词 + 型号数字词严格匹配 | `exclude_keywords` 过滤配件，`must_include` 区分同配置，`15` 不会被 `14` 冒充 |
| **卖家信用** | 8 级信用体系过滤 | 自动提取卡片上的 `卖家信用极好 … 信用极差`，低于阈值直接过滤 |
| **引流识别** | 6 类文案规则 | 站外导流/先款诈骗/仿品 → 直接过滤；夸张承诺/压力话术/规避用语 → 标记提醒 |
| **价格异常** | IQR 离群剔除 | `filtered_avg / core_count` 与原始均值对照，万元级差价不再被平均数掩盖 |
| **推送** | Bark 多地址 | 数据库表 `bark_targets` 存储多 Key/server，可在仪表盘增删改、启用/停用、单独测试 |
| **其他推送** | PushPlus / SMTP / Telegram | 统一 `NotifierManager` 抽象，任选其一或全部启用 |
| **仪表盘** | Web 可视化 | 总览、监控商品、市场分析（价格分布直方图 + 多曲线走势）、降价记录、通知记录、设置、Bark 推送 |
| **监控服务** | 后台守护 | 单例 PID 校验、浏览器自动重启、间隔可调、立即检查 |

---

## 4. 核心算法与反制设计

### 4.1 卖家信用评分表

```
信用极好 5 → 信用优秀 4.5 → 百分百好评 4.5 → 信用较好 4 → 信用一般 3
     ↓ 默认阈值
初出茅庐 2 → 信用较差 1 → 信用极差 0  （直接过滤）
```

### 4.2 引流文案规则（`config.py / SCAM_RULES`）

- **站外导流**（加微信/vx/qq/公众号）、**先款定金**（先款/直款/定金）、**仿品标识**（高仿/复刻/原单）：`mode=exclude` 直接过滤。
- **夸张承诺/压力话术/规避用语**：`mode=mark` 仅打标，随推送展示 `⚠️ 注意: …`。
- **手机号正则** + **价格低于中位数 50%**：追加警告标记。

### 4.3 价格核心带计算（`_iqr_trim`）

1. 用 `must_include` 先圈定同配置样本。
2. IQR 剔除：仅保留 `[Q1-1.5×IQR, Q3+1.5×IQR]`，样本 <5 时不剔除。
3. `core_prices` 均值存 `filtered_avg`，与原始 `avg_price` 对照展示。

### 4.4 反封号策略

- 只读不写（不评论/不购买/不发布），请求间隔 ≥15 分钟。
- 默认有头浏览器（`headless=False`），页面可最小化，不刷新登录页。
- `run.py --login` 单独登录流程，`browser_profile/` 持久化 cookie。
- 监控异常分类：非浏览器异常 → 30 秒后重试；浏览器崩溃 → 5–120 秒指数退避自动重启。

---

## 5. 技术栈

| 层 | 选型 | 版本约束 |
|----|------|----------|
| 浏览器自动化 | Playwright | `playwright>=1.40.0` + `chromium` |
| 后端 | Flask | `flask>=3.0.0` |
| 定时 | APScheduler | `apscheduler>=3.10.0`（run.py 旧路径保留） |
| HTTP | requests | `requests>=2.31.0` |
| 数据存储 | SQLite | `sqlite3`（标准库），BOM CSV 导出（Excel 兼容） |
| 可视化 | ECharts | `echarts.min.js`（静态资源） |
| 系统 | Windows / Linux 通用 | GBK→UTF-8 容错，pathlib 兼容 |

---

## 6. 目录结构

```
buy/
├── app.py                 # Flask 仪表盘（含 Bark API、市场分析、CSV 导出）
├── monitor.py             # 闲鱼搜索核心（DOM+API 提取、万元修正、信用/引流评估）
├── monitor_service.py     # 后台监控守护线程（含 IQR 核心价、评估流水）
├── database.py            # SQLite 数据层（商品/历史/通知/setting + bark_targets）
├── notifier.py            # 推送抽象（Bark 多目标动态加载 + PushPlus/SMTP/Telegram）
├── config.py              # 监控商品/信用阈值/引流规则/推送与运行配置（示例化占位符）
├── run.py                 # CLI 入口（--login / --once / --stats）
├── storage.py             # 旧版文件存储（保留兼容）
├── requirements.txt
├── templates/
│   └── index.html         # 单页仪表盘（7 个视图 + 2 个编辑弹窗）
└── static/
    ├── app.js             # 前端逻辑（视图路由 + Bark 控制盘 + 图表）
    ├── style.css
    └── echarts.min.js

运行时生成（已 .gitignore）：
  browser_profile/   data/   monitor.db   *.db   *.log   dashboard.pid   __pycache__/
```

---

## 7. 数据库设计

| 表 | 作用 |
|----|------|
| `monitored_products` | 监控商品（keyword / max_price / min_price / exclude_keywords / must_include / enabled） |
| `items` | 商品池（item_id 去重 / seller_credit / risk_flags / notified / first/last_seen） |
| `price_history` | 价格历史（median_price / avg_price / filtered_avg / core_count / min/max/count） |
| `bark_targets` | Bark 推送目标（label / server / bark_key / enabled） |
| `item_price_changes` | 降价记录 |
| `checks_log` | 检查日志（status / total_items / matched / message） |
| `notifications` | 通知记录 |
| `settings` | 键值设置（interval_minutes / headless 等） |

通过 `_migrate()` 兼容旧库增量建表。

---

## 8. 部署与使用

```bash
# 1) 安装
pip install -r requirements.txt
playwright install chromium

# 2) 登录（仅一次）
python run.py --login   # 浏览器扫码，状态落盘至 browser_profile/

# 3) 配置
#   a) 命令行：编辑 config.py → 填 MONITOR_ITEMS / Bark 等
#   b) 仪表盘：python app.py →  http://127.0.0.1:5000  → 监控商品 / Bark 推送 / 设置

# 4) 运行
python app.py                   # 仪表盘 + 后台监控（自动开浏览器）
python app.py --no-browser      # 仅后端，不弹浏览器
python run.py                   # 无界面持续监控
python run.py --once            # 单轮检查调试
python run.py --stats           # 打印统计摘要
```

---

## 9. 已知取舍与后续方向

- **最近成交价**：闲鱼搜索 API 不返回真实历史成交价，逐商品调成交详情会显著增加请求与风控风险，本版用 IQR `filtered_avg` 作为稳定的参考价（README 已说明）。
- **引流文案**：规则词表在 `config.py / SCAM_RULES` 可热增，后续可考虑标题 embedding 相似度辅助判别。
- **图片与描述**：当前仅标题/价格/地区/信用/成色，后续可扩展图片比对与详情页文本抽取。
- **云端 24 小时**：可部署到轻量云服务器 + 无头模式（需评估风控），或常驻本机最小化窗口运行。

---

## 10. 许可证

仅供学习与个人使用，请遵守闲鱼平台服务条款。
