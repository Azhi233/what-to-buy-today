# 闲鱼监控脚本 — 优化修复清单

> 本清单由测试阶段整理，供后续修复 AI 使用。
> 使用约定（给修复 AI）：
> 1. 按 **P0 → P1 → P2** 顺序修复；P0 是会影响"是否买到对且安全的产品"的核心缺陷，优先。
> 2. 每修完一项，把标题前的 `- [ ]` 改成 `- [x]`，并在该项末尾追加一行 `> ✅ 修复说明：……`（说明改了什么、影响范围）。
> 3. 每项都标注了 **文件 / 行号 / 问题 / 影响 / 建议**，行号基于当前版本，改动后行号可能偏移，以函数名/代码片段定位为准。
> 4. 修复时保持现有架构风格（线程 + asyncio + SQLite），避免引入大重构；改动后 `python -m py_compile` 验证语法。

---

## P0 — 高危：直接影响"买到心仪且安全的产品"

### - [x] P0-1 降价提醒绕过风险过滤（可能推送诈骗/被排除商品）
> ✅ 修复说明：老商品每次价格变化都会重新执行完整风险评估，仅对通过价格、排除词、必含词、卖家信用和诈骗规则校验的降价商品发送提醒。
- **文件**：`monitor_service.py`，函数 `_check_keyword()`（约 416–457 行）
- **问题**：只有**新商品**才调用 `evaluate_item()` 做全套校验（诈骗词 / 卖家信用 / 排除词 / 必含词 / 价格区间）。**已入库的老商品**跳过评估，只要 `upsert_item()` 返回 `price_dropped=True` 就直接推"降价提醒"，不再校验它是否是第一轮就被过滤掉的诈骗/排除商品。
- **影响**：诈骗链接、信用差卖家、含排除词的商品，一旦降价（哪怕 1 元）就会被推送，是最高风险漏洞。
- **建议**：降价前对老商品**重新调用一次 `evaluate_item()`**，仅当 `verdict["pass"]` 时才推降价。参考改法：

```python
# _check_keyword 内，处理 price_dropped 前：
if result["price_dropped"]:
    # 老商品也必须重新过一遍硬校验，避免给已过滤的风险商品推降价
    verdict = evaluate_item(
        item,
        max_price=max_price, min_price=min_price,
        exclude_keywords=exclude_keywords, must_include=must_include,
        min_seller_credit=MIN_SELLER_CREDIT, median_price=median_price,
        scam_rules=SCAM_RULES,
    )
    if verdict["pass"]:
        price_drop_notices.append(item)
```

---

### - [x] P0-2 万元缩写误判 1~9.99 元低价商品为万元
> ✅ 修复说明：万元换算现在要求显式“万”或监控最高价达到万元量级；普通低价小数商品按字面价格解析。
- **文件**：`monitor.py`，函数 `parse_price_extended()`（约 113–116 行）
- **问题**：只要价格元素拆成 `number`(1~9) + `decimal`(非空)，就按"X.YY 万元"×10000 处理，无法区分"3.5 元的小配件"与"3.5 万元的数码产品"。
- **影响**：实测 `¥3.5 → 35000`、`¥8.8 → 88000`、`¥9.9 → 99000`，污染价格统计与降价判断，误导用户。
- **建议**：万元判定需要结合**品类量级/价格量级**。可选方案：
  - 用监控商品的 `max_price` 做参考：若 `num_val * 10000` 远超 `max_price`，且 `num_val` 直译价明显低于量级，则按直译（`num_val + frac`）处理；
  - 或优先识别 DOM 中是否真的存在"万"字 / 价格单位节点，而不是仅凭 number+decimal 结构猜测。

---

### - [x] P0-3 关键词匹配漏判型号（"iPhone 15 256G"冒充"iPhone 15 Pro Max"）
> ✅ 修复说明：常见型号后缀 Pro/Max/Plus/Mini/Ultra/Pro Max 与数字型号一样要求全部命中，减少不同版本误推送。
- **文件**：`monitor.py`，函数 `matches_keyword()`（约 581–615 行）
- **问题**：函数只强制"**含数字的词元**"（如 `15`）必须命中，`Pro / Max / Plus / Mini / Ultra` 等**字母型号词**没有强制匹配；只要命中一个长词（如 `iPhone`）+ 任一其他词元即通过。
- **影响**：实测搜 `iPhone 15 Pro Max` 时，标题 `iPhone 15 256G 国行`、`iPhone 15 128G` 均误通过，会把基础款/低配款当目标型号推送。默认示例 `must_include=["国行","256G"]` 拦不住。
- **建议**：把字母型号词也纳入"必须命中"集合（与数字型号词同等对待），例如维护一个型号后缀词表 `PRO_MODEL_WORDS = {"pro","max","plus","mini","ultra","promax"}`，标题必须命中其中存在的项；或在 `must_include` 明确要求用户写型号差异词并在前端提示。

---

### - [x] P0-4 `search()` 吞掉浏览器崩溃异常，自动重启失效
> ✅ 修复说明：浏览器/页面上下文关闭类异常统一重新抛出，由监控服务外层执行自动重启；普通搜索异常仍返回空结果。
- **文件**：`monitor.py`，函数 `search()`（约 490–547 行）
- **问题**：`page = await self._new_page()` 在 `try` 外，但 `page.goto()` / `wait_for_selector()` / 滚动 / DOM 解析全在 `try` 内，`except Exception` 统一 `return []`。浏览器在**搜索过程中**崩溃（如 `Target page/context closed`）也被当成"无结果"返回。
- **影响**：`monitor_service` 外层的 `_is_browser_error()` 永远收不到信号，浏览器不会重启；连续 3 轮空结果还会被误报成"登录过期/被风控"。
- **建议**：在 `search()` 的 `except` 里判断浏览器类异常并**重新 `raise`**，仅对真正的"无结果"返回 `[]`：

```python
except Exception as e:
    if _is_browser_error(e):   # 复用/引入同一判断函数
        raise
    logger.error(f"搜索 {keyword} 失败: {e}")
    return []
```

> 注：`_is_browser_error()` 目前在 `monitor_service.py`，若 `monitor.py` 需要复用，建议把它下沉到 `monitor.py` 或公共工具模块，避免循环导入。

---

## P1 — 中危：功能正确性 / 状态误导

### - [x] P1-5 登录失败时状态被覆盖回"运行中"
> ✅ 修复说明：新增登录失败标志，`_check_round()` 的 finally 不再覆盖显式 error 状态。
- **文件**：`monitor_service.py`，函数 `_check_round()`（约 321–328 与 351–355 行）
- **问题**：登录检查失败时设置 `self.status = "error"` 并 `return`，但 `finally` 里 `if exc is None: self.status = "running"` 会立刻覆盖回 `running`。
- **影响**：仪表盘状态点仍是绿色"运行中"，用户看不到"未登录/需扫码"。
- **建议**：`finally` 只在"本轮正常完成"时置 `running`；登录失败走独立分支，不被 `finally` 覆盖（例如用标志位区分是否已显式设置 error）。

### - [x] P1-6 `/api/status` 的 `login_ok` 硬编码为 `True`
> ✅ 修复说明：`login_ok` 改为返回监控服务实际检测结果。
- **文件**：`app.py`（约 106 行）
- **问题**：`"login_ok": True` 写死，前端永远显示"已登录"。
- **影响**：误导用户，无法据此判断是否需重新扫码。
- **建议**：从 `service` 暴露真实登录检测结果（复用 `service.last_error` 或新增字段），或直接移除该字段。

### - [x] P1-7 `run.py`（CLI）与 `app.py`（仪表盘）两套过滤逻辑不一致
> ✅ 修复说明：CLI 正常监控和 `--once` 已切换到 MonitorService + SQLite 管线，与仪表盘共用过滤、风控和去重逻辑。
- **文件**：`run.py` 函数 `check_once()`（约 81–110 行）vs `monitor_service.py`
- **问题**：`run.py` 用旧的 `filter_items()`：**没有**卖家信用、引流文案、`must_include`、万元修正；去重用文件 `seen_items.json`，而仪表盘用 SQLite `items.item_id UNIQUE`。
- **影响**：跑 `python run.py` 会收到信用差/诈骗/配置不符的商品推送；两入口混跑产生重复通知。
- **建议**：统一收敛到 `MonitorService` 这一条管线，`run.py` 只做 CLI 壳；去重只保留数据库一套。

### - [x] P1-8 `ConsoleNotifier` 恒真导致"推送/控制台"标记失效
> ✅ 修复说明：NotifierManager 的成功渠道列表排除 ConsoleNotifier，通知记录仅在真实外部渠道成功时标记为推送。
- **文件**：`notifier.py`（`ConsoleNotifier.send` 约 208–218、`NotifierManager.send` 约 266–278）+ `monitor_service.py`（约 477、519 行）
- **问题**：`ConsoleNotifier.send()` 永远返回 `True` 且总在渠道列表里，`NotifierManager.send()` 返回的成功列表永远非空，于是 `log_notification(..., "推送" if ok else "控制台")` 永远走"推送"。实测 `monitor.db` 99 条通知 `channel` 只有同一个值。
- **影响**：通知记录无法区分"真推到手机"与"仅控制台打印"，排查失败困难。
- **建议**：`NotifierManager.send()` 返回的"成功渠道"应**排除** `ConsoleNotifier`；`_notify_match/_notify_drop` 依据真实渠道数判断 `ok`。

### - [x] P1-9 `PUT /api/products/<id>` 缺少 `max_price` 校验
> ✅ 修复说明：新增和编辑接口统一校验最高价大于 0、最低价不小于 0 且不超过最高价。
- **文件**：`app.py` 函数 `api_update_product()`（约 185–199 行）
- **问题**：`POST`（新增）有 `max_price <= 0` 校验，`PUT`（编辑）没有；也没有 `min_price <= max_price` 校验。
- **影响**：可把最高价改成 0/负数，导致所有结果被"高于最高价"过滤、不再推送；或 min>max 使区间为空。
- **建议**：`PUT` 复用 `POST` 的价格校验，并增加 `min_price <= max_price` 检查。

---

## P2 — 低危 / 健壮性

### - [x] P2-10 排除词/必含词被空格硬拆分
> ✅ 修复说明：词组现在仅按中英文逗号分隔，`iPhone 14` 等包含空格的条件保持完整。
- **文件**：`database.py` 函数 `parse_exclude_keywords()`（约 514–519 行）
- **问题**：`replace(" ", ",")` 把 `"iPhone 14"` 拆成 `["iPhone","14"]`，英文多词条件失效。
- **建议**：只按逗号（含中文逗号 `，`）分隔，保留词内部空格。

### - [x] P2-11 无 `seller_credit` 文本的商品绕过信用过滤
> ✅ 修复说明：信用为空或无法解析时标记为“卖家信用未知”并硬过滤，避免未知信用商品静默放行。
- **文件**：`monitor.py` 函数 `evaluate_item()`（约 735–741 行）
- **问题**：`if credit:` 只有非空才过滤；抓不到信用文本时等于关闭信用门槛。
- **建议**：抓不到信用时按"未知"从严处理，或显式标记 `卖家信用未知`，避免静默放行。

### - [x] P2-12 清理策略时区不一致
> ✅ 修复说明：清理查询统一使用 `julianday('now','localtime')`，与本地时间字段保持一致。
- **文件**：`database.py` 函数 `cleanup_expired()`（约 426、429 行）
- **问题**：`julianday('now')` 是 UTC，而 `last_seen/check_time` 是 `datetime('now','localtime')` 字符串，保留期会偏差约 8 小时。
- **建议**：统一用 `julianday('now','localtime')`。

### - [x] P2-13 同步网络/`time.sleep` 阻塞 asyncio 事件循环
> ✅ 修复说明：通知发送已移入后台线程执行，Bark 限流等待不会阻塞监控事件循环。
- **文件**：`notifier.py`（`requests`、`smtplib`、`BarkNotifier.send` 的 `time.sleep`）
- **问题**：`NotifierManager.send()` 是同步函数，在 async 上下文里被同步调用；SMTP 超时 15s 会卡住整个监控线程。
- **建议**：用 `asyncio.to_thread()` / `loop.run_in_executor()` 包装同步发送；Bark 的限流 `time.sleep` 改为异步等待。

### - [x] P2-14 降价无阈值，微小降价也推送
> ✅ 修复说明：降价需同时达到至少 20 元或 5% 的相对阈值后，才会生成降价提醒。
- **文件**：`database.py` 函数 `upsert_item()`（约 300 行）
- **问题**：`price_dropped = old["price"] and price < old["price"]`，任何降价都算。
- **建议**：加最低降幅（如 ≥5% 或 ≥20 元）再推送，避免刷屏。

### - [x] P2-15 Bark Key 明文返回
> ✅ 修复说明：Bark 列表接口不再返回明文 Key；编辑时 Key 留空表示保持原值。
- **文件**：`app.py` 函数 `api_bark_targets()`（约 355–366 行）
- **问题**：GET 同时返回 `bark_key`（明文）和 `bark_key_masked`，脱敏字段形同虚设。
- **建议**：GET 只回脱敏值；编辑时用单独接口，或接受"不修改则留空、提交时才覆盖"。

### - [x] P2-16 `interval_minutes` 被破坏时死循环报错
> ✅ 修复说明：轮询间隔解析失败时回退到 30 分钟，并记录警告，不再触发持续异常重试。
- **文件**：`monitor_service.py`（约 171 行）
- **问题**：`float(self.db.get_setting("interval_minutes", 30))` 若为非数字会抛异常，被外层 `except` 捕获后 30s 重试，永不停。
- **建议**：`float` 前 try/except，回退默认 30。

### - [x] P2-17 `seed_products_from_config` 仅在表空时同步，删除后重启会回灌示例
> ✅ 修复说明：首次同步后写入 `products_seeded` 设置标志，用户删除全部商品后重启不会再次回灌示例。
- **文件**：`monitor_service.py` 函数 `seed_products_from_config()`（约 51–70 行）
- **问题**：用户把监控商品全删后重启，会把 config 里的示例商品重新灌入。
- **建议**：加"已初始化"标记（如 settings 里写 `seeded=1`），避免反复回灌示例。

### - [x] P2-18 降价记录链接 `item_id` 未转义（潜在 XSS）
> ✅ 修复说明：降价记录链接中的 `item_id` 已统一经过前端 `esc()` 转义。
- **文件**：`static/app.js`（约 451 行）
- **问题**：`href="https://www.goofish.com/item?id=${c.item_id}"` 未走 `esc()`。
- **建议**：统一 `esc(c.item_id)`。本地单机风险低，但顺手加固。

---

## 附：验证中表现正常的点（避免重复排查）

- `_iqr_trim()` 能正确剔除极端离群值 ✅
- `credit_score()` 各信用等级映射正确（含"百分百好评"→4.5、"信用极差"→0）✅
- `evaluate_item()` 对诈骗词、低信用、缺必含词的硬过滤逻辑正确 ✅
- SQLite 线程安全（`_lock` + WAL）与旧库 `_migrate()` 增量迁移设计合理 ✅
- 单实例 PID 保护、浏览器指数退避重启、±5 分钟抖动等稳定性设计到位 ✅

---

## 修复优先级总览

| 优先级 | 条目 | 一句话原因 |
|---|---|---|
| **P0 立即修** | P0-1 / P0-2 / P0-3 / P0-4 | 决定"是否买到对且安全的产品" |
| **P1 尽快修** | P1-5 ~ P1-9 | 状态误导、双管线不一致、通知标记失效 |
| **P2 后续优化** | P2-10 ~ P2-18 | 健壮性、边界条件、体验 |
