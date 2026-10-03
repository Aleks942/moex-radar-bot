"""Causal M10 pullback/retest tracking after delivered MOEX radar alerts.

SQLite tables share the journal database; existing journal rows are untouched.
Only settled candles can advance the pattern. This is a research signal,
not an order or evidence of a real fill.
"""

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from statistics import mean

MSK = timezone(timedelta(hours=3))
SETTLE_SECONDS = 20 * 60
MAX_SIGNAL_AGE_SECONDS = 45 * 60
MAX_SETUP_AGE_SECONDS = 8 * 3600
MAX_WAIT_BARS = 36
BREAKOUT_LOOKBACK = 12
MIN_IMPULSE_PCT = 0.35
MIN_IMPULSE_VOLUME = 1.3


def _ts(value):
    stamp = datetime.fromisoformat(str(value))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=MSK)
    return stamp.timestamp()


def _text_time(stamp):
    return datetime.fromtimestamp(stamp, MSK).strftime("%Y-%m-%d %H:%M:%S")


def _bars(columns, rows, now_ts):
    names = ("begin", "end", "open", "high", "low", "close", "volume")
    indices = {name: columns.index(name) for name in names}
    bars, seen = [], set()
    for row in rows:
        bar = {name: row[index] for name, index in indices.items()}
        begin, actual_end = _ts(bar["begin"]), _ts(bar["end"])
        if begin in seen or not begin <= actual_end < begin + 600:
            raise ValueError("Некорректное время/дубликат M10")
        seen.add(begin)
        for name in ("open", "high", "low", "close", "volume"):
            number = float(bar[name])
            if not math.isfinite(number) or number < 0 or (name != "volume" and number == 0):
                raise ValueError("Некорректные цены/объём M10")
            bar[name] = number
        if (bar["low"] > min(bar["open"], bar["close"], bar["high"])
                or bar["high"] < max(bar["open"], bar["close"], bar["low"])):
            raise ValueError("Некорректный OHLC M10")
        if begin > now_ts + 60:
            raise ValueError("M10 из будущего: проверь часовой пояс")
        bar.update(begin=begin, end=begin + 600,
                   settled=begin + 600 <= now_ts - SETTLE_SECONDS)
        bars.append(bar)
    return sorted(bars, key=lambda bar: bar["begin"])


class PullbackRetest:
    def __init__(self, db, retrace_min=30, retrace_max=60,
                 volume_max=0.80, cooldown_seconds=180 * 60):
        if not 0 < retrace_min < retrace_max < 100:
            raise ValueError("Неверные границы отката")
        if not 0 < volume_max <= 1 or cooldown_seconds < 0:
            raise ValueError("Неверные настройки объёма/cooldown")
        self.db = db
        self.retrace_min, self.retrace_max = retrace_min, retrace_max
        self.volume_max, self.cooldown_seconds = volume_max, cooldown_seconds
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS pullback_retest_setups (
                ticker TEXT PRIMARY KEY, payload TEXT NOT NULL
            )""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS pullback_retest_deliveries (
                setup_id TEXT PRIMARY KEY, ticker TEXT NOT NULL,
                delivered_ts REAL NOT NULL
            )""")
        print("[RETEST_READY] persistent_state=journal_db interval=M10", flush=True)

    def _load(self, ticker):
        row = self.db.execute(
            "SELECT payload FROM pullback_retest_setups WHERE ticker=?", (ticker,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, state):
        with self.db:
            self.db.execute("""INSERT INTO pullback_retest_setups VALUES (?, ?)
                ON CONFLICT(ticker) DO UPDATE SET payload=excluded.payload""",
                (state["ticker"], json.dumps(state, allow_nan=False)))

    def _cooling(self, ticker, now_ts):
        row = self.db.execute(
            "SELECT MAX(delivered_ts) FROM pullback_retest_deliveries WHERE ticker=?",
            (ticker,),
        ).fetchone()
        return row[0] is not None and now_ts - row[0] < self.cooldown_seconds

    @staticmethod
    def _cancel(state, reason):
        state.update(stage="cancelled", reason=reason)
        print(f"[RETEST_CANCEL] {state['ticker']} reason={reason}", flush=True)

    def arm(self, ticker, kind, direction, columns, rows, delivered_ts, seed_signal_id=None):
        if direction not in ("UP", "DOWN") or kind not in ("FAST", "FLOW", "AGG", "SAFE"):
            return False
        bars = _bars(columns, rows, delivered_ts)
        if len(bars) < BREAKOUT_LOOKBACK + 1 or self._cooling(ticker, delivered_ts):
            return False
        source = bars[-1]
        if delivered_ts - source["begin"] > MAX_SIGNAL_AGE_SECONDS:
            return False
        previous = self._load(ticker)
        if previous:
            if previous["source_begin"] == source["begin"] and previous["direction"] == direction:
                return False
            if previous["stage"] not in ("sent", "cancelled"):
                if previous["direction"] == direction:
                    return False  # New radar alerts must not reset an existing wait.
                self._cancel(previous, "opposite_radar_signal")
        reference = bars[-BREAKOUT_LOOKBACK-1:-1]
        level = (max(bar["high"] for bar in reference) if direction == "UP"
                 else min(bar["low"] for bar in reference))
        state = dict(id=uuid.uuid4().hex, ticker=ticker, kind=kind,
                     direction=direction, seeded_ts=float(delivered_ts),
                     source_begin=source["begin"], level=level, seed_signal_id=seed_signal_id,
                     reference_volume=mean(bar["volume"] for bar in reference),
                     stage="awaiting_impulse", bars_waited=0)
        self._save(state)
        print(f"[RETEST_WATCH] {ticker} source={kind} dir={direction}", flush=True)
        return True

    def _activate(self, state, bars):
        source = next((bar for bar in bars if bar["begin"] == state["source_begin"]), None)
        if source is None or not source["settled"]:
            return False
        up = state["direction"] == "UP"
        level = state["level"]
        crosses = (source["open"] <= level < source["close"] if up
                   else source["open"] >= level > source["close"])
        move = (source["close"] / source["open"] - 1) * (100 if up else -100)
        if (not crosses or move < MIN_IMPULSE_PCT
                or state["reference_volume"] <= 0
                or source["volume"] < MIN_IMPULSE_VOLUME * state["reference_volume"]):
            self._cancel(state, "impulse_not_confirmed")
            return False
        state.update(stage="wait_pullback", origin=source["open"],
                     peak=source["high"] if up else source["low"],
                     impulse_volume=source["volume"], last_begin=source["begin"],
                     tolerance=max(level * 0.0005, min(
                         (source["high"] - source["low"]) * 0.15, level * 0.0015)))
        print(f"[RETEST_IMPULSE] {state['ticker']} level={level:g}", flush=True)
        return True

    def _advance(self, state, bars, now_ts):
        if now_ts - state["seeded_ts"] > MAX_SETUP_AGE_SECONDS:
            self._cancel(state, "setup_expired")
            return
        if state["stage"] == "awaiting_impulse" and not self._activate(state, bars):
            return
        if state["stage"] == "ready":
            return
        up = state["direction"] == "UP"
        sign = 1 if up else -1
        level, tolerance = state["level"], state["tolerance"]
        for bar in bars:
            # Require complete future candles after the original message.
            if (not bar["settled"] or bar["begin"] <= state["last_begin"]
                    or bar["begin"] < state["seeded_ts"]):
                continue
            state["last_begin"] = bar["begin"]
            state["bars_waited"] += 1
            if state["bars_waited"] > MAX_WAIT_BARS:
                self._cancel(state, "bar_window_expired")
                return
            if sign * (bar["close"] - level) < -tolerance:
                self._cancel(state, "level_lost")
                return
            extreme = bar["high"] if up else bar["low"]
            adverse = bar["low"] if up else bar["high"]
            if state["stage"] == "wait_pullback" and sign * (extreme - state["peak"]) > 0:
                state["peak"] = extreme
                continue  # OHLC cannot establish peak-before-pullback within one bar.
            width = sign * (state["peak"] - state["origin"])
            retrace = sign * (state["peak"] - adverse) / width * 100
            if retrace > self.retrace_max:
                self._cancel(state, "pullback_too_deep")
                return
            if state["stage"] == "wait_pullback":
                touches = bar["low"] <= level + tolerance and bar["high"] >= level - tolerance
                if (self.retrace_min <= retrace <= self.retrace_max and touches
                        and 0 < bar["volume"] <= self.volume_max * state["impulse_volume"]):
                    state.update(stage="wait_retest", pullback_begin=bar["begin"],
                                 pullback_high=bar["high"], pullback_low=bar["low"],
                                 pullback_volume=bar["volume"], retrace_pct=retrace,
                                 adverse_price=adverse)
                    print(f"[PULLBACK_OK] {state['ticker']} retrace={retrace:.1f}%", flush=True)
                continue
            # Confirmation must be a DIFFERENT, later settled candle.
            state["adverse_price"] = (min(state["adverse_price"], adverse) if up
                                      else max(state["adverse_price"], adverse))
            trigger = state["pullback_high"] if up else state["pullback_low"]
            if (sign * (bar["close"] - trigger) > 0
                    and sign * (bar["close"] - bar["open"]) > 0
                    and sign * (bar["close"] - level) > 0
                    and bar["volume"] >= state["pullback_volume"]):
                state.update(stage="ready", confirmation=bar,
                             invalidation_price=state["adverse_price"])
                return

    def _event(self, state, bars, now_ts):
        confirmation, latest = state["confirmation"], bars[-1]
        up = state["direction"] == "UP"
        sign = 1 if up else -1
        stop = state["invalidation_price"]
        risk = sign * (confirmation["close"] - stop)
        lost_after_confirmation = any(
            bar["begin"] > confirmation["begin"] and
            sign * ((bar["low"] if up else bar["high"]) - stop) <= 0
            for bar in bars
        )
        if now_ts - confirmation["end"] > MAX_SIGNAL_AGE_SECONDS:
            self._cancel(state, "confirmation_stale")
        elif now_ts - latest["begin"] > MAX_SIGNAL_AGE_SECONDS:
            self._cancel(state, "latest_price_stale")
        elif (lost_after_confirmation or risk <= 0 or sign * (latest["close"] - stop) <= 0
              or sign * (latest["close"] - state["level"]) < -state["tolerance"]):
            self._cancel(state, "structure_lost_before_delivery")
        elif sign * (latest["close"] - confirmation["close"]) > 0.5 * risk:
            self._cancel(state, "late_move_before_delivery")
        elif self._cooling(state["ticker"], now_ts):
            self._cancel(state, "cooldown")
        if state["stage"] != "ready":
            return None
        return dict(setup_id=state["id"], ticker=state["ticker"],
                    direction=state["direction"], source_kind=state["kind"],
                    seed_signal_id=state["seed_signal_id"],
                    source_begin=_text_time(confirmation["begin"]),
                    source_end=_text_time(confirmation["end"] - 1),
                    source_price=confirmation["close"], level=state["level"],
                    invalidation_price=stop, retrace_pct=state["retrace_pct"],
                    pullback_volume_ratio=state["pullback_volume"] / state["impulse_volume"],
                    observed_price=latest["close"], observed_begin=_text_time(latest["begin"]))

    def update(self, fetch_candles, now_ts):
        events, errors = [], 0
        rows = self.db.execute("SELECT ticker, payload FROM pullback_retest_setups").fetchall()
        for ticker, payload in rows:
            try:
                state = json.loads(payload)
                if state["stage"] in ("sent", "cancelled"):
                    continue
                if now_ts - state["seeded_ts"] > MAX_SETUP_AGE_SECONDS:
                    self._cancel(state, "setup_expired")
                    self._save(state)
                    continue
                columns, raw = fetch_candles(ticker, 10, 10)
                if not columns or not raw:
                    continue  # Missing data is not a failed trade/pattern.
                bars = _bars(columns, raw, now_ts)
                self._advance(state, bars, now_ts)
                if state["stage"] == "ready":
                    event = self._event(state, bars, now_ts)
                    if event:
                        events.append(event)
                self._save(state)
            except Exception as exc:
                errors += 1
                print(f"[RETEST_ERROR] {ticker} {type(exc).__name__}: {exc}", flush=True)
        pending = sum(json.loads(row[0])["stage"] not in ("sent", "cancelled")
                      for row in self.db.execute("SELECT payload FROM pullback_retest_setups"))
        print(f"[RETEST_UPDATE] pending={pending} ready={len(events)} errors={errors}", flush=True)
        return events

    def mark_sent(self, event, delivered_ts):
        state = self._load(event["ticker"])
        if not state or state["id"] != event["setup_id"] or state["stage"] != "ready":
            raise ValueError("Сценарий изменился до подтверждения отправки")
        state.update(stage="sent", delivered_ts=delivered_ts)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO pullback_retest_deliveries VALUES (?, ?, ?)",
                            (state["id"], state["ticker"], delivered_ts))
            self.db.execute("UPDATE pullback_retest_setups SET payload=? WHERE ticker=?",
                            (json.dumps(state, allow_nan=False), state["ticker"]))
        print(f"[RETEST_SIGNAL] {state['ticker']} {state['direction']} id={state['id']}", flush=True)
