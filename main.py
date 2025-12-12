import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300
LOOKBACK_BARS = 20
ATR_PERIOD = 14
INTERVAL_MIN = 60
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
def get_candles(t):
    try:
        r = requests.get(
            f"{MOEX}/{t}/candles.json",
            params={
                "interval": INTERVAL_MIN,
                "from": (datetime.utcnow()-timedelta(days=7)).strftime("%Y-%m-%d")
            },
            timeout=10
        ).json()
        return r["candles"]["data"][-max(LOOKBACK_BARS, ATR_PERIOD+1):]
    except:
        return []

def get_price(t):
    try:
        r = requests.get(f"{MOEX}/{t}.json", timeout=10).json()
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

# ---------- LOGIC ----------
def check(t):
    candles = get_candles(t)
    if len(candles) < ATR_PERIOD + 1:
        return

    highs = [c[2] for c in candles[-LOOKBACK_BARS:]]
    lows  = [c[3] for c in candles[-LOOKBACK_BARS:]]
    vols  = [c[5] for c in candles[-LOOKBACK_BARS:]]

    hi = max(highs)
    lo = min(lows)

    avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    curr_vol = vols[-1]
    vol_ratio = curr_vol / avg_vol if avg_vol else 0

    price = get_price(t)
    if not price:
        return

    atr = calc_atr(candles)
    if not atr:
        return

    s = state.setdefault(t, {"status": "INSIDE", "level": None})

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
                tp1 = round(price + atr, 2)
                tp2 = round(price + atr * 2, 2)
                tp3 = round(price + atr * 3, 2)

                send(
                    f"✅ РЕТЕСТ УРОВНЯ УДЕРЖАН\n"
                    f"{t}\n"
                    f"Цена: {round(price,2)}\n"
                    f"Уровень: {round(level,2)}\n\n"
                    f"ATR(1H): {round(atr,2)}\n\n"
                    f"🎯 Цели:\n"
                    f"TP1: {tp1}\n"
                    f"TP2: {tp2}\n"
                    f"TP3: {tp3}\n\n"
                    f"Сила сценария: 🔥🔥🔥🔥🔥 (5/5)\n\n"
                    f"🧠 Вывод: Подтверждённый импульс"
                )
                s["status"] = "CONFIRMED"

        if price < level * (1 - RETEST_TOLERANCE):
            s["status"] = "INSIDE"
            s["level"] = None

# ---------- START ----------
send("🇷🇺 МОЕХ-РАДАР\nATR-цели после ретеста активированы")

while True:
    try:
        for t in TICKERS:
            check(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
