"""
存储已发现的商品，避免重复推送通知。
"""

import json
import os
from pathlib import Path
from datetime import datetime


class SeenStorage:
    """记录已推送过的商品 ID，防止重复通知。"""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "seen_items.json"
        self._seen_ids: set[str] = set()
        self._load()

    def _load(self):
        """从磁盘加载已见商品 ID。"""
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                self._seen_ids = set(data.get("ids", []))
            except (json.JSONDecodeError, KeyError):
                self._seen_ids = set()

    def _save(self):
        """保存到磁盘。"""
        data = {
            "ids": list(self._seen_ids),
            "updated_at": datetime.now().isoformat(),
        }
        self.file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_new(self, item_id: str) -> bool:
        """检查商品是否是新发现的。"""
        return item_id not in self._seen_ids

    def mark_seen(self, item_id: str):
        """标记商品为已见。"""
        self._seen_ids.add(item_id)
        self._save()

    @property
    def count(self) -> int:
        return len(self._seen_ids)


class StatsCollector:
    """收集监控运行的统计信息。"""

    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "stats.json"
        self._stats = self._load()

    def _load(self) -> dict:
        if self.file_path.exists():
            try:
                return json.loads(self.file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {
            "total_checks": 0,
            "total_items_found": 0,
            "total_matches": 0,
            "total_notifications_sent": 0,
            "last_check_time": None,
            "errors": [],
        }

    def _save(self):
        self.file_path.write_text(
            json.dumps(self._stats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record_check(self, items_found: int, matches: int):
        self._stats["total_checks"] += 1
        self._stats["total_items_found"] += items_found
        self._stats["total_matches"] += matches
        self._stats["last_check_time"] = datetime.now().isoformat()
        self._save()

    def record_notification(self):
        self._stats["total_notifications_sent"] += 1
        self._save()

    def record_error(self, error: str):
        self._stats["errors"].append({
            "time": datetime.now().isoformat(),
            "message": str(error),
        })
        # 最多保留 50 条错误记录
        if len(self._stats["errors"]) > 50:
            self._stats["errors"] = self._stats["errors"][-50:]
        self._save()

    def get_summary(self) -> str:
        s = self._stats
        return (
            f"总检查次数: {s['total_checks']}\n"
            f"总发现商品: {s['total_items_found']}\n"
            f"匹配商品: {s['total_matches']}\n"
            f"已发送通知: {s['total_notifications_sent']}\n"
            f"最后检查: {s['last_check_time'] or '从未'}\n"
            f"错误数: {len(s['errors'])}"
        )