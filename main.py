import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300
LOOKBACK_BARS = 20
VOLUME_MULT_BASE = 1.0
INTERVAL_MIN = 60

TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"
last_state = {}

def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

def get_candles(ticker):
    try:
        r = requests.get(
            f"{MOEX}/{ticker}/candles.json",
            params={
                "interval": INTERVAL_MIN,
                "from": (datetime.utcnow()-timedelta(days=7)).strftime("%Y-%m-%d")
            },
            timeout=10
        ).json()
        return r["candles"]["data"][-LOOKBACK_BARS:]
    except:
        return []

def get_price(ticker):
    try:
        r = requests.get(f"{MOEX}/{ticker}.json", timeout=10).json()
        md = r["marketdata"]
        cols = md["columns"]
        data = md["data"]
        return float(data[0][cols.index("LAST")])
    except:
        return None

def get_imoex_dir():
    price = get_price("IMOEX")
    return "FLAT" if price else "FLAT"

def calc_strength(vol_ratio, breakout_pct, imoex):
    strength = 1

    # объём
    if vol_ratio >= 1.5: strength += 1
    if vol_ratio >= 2.5: strength += 1
    if vol_ratio >= 3.5: strength += 1

    # выход
    if breakout_pct >= 0.3: strength += 1
    if breakout_pct >= 0.7: strength += 1

    # рынок
    if imoex == "UP": strength += 1
    if imoex == "DOWN": strength -= 1

    return max(1, min(5, strength))

def check(t):
    candles = get_candles(t)
    if len(candles) < LOOKBACK_BARS:
        return

    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    vols = [c[5] for c in candles]

    hi = max(highs)
    lo = min(lows)
    avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    curr_vol = vols[-1]

    price = get_price(t)
    if not price:
        return

    state = last_state.get(t, "INSIDE")
    imoex = get_imoex_dir()

    if price > hi and state != "UP":
        vol_ratio = curr_vol / avg_vol if avg_vol else 0
        breakout_pct = (price - hi) / hi * 100
        strength = calc_strength(vol_ratio, breakout_pct, imoex)

        send(
            f"🚀 ПРОБОЙ ВВЕРХ\n{t}\n"
            f"Цена: {round(price,2)}\n"
            f"Диапазон: {round(lo,2)} – {round(hi,2)}\n\n"
            f"Объём: {round(vol_ratio,2)}×\n"
            f"Выход: +{round(breakout_pct,2)}%\n"
            f"IMOEX: {imoex}\n\n"
            f"Сила пробоя: {'🔥'*strength} ({strength}/5)"
        )
        last_state[t] = "UP"

    elif price < lo and state != "DOWN":
        vol_ratio = curr_vol / avg_vol if avg_vol else 0
        breakout_pct = (lo - price) / lo * 100
        strength = calc_strength(vol_ratio, breakout_pct, imoex)

        send(
            f"📉 ПРОБОЙ ВНИЗ\n{t}\n"
            f"Цена: {round(price,2)}\n"
            f"Диапазон: {round(lo,2)} – {round(hi,2)}\n\n"
            f"Объём: {round(vol_ratio,2)}×\n"
            f"Выход: -{round(breakout_pct,2)}%\n"
            f"IMOEX: {imoex}\n\n"
            f"Сила пробоя: {'🔥'*strength} ({strength}/5)"
        )
        last_state[t] = "DOWN"

    elif lo <= price <= hi:
        last_state[t] = "INSIDE"

send("🇷🇺 МОЕХ-РАДАР\nСила пробоя (1–5) активирована")

while True:
    try:
        for t in TICKERS:
            check(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
