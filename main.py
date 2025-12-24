import os
import time
import json
import requests
from datetime import datetime, timedelta, timezone
from statistics import mean

print("=== MOEX RADAR (AGG + SAFE + CONFIRM + STATS + REPORTS) ===", flush=True)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# MSK = UTC+3
MSK_OFFSET_HOURS = 3

# =========================
# SETTINGS
# =========================
CHECK_INTERVAL_SEC = 60 * 5

LOOKBACK_H1_BARS = 24
EMA_PERIOD = 20

COOLDOWN_MIN = 90

AGG_VOL_MULT_MIN = 1.5
AGG_BREAK_PCT_MIN = 0.35

SAFE_MIN_STRENGTH = 4
CONFIRM_WINDOW_HOURS = 48

OVERHEAT_D1_PCT = 8.0

DAILY_REPORT_HOUR = 19
DAILY_REPORT_MINUTE = 0

WEEKLY_REPORT_WEEKDAY = 0
WEEKLY_REPORT_HOUR = 10
WEEKLY_REPORT_MINUTE = 0

STATE_DIR = os.getenv("STATE_DIR", ".")
STATE_FILE = os.path.join(STATE_DIR, "moex_radar_state.json")

# =========================
# TICKERS
# =========================
BASE_TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

PRIORITY_TICKERS = [
    "YNDX","OZON","AFKS","SMLT","PIKK",
    "MOEX","RUAL","FLOT","POLY","SBERP"
]

ALL_TICKERS = list(dict.fromkeys(BASE_TICKERS + PRIORITY_TICKERS))
INDEX_TICKER = "IMOEX"

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

# =========================
# TELEGRAM
# =========================
def send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15
        )
    except:
        pass

# =========================
# TIME
# =========================
def msk_now():
    return datetime.now(timezone.utc) + timedelta(hours=MSK_OFFSET_HOURS)

def should_fire_at(now_dt, hour, minute):
    return now_dt.hour == hour and now_dt.minute == minute

# =========================
# STATE
# =========================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(state: dict):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except:
        pass

# =========================
# DATA (SAFE PARSE)
# =========================
def get_candles(ticker: str, interval: int, days: int):
    """
    Возвращает (columns, data)
    columns: список названий колонок
    data: список строк
    """
    try:
        r = requests.get(
            f"{MOEX}/{ticker}/candles.json",
            params={
                "interval": interval,
                "from": (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            },
            timeout=20
        ).json()

        candles = r.get("candles", {})
        cols = candles.get("columns", [])
        data = candles.get("data", [])
        if not cols or not data:
            return [], []
        return cols, data
    except:
        return [], []

def col_idx(cols, name):
    try:
        return cols.index(name)
    except:
        return None

def extract_series(cols, data, n):
    """
    Универсально достаём high/low/close/volume по именам колонок.
    Если какой-то колонки нет — возвращаем None в соответствующем списке.
    """
    if not cols or not data:
        return [], [], [], []

    tail = data[-n:] if len(data) >= n else data

    i_close = col_idx(cols, "close")
    i_high  = col_idx(cols, "high")
    i_low   = col_idx(cols, "low")
    i_vol   = col_idx(cols, "volume")

    highs, lows, closes, vols = [], [], [], []

    for row in tail:
        # row может быть короче чем cols, поэтому каждое чтение защищаем
        try:
            close = float(row[i_close]) if i_close is not None and i_close < len(row) and row[i_close] is not None else None
            high  = float(row[i_high])  if i_high  is not None and i_high  < len(row) and row[i_high]  is not None else None
            low   = float(row[i_low])   if i_low   is not None and i_low   < len(row) and row[i_low]   is not None else None
            vol   = float(row[i_vol])   if i_vol   is not None and i_vol   < len(row) and row[i_vol]   is not None else 0.0
        except:
            continue

        if close is None or high is None or low is None:
            continue

        closes.append(close)
        highs.append(high)
        lows.append(low)
        vols.append(vol)

    return highs, lows, closes, vols

def pct(a, b):
    if a is None or b is None or b == 0:
        return 0.0
    return (a - b) / b * 100.0

def ema_simple(values, period):
    if len(values) < period:
        return None
    return mean(values[-period:])

# =========================
# INDEX TREND (IMOEX)
# =========================
def index_trend():
    # D1 candles: interval=24 (в ISS candles так обычно)
    cols, data = get_candles(INDEX_TICKER, 24, 220)
    _, _, closes, _ = extract_series(cols, data, 60)
    if len(closes) < EMA_PERIOD:
        return "FLAT"

    ema = ema_simple(closes, EMA_PERIOD)
    last = closes[-1]
    if ema is None:
        return "FLAT"

    if last > ema * 1.01:
        return "UP"
    if last < ema * 0.99:
        return "DOWN"
    return "FLAT"

def market_mode_text(tr):
    if tr == "UP":
        return "🟢 РЫНОК СИЛЬНЫЙ (IMOEX UP)"
    if tr == "DOWN":
        return "🔴 РЫНОК СЛАБЫЙ (IMOEX DOWN)"
    return "🟡 РЫНОК НЕЙТРАЛЬНЫЙ (IMOEX FLAT)"

# =========================
# STAGES + SIGNALS
# =========================
def stage_and_signal(ticker: str, idx_tr: str):
    """
    Возвращает:
    stage, direction, strength(1-5), vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, is_overheat
    """
    # H1
    cols_h1, data_h1 = get_candles(ticker, 60, 20)
    highs, lows, closes, vols = extract_series(cols_h1, data_h1, LOOKBACK_H1_BARS)
    if len(closes) < LOOKBACK_H1_BARS or len(highs) < LOOKBACK_H1_BARS or len(lows) < LOOKBACK_H1_BARS:
        return None

    price = closes[-1]
    hi = max(highs)
    lo = min(lows)

    h1_prev = closes[-2] if len(closes) >= 2 else closes[-1]
    h1_chg = pct(price, h1_prev)
    direction = "UP" if h1_chg >= 0 else "DOWN"

    # объём H1
    vol_now = vols[-1] if vols else 0.0
    vol_avg = mean(vols[:-1]) if len(vols) > 6 else (mean(vols) if vols else 0.0)
    vol_mult = (vol_now / vol_avg) if vol_avg and vol_avg > 0 else 0.0

    # D1
    cols_d1, data_d1 = get_candles(ticker, 24, 450)
    _, _, d1_closes, _ = extract_series(cols_d1, data_d1, 60)
    d1_last = d1_closes[-1] if d1_closes else None
    d1_prev = d1_closes[-2] if len(d1_closes) >= 2 else d1_last
    d1_chg = pct(d1_last, d1_prev)

    # перегрев D1 за 5 баров
    is_overheat = False
    if len(d1_closes) >= 6:
        d1_5 = pct(d1_closes[-1], d1_closes[-6])
        if abs(d1_5) >= OVERHEAT_D1_PCT:
            is_overheat = True

    # стадия + сила
    stage = "ACCUM"
    reasons = []
    strength = 0

    break_up = price > hi * (1 + AGG_BREAK_PCT_MIN / 100.0)
    break_dn = price < lo * (1 - AGG_BREAK_PCT_MIN / 100.0)

    if break_up:
        stage = "IMPULSE_UP"
        reasons.append("Выход вверх из диапазона H1")
        strength += 1
    elif break_dn:
        stage = "IMPULSE_DOWN"
        reasons.append("Выход вниз из диапазона H1")
        strength += 1
    else:
        # накопление + рост объёма
        rng = (hi - lo) / price * 100.0 if price else 0.0
        if rng <= 2.0 and vol_mult >= 1.3:
            reasons.append("Сжатие диапазона + рост объёма")
            strength += 1

    # объём сила
    if vol_mult >= 1.5:
        strength += 1
        reasons.append(f"Объём x{vol_mult:.2f}")
    if vol_mult >= 2.2:
        strength += 1
    if vol_mult >= 3.0:
        strength += 1

    # согласие H1 и D1
    if d1_chg * h1_chg > 0 and abs(d1_chg) > 0.2:
        strength += 1
        reasons.append("H1 + D1 в одну сторону")

    # индекс фильтр
    if idx_tr == "UP" and direction == "UP":
        strength += 1
        reasons.append("IMOEX поддерживает вверх")
    elif idx_tr == "DOWN" and direction == "DOWN":
        strength += 1
        reasons.append("IMOEX поддерживает вниз")
    elif idx_tr == "DOWN" and direction == "UP":
        reasons.append("IMOEX против направления")
    elif idx_tr == "UP" and direction == "DOWN":
        reasons.append("IMOEX против направления")

    # приоритет
    if ticker in PRIORITY_TICKERS:
        strength += 1
        reasons.append("Приоритетная бумага")

    # перегрев режет сигналы
    if is_overheat:
        stage = "OVERHEAT"
        reasons.append("Перегрев по D1")

    strength = max(1, min(strength, 5))

    # AGG / SAFE
    is_agg = (vol_mult >= AGG_VOL_MULT_MIN and stage in ("IMPULSE_UP", "IMPULSE_DOWN") and not is_overheat)

    idx_ok = (idx_tr == "FLAT") or (idx_tr == "UP" and direction == "UP") or (idx_tr == "DOWN" and direction == "DOWN")
    tf_ok = (d1_chg * h1_chg > 0) and (abs(d1_chg) > 0.2)
    is_safe = (is_agg and tf_ok and idx_ok and strength >= SAFE_MIN_STRENGTH)

    return stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, is_overheat

def stage_emoji(stage):
    if stage.startswith("IMPULSE"):
        return "🟡"
    if stage == "OVERHEAT":
        return "🔴"
    return "🟢"

def memo_intraday():
    return (
        "🕒 <b>Чек</b>\n"
        "1) вход только после паузы/ретеста\n"
        "2) стоп за локальный экстремум\n"
        "⛔ если нет структуры — SKIP"
    )

# =========================
# MAIN
# =========================
def run():
    state = load_state()
    coins_state = state.get("coins", {})
    stats = state.get("stats", {})

    now = msk_now()
    day_key = now.strftime("%Y-%m-%d")
    week_key = now.strftime("%G-%V")

    if not stats:
        stats = {
            "day": day_key, "agg": 0, "safe": 0, "confirmed": 0,
            "week": week_key, "w_agg": 0, "w_safe": 0, "w_confirmed": 0
        }

    # стартовое сообщение 1 раз в сутки
    if state.get("start_day") != day_key:
        send("🇷🇺 <b>MOEX-радар активен</b>\nАкции РФ • H1 + D1 • AGG + SAFE • подтверждение • статистика")
        state["start_day"] = day_key
        state["coins"] = coins_state
        state["stats"] = stats
        save_state(state)

    while True:
        try:
            now = msk_now()
            day_key = now.strftime("%Y-%m-%d")
            week_key = now.strftime("%G-%V")

            # rollover
            if stats.get("day") != day_key:
                stats["day"] = day_key
                stats["agg"] = 0
                stats["safe"] = 0
                stats["confirmed"] = 0

            if stats.get("week") != week_key:
                stats["week"] = week_key
                stats["w_agg"] = 0
                stats["w_safe"] = 0
                stats["w_confirmed"] = 0

            idx_tr = index_trend()
            mode_text = market_mode_text(idx_tr)

            # DAILY REPORT
            if should_fire_at(now, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE) and state.get("last_daily_day") != day_key:
                agg = stats.get("agg", 0)
                safe = stats.get("safe", 0)
                conf = stats.get("confirmed", 0)
                rate = (conf / agg * 100.0) if agg > 0 else 0.0

                quality = "🟡 НЕЙТРАЛЬНОЕ"
                if agg >= 6 and rate >= 25:
                    quality = "🟢 ХОРОШЕЕ"
                elif agg >= 6 and rate < 12:
                    quality = "🔴 ШУМНОЕ"

                send(
                    "🇷🇺 <b>ОБЗОР МОЕХ — СЕГОДНЯ</b>\n\n"
                    f"🧠 Режим рынка:\n{mode_text}\n\n"
                    f"AGGRESSIVE: {agg}\n"
                    f"SAFE: {safe}\n"
                    f"Подтверждений: {conf}\n"
                    f"Качество: <b>{quality}</b>\n"
                )
                state["last_daily_day"] = day_key

            # WEEKLY REPORT
            if (now.weekday() == WEEKLY_REPORT_WEEKDAY and
                should_fire_at(now, WEEKLY_REPORT_HOUR, WEEKLY_REPORT_MINUTE) and
                state.get("last_weekly_week") != week_key):

                send(
                    "🇷🇺 <b>НЕДЕЛЬНЫЙ ОБЗОР МОЕХ</b>\n\n"
                    f"{mode_text}\n\n"
                    f"AGGRESSIVE: {stats.get('w_agg', 0)}\n"
                    f"SAFE: {stats.get('w_safe', 0)}\n"
                    f"Подтверждений: {stats.get('w_confirmed', 0)}\n"
                )
                state["last_weekly_week"] = week_key

            # RADAR
            now_ts = datetime.now(timezone.utc).timestamp()

            for t in ALL_TICKERS:
                cs = coins_state.get(t, {})
                last_sent_ts = cs.get("last_sent_ts", 0)
                if last_sent_ts and (now_ts - last_sent_ts) < (COOLDOWN_MIN * 60):
                    continue

                pack = stage_and_signal(t, idx_tr)
                if pack is None:
                    continue

                stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, _ = pack
                if not is_agg and not is_safe:
                    continue

                sig_type = "SAFE" if is_safe else "AGG"

                # анти-дубликат
                if cs.get("last_type") == sig_type and cs.get("last_stage") == stage and cs.get("last_strength") == strength:
                    continue

                # confirm AGG -> SAFE
                confirmed = False
                confirmed_tag = ""
                if sig_type == "SAFE":
                    last_agg_ts = cs.get("last_agg_ts", 0)
                    last_agg_dir = cs.get("last_agg_dir")
                    if last_agg_ts and (now_ts - last_agg_ts) <= (CONFIRM_WINDOW_HOURS * 3600) and last_agg_dir == direction:
                        confirmed = True
                        confirmed_tag = "\n<b>AGGRESSIVE → SAFE подтверждён</b>"

                fire = "🔥" * strength
                emoji = stage_emoji(stage)

                if sig_type == "AGG":
                    title = "⚠️ <b>AGGRESSIVE</b> — ранний радар"
                    conclusion = "🔴 <b>НЕ ВХОД</b>\n(наблюдать и ждать структуру)"
                else:
                    title = f"✅ <b>SAFE</b>{confirmed_tag}"
                    conclusion = "🟢 <b>МОЖНО ПЛАНИРОВАТЬ</b>\n(вход только по структуре)"

                msg = (
                    f"{title}\n"
                    f"{emoji} <b>{t}</b>\n"
                    f"Стадия: <b>{stage}</b>\n"
                    f"Сила: {fire} ({strength}/5)\n\n"
                    f"H1: {h1_chg:.2f}% | D1: {d1_chg:.2f}%\n"
                    f"Объём: x{vol_mult:.2f}\n\n"
                    f"Причины:\n• " + "\n• ".join(reasons[:10]) +
                    f"\n\n{memo_intraday()}\n\n"
                    f"🧠 <b>ВЫВОД</b>:\n{conclusion}"
                )

                send(msg)

                # update state for ticker
                cs["last_sent_ts"] = now_ts
                cs["last_type"] = sig_type
                cs["last_stage"] = stage
                cs["last_strength"] = strength

                if sig_type == "AGG":
                    cs["last_agg_ts"] = now_ts
                    cs["last_agg_dir"] = direction

                coins_state[t] = cs

                # stats
                if sig_type == "AGG":
                    stats["agg"] = stats.get("agg", 0) + 1
                    stats["w_agg"] = stats.get("w_agg", 0) + 1
                else:
                    stats["safe"] = stats.get("safe", 0) + 1
                    stats["w_safe"] = stats.get("w_safe", 0) + 1
                    if confirmed:
                        stats["confirmed"] = stats.get("confirmed", 0) + 1
                        stats["w_confirmed"] = stats.get("w_confirmed", 0) + 1

            # save state
            state["coins"] = coins_state
            state["stats"] = stats
            save_state(state)

        except Exception as e:
            send(f"❌ <b>BOT ERROR</b>: {e}")

        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    run()
