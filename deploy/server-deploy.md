# 服务器版部署手册（本地扫码模式）

本仓库已内置服务器部署所需的全部文件，你只需：**本地扫码一次 → 上传登录态 → 服务器跑起来**。服务器上只监控 **DGX Spark**（25000–30000 元），标题含"询价/私聊"的商品跳过，全程无人值守。

## 一、目录结构（服务器版相关）

```
.
├── Dockerfile                 # 已含 Xvfb 虚拟显示（有头浏览器，规避闲鱼无头拦截）
├── entrypoint.sh              # 启动脚本：Xvfb + 降权非 root 运行
├── docker-compose.server.yml  # 服务器编排（本次新增）
├── .env.server.example        # 服务器配置模板（本次新增，DGX Spark 单品）
├── config.py                  # 已支持 MONITOR_KEYWORD 单品覆盖
├── tools/check_login.py       # 本地登录态自检脚本（本次新增）
└── deploy/server-deploy.md    # 本文档
```

## 二、快速开始（3 步）

### 第 1 步：本地扫码登录（只需一次）

在有桌面的本地机器上（本项目目录）：

```bash
python run.py --login        # 自动弹浏览器 → 用闲鱼 App 扫码
```

登录成功后（窗口自动识别并提示"登录成功"），运行自检脚本确认登录态：

```bash
python tools/check_login.py
# 期望输出：  [✓] 检测到有效登录态（tracknick, unb），可上传服务器复用。
```

若显示未登录，请重新扫码后再检查，**不要把未登录的目录传上去**。

### 第 2 步：打包上传登录态与配置

```bash
cd 本仓库目录
# 1) 配置服务器环境（按需填推送密钥）
cp .env.server.example .env.server
#    编辑 .env.server：至少开启一个推送渠道（Bark/邮件/TG），并确认价格/屏蔽词

# 2) 上传到服务器（示例：用户 ubuntu，目录 /opt/xianyu）
scp -r browser_profile data .env.server docker-compose.server.yml ubuntu@<服务器IP>:/opt/xianyu/
```

> `browser_profile/` 是登录态，`data/` 是数据库（含商品配置），都要传。首次可只传这两个目录。

### 第 3 步：服务器启动

```bash
ssh ubuntu@<服务器IP>
cd /opt/xianyu
docker compose -f docker-compose.server.yml up -d --build
```

启动后验证：

```bash
# 健康检查（有 ok:true、status:running 即正常）
curl http://127.0.0.1:5000/api/healthz

# 日志（应看到"正在搜索: DGX spark"，且无无头拦截报错）
docker compose -f docker-compose.server.yml logs -f --tail 50
```

仪表盘：`http://<服务器IP>:5000`（配置了 `DASHBOARD_TOKEN` 则访问需带 `?token=` 或请求头）。

## 三、本服务器版做了什么 / 没做什么

**做了：**
- 只监控 `DGX spark`；价格 `min=25000, max=30000`；屏蔽 `询价,私聊`（见 `.env.server.example`）
- 服务器用 **Xvfb 虚拟显示 + 有头浏览器**（`MONITOR_HEADLESS=0`），保持真实浏览器行为，降低风控拦截概率
- 登录态复用本地扫码结果（`run.py --login` 生成的 `browser_profile`）
- 服务器端有商品+有推送渠道时，前端**不弹新手指引**（引导按配置状态自动判定）

**没做（明确边界）：**
- 服务器内不扫码登录（采用本地扫码模式；`browser_profile` 是复用本地登录态）
- 不改 `monitor.py` 风控逻辑；不改 `Dockerfile`/`entrypoint.sh`（已满足服务器有头）
- 不做多商品服务器配置（保持单品最小面；有需要可在 `.env.server` 追加，或改走 `config.py`）

## 四、运维

### 登录过期 / 需要重新登录

服务器监控若连续 3 轮抓不到商品，会提示"登录过期或被风控"。处理：

```bash
# 1) 本地重新扫码，覆盖本地 browser_profile
python run.py --login
python tools/check_login.py

# 2) 重新上传 browser_profile 到服务器并重启容器
scp -r browser_profile ubuntu@<服务器IP>:/opt/xianyu/
docker compose -f docker-compose.server.yml restart xianyu-monitor
```

### 更新代码

```bash
cd /opt/xianyu
git pull            # 或在服务器上更新到最新版
docker compose -f docker-compose.server.yml up -d --build --force-recreate
```

### 查看统计 / 手动立即检查

- 仪表盘右上角"立即检查"；或 `docker exec xianyu-monitor python run.py --once`
- 数据视图（降价/历史）都在仪表盘内

## 五、安全与资源

- `DASHBOARD_TOKEN`：监听 `0.0.0.0`（公网可达）时**务必设置**，否则任何人可访问仪表盘与配置。
- `browser_profile`/`data` 用 volume 持久化，`restart: unless-stopped` 崩溃自动拉起。
- 资源：默认 30 分钟一轮，单商品，内存占用约 300–500MB（含常驻浏览器），2 核 2G 即可。