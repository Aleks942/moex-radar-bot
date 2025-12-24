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

def save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except:
        pass

# =========================
# DATA
# =========================
def get_candles(ticker, interval, days):
    try:
        r = requests.get(
            f"{MOEX}/{ticker}/candles.json",
            params={
                "interval": interval,
                "from": (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            },
            timeout=20
        ).json()
        return r.get("candles", {}).get("data", [])
    except:
        return []

def extract_hlc(candles, n):
    tail = candles[-n:] if len(candles) >= n else candles
    highs, lows, closes, vols = [], [], [], []
    for c in tail:
        try:
            closes.append(float(c[2]))
            highs.append(float(c[3]))
            lows.append(float(c[4]))
            vols.append(float(c[6]) if len(c) > 6 else 0.0)
        except:
            continue
    return highs, lows, closes, vols

def pct(a, b):
    if not a or not b or b == 0:
        return 0.0
    return (a - b) / b * 100.0

# =========================
# INDEX TREND
# =========================
def index_trend():
    d1 = get_candles(INDEX_TICKER, 24, 200)
    _, _, d1_closes, _ = extract_hlc(d1, 40)

    if len(d1_closes) < EMA_PERIOD:
        return "FLAT"

    ema = mean(d1_closes[-EMA_PERIOD:])
    last = d1_closes[-1]

    if last > ema * 1.01:
        return "UP"
    if last < ema * 0.99:
        return "DOWN"
    return "FLAT"

# =========================
# SIGNAL LOGIC
# =========================
def stage_and_signal(ticker, idx_trend):
    h1 = get_candles(ticker, 60, 15)
    if len(h1) < LOOKBACK_H1_BARS:
        return None

    highs, lows, closes, vols = extract_hlc(h1, LOOKBACK_H1_BARS)
    price = closes[-1]

    hi, lo = max(highs), min(lows)
    h1_prev = closes[-2]
    h1_chg = pct(price, h1_prev)

    vol_now = vols[-1]
    vol_avg = mean(vols[:-1]) if len(vols) > 5 else mean(vols)
    vol_mult = vol_now / vol_avg if vol_avg > 0 else 0

    stage = "ACCUM"
    strength = 0
    reasons = []

    if price > hi * (1 + AGG_BREAK_PCT_MIN / 100):
        stage = "IMPULSE_UP"
        strength += 1
        reasons.append("Пробой H1 вверх")
    elif price < lo * (1 - AGG_BREAK_PCT_MIN / 100):
        stage = "IMPULSE_DOWN"
        strength += 1
        reasons.append("Пробой H1 вниз")

    if vol_mult >= 1.5:
        strength += 1
        reasons.append(f"Объём x{vol_mult:.2f}")

    direction = "UP" if h1_chg >= 0 else "DOWN"

    if idx_trend == direction:
        strength += 1
        reasons.append("IMOEX поддерживает")

    if ticker in PRIORITY_TICKERS:
        strength += 1
        reasons.append("Приоритетная акция")

    strength = max(1, min(strength, 5))

    is_agg = vol_mult >= AGG_VOL_MULT_MIN and stage.startswith("IMPULSE")
    is_safe = is_agg and strength >= SAFE_MIN_STRENGTH

    return stage, direction, strength, vol_mult, h1_chg, reasons, is_agg, is_safe

# =========================
# MAIN
# =========================
def run():
    state = load_state()
    coins_state = state.get("coins", {})
    stats = state.get("stats", {})

    now = msk_now()
    day_key = now.strftime("%Y-%m-%d")

    if state.get("start_day") != day_key:
        send("🇷🇺 <b>MOEX-радар активен</b>\nАкции РФ • H1 + D1 • AGG + SAFE")
        state["start_day"] = day_key
        save_state(state)

    while True:
        try:
            idx_tr = index_trend()
            now_ts = datetime.now(timezone.utc).timestamp()

            for t in ALL_TICKERS:
                cs = coins_state.get(t, {})
                if cs.get("last_sent_ts") and now_ts - cs["last_sent_ts"] < COOLDOWN_MIN * 60:
                    continue

                pack = stage_and_signal(t, idx_tr)
                if not pack:
                    continue

                stage, direction, strength, vol_mult, h1_chg, reasons, is_agg, is_safe = pack
                if not is_agg and not is_safe:
                    continue

                sig = "SAFE" if is_safe else "AGG"

                msg = (
                    f"{'✅ SAFE' if is_safe else '⚠️ AGGRESSIVE'}\n"
                    f"<b>{t}</b>\n"
                    f"Стадия: {stage}\n"
                    f"Сила: {'🔥'*strength} ({strength}/5)\n"
                    f"H1: {h1_chg:.2f}%\n"
                    f"Объём: x{vol_mult:.2f}\n\n"
                    "Причины:\n• " + "\n• ".join(reasons)
                )

                send(msg)

                coins_state[t] = {
                    "last_sent_ts": now_ts,
                    "last_type": sig,
                    "last_stage": stage,
                    "last_strength": strength
                }

            state["coins"] = coins_state
            save_state(state)

        except Exception as e:
            send(f"❌ ERROR: {e}")

        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    run()
