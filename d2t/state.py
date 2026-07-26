"""SQLite 状态库：作品去重、处理队列、冷却标记。"""

import sqlite3
import time
from pathlib import Path

from d2t.models import Work

MAX_RETRIES = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    aweme_id   TEXT PRIMARY KEY,
    sort_key   INTEGER NOT NULL,
    aweme_type TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    author     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'pending',
    retries    INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(_SCHEMA)

    def add_works(self, records: list[dict]) -> int:
        """入库新作品。records 按新→旧排列；旧的分配更小的 sort_key。

        前置契约：
        - records 中的所有作品应为尚未入库的新作品（由上游 collect_new 过滤保证）
        - 同一作品在批次内重复出现会被安全忽略（第二次 INSERT OR IGNORE 不占号）
        - 不支持已入库作品与新作品交错的输入（上游 collect_new 保证不产生此类输入）

        返回：
        - 新增入库的作品数量（重复的作品不计入）
        """
        row = self.conn.execute("SELECT COALESCE(MAX(sort_key), -1) FROM works").fetchone()
        next_key = row[0] + 1
        inserted = 0
        for rec in reversed(records):  # 旧→新依次分配递增 sort_key
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO works (aweme_id, sort_key, aweme_type, title, author, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (rec["aweme_id"], next_key, rec["aweme_type"],
                 rec.get("title", ""), rec.get("author", ""), time.time()),
            )
            if cur.rowcount:
                inserted += 1
                next_key += 1
        self.conn.commit()
        return inserted

    def is_known(self, aweme_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM works WHERE aweme_id = ?", (aweme_id,)
        ).fetchone() is not None

    def next_batch(self, limit: int) -> list[Work]:
        rows = self.conn.execute(
            "SELECT aweme_id, aweme_type, title, author, status, retries FROM works"
            " WHERE status = 'pending' ORDER BY sort_key ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Work(*row) for row in rows]

    def _set(self, aweme_id: str, status: str, error: str | None = None):
        self.conn.execute(
            "UPDATE works SET status = ?, error = ?, updated_at = ? WHERE aweme_id = ?",
            (status, error, time.time(), aweme_id),
        )
        self.conn.commit()

    def mark_uploaded(self, aweme_id: str):
        self._set(aweme_id, "uploaded")

    def mark_skipped(self, aweme_id: str, reason: str):
        self._set(aweme_id, "skipped", reason)

    def mark_failed(self, aweme_id: str, error: str) -> str:
        """累计重试次数，达到 MAX_RETRIES 转 failed。返回最新状态。"""
        self.conn.execute(
            "UPDATE works SET retries = retries + 1, error = ?, updated_at = ? WHERE aweme_id = ?",
            (error, time.time(), aweme_id),
        )
        retries = self.conn.execute(
            "SELECT retries FROM works WHERE aweme_id = ?", (aweme_id,)
        ).fetchone()[0]
        status = "failed" if retries >= MAX_RETRIES else "pending"
        if status == "failed":
            self._set(aweme_id, "failed", error)
        else:
            self.conn.commit()
        return status

    def reset_failed(self) -> int:
        cur = self.conn.execute(
            "UPDATE works SET status = 'pending', retries = 0, updated_at = ? WHERE status = 'failed'",
            (time.time(),),
        )
        self.conn.commit()
        return cur.rowcount

    def set_cooldown(self, until_ts: float):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('cooldown_until', ?)",
            (str(until_ts),),
        )
        self.conn.commit()

    def in_cooldown(self) -> bool:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'cooldown_until'"
        ).fetchone()
        return bool(row) and float(row[0]) > time.time()
