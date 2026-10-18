"""SQLite-backed aiogram FSM storage, so a split in progress survives a bot restart."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS fsm ("
            " key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT '{}',"
            " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._db.commit()
        self._keys = DefaultKeyBuilder(with_destiny=True)

    def _upsert(self, key: StorageKey, column: str, value: str | None) -> None:
        self._db.execute(
            f"INSERT INTO fsm (key, {column}) VALUES (?, ?) "
            f"ON CONFLICT(key) DO UPDATE SET {column} = excluded.{column}, "
            "updated_at = CURRENT_TIMESTAMP",
            (self._keys.build(key), value),
        )
        self._db.commit()

    def _get(self, key: StorageKey, column: str) -> Any:
        row = self._db.execute(
            f"SELECT {column} FROM fsm WHERE key = ?", (self._keys.build(key),)
        ).fetchone()
        return row[0] if row else None

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        self._upsert(key, "state", value)

    async def get_state(self, key: StorageKey) -> str | None:
        return self._get(key, "state")

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        self._upsert(key, "data", json.dumps(dict(data), ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        raw = self._get(key, "data")
        return json.loads(raw) if raw else {}

    async def close(self) -> None:
        self._db.close()
