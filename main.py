import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300
LOOKBACK_BARS = 20
VOLUME_CONFIRM = 1.5
INTERVAL_MIN = 60

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
        return r["candles"]["data"][-LOOKBACK_BARS:]
    except:
        return []

def get_price(t):
    try:
        r = requests.get(f"{MOEX}/{t}.json", timeout=10).json()
        md = r["marketdata"]
        return float(md["data"][0][md["columns"].index("LAST")])
    except:
        return None

# ---------- STRENGTH ----------
def calc_strength(vol_ratio, breakout_pct, repeat):
    s = 1
    if breakout_pct > 0.7: s += 2
    elif breakout_pct > 0.3: s += 1

    if vol_ratio >= 1.5: s += 1
    if vol_ratio >= 2.5: s += 1

    if repeat: s += 1

    return max(1, min(5, s))

# ---------- LOGIC ----------
def check(t):
    candles = get_candles(t)
    if len(candles) < LOOKBACK_BARS:
        return

    highs = [c[2] for c in candles]
    lows  = [c[3] for c in candles]
    vols  = [c[5] for c in candles]

    hi = max(highs)
    lo = min(lows)

    avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
    curr_vol = vols[-1]
    vol_ratio = curr_vol / avg_vol if avg_vol else 0

    price = get_price(t)
    if not price:
        return

    s = state.setdefault(t, {
        "status": "INSIDE",
        "level": None
    })

    # ---------- FIRST BREAK ----------
    if price > hi and s["status"] == "INSIDE":
        s["status"] = "FIRST_UP"
        s["level"] = hi
        return

    # ---------- REPEAT BREAK ----------
    if price > hi and s["status"] == "FIRST_UP" and vol_ratio >= VOLUME_CONFIRM:
        breakout_pct = (price - hi) / hi * 100
        strength = calc_strength(vol_ratio, breakout_pct, repeat=True)

        send(
            f"🔥 ПОВТОРНЫЙ ПРОБОЙ ВВЕРХ (ОБЪЁМ)\n"
            f"{t}\n"
            f"Цена: {round(price,2)}\n"
            f"Уровень: {round(hi,2)}\n\n"
            f"Объём: {round(vol_ratio,2)}× среднего\n"
            f"Сила пробоя: {'🔥'*strength} ({strength}/5)\n\n"
            f"🧠 Вывод: ПРИОРИТЕТНЫЙ СИГНАЛ"
        )
        s["status"] = "CONFIRMED"

    # ---------- RESET ----------
    if lo <= price <= hi:
        s["status"] = "INSIDE"
        s["level"] = None

# ---------- START ----------
send("🇷🇺 МОЕХ-РАДАР\nПовторный пробой с объёмом активирован")

while True:
    try:
        for t in TICKERS:
            check(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
