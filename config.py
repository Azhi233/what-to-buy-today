"""
============================================
  闲鱼价格监控 - 配置（可提交的占位模板）
============================================
使用说明：
  1. 修改 MONITOR_ITEMS 添加你要监控的商品
  2. 根据需要开启推送渠道（Bark / PushPlus / 邮件 / Telegram）
  3. 首次运行 python run.py --login 扫码登录闲鱼（只需一次）
  4. 运行 python app.py 即可启动监控 + 仪表盘

⚠️ 密钥边界：本文件被 Git 追踪，但仅含占位默认值，不含真实密钥。
   真实密钥请通过环境变量（或 .env）注入，勿在本文件填写真实密钥后提交。
   容器/服务部署推荐用环境变量（见 .env.example）。
============================================
"""

import os
import sys

# 项目根目录：所有默认路径（数据/浏览器配置/日志）基于此，
# 避免从服务/计划任务启动（工作目录非项目根）时把数据写到错误位置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env(name: str, default: str = "") -> str:
    """读取并清理环境变量；未设置时回退到本地文件值。"""
    return os.environ.get(name, default).strip()


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _warn_invalid(name: str, value: str, expected: str) -> None:
    """环境变量非法时告警，便于部署时发现配置错误。"""
    print(f"[config] 环境变量 {name}={value!r} 无法识别（期望 {expected}），已回退到默认值", file=sys.stderr)


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    lowered = value.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    _warn_invalid(name, value, "1/0/true/false/yes/no/on/off")
    return default


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        _warn_invalid(name, value, "整数")
        return default


# 仪表盘 API 鉴权 Token（环境变量 DASHBOARD_TOKEN；空 = 不启用鉴权，仅建议本机使用）
DASHBOARD_TOKEN = _env("DASHBOARD_TOKEN")

# ═══════════════════════════════════════════
#  监控商品列表 - 在这里添加你想监控的商品
# ═══════════════════════════════════════════
MONITOR_ITEMS = [
    # 示例：监控 "iPhone 15 Pro Max"，期望价格 <= 5000，排除低价垃圾信息
    {
        "keyword": "iPhone 15 Pro Max",
        "max_price": 5000,        # 最高接受价格（元）
        "min_price": 500,         # 最低价格过滤（排除明显标错或骗局）
        "exclude_keywords": [     # 标题包含这些关键词的商品跳过
            "换屏", "爆屏", "拆机", "配件", "模型", "模型机",
            "绑定id", "有锁", "卡贴", "扩容"
        ],
        # 必须包含词：标题必须包含这些词才视为同一配置/正品，
        # 用于过滤配件、壳、贴膜等无关产品及不同配置，保证价格有参考意义。
        # 留空 [] 表示不强制。
        "must_include": ["国行", "256G"],
    },
    # 示例：监控 "MacBook Pro M3"
    # {
    #     "keyword": "MacBook Pro M3",
    #     "max_price": 9000,
    #     "min_price": 1000,
    #     "exclude_keywords": ["配件", "模型", "外壳", "贴膜"],
    #     "must_include": ["16G"],
    # },
    # 示例：监控 "索尼 A7M4"
    # {
    #     "keyword": "索尼 A7M4",
    #     "max_price": 13000,
    #     "min_price": 5000,
    #     "exclude_keywords": ["配件", "镜头盖"],
    #     "must_include": ["机身"],
    # },
    # 示例：监控 "任天堂 Switch OLED"
    # {
    #     "keyword": "任天堂 Switch OLED",
    #     "max_price": 1500,
    #     "min_price": 500,
    #     "exclude_keywords": ["壳", "贴膜", "包", "卡带"],
    #     "must_include": ["日版", "续航"],
    # },
]

# ═══════════════════════════════════════════
#  卖家信用与防骗过滤
# ═══════════════════════════════════════════

# 最低接受的卖家信用等级。
# 等级从高到低：信用极好 > 信用优秀 > 信用较好 > 信用一般 > 初出茅庐 > 信用较差 > 信用极差
# 低于此等级的卖家商品会被过滤（"初出茅庐"=新注册小号，风险高；"较差/极差"=差评卖家）
# 设为 "" 表示不按信用过滤。
MIN_SELLER_CREDIT = "信用一般"
# 信用字段抓取不到时是否过滤。默认 False：仅标记“信用未知”，避免因页面字段缺失漏掉正常商品。
STRICT_UNKNOWN_CREDIT = False

# 引流/虚假商品文案识别规则。
# 每条规则: 名称 -> {"keywords": [触发词], "mode": "exclude" 或 "mark"}
#   exclude = 直接过滤（高风险：站外交易脱离平台保障、仿品、先款诈骗）
#   mark    = 仅标记并在推送中提示（可能误伤真实卖家，保留但不隐藏）
SCAM_RULES = {
    # 站外导流：把交易引导到微信/QQ，脱离平台保障（最高风险）
    "站外导流": {
        "keywords": [
            "加微信", "加vx", "加v信", "微信同号", "加qq", "加企鹅",
            "公众号", "微信聊", "q聊", "vx咨询", "加v",
        ],
        "mode": "exclude",
    },
    # 先款/定金：要求先付款后发货，典型诈骗话术
    "先款定金": {
        "keywords": [
            "先款", "直款", "先付", "定金", "交定金", "意向金",
            "转账秒发", "款到发货", "付了再发",
        ],
        "mode": "exclude",
    },
    # 仿品标识：明确是高仿/假货
    "仿品标识": {
        "keywords": [
            "高仿", "精仿", "复刻", "原单", "尾货", "工厂流出",
            "a货", "顶级货", "1:1", "仿品",
        ],
        "mode": "exclude",
    },
    # 夸张承诺：假货商常用话术
    "夸张承诺": {
        "keywords": [
            "假一赔十", "支持专柜验货", "专柜小票", "海关扣留",
            "内部渠道", "原厂流出", "保证正品假一赔命",
        ],
        "mode": "mark",
    },
    # 压力话术：制造稀缺感催促冲动下单
    "压力话术": {
        "keywords": [
            "仅此一台", "最后一台", "最后一天", "秒出",
            "手慢无", "错过拍大腿", "不买后悔",
        ],
        "mode": "mark",
    },
    # 规避用语：用隐晦词暗示非正规渠道
    "规避用语": {
        "keywords": [
            "懂的都懂", "懂的来", "dddd", "zzzz", "mmp", "dddddd",
        ],
        "mode": "mark",
    },
}

# 价格异常提醒：商品价格低于同关键词本轮市场中位价该比例时，推送中标记提醒。
# 设为 0 关闭。真捡漏和引流标低价都符合此特征，因此只标记、不过滤。
PRICE_ANOMALY_RATIO = 0.5

# ═══════════════════════════════════════════
#  推送通知配置（选择一个或多个开启即可）
#  每项均支持环境变量覆盖：BARK_ENABLED / BARK_KEY / BARK_SERVER、
#  PUSHPLUS_ENABLED / PUSHPLUS_TOKEN、SMTP_ENABLED / SMTP_HOST / SMTP_PORT /
#  SMTP_USER / SMTP_PASSWORD / SMTP_TO、TELEGRAM_ENABLED / TELEGRAM_BOT_TOKEN /
#  TELEGRAM_CHAT_ID
#  注意：enabled=True 且密钥为占位符时该渠道不会真正启用，请填入真实密钥。
# ═══════════════════════════════════════════

# --- Bark（iOS 推送，推荐） ---
# 在 App Store 下载 Bark，复制给你的 Key
BARK_CONFIG = {
    "enabled": _env_bool("BARK_ENABLED", False),
    "server": _env("BARK_SERVER", "https://api.day.app"),
    "key": _env("BARK_KEY", "your_bark_key_here"),
}

# --- PushPlus（微信推送） ---
# 在 http://www.pushplus.plus 获取 Token
PUSHPLUS_CONFIG = {
    "enabled": _env_bool("PUSHPLUS_ENABLED", False),
    "token": _env("PUSHPLUS_TOKEN", "your_pushplus_token_here"),
}

# --- SMTP 邮件推送 ---
# 支持 QQ邮箱 / Gmail / 163 等
SMTP_CONFIG = {
    "enabled": _env_bool("SMTP_ENABLED", False),
    "host": _env("SMTP_HOST", "smtp.qq.com"),       # SMTP 服务器
    "port": _env_int("SMTP_PORT", 465),             # SSL 端口
    "user": _env("SMTP_USER", "your_email@qq.com"), # 邮箱地址
    "password": _env("SMTP_PASSWORD", "your_smtp_code"),  # SMTP 授权码（非邮箱密码）
    "to": _env("SMTP_TO", "your_email@qq.com"),     # 接收通知的邮箱
}

# --- Telegram Bot 推送 ---
# 在 Telegram 中搜索 @BotFather 创建 Bot 获取 Token；
# 发送 /start 后访问 https://api.telegram.org/bot<YourToken>/getUpdates 获取 chat_id
TELEGRAM_CONFIG = {
    "enabled": _env_bool("TELEGRAM_ENABLED", False),
    "bot_token": _env("TELEGRAM_BOT_TOKEN", "your_bot_token_here"),
    "chat_id": _env("TELEGRAM_CHAT_ID", "your_chat_id_here"),
}

# ═══════════════════════════════════════════
#  监控运行设置
#  环境变量覆盖：MONITOR_INTERVAL_MINUTES / MONITOR_HEADLESS /
#  MONITOR_BROWSER_PROFILE / MONITOR_DATA_DIR / MONITOR_MAX_ITEMS
# ═══════════════════════════════════════════
MONITOR_SETTINGS = {
    # 每轮监控的间隔时间（分钟）—— 建议 30 分钟以上
    # 太频繁容易被闲鱼风控识别为机器人
    "interval_minutes": _env_int("MONITOR_INTERVAL_MINUTES", 30),

    # 浏览器模式（重要！）
    # 闲鱼风控会拦截无头浏览器（headless=True 时容易触发"非法访问"）
    # 强烈建议保持 False（有头模式，浏览器窗口可以最小化）
    # 如果想尝试无头模式，先登录后再改，但有一定风险
    "headless": _env_bool("MONITOR_HEADLESS", False),

    # 浏览器配置目录（保存登录状态）
    # 首次运行 python run.py --login 扫码登录后，登录态保存在这里
    "user_data_dir": _env("MONITOR_BROWSER_PROFILE", os.path.join(BASE_DIR, "browser_profile")),

    # 数据存储目录（已发现的商品会记录在这里，避免重复推送）
    "data_dir": _env("MONITOR_DATA_DIR", os.path.join(BASE_DIR, "data")),

    # 每页监控的商品数量上限
    "max_items_per_page": _env_int("MONITOR_MAX_ITEMS", 60),
}
