import requests
import time
import os
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== НАСТРОЙКИ =====
CHECK_INTERVAL = 300  # 5 минут
LOOKBACK_BARS = 20    # диапазон накопления
TIMEFRAME = "1h"

TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN",
    "NVTK", "TATN", "MTSS", "ALRS", "CHMF",
    "MAGN", "PLZL"
]

MOEX_API = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

# ===== ПАМЯТЬ СОСТОЯНИЙ =====
last_state = {}

# ===== TELEGRAM =====
def send(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    requests.post(url, data=payload, timeout=10)

# ===== ДАННЫЕ МОЕХ =====
def get_candles(ticker):
    url = f"{MOEX_API}/{ticker}/candles.json"
    params = {
        "interval": 60,
        "from": (datetime.utcnow()).strftime("%Y-%m-%d"),
    }
    r = requests.get(url, params=params, timeout=10).json()
    candles = r["candles"]["data"]
    return candles[-LOOKBACK_BARS:]

def get_last_price(ticker):
    url = f"{MOEX_API}/{ticker}.json"
    r = requests.get(url, timeout=10).json()
    return r["marketdata"]["data"][0][12]

# ===== ОСНОВНАЯ ЛОГИКА =====
def check_ticker(ticker):
    candles = get_candles(ticker)
    if len(candles) < LOOKBACK_BARS:
        return

    lows = [c[3] for c in candles]
    highs = [c[2] for c in candles]

    low_range = round(min(lows), 2)
    high_range = round(max(highs), 2)

    price = round(get_last_price(ticker), 2)

    state = last_state.get(ticker, "INSIDE")

    if price > high_range and state != "BREAK_UP":
        send(
            f"🚀 ПРОБОЙ ВВЕРХ\n"
            f"{ticker}\n"
            f"Цена: {price}\n"
            f"Диапазон: {low_range} – {high_range}\n\n"
            f"🧠 Выход вверх — начало движения"
        )
        last_state[ticker] = "BREAK_UP"

    elif price < low_range and state != "BREAK_DOWN":
        send(
            f"📉 ПРОБОЙ ВНИЗ\n"
            f"{ticker}\n"
            f"Цена: {price}\n"
            f"Диапазон: {low_range} – {high_range}\n\n"
            f"🧠 Выход вниз — усиление давления"
        )
        last_state[ticker] = "BREAK_DOWN"

    elif low_range <= price <= high_range:
        last_state[ticker] = "INSIDE"

# ===== СТАРТ =====
send("🇷🇺 МОЕХ-РАДАР АКТИВЕН\nАлерты при выходе из диапазона включены.")

while True:
    try:
        for ticker in TICKERS:
            check_ticker(ticker)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ MOEX BOT ERROR: {e}")
        time.sleep(60)
