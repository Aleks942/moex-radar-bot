import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== НАСТРОЙКИ =====
CHECK_INTERVAL = 300
WEEKLY_REPORT_WEEKDAY = 0   # 0 = понедельник
WEEKLY_REPORT_HOUR = 10     # 10:00 МСК

INTERVAL_D1 = 1440
INTERVAL_W1 = 10080

TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"
last_weekly_report = None

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

# ===== W1 TREND =====
def get_w1_trend(ticker):
    candles = get_candles(ticker, INTERVAL_W1, 400)
    if len(candles) < 20:
        return "FLAT", None, None

    closes = [c[1] for c in candles[-20:]]
    ema20 = sum(closes) / len(closes)
    price = closes[-1]

    highs = [c[2] for c in candles[-12:]]
    lows  = [c[3] for c in candles[-12:]]

    w1_high = round(max(highs), 2)
    w1_low  = round(min(lows), 2)

    if price > ema20 * 1.01:
        return "UP", w1_low, w1_high
    if price < ema20 * 0.99:
        return "DOWN", w1_low, w1_high
    return "FLAT", w1_low, w1_high

# ===== WEEKLY REPORT =====
def send_weekly_report():
    global last_weekly_report

    now = datetime.utcnow() + timedelta(hours=3)  # МСК
    today = now.date()

    if (
        last_weekly_report == today or
        now.weekday() != WEEKLY_REPORT_WEEKDAY or
        now.hour != WEEKLY_REPORT_HOUR
    ):
        return

    # IMOEX
    imoex_trend, imoex_low, imoex_high = get_w1_trend("IMOEX")

    counts = {"UP": 0, "DOWN": 0, "FLAT": 0}
    focus = []

    for t in TICKERS:
        trend, low, high = get_w1_trend(t)
        counts[trend] += 1

        if trend == "UP" and low:
            focus.append(f"{t} — у поддержки {low}")
        if trend == "DOWN" and high:
            focus.append(f"{t} — под сопротивлением {high}")

    focus = focus[:3]

    if imoex_trend == "UP":
        mode = "🟢 РЕЖИМ РОСТА\nПриоритет — лонги по тренду"
    elif imoex_trend == "DOWN":
        mode = "🔴 РЕЖИМ ДАВЛЕНИЯ\nОсторожно, защита капитала"
    else:
        mode = "🟡 ШИРОКИЙ ФЛЭТ\nРабота от уровней"

    msg = (
        "🇷🇺 НЕДЕЛЬНЫЙ ОБЗОР МОЕХ (W1)\n\n"
        f"IMOEX:\n"
        f"Тренд: {imoex_trend}\n"
        f"W1 диапазон: {imoex_low} – {imoex_high}\n\n"
        "📊 Акции (W1):\n"
        f"📈 UP: {counts['UP']}\n"
        f"📉 DOWN: {counts['DOWN']}\n"
        f"➖ FLAT: {counts['FLAT']}\n\n"
        "🔥 В ФОКУСЕ НЕДЕЛИ:\n" +
        ("\n".join(focus) if focus else "Нет явных точек") +
        f"\n\n🧠 {mode}"
    )

    send(msg)
    last_weekly_report = today

# ===== START =====
send("🇷🇺 МОЕХ-РАДАР\nНедельный обзор W1 активирован")

while True:
    try:
        send_weekly_report()
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
