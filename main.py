import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== НАСТРОЙКИ =====
CHECK_INTERVAL = 300
DAILY_REPORT_HOUR = 19  # 19:00 МСК
LOOKBACK_BARS = 20
INTERVAL_H1 = 60
INTERVAL_D1 = 1440

TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

last_daily_report_date = None

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

# ===== D1 TREND =====
def get_d1_trend_imoex():
    d1 = get_candles("IMOEX", INTERVAL_D1, 120)
    if len(d1) < 60:
        return "FLAT"

    closes = [c[1] for c in d1[-50:]]
    ema50 = sum(closes) / len(closes)
    price = closes[-1]

    if price > ema50 * 1.005:
        return "UP"
    if price < ema50 * 0.995:
        return "DOWN"
    return "FLAT"

# ===== STAGE (упрощённо для обзора) =====
def get_stage(ticker):
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

# ===== DAILY REPORT =====
def send_daily_report():
    global last_daily_report_date

    now = datetime.utcnow() + timedelta(hours=3)  # МСК
    today = now.date()

    if last_daily_report_date == today or now.hour != DAILY_REPORT_HOUR:
        return

    imoex = get_d1_trend_imoex()

    stages = {"UP": 0, "DOWN": 0, "ACCUM": 0}
    strengths = []

    for t in TICKERS:
        stage = get_stage(t)
        stages[stage] += 1

        # грубая сила для обзора
        if stage == "UP":
            strengths.append((t, 4))
        elif stage == "DOWN":
            strengths.append((t, 4))

    strengths = sorted(strengths, key=lambda x: x[1], reverse=True)[:3]

    if imoex == "UP" and stages["UP"] > stages["DOWN"]:
        mode = "🟢 РЕЖИМ ТРЕНДА\nМожно работать импульсы"
    elif imoex == "DOWN" and stages["DOWN"] > stages["UP"]:
        mode = "🔴 РЕЖИМ ЗАЩИТЫ\nРиск повышен, осторожно"
    else:
        mode = "🟡 РЕЖИМ ФЛЭТА\nРабота от уровней"

    msg = (
        "🇷🇺 ОБЗОР МОЕХ — СЕГОДНЯ\n\n"
        f"IMOEX: {imoex}\n\n"
        "📊 Стадии рынка:\n"
        f"🟢 Накопление: {stages['ACCUM']}\n"
        f"📈 Импульс вверх: {stages['UP']}\n"
        f"📉 Импульс вниз: {stages['DOWN']}\n\n"
        "🔥 ТОП СИЛА:\n" +
        "\n".join([f"{i+1}) {s[0]} ({s[1]}/5)" for i, s in enumerate(strengths)]) +
        f"\n\n🧠 {mode}"
    )

    send(msg)
    last_daily_report_date = today

# ===== START =====
send("🇷🇺 МОЕХ-РАДАР\nДневной обзор «Где рынок» активирован")

while True:
    try:
        send_daily_report()
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
