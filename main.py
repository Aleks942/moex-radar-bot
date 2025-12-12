import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== НАСТРОЙКИ =====
CHECK_INTERVAL = 300          # 5 минут
LOOKBACK_BARS = 20            # свечей для диапазона и среднего объёма
VOLUME_MULT = 1.5             # порог подтверждения объёмом
INTERVAL_MIN = 60             # 1h свечи

TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN",
    "NVTK", "TATN", "MTSS", "ALRS", "CHMF",
    "MAGN", "PLZL"
]

MOEX_BASE = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

# ===== ПАМЯТЬ СОСТОЯНИЙ (анти-спам) =====
# INSIDE / BREAK_UP / BREAK_DOWN
last_state = {}

# ===== TELEGRAM =====
def send(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except:
        pass

# ===== MOEX: СВЕЧИ =====
def get_candles(ticker):
    try:
        url = f"{MOEX_BASE}/{ticker}/candles.json"
        params = {
            "interval": INTERVAL_MIN,
            "from": (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        }
        r = requests.get(url, params=params, timeout=10).json()
        candles = r.get("candles", {}).get("data", [])
        # Формат: [open, close, high, low, value, volume, begin, end]
        return candles[-LOOKBACK_BARS:]
    except:
        return []

# ===== MOEX: ТЕКУЩАЯ ЦЕНА =====
def get_last_price(ticker):
    try:
        url = f"{MOEX_BASE}/{ticker}.json"
        r = requests.get(url, timeout=10).json()
        md = r.get("marketdata", {})
        data = md.get("data", [])
        cols = md.get("columns", [])
        if not data or "LAST" not in cols:
            return None
        p = data[0][cols.index("LAST")]
        return float(p) if p else None
    except:
        return None

# ===== ОСНОВНАЯ ЛОГИКА =====
def check_ticker(ticker):
    candles = get_candles(ticker)
    if len(candles) < LOOKBACK_BARS:
        return

    # Индексы полей в свечах MOEX:
    # open=0, close=1, high=2, low=3, value=4, volume=5
    highs = [c[2] for c in candles if c[2] is not None]
    lows  = [c[3] for c in candles if c[3] is not None]
    vols  = [c[5] for c in candles if c[5] is not None]

    if not highs or not lows or not vols:
        return

    low_range = round(min(lows), 2)
    high_range = round(max(highs), 2)

    avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))  # средний объём без текущей
    curr_vol = vols[-1]

    price = get_last_price(ticker)
    if price is None:
        return

    state = last_state.get(ticker, "INSIDE")

    # ===== ПРОБОЙ ВВЕРХ =====
    if price > high_range and state != "BREAK_UP":
        ratio = curr_vol / avg_vol if avg_vol > 0 else 0
        if ratio >= VOLUME_MULT:
            send(
                f"🚀 ПРОБОЙ ВВЕРХ (ПОДТВЕРЖДЁН ОБЪЁМОМ)\n"
                f"{ticker}\n"
                f"Цена: {round(price,2)}\n"
                f"Диапазон: {low_range} – {high_range}\n"
                f"Объём: {round(ratio,2)}× среднего\n\n"
                f"🧠 Деньги вошли — движение имеет шанс"
            )
        else:
            send(
                f"⚠️ ВЫХОД ВВЕРХ БЕЗ ОБЪЁМА\n"
                f"{ticker}\n"
                f"Цена: {round(price,2)}\n"
                f"Диапазон: {low_range} – {high_range}\n"
                f"Объём: {round(ratio,2)}× среднего\n\n"
                f"🧠 Возможен ложный пробой"
            )
        last_state[ticker] = "BREAK_UP"

    # ===== ПРОБОЙ ВНИЗ =====
    elif price < low_range and state != "BREAK_DOWN":
        ratio = curr_vol / avg_vol if avg_vol > 0 else 0
        if ratio >= VOLUME_MULT:
            send(
                f"📉 ПРОБОЙ ВНИЗ (ПОДТВЕРЖДЁН ОБЪЁМОМ)\n"
                f"{ticker}\n"
                f"Цена: {round(price,2)}\n"
                f"Диапазон: {low_range} – {high_range}\n"
                f"Объём: {round(ratio,2)}× среднего\n\n"
                f"🧠 Усиление давления продавца"
            )
        else:
            send(
                f"⚠️ ВЫХОД ВНИЗ БЕЗ ОБЪЁМА\n"
                f"{ticker}\n"
                f"Цена: {round(price,2)}\n"
                f"Диапазон: {low_range} – {high_range}\n"
                f"Объём: {round(ratio,2)}× среднего\n\n"
                f"🧠 Возможен ложный выход"
            )
        last_state[ticker] = "BREAK_DOWN"

    # ===== ВНУТРИ ДИАПАЗОНА =====
    elif low_range <= price <= high_range:
        last_state[ticker] = "INSIDE"

# ===== СТАРТ =====
send("🇷🇺 МОЕХ-РАДАР АКТИВЕН\nАлерты при выходе из диапазона с подтверждением объёмом включены.")

while True:
    try:
        for t in TICKERS:
            check_ticker(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ MOEX BOT ERROR: {e}")
        time.sleep(60)
