import os
import time
import requests
from datetime import datetime, timedelta

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300
LOOKBACK_BARS = 20
INTERVAL_MIN = 60
VOLUME_CONFIRM = 1.5
RETEST_TOLERANCE = 0.003  # 0.3%

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

    s = state.setdefault(t, {"status": "INSIDE", "level": None})

    # ----- FIRST BREAK UP -----
    if price > hi and s["status"] == "INSIDE":
        s["status"] = "BROKE_UP"
        s["level"] = hi
        return

    # ----- RETEST -----
    if s["status"] == "BROKE_UP":
        level = s["level"]

        # цена вернулась к уровню
        if abs(price - level) / level <= RETEST_TOLERANCE:
            # удержание + объём
            if vol_ratio >= VOLUME_CONFIRM and price >= level:
                send(
                    f"✅ РЕТЕСТ УРОВНЯ УДЕРЖАН\n"
                    f"{t}\n"
                    f"Цена: {round(price,2)}\n"
                    f"Уровень: {round(level,2)}\n\n"
                    f"Объём: {round(vol_ratio,2)}× среднего\n"
                    f"Сила сценария: 🔥🔥🔥🔥🔥 (5/5)\n\n"
                    f"🧠 Вывод: Уровень принят рынком"
                )
                s["status"] = "CONFIRMED"

        # провал — сброс
        if price < level * (1 - RETEST_TOLERANCE):
            s["status"] = "INSIDE"
            s["level"] = None

    # ----- RESET -----
    if lo <= price <= hi and s["status"] == "CONFIRMED":
        s["status"] = "INSIDE"
        s["level"] = None

# ---------- START ----------
send("🇷🇺 МОЕХ-РАДАР\nРетест уровня после пробоя активирован")

while True:
    try:
        for t in TICKERS:
            check(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
