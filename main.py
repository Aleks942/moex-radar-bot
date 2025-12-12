import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300

INTERVAL_H1 = 60
INTERVAL_D1 = 1440
INTERVAL_W1 = 10080

LOOKBACK_BARS = 20

TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

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

# ===== TREND HELPERS =====
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

# ===== MARKET MODE =====
def get_market_mode():
    score = 0

    # 1️⃣ IMOEX D1 + W1
    imoex_d1 = trend_by_ema(get_candles("IMOEX", INTERVAL_D1, 120))
    imoex_w1 = trend_by_ema(get_candles("IMOEX", INTERVAL_W1, 400))

    if imoex_d1 == "UP" and imoex_w1 == "UP":
        score += 1
    elif imoex_d1 == "DOWN" and imoex_w1 == "DOWN":
        score -= 1

    # 2️⃣ Баланс стадий
    up_cnt = down_cnt = 0
    for t in TICKERS:
        h1 = get_candles(t, INTERVAL_H1, 7)
        if len(h1) < LOOKBACK_BARS:
            continue
        recent = h1[-LOOKBACK_BARS:]
        highs = [c[2] for c in recent]
        lows  = [c[3] for c in recent]
        price = get_price(t)
        if not price:
            continue
        if price > max(highs):
            up_cnt += 1
        elif price < min(lows):
            down_cnt += 1

    if up_cnt > down_cnt:
        score += 1
    elif down_cnt > up_cnt:
        score -= 1

    # 3️⃣ Качество среды (упрощённо)
    if up_cnt >= 3:
        score += 1
    if down_cnt >= 3:
        score -= 1

    if score >= 2:
        return "🟢 РЫНОК СИЛЬНЫЙ"
    if score <= -2:
        return "🔴 РЫНОК СЛАБЫЙ"
    return "🟡 РЫНОК НЕЙТРАЛЬНЫЙ"

# ===== DAILY REPORT =====
last_report_date = None

def send_daily_report():
    global last_report_date
    now = datetime.utcnow() + timedelta(hours=3)
    today = now.date()

    if last_report_date == today or now.hour != 19:
        return

    mode = get_market_mode()

    send(
        "🇷🇺 ОБЗОР МОЕХ — СЕГОДНЯ\n\n"
        f"🧠 РЕЖИМ РЫНКА:\n{mode}\n\n"
        "Комментарий:\n"
        "🟢 сильный — приоритет импульсы\n"
        "🟡 нейтр — работа от уровней\n"
        "🔴 слабый — защита капитала"
    )

    last_report_date = today

# ===== START =====
send("🇷🇺 МОЕХ-РАДАР\nРежим рынка встроен")

while True:
    try:
        send_daily_report()
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
