"""
============================================
  闲鱼价格监控 - 配置文件
============================================
使用说明：
  1. 修改 MONITOR_ITEMS 添加你要监控的商品
  2. 根据需要开启推送渠道（Bark / PushPlus / 邮件 / Telegram）
  3. 首次运行 python run.py --login 扫码登录闲鱼（只需一次）
  4. 运行 python run.py 即可启动监控
============================================
"""

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
# ═══════════════════════════════════════════

# --- Bark（iOS 推送，推荐） ---
# 在 App Store 下载 Bark，复制给你的 Key
# 格式：https://api.day.app/你的Key/标题/内容
# ⚠️ 请替换为你自己的 Bark Key，示例："abc123def456..."
BARK_CONFIG = {
    "enabled": True,
    "key": "your_bark_key_here",
}

# --- PushPlus（微信推送） ---
# 在 http://www.pushplus.plus 获取 Token
PUSHPLUS_CONFIG = {
    "enabled": False,
    "token": "your_pushplus_token_here",
}

# --- SMTP 邮件推送 ---
# 支持 QQ邮箱 / Gmail / 163 等
SMTP_CONFIG = {
    "enabled": False,
    "host": "smtp.qq.com",          # SMTP 服务器
    "port": 465,                     # SSL 端口
    "user": "your_email@qq.com",     # 邮箱地址
    "password": "your_smtp_code",    # SMTP 授权码（非邮箱密码）
    "to": "your_email@qq.com",       # 接收通知的邮箱
}

# --- Telegram Bot 推送 ---
# 在 Telegram 中搜索 @BotFather 创建 Bot，获取 Token
# 搜索你的 Bot 并发送 /start，然后访问 https://api.telegram.org/bot<YourToken>/getUpdates 获取 chat_id
TELEGRAM_CONFIG = {
    "enabled": False,
    "bot_token": "your_bot_token_here",
    "chat_id": "your_chat_id_here",
}

# ═══════════════════════════════════════════
#  监控运行设置
# ═══════════════════════════════════════════
MONITOR_SETTINGS = {
    # 每轮监控的间隔时间（分钟）—— 建议 30 分钟以上
    # 太频繁容易被闲鱼风控识别为机器人
    "interval_minutes": 30,

    # 浏览器模式（重要！）
    # 闲鱼风控会拦截无头浏览器（headless=True 时容易触发"非法访问"）
    # 强烈建议保持 False（有头模式，浏览器窗口可以最小化）
    # 如果想尝试无头模式，先登录后再改，但有一定风险
    "headless": False,

    # 浏览器配置目录（保存登录状态）
    # 首次运行 python run.py --login 扫码登录后，登录态保存在这里
    "user_data_dir": "./browser_profile",

    # 数据存储目录（已发现的商品会记录在这里，避免重复推送）
    "data_dir": "./data",

    # 每页监控的商品数量上限
    "max_items_per_page": 60,
}