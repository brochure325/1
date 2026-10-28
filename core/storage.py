"""Small persistent SQLite store; no login secrets are kept in this database."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class Storage:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS bindings (
                scope TEXT PRIMARY KEY, region TEXT NOT NULL,
                account_id INTEGER NOT NULL, nickname TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                region TEXT NOT NULL, account_id INTEGER NOT NULL,
                mode TEXT NOT NULL, ship_id INTEGER NOT NULL DEFAULT 0,
                at REAL NOT NULL, totals TEXT NOT NULL,
                PRIMARY KEY (region, account_id, mode, ship_id, at)
            );
            CREATE INDEX IF NOT EXISTS snapshot_time
                ON snapshots(region, account_id, mode, ship_id, at);
        """)
        self.connection.commit()

    def bind(self, scope: str, region: str, account_id: int, nickname: str = "") -> dict:
        with self.connection:
            self.connection.execute(
                "INSERT INTO bindings(scope,region,account_id,nickname,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET region=excluded.region,account_id=excluded.account_id,"
                "nickname=excluded.nickname,updated_at=excluded.updated_at",
                (str(scope), str(region), int(account_id), str(nickname), time.time()),
            )
        return self.get_binding(scope)

    def get_binding(self, scope: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM bindings WHERE scope=?", (str(scope),)).fetchone()
        return dict(row) if row is not None else None

    def list_bindings(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM bindings ORDER BY scope")]

    def unbind(self, scope: str):
        with self.connection:
            self.connection.execute("DELETE FROM bindings WHERE scope=?", (str(scope),))

    def save_snapshot(self, region: str, account_id: int, mode: str, totals: dict, at: float | None = None, ship_id: int = 0):
        at = time.time() if at is None else float(at)
        encoded = json.dumps(totals, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO snapshots(region,account_id,mode,ship_id,at,totals) VALUES(?,?,?,?,?,?)",
                (str(region), int(account_id), str(mode), int(ship_id), at, encoded),
            )

    @staticmethod
    def _snapshot(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result["totals"] = json.loads(result["totals"])
        return result

    def baseline(self, region: str, account_id: int, mode: str, before: float, ship_id: int = 0) -> dict | None:
        """Closest recorded sample at or before the requested boundary."""
        row = self.connection.execute(
            "SELECT * FROM snapshots WHERE region=? AND account_id=? AND mode=? AND ship_id=? AND at<=? ORDER BY at DESC LIMIT 1",
            (str(region), int(account_id), str(mode), int(ship_id), float(before)),
        ).fetchone()
        return self._snapshot(row)

    def earliest_snapshot(self, region: str, account_id: int, mode: str, after: float, before: float, ship_id: int = 0) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM snapshots WHERE region=? AND account_id=? AND mode=? AND ship_id=? AND at>=? AND at<? ORDER BY at ASC LIMIT 1",
            (str(region), int(account_id), str(mode), int(ship_id), float(after), float(before)),
        ).fetchone()
        return self._snapshot(row)

    def history(self, region: str, account_id: int, mode: str = "pvp", after: float = 0, ship_id: int = 0) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM snapshots WHERE region=? AND account_id=? AND mode=? AND ship_id=? AND at>=? ORDER BY at ASC",
            (str(region), int(account_id), str(mode), int(ship_id), float(after)),
        ).fetchall()
        return [self._snapshot(row) for row in rows]

    def delete_player_data(self, region: str, account_id: int):
        """Delete local samples and all bindings to this account."""
        with self.connection:
            self.connection.execute("DELETE FROM snapshots WHERE region=? AND account_id=?", (str(region), int(account_id)))
            self.connection.execute("DELETE FROM bindings WHERE region=? AND account_id=?", (str(region), int(account_id)))

    def close(self):
        self.connection.close()
