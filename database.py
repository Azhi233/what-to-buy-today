"""
SQLite 数据存储层。
存储：监控商品、发现的商品池、价格历史、降价记录、检查日志、通知记录、运行设置。
"""

import sqlite3
import threading
import time
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitored_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    max_price REAL NOT NULL,
    min_price REAL DEFAULT 0,
    exclude_keywords TEXT DEFAULT '',
    must_include TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL UNIQUE,
    keyword TEXT NOT NULL,
    title TEXT,
    price REAL,
    url TEXT,
    image TEXT,
    location TEXT,
    status TEXT,
    seller_credit TEXT DEFAULT '',
    risk_flags TEXT DEFAULT '',
    notified INTEGER DEFAULT 0,
    first_seen TEXT DEFAULT (datetime('now', 'localtime')),
    last_seen TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_items_keyword ON items(keyword);
CREATE INDEX IF NOT EXISTS idx_items_price ON items(price);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    check_time TEXT DEFAULT (datetime('now', 'localtime')),
    median_price REAL,
    avg_price REAL,
    filtered_avg REAL,
    core_count INTEGER,
    min_price REAL,
    max_price REAL,
    item_count INTEGER,
    epoch INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_keyword ON price_history(keyword);

CREATE TABLE IF NOT EXISTS item_price_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    time TEXT DEFAULT (datetime('now', 'localtime')),
    old_price REAL,
    new_price REAL,
    title TEXT
);

CREATE TABLE IF NOT EXISTS checks_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_time TEXT DEFAULT (datetime('now', 'localtime')),
    keyword TEXT,
    status TEXT,
    total_items INTEGER,
    matched INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT DEFAULT (datetime('now', 'localtime')),
    item_id TEXT,
    keyword TEXT,
    title TEXT,
    price REAL,
    url TEXT,
    channel TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS bark_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT DEFAULT '',
    server TEXT NOT NULL DEFAULT 'https://api.day.app',
    bark_key TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


class Database:
    """SQLite 数据层，线程安全。"""

    def __init__(self, db_path: str = "monitor.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _migrate(self):
        """旧库迁移：补充新字段。"""
        cols = [r["name"] for r in self._conn.execute("PRAGMA table_info(items)")]
        if "seller_credit" not in cols:
            self._conn.execute("ALTER TABLE items ADD COLUMN seller_credit TEXT DEFAULT ''")
        if "risk_flags" not in cols:
            self._conn.execute("ALTER TABLE items ADD COLUMN risk_flags TEXT DEFAULT ''")

        pcols = [r["name"] for r in self._conn.execute("PRAGMA table_info(monitored_products)")]
        if "must_include" not in pcols:
            self._conn.execute("ALTER TABLE monitored_products ADD COLUMN must_include TEXT DEFAULT ''")

        hcols = [r["name"] for r in self._conn.execute("PRAGMA table_info(price_history)")]
        if "filtered_avg" not in hcols:
            self._conn.execute("ALTER TABLE price_history ADD COLUMN filtered_avg REAL")
        if "core_count" not in hcols:
            self._conn.execute("ALTER TABLE price_history ADD COLUMN core_count INTEGER")

        # Bark 多地址支持：确保 bark_targets 表存在
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bark_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT DEFAULT '',
                server TEXT NOT NULL DEFAULT 'https://api.day.app',
                bark_key TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)

        # 时间一致性：price_history 增加 epoch 字段（C2）
        try:
            hcol2 = [r["name"] for r in self._conn.execute("PRAGMA table_info(price_history)")]
            if "epoch" not in hcol2:
                self._conn.execute("ALTER TABLE price_history ADD COLUMN epoch INTEGER DEFAULT 0")
                # 回填历史 epoch
                self._conn.execute(
                    "UPDATE price_history SET epoch = CAST(strftime('%s', check_time) AS INTEGER) WHERE epoch IS NULL OR epoch=0"
                )
        except Exception:
            pass

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # ─────────────────────────────────────
    #  监控商品
    # ─────────────────────────────────────

    def get_products(self, enabled_only: bool = False) -> list[sqlite3.Row]:
        if enabled_only:
            return self._query(
                "SELECT * FROM monitored_products WHERE enabled=1 ORDER BY id"
            )
        return self._query("SELECT * FROM monitored_products ORDER BY id")

    def get_product(self, product_id: int) -> Optional[sqlite3.Row]:
        rows = self._query("SELECT * FROM monitored_products WHERE id=?", (product_id,))
        return rows[0] if rows else None

    def add_product(self, keyword: str, max_price: float, min_price: float = 0,
                    exclude_keywords: str = "", enabled: int = 1,
                    must_include: str = "") -> int:
        cur = self._execute(
            "INSERT INTO monitored_products (keyword, max_price, min_price, exclude_keywords, must_include, enabled)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (keyword, max_price, min_price, exclude_keywords, must_include, enabled),
        )
        return cur.lastrowid

    def update_product(self, product_id: int, keyword: str, max_price: float,
                       min_price: float, exclude_keywords: str,
                       must_include: str = "") -> None:
        self._execute(
            "UPDATE monitored_products SET keyword=?, max_price=?, min_price=?, exclude_keywords=?, must_include=?"
            " WHERE id=?",
            (keyword, max_price, min_price, exclude_keywords, must_include, product_id),
        )

    def delete_product(self, product_id: int) -> None:
        self._execute("DELETE FROM monitored_products WHERE id=?", (product_id,))

    def toggle_product(self, product_id: int) -> bool:
        row = self.get_product(product_id)
        if not row:
            return False
        new_val = 0 if row["enabled"] else 1
        self._execute("UPDATE monitored_products SET enabled=? WHERE id=?", (new_val, product_id))
        return bool(new_val)

    def count_products(self) -> int:
        row = self._query("SELECT COUNT(*) AS c FROM monitored_products WHERE enabled=1")
        return row[0]["c"] if row else 0

    # ─────────────────────────────────────
    #  Bark 推送目标
    # ─────────────────────────────────────

    def get_bark_targets(self) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM bark_targets ORDER BY id")

    def get_bark_target(self, tid: int) -> Optional[sqlite3.Row]:
        rows = self._query("SELECT * FROM bark_targets WHERE id=?", (tid,))
        return rows[0] if rows else None

    def add_bark_target(self, label: str, server: str, bark_key: str, enabled: int = 1) -> int:
        server = (server or "").strip().rstrip("/") or "https://api.day.app"
        cur = self._execute(
            "INSERT INTO bark_targets (label, server, bark_key, enabled) VALUES (?, ?, ?, ?)",
            (label.strip(), server, bark_key.strip(), 1 if enabled else 0),
        )
        return cur.lastrowid

    def update_bark_target(self, tid: int, label: str, server: str, bark_key: str) -> None:
        server = (server or "").strip().rstrip("/") or "https://api.day.app"
        self._execute(
            "UPDATE bark_targets SET label=?, server=?, bark_key=? WHERE id=?",
            (label.strip(), server, bark_key.strip(), tid),
        )

    def delete_bark_target(self, tid: int) -> None:
        self._execute("DELETE FROM bark_targets WHERE id=?", (tid,))

    def toggle_bark_target(self, tid: int) -> bool:
        row = self.get_bark_target(tid)
        if not row:
            return False
        new_val = 0 if row["enabled"] else 1
        self._execute("UPDATE bark_targets SET enabled=? WHERE id=?", (new_val, tid))
        return bool(new_val)

    def seed_bark_from_config(self) -> int:
        """首次运行时把 config.py 的 BARK_CONFIG 迁入数据库。"""
        if self._query("SELECT COUNT(*) AS c FROM bark_targets")[0]["c"] > 0:
            return 0
        try:
            from config import BARK_CONFIG as _bc
            key = (_bc or {}).get("key", "").strip()
            enabled = bool((_bc or {}).get("enabled"))
            if key and key != "your_bark_key_here":
                self.add_bark_target("默认", "https://api.day.app", key, 1 if enabled else 0)
                return 1
        except Exception:
            pass
        return 0

    # ─────────────────────────────────────
    #  商品池
    # ─────────────────────────────────────

    def upsert_item(self, item: dict, keyword: str) -> dict:
        """写入或更新商品，返回 (是否新增, 是否降价)。"""
        item_id = item["item_id"]
        price = item.get("price", 0)
        title = item.get("title", "")
        url = item.get("url", "")
        image = item.get("image", "")
        location = item.get("location", "")
        status = item.get("status", "")
        seller_credit = item.get("seller_credit", "") or ""
        risk_flags = item.get("risk_flags", "") or ""

        rows = self._query("SELECT * FROM items WHERE item_id=?", (item_id,))
        if rows:
            old = rows[0]
            is_new = False
            old_price = old["price"] or 0
            price_dropped = bool(
                old_price > 0 and price < old_price
                and (old_price - price >= 20 or (old_price - price) / old_price >= 0.05)
            )
            self._execute(
                "UPDATE items SET title=?, price=?, url=?, image=?, location=?, status=?,"
                " seller_credit=?, risk_flags=?, keyword=?,"
                " last_seen=datetime('now','localtime') WHERE item_id=?",
                (title, price, url, image, location, status, seller_credit,
                 risk_flags, keyword, item_id),
            )
            if price_dropped:
                self.add_price_change(item_id, keyword, old["price"], price, title)
        else:
            is_new = True
            price_dropped = False
            self._execute(
                "INSERT INTO items (item_id, keyword, title, price, url, image, location, status,"
                " seller_credit, risk_flags)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, keyword, title, price, url, image, location, status,
                 seller_credit, risk_flags),
            )
        return {"is_new": is_new, "price_dropped": price_dropped}

    def get_latest_items(self, keyword: Optional[str] = None, limit: int = 200,
                         price_min: Optional[float] = None,
                         price_max: Optional[float] = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items WHERE 1=1"
        params: list = []
        if keyword:
            sql += " AND keyword=?"
            params.append(keyword)
        if price_min is not None:
            sql += " AND price>=?"
            params.append(price_min)
        if price_max is not None:
            sql += " AND price<=?"
            params.append(price_max)
        sql += " ORDER BY last_seen DESC, price ASC LIMIT ?"
        params.append(limit)
        return self._query(sql, tuple(params))

    def get_item(self, item_id: str) -> Optional[sqlite3.Row]:
        rows = self._query("SELECT * FROM items WHERE item_id=?", (item_id,))
        return rows[0] if rows else None

    def mark_notified(self, item_id: str) -> None:
        self._execute("UPDATE items SET notified=1 WHERE item_id=?", (item_id,))

    def get_keywords(self) -> list[str]:
        rows = self._query("SELECT DISTINCT keyword FROM items ORDER BY keyword")
        return [r["keyword"] for r in rows]

    # ─────────────────────────────────────
    #  价格历史 / 降价记录
    # ─────────────────────────────────────

    def record_price_history(self, keyword: str, median_price: float,
                             avg_price: float, filtered_avg: float,
                             core_count: int, min_price: float,
                             max_price: float, item_count: int) -> None:
        now_epoch = int(time.time())
        self._execute(
            "INSERT INTO price_history (keyword, median_price, avg_price, filtered_avg, core_count,"
            " min_price, max_price, item_count, epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (keyword, median_price, avg_price, filtered_avg, core_count,
             min_price, max_price, item_count, now_epoch),
        )

    def get_price_history(self, keyword: str, limit: int = 200) -> list[sqlite3.Row]:
        # 按 epoch 排序（C2），兼容旧 epoch=0 的历史数据回落到 id 排序
        return self._query(
            "SELECT * FROM price_history WHERE keyword=? ORDER BY COALESCE(NULLIF(epoch,0), id) DESC LIMIT ?",
            (keyword, limit),
        )

    def add_price_change(self, item_id: str, keyword: str, old_price: float,
                         new_price: float, title: str) -> None:
        self._execute(
            "INSERT INTO item_price_changes (item_id, keyword, old_price, new_price, title)"
            " VALUES (?, ?, ?, ?, ?)",
            (item_id, keyword, old_price, new_price, title),
        )

    def get_recent_price_changes(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM item_price_changes ORDER BY id DESC LIMIT ?", (limit,)
        )

    # ─────────────────────────────────────
    #  检查日志 / 通知记录
    # ─────────────────────────────────────

    def log_check(self, keyword: str, status: str, total_items: int = 0,
                  matched: int = 0, message: str = "") -> None:
        self._execute(
            "INSERT INTO checks_log (keyword, status, total_items, matched, message)"
            " VALUES (?, ?, ?, ?, ?)",
            (keyword, status, total_items, matched, message),
        )

    def get_checks(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM checks_log ORDER BY id DESC LIMIT ?", (limit,))

    def log_notification(self, item_id: str, keyword: str, title: str,
                         price: float, url: str, channel: str) -> None:
        self._execute(
            "INSERT INTO notifications (item_id, keyword, title, price, url, channel)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, keyword, title, price, url, channel),
        )

    def get_notifications(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._query(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
        )

    # ─────────────────────────────────────
    #  保留策略与清理（B1）
    # ─────────────────────────────────────

    def cleanup_expired(self, items_days: int = 30, history_days: int = 60,
                        checks_keep: int = 500, notifications_keep: int = 1000,
                        vacuum: bool = False) -> dict:
        """按保留策略清理过期数据，可选 VACUUM 回收空间。"""
        stats = {}
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM items WHERE julianday('now','localtime')-julianday(last_seen) > ?", (items_days,))
            stats["items_deleted"] = cur.rowcount
            cur = self._conn.execute(
                "DELETE FROM price_history WHERE julianday('now','localtime')-julianday(check_time) > ?", (history_days,))
            stats["history_deleted"] = cur.rowcount
            cur = self._conn.execute(
                "DELETE FROM checks_log WHERE id NOT IN (SELECT id FROM checks_log ORDER BY id DESC LIMIT ?)",
                (checks_keep,))
            stats["checks_deleted"] = cur.rowcount
            cur = self._conn.execute(
                "DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
                (notifications_keep,))
            stats["notifications_deleted"] = cur.rowcount
            self._conn.commit()
            if vacuum:
                try:
                    self._conn.execute("VACUUM")
                except Exception:
                    pass
        return stats

    def get_retention(self) -> dict:
        """从 settings 读取保留策略，缺省用 DEFAULT_RETENTION."""
        def _int(key: str, default: int) -> int:
            v = self.get_setting(key, "")
            try:
                return int(v) if v else default
            except Exception:
                return default
        return {
            "items_days": _int("retention_items_days", DEFAULT_RETENTION["items_days"]),
            "history_days": _int("retention_history_days", DEFAULT_RETENTION["history_days"]),
            "checks_keep": _int("retention_checks_keep", DEFAULT_RETENTION["checks_keep"]),
            "notifications_keep": _int("retention_notifications_keep", DEFAULT_RETENTION["notifications_keep"]),
        }

    def set_retention(self, **kwargs) -> None:
        key_map = {
            "items_days": "retention_items_days",
            "history_days": "retention_history_days",
            "checks_keep": "retention_checks_keep",
            "notifications_keep": "retention_notifications_keep",
        }
        for k, v in kwargs.items():
            if k in key_map:
                self.set_setting(key_map[k], str(int(v)))

    # ─────────────────────────────────────
    #  运行设置
    # ─────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        rows = self._query("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # ─────────────────────────────────────
    #  统计
    # ─────────────────────────────────────

    def get_stats(self) -> dict:
        items = self._query("SELECT COUNT(*) AS c FROM items")
        notified = self._query("SELECT COUNT(*) AS c FROM notifications")
        drops = self._query("SELECT COUNT(*) AS c FROM item_price_changes")
        today = self._query(
            "SELECT COUNT(*) AS c FROM notifications WHERE date(time)=date('now','localtime')"
        )
        checks = self._query("SELECT COUNT(*) AS c FROM checks_log")
        newest = self._query(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT 5"
        )
        return {
            "total_items": items[0]["c"] if items else 0,
            "total_notified": notified[0]["c"] if notified else 0,
            "total_drops": drops[0]["c"] if drops else 0,
            "today_notified": today[0]["c"] if today else 0,
            "total_checks": checks[0]["c"] if checks else 0,
            "monitored_products": self.count_products(),
            "recent_notifications": [dict(r) for r in newest],
        }


def parse_exclude_keywords(raw: str) -> list[str]:
    """解析逗号/空格分隔的排除关键词列表。"""
    if not raw:
        return []
    parts = raw.replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


# 保留策略默认值（B1）
DEFAULT_RETENTION = {
    "items_days": 30,
    "history_days": 60,
    "checks_keep": 500,
    "notifications_keep": 1000,
}
