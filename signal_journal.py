"""MOEX signal journal: future-bar simulation, never real trades."""

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

    return str(
        (Path(directory) / "moex_signal_journal.sqlite3").resolve()
    )


class SignalJournal:
    def __init__(self, path, cost_bps=None):
        self.path = str(Path(path).resolve())

        if cost_bps is not None and (
            isinstance(cost_bps, bool)
            or not math.isfinite(float(cost_bps))
            or float(cost_bps) < 0
        ):
            raise ValueError("Расходы должны быть конечными и >= 0")

        self.cost_bps = (
            None if cost_bps is None else float(cost_bps)
        )

        Path(self.path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = sqlite3.connect(
            self.path,
            timeout=5,
        )

        self.db.row_factory = sqlite3.Row

        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")

            version = self.db.execute(
                "PRAGMA user_version"
            ).fetchone()[0]

            if version not in (0, 1):
                raise ValueError(
                    f"Неизвестная версия журнала: {version}"
                )

            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    direction TEXT NOT NULL
                        CHECK(direction IN ('UP', 'DOWN')),
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
                    expired_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS signals_pending
                    ON signals(status, ticker, emitted_ts);

                CREATE TABLE IF NOT EXISTS outcomes (
                    signal_id TEXT NOT NULL REFERENCES signals(id),
                    horizon_bars INTEGER NOT NULL,
                    end_ts REAL NOT NULL,
                    evaluated_ts REAL NOT NULL,
                    elapsed_minutes REAL NOT NULL,
                    gross_pct REAL NOT NULL,
                    net_pct REAL,
                    mfe_pct REAL NOT NULL,
                    mae_pct REAL NOT NULL,
                    PRIMARY KEY(signal_id, horizon_bars)
                );

                PRAGMA user_version=1;
            """)

        except Exception:
            self.db.close()
            raise

    @classmethod
    def from_env(cls, state_dir="."):
        return cls(
            journal_path(state_dir),
            _cost_from_env(),
        )

    def close(self):
        self.db.close()

    def record(
        self,
        ticker,
        kind,
        direction,
        emitted_ts,
        source_interval,
        source_begin,
        source_end,
        source_price,
        score=None,
        metadata=None,
        event_id=None,
    ):
        if direction not in ("UP", "DOWN"):
            raise ValueError(
                "Журналу нужно направление UP или DOWN"
            )

        if kind not in (
            "FAST",
            "FLOW",
            "AGG",
            "SAFE",
            "RETEST",
        ):
            raise ValueError("Неизвестный тип сигнала")

        if source_interval not in (10, 60):
            raise ValueError(
                "Неизвестный таймфрейм источника"
            )

        emitted_ts = float(emitted_ts)

        if not math.isfinite(emitted_ts):
            raise ValueError(
                "Некорректное время отправки"
            )

        begin_ts = _timestamp(source_begin)
        end_ts = _timestamp(source_end)

        if begin_ts > emitted_ts + 60 or end_ts < begin_ts:
            raise ValueError(
                "Некорректные времена исходной свечи"
            )

        price = _positive(source_price)

        if score is not None and not math.isfinite(float(score)):
            raise ValueError("Некорректный score")

        signal_id = event_id or uuid.uuid4().hex

        with self.db:
            cursor = self.db.execute("""
                INSERT OR IGNORE INTO signals (
                    id,
                    ticker,
                    kind,
                    direction,
                    emitted_ts,
                    source_interval,
                    source_begin,
                    source_end,
                    source_price,
                    score,
                    metadata_json,
                    cost_bps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_id,
                ticker,
                kind,
                direction,
                emitted_ts,
                source_interval,
                str(source_begin),
                str(source_end),
                price,
                score,
                json.dumps(
                    metadata or {},
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                self.cost_bps,
            ))

        if cursor.rowcount:
            print(
                f"[JOURNAL_SIGNAL] {ticker} {kind} "
                f"{direction} id={signal_id}",
                flush=True,
            )

        return signal_id

    @staticmethod
    def _bars(columns, rows, now_ts):
        required = (
            "begin",
            "end",
            "open",
            "high",
            "low",
            "close",
        )

        indices = {
            name: columns.index(name)
            for name in required
        }

        bars = []
        seen = set()

        for row in rows:
            begin = _timestamp(
                row[indices["begin"]]
            )

            if begin in seen:
                raise ValueError(
                    "Повтор M10-свечи в журнале"
                )

            seen.add(begin)

            actual_end = _timestamp(
                row[indices["end"]]
            )

            if actual_end < begin or actual_end >= begin + 600:
                raise ValueError(
                    "Некорректный диапазон времени M10"
                )

            if begin + 600 > now_ts - SETTLE_DELAY_MIN * 60:
                continue

            op, high, low, close = (
                _positive(row[indices[name]])
                for name in (
                    "open",
                    "high",
                    "low",
                    "close",
                )
            )

            if (
                high < max(op, low, close)
                or low > min(op, high, close)
            ):
                raise ValueError(
                    "Некорректные цены M10"
                )

            bars.append({
                "begin": begin,
                "end": begin + 600,
                "open": op,
                "high": high,
                "low": low,
                "close": close,
            })

        return sorted(
            bars,
            key=lambda bar: bar["begin"],
        )

    def update(self, fetch_candles, now_ts=None):
        if now_ts is None:
            now_ts = datetime.now(
                timezone.utc
            ).timestamp()

        pending = self.db.execute("""
            SELECT *
            FROM signals
            WHERE status IN ('awaiting_entry', 'tracking')
            ORDER BY emitted_ts
        """).fetchall()

        by_ticker = {}
        expired = 0

        for signal in pending:
            age = now_ts - signal["emitted_ts"]

            if age > MAX_HISTORY_DAYS * 86400:
                with self.db:
                    self.db.execute("""
                        UPDATE signals
                        SET
                            status='expired',
                            expired_reason='history_window_exceeded'
                        WHERE id=?
                    """, (signal["id"],))

                expired += 1

            else:
                by_ticker.setdefault(
                    signal["ticker"],
                    [],
                ).append(signal)

        evaluated = 0
        errors = 0

        for ticker, signals in by_ticker.items():
            try:
                ticker_added = 0

                days = max(
                    10,
                    math.ceil(
                        (now_ts - signals[0]["emitted_ts"])
                        / 86400
                    ) + 2,
                )

                columns, rows = fetch_candles(
                    ticker,
                    10,
                    days,
                )

                if not columns or not rows:
                    continue

                bars = self._bars(
                    columns,
                    rows,
                    now_ts,
                )

                with self.db:
                    for signal in signals:
                        future = [
                            bar
                            for bar in bars
                            if bar["begin"] >= signal["emitted_ts"]
                        ]

                        if not future:
                            continue

                        if signal["entry_begin_ts"] is not None:
                            future = [
                                bar
                                for bar in future
                                if bar["begin"] >= signal["entry_begin_ts"]
                            ]

                            if (
                                not future
                                or future[0]["begin"]
                                != signal["entry_begin_ts"]
                            ):
                                raise ValueError(
                                    "Нет сохранённой свечи условного входа"
                                )

                            entry = signal["entry_price"]

                        else:
                            entry = future[0]["open"]

                            self.db.execute("""
                                UPDATE signals
                                SET
                                    status='tracking',
                                    entry_begin_ts=?,
                                    entry_price=?
                                WHERE id=?
                            """, (
                                future[0]["begin"],
                                entry,
                                signal["id"],
                            ))

                        existing = {
                            row[0]
                            for row in self.db.execute(
                                """
                                SELECT horizon_bars
                                FROM outcomes
                                WHERE signal_id=?
                                """,
                                (signal["id"],),
                            )
                        }

                        sign = (
                            1
                            if signal["direction"] == "UP"
                            else -1
                        )

                        for horizon in HORIZONS:
                            if (
                                horizon in existing
                                or len(future) < horizon
                            ):
                                continue

                            window = future[:horizon]

                            gross = (
                                sign
                                * (
                                    window[-1]["close"] / entry
                                    - 1
                                )
                                * 100
                            )

                            if sign == 1:
                                favourable = (
                                    max(
                                        bar["high"]
                                        for bar in window
                                    )
                                    / entry
                                    - 1
                                )

                                adverse = (
                                    1
                                    - min(
                                        bar["low"]
                                        for bar in window
                                    )
                                    / entry
                                )

                            else:
                                favourable = (
                                    1
                                    - min(
                                        bar["low"]
                                        for bar in window
                                    )
                                    / entry
                                )

                                adverse = (
                                    max(
                                        bar["high"]
                                        for bar in window
                                    )
                                    / entry
                                    - 1
                                )

                            net = (
                                None
                                if signal["cost_bps"] is None
                                else (
                                    gross
                                    - signal["cost_bps"] / 100
                                )
                            )

                            self.db.execute("""
                                INSERT INTO outcomes
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                signal["id"],
                                horizon,
                                window[-1]["end"],
                                now_ts,
                                (
                                    window[-1]["end"]
                                    - signal["emitted_ts"]
                                ) / 60,
                                gross,
                                net,
                                max(0, favourable * 100),
                                max(0, adverse * 100),
                            ))

                            existing.add(horizon)
                            ticker_added += 1

                        if all(
                            horizon in existing
                            for horizon in HORIZONS
                        ):
                            self.db.execute(
                                """
                                UPDATE signals
                                SET status='complete'
                                WHERE id=?
                                """,
                                (signal["id"],),
                            )

                evaluated += ticker_added

            except Exception as exc:
                errors += 1

                print(
                    f"[JOURNAL_ERROR] update {ticker} "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

        counts = self.db.execute("""
            SELECT COUNT(*)
            FROM signals
            WHERE status IN ('awaiting_entry', 'tracking')
        """).fetchone()[0]

        print(
            f"[JOURNAL_UPDATE] pending={counts} "
            f"outcomes_added={evaluated} "
            f"expired={expired} errors={errors}",
            flush=True,
        )

        return {
            "pending": counts,
            "outcomes_added": evaluated,
            "expired": expired,
            "errors": errors,
        }

    def summary_text(
        self,
        days=30,
        horizon_bars=6,
        now_ts=None,
    ):
        if horizon_bars not in HORIZONS:
            raise ValueError(
                "Неизвестный горизонт"
            )

        if now_ts is None:
            now_ts = datetime.now(
                timezone.utc
            ).timestamp()

        cutoff = now_ts - days * 86400

        counts = dict(
            self.db.execute("""
                SELECT status, COUNT(*)
                FROM signals
                WHERE emitted_ts >= ?
                GROUP BY status
            """, (cutoff,)).fetchall()
        )

        total = sum(counts.values())

        pending = (
            counts.get("awaiting_entry", 0)
            + counts.get("tracking", 0)
        )

        lines = [
            f"📒 Журнал сигналов — последние {days} дней",
            (
                f"Записано: {total} | "
                f"ожидают полного окна: {pending} | "
                f"истекли: {counts.get('expired', 0)}"
            ),
            (
                f"Окно: {horizon_bars} доступных M10 "
                "(время зависит от перерывов)"
            ),
            "Условный вход: первая M10 после отправки сообщения.",
        ]

        groups = self.db.execute("""
            SELECT
                s.kind,
                COUNT(*) AS n,
                AVG(o.gross_pct) AS avg_gross,
                SUM(o.gross_pct > 0) AS positive,
                AVG(o.mfe_pct) AS mfe,
                AVG(o.mae_pct) AS mae,
                COUNT(o.net_pct) AS n_net,
                AVG(o.net_pct) AS avg_net
            FROM signals s
            JOIN outcomes o ON o.signal_id=s.id
            WHERE
                s.emitted_ts >= ?
                AND o.horizon_bars=?
            GROUP BY s.kind
            ORDER BY s.kind
        """, (
            cutoff,
            horizon_bars,
        )).fetchall()

        if not groups:
            lines.append(
                "Оценённых окон пока нет; ждём будущие свечи."
            )

        for group in groups:
            lines.append(
                f"{group['kind']}: n={group['n']}, "
                f"движение {group['avg_gross']:+.2f}% "
                "без расходов, "
                f"положительных {group['positive']}/{group['n']}; "
                f"макс. по направлению {group['mfe']:.2f}%, "
                f"против {group['mae']:.2f}%"
            )

            if group["n_net"]:
                lines.append(
                    "  После заданных расходов: "
                    f"{group['avg_net']:+.2f}% "
                    f"(n={group['n_net']})."
                )

        if self.cost_bps is None:
            lines.append(
                "Для новых сигналов расходы не заданы."
            )

        else:
            lines.append(
                "Расходы новых сигналов за полный оборот: "
                f"{self.cost_bps:g} б.п. "
                "(1 б.п. = 0,01%)."
            )

        lines.append(
            "Симуляция без реальных сделок; "
            "окна могут пересекаться."
        )

        return "\n".join(lines)


if __name__ == "__main__":
    journal = SignalJournal.from_env(
        os.getenv("STATE_DIR", ".")
    )

    try:
        print(journal.summary_text())

    finally:
        journal.close()
