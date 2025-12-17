import os
import time
import json
import requests
from datetime import datetime, timedelta

# ===== ENV =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== НАСТРОЙКИ =====
CHECK_INTERVAL = 300

DAILY_REPORT_HOUR = 19      # 19:00 МСК
WEEKLY_REPORT_WEEKDAY = 0  # Понедельник
WEEKLY_REPORT_HOUR = 10    # 10:00 МСК

INTERVAL_H1 = 60
INTERVAL_D1 = 1440
INTERVAL_W1 = 10080
LOOKBACK_BARS = 20

RADAR_STATE_FILE = "radar_state.json"

# ===== ПУЛЫ АКЦИЙ =====
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

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

last_daily_report = None
last_weekly_report = None
last_start_in_memory = None

# ===== TELEGRAM =====
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# ===== RADAR START (1 РАЗ В СУТКИ) =====
def load_radar_state():
    if not os.path.exists(RADAR_STATE_FILE):
        return {}
    try:
        with open(RADAR_STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_radar_state(state):
    try:
        with open(RADAR_STATE_FILE, "w") as f:
            json.dump(state, f)
    except:
        pass

def send_radar_start_once_per_day():
    global last_start_in_memory

    today = (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d")

    # защита в памяти
    if last_start_in_memory == today:
        return

    state = load_radar_state()
    if state.get("last_start") == today:
        last_start_in_memory = today
        return

    send(
        "🇷🇺 МОЕХ-РАДАР АКТИВЕН\n"
        "Акции РФ • H1 / D1 / W1 • стадии • сила • обзоры"
    )

    state["last_start"] = today
    save_radar_state(state)
    last_start_in_memory = today

# ===== DATA =====
def get_candles(ticker, interval, days):
    try:
        r = requests.get(
            f"{MOEX}/{ticker}/candles.json",
            params={
                "interval": interval,
                "from": (datetime.utcnow()-timedelta(days=days)).strftime("%Y-%m-%d")
            },
            timeout=10
        ).json()
        return r["candles"]["data"]
    except:
        return []

def get_price(ticker):
    try:
        r = requests.get(f"{MOEX}/{ticker}.json", timeout=10).json()
        md = r["marketdata"]
        return float(md["data"][0][md["columns"].index("LAST")])
    except:
        return None

# ===== HELPERS =====
def trend_by_ema(candles, period=20):
    if len(candles) < period:
        return "FLAT"
    closes = [c[1] for c in candles[-period:]]
    ema = sum(closes) / len(closes)
    price = closes[-1]
    if price > ema * 1.01:
        return "UP"
    if price < ema * 0.99:
        return "DOWN"
    return "FLAT"

def get_stage_h1(ticker):
    h1 = get_candles(ticker, INTERVAL_H1, 7)
    if len(h1) < LOOKBACK_BARS:
        return "ACCUM"
    recent = h1[-LOOKBACK_BARS:]
    highs = [c[2] for c in recent]
    lows  = [c[3] for c in recent]
    price = get_price(ticker)
    if not price:
        return "ACCUM"
    if price > max(highs):
        return "UP"
    if price < min(lows):
        return "DOWN"
    return "ACCUM"

# ===== РЕЖИМ РЫНКА =====
def get_market_mode():
    score = 0

    imoex_d1 = trend_by_ema(get_candles("IMOEX", INTERVAL_D1, 120))
    imoex_w1 = trend_by_ema(get_candles("IMOEX", INTERVAL_W1, 400))

    if imoex_d1 == "UP" and imoex_w1 == "UP":
        score += 1
    elif imoex_d1 == "DOWN" and imoex_w1 == "DOWN":
        score -= 1

    up_cnt = down_cnt = 0
    for t in ALL_TICKERS:
        st = get_stage_h1(t)
        if st == "UP": up_cnt += 1
        if st == "DOWN": down_cnt += 1

    if up_cnt > down_cnt: score += 1
    elif down_cnt > up_cnt: score -= 1

    if up_cnt >= 3: score += 1
    if down_cnt >= 3: score -= 1

    if score >= 2: return "🟢 РЫНОК СИЛЬНЫЙ"
    if score <= -2: return "🔴 РЫНОК СЛАБЫЙ"
    return "🟡 РЫНОК НЕЙТРАЛЬНЫЙ"

# ===== ОБЗОРЫ =====
def send_daily_report():
    global last_daily_report
    now = datetime.utcnow() + timedelta(hours=3)
    today = now.date()
    if last_daily_report == today or now.hour != DAILY_REPORT_HOUR:
        return

    mode = get_market_mode()
    stages = {"UP":0,"DOWN":0,"ACCUM":0}
    top = []

    for t in ALL_TICKERS:
        st = get_stage_h1(t)
        stages[st] += 1
        strength = 4 if st in ("UP","DOWN") else 2
        if t in PRIORITY_TICKERS:
            strength = min(strength + 1, 5)
        top.append((t, strength))

    top = sorted(top, key=lambda x: x[1], reverse=True)[:3]

    msg = (
        "🇷🇺 ОБЗОР МОЕХ — СЕГОДНЯ\n\n"
        f"🧠 РЕЖИМ РЫНКА:\n{mode}\n\n"
        "📊 Стадии:\n"
        f"🟢 Накопление: {stages['ACCUM']}\n"
        f"📈 Импульс вверх: {stages['UP']}\n"
        f"📉 Импульс вниз: {stages['DOWN']}\n\n"
        "🔥 ТОП СИЛА:\n" +
        "\n".join([f"{i+1}) {t} ({s}/5){' ⭐' if t in PRIORITY_TICKERS else ''}" for i,(t,s) in enumerate(top)])
    )

    send(msg)
    last_daily_report = today

def send_weekly_report():
    global last_weekly_report
    now = datetime.utcnow() + timedelta(hours=3)
    today = now.date()
    if (last_weekly_report == today or
        now.weekday() != WEEKLY_REPORT_WEEKDAY or
        now.hour != WEEKLY_REPORT_HOUR):
        return

    imoex_trend = trend_by_ema(get_candles("IMOEX", INTERVAL_W1, 400))
    counts = {"UP":0,"DOWN":0,"FLAT":0}
    focus = []

    for t in ALL_TICKERS:
        w1 = trend_by_ema(get_candles(t, INTERVAL_W1, 400))
        counts[w1] += 1
        if t in PRIORITY_TICKERS:
            focus.append(t)

    msg = (
        "🇷🇺 НЕДЕЛЬНЫЙ ОБЗОР МОЕХ (W1)\n\n"
        f"IMOEX W1: {imoex_trend}\n\n"
        "📊 Распределение:\n"
        f"📈 UP: {counts['UP']}\n"
        f"📉 DOWN: {counts['DOWN']}\n"
        f"➖ FLAT: {counts['FLAT']}\n\n"
        "⭐ Приоритеты недели:\n" + ", ".join(focus[:5])
    )

    send(msg)
    last_weekly_report = today

# ===== START =====
send_radar_start_once_per_day()

while True:
    try:
        send_daily_report()
        send_weekly_report()
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
