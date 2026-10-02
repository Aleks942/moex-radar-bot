"""MOEX signal journal: future-bar simulation, never a record of real fills.

Only delivered messages are recorded by main.py. The hypothetical entry is the
open of the first M10 bar whose BEGIN is at or after message acknowledgement.
Horizons count trading bars, not wall-clock minutes. No price from the source
signal candle is used as the hypothetical entry. SQLite is standard-library.
"""

import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


MSK = timezone(timedelta(hours=3))
HORIZONS = (3, 6, 12, 36)
MAX_HISTORY_DAYS = 45
SETTLE_DELAY_MIN = 20


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MSK)
    return parsed.timestamp()


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("Цена не может быть bool")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Цена должна быть конечной и положительной")
    return number


def _cost_from_env():
    raw = os.getenv("JOURNAL_ROUND_TRIP_COST_BPS", "").strip()
    if not raw:
        return None
    cost = float(raw)
    if not math.isfinite(cost) or cost < 0:
        raise ValueError("JOURNAL_ROUND_TRIP_COST_BPS должен быть >= 0")
    return cost


def journal_path(state_dir="."):
    explicit = os.getenv("JOURNAL_DB_PATH", "").strip()
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    directory = os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or state_dir
    return str((Path(directory) / "moex_signal_journal.sqlite3").resolve())


class SignalJournal:
    def __init__(self, path, cost_bps=None):
        self.path = str(Path(path).resolve())
        if cost_bps is not None and (
            isinstance(cost_bps, bool) or not math.isfinite(float(cost_bps))
            or float(cost_bps) < 0
        ):
            raise ValueError("Расходы должны быть конечными и >= 0")
        self.cost_bps = None if cost_bps is None else float(cost_bps)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Неизвестная версия журнала: {version}")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('UP', 'DOWN')),
                    emitted_ts REAL NOT NULL,
                    source_interval INTEGER NOT NULL,
                    source_begin TEXT NOT NULL,
                    source_end TEXT NOT NULL,
                    source_price REAL NOT NULL,
                    score REAL,
                    metadata_json TEXT NOT NULL,
                    cost_bps REAL,
                    status TEXT NOT NULL DEFAULT 'awaiting_entry',
                    entry_begin_ts REAL,
                    entry_price REAL,
