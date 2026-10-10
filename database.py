"""Слой доступа к SQLite (пользователи, счётчики трафика, мониторы)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    traffic_in  INTEGER NOT NULL DEFAULT 0,
    traffic_out INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS monitors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    chat_id      INTEGER NOT NULL,
    url          TEXT    NOT NULL,
    interval     INTEGER NOT NULL DEFAULT 300,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_ok      INTEGER,
    last_status  INTEGER,
    last_error   TEXT,
    last_checked INTEGER,
    created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monitors_enabled ON monitors(enabled);
CREATE INDEX IF NOT EXISTS idx_monitors_user    ON monitors(user_id);

CREATE TABLE IF NOT EXISTS speedtests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    ping_ms       REAL,
    download_mbps REAL,
    upload_mbps   REAL
);

CREATE INDEX IF NOT EXISTS idx_speedtests_user ON speedtests(user_id);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена: вызовите Database.connect()")
        return self._conn

    # ----------------------------- пользователи -----------------------------

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        async with self._lock:
            db = self._db()
            await db.execute(
                """
                INSERT INTO users (user_id, username, first_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name
                """,
                (user_id, username, first_name, int(time.time())),
            )
            await db.commit()

    async def add_traffic(self, user_id: int, in_bytes: int = 0, out_bytes: int = 0) -> None:
        if in_bytes <= 0 and out_bytes <= 0:
            return
        async with self._lock:
            db = self._db()
            await db.execute(
                "UPDATE users SET traffic_in = traffic_in + ?, traffic_out = traffic_out + ? WHERE user_id = ?",
                (max(0, in_bytes), max(0, out_bytes), user_id),
            )
            await db.commit()

    async def get_user_traffic(self, user_id: int) -> tuple[int, int]:
        async with self._lock:
            cur = await self._db().execute(
                "SELECT traffic_in, traffic_out FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cur.fetchone()
        if row is None:
            return 0, 0
        return int(row["traffic_in"]), int(row["traffic_out"])

    # ------------------------------- мониторы -------------------------------

    async def count_monitors(self, user_id: int) -> int:
        async with self._lock:
            cur = await self._db().execute(
                "SELECT COUNT(*) AS n FROM monitors WHERE user_id = ?", (user_id,)
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def add_monitor(self, user_id: int, chat_id: int, url: str, interval: int) -> int:
        async with self._lock:
            db = self._db()
            cur = await db.execute(
                """
                INSERT INTO monitors (user_id, chat_id, url, interval, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, chat_id, url, interval, int(time.time())),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def list_monitors(self, user_id: int) -> list[dict[str, Any]]:
        async with self._lock:
            cur = await self._db().execute(
                "SELECT * FROM monitors WHERE user_id = ? ORDER BY id", (user_id,)
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_monitor(self, monitor_id: int) -> dict[str, Any] | None:
        async with self._lock:
            cur = await self._db().execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_monitor(self, monitor_id: int, user_id: int) -> bool:
        async with self._lock:
            db = self._db()
            cur = await db.execute(
                "DELETE FROM monitors WHERE id = ? AND user_id = ?", (monitor_id, user_id)
            )
            await db.commit()
            return cur.rowcount > 0

    async def set_monitor_enabled(self, monitor_id: int, user_id: int, enabled: bool) -> bool:
        async with self._lock:
            db = self._db()
            cur = await db.execute(
                "UPDATE monitors SET enabled = ?, last_checked = NULL WHERE id = ? AND user_id = ?",
                (1 if enabled else 0, monitor_id, user_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_due_monitors(self) -> list[dict[str, Any]]:
        now = int(time.time())
        async with self._lock:
            cur = await self._db().execute(
                """
                SELECT * FROM monitors
                WHERE enabled = 1
                  AND (last_checked IS NULL OR last_checked <= ? - interval)
                """,
                (now,),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_monitor_status(
        self,
        monitor_id: int,
        ok: bool,
        status: int | None,
        error: str | None,
        checked_at: int,
    ) -> None:
        async with self._lock:
            db = self._db()
            await db.execute(
                """
                UPDATE monitors
                SET last_ok = ?, last_status = ?, last_error = ?, last_checked = ?
                WHERE id = ?
                """,
                (1 if ok else 0, status, error, checked_at, monitor_id),
            )
            await db.commit()

    # ------------------------- история замеров скорости -------------------------

    async def add_speedtest(
        self,
        user_id: int,
        ping_ms: float | None,
        download_mbps: float | None,
        upload_mbps: float | None,
    ) -> None:
        async with self._lock:
            db = self._db()
            await db.execute(
                """
                INSERT INTO speedtests (user_id, created_at, ping_ms, download_mbps, upload_mbps)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, int(time.time()), ping_ms, download_mbps, upload_mbps),
            )
            await db.commit()

    async def get_speedtests(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with self._lock:
            cur = await self._db().execute(
                "SELECT * FROM speedtests WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            db = self._db()
            users = await (await db.execute("SELECT COUNT(*) AS n FROM users")).fetchone()
            monitors = await (await db.execute("SELECT COUNT(*) AS n FROM monitors")).fetchone()
            enabled = await (
                await db.execute("SELECT COUNT(*) AS n FROM monitors WHERE enabled = 1")
            ).fetchone()
        return {
            "users": int(users["n"]),
            "monitors": int(monitors["n"]),
            "enabled_monitors": int(enabled["n"]),
        }
