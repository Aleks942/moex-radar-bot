import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300
LOOKBACK_BARS = 20
ATR_PERIOD = 14
INTERVAL_H1 = 60
INTERVAL_D1 = 1440
VOLUME_CONFIRM = 1.5
RETEST_TOLERANCE = 0.003

TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"
state = {}

# ---------- TELEGRAM ----------
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# ---------- DATA ----------
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

# ---------- ATR ----------
def calc_atr(candles):
    trs = []
    for i in range(1, len(candles)):
        high = candles[i][2]
        low = candles[i][3]
        prev_close = candles[i-1][1]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)
    if len(trs) < ATR_PERIOD:
        return None
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

# ---------- D1 FILTER ----------
def get_d1_trend(ticker):
    candles = get_candles(ticker, INTERVAL_D1, 120)
    if len(candles) < 60:
        return "FLAT"

    closes = [c[1] for c in candles[-50:]]
    ema50 = sum(closes) / len(closes)
    price = closes[-1]

    if price > ema50 * 1.005:
        return "UP"
    if price < ema50 * 0.995:
        return "DOWN"
    return "FLAT"

# ---------- LOGIC ----------
def check(ticker):
    h1 = get_candles(ticker, INTERVAL_H1, 7)
    if len(h1) < max(LOOKBACK_BARS, ATR_PERIOD+1):
        return

    recent = h1[-LOOKBACK_BARS:]
    highs = [c[2] for c in recent]
    lows  = [c[3] for c in recent]
    vols  = [c[5] for c in recent]

    hi = max(highs)
    lo = min(lows)

    avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    curr_vol = vols[-1]
    vol_ratio = curr_vol / avg_vol if avg_vol else 0

    price = get_price(ticker)
    if not price:
        return

    atr = calc_atr(h1)
    if not atr:
        return

    d1 = get_d1_trend(ticker)
    s = state.setdefault(ticker, {"status": "INSIDE", "level": None})

    # ----- ПРОБОЙ ВВЕРХ -----
    if price > hi and s["status"] == "INSIDE":
        s["status"] = "BROKE_UP"
        s["level"] = hi
        return

    # ----- РЕТЕСТ -----
    if s["status"] == "BROKE_UP":
        level = s["level"]

        if abs(price - level) / level <= RETEST_TOLERANCE:
            if vol_ratio >= VOLUME_CONFIRM and price >= level:
                strength = 4
                if d1 == "UP": strength += 1
                if d1 == "DOWN": strength -= 1
                strength = max(1, min(5, strength))

                tp1 = round(price + atr, 2)
                tp2 = round(price + atr * 2, 2)
                tp3 = round(price + atr * 3, 2)

                send(
                    f"✅ РЕТЕСТ УРОВНЯ УДЕРЖАН\n"
                    f"{ticker}\n"
                    f"Цена: {round(price,2)}\n\n"
                    f"D1: {d1}\n"
                    f"ATR(1H): {round(atr,2)}\n\n"
                    f"🎯 Цели:\nTP1: {tp1}\nTP2: {tp2}\nTP3: {tp3}\n\n"
                    f"Сила сценария: {'🔥'*strength} ({strength}/5)\n\n"
                    f"🧠 Вывод: {'Приоритетный сценарий' if strength>=4 else 'Наблюдать'}"
                )
                s["status"] = "CONFIRMED"

        if price < level * (1 - RETEST_TOLERANCE):
            s["status"] = "INSIDE"
            s["level"] = None

# ---------- START ----------
send("🇷🇺 МОЕХ-РАДАР\nD1-фильтр включён (мягкий режим)")

while True:
    try:
        for t in TICKERS:
            check(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
