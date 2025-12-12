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
D1_LOOKBACK = 10

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
        h = candles[i][2]
        l = candles[i][3]
        pc = candles[i-1][1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < ATR_PERIOD:
        return None
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

# ---------- D1 LEVELS ----------
def get_d1_levels(ticker):
    d1 = get_candles(ticker, INTERVAL_D1, 120)
    if len(d1) < D1_LOOKBACK:
        return None, None
    recent = d1[-D1_LOOKBACK:]
    highs = [c[2] for c in recent]
    lows  = [c[3] for c in recent]
    return round(min(lows),2), round(max(highs),2)

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
    vol_ratio = vols[-1] / avg_vol if avg_vol else 0

    price = get_price(ticker)
    if not price:
        return

    atr = calc_atr(h1)
    if not atr:
        return

    d1_sup, d1_res = get_d1_levels(ticker)
    strength_adj = 0
    level_note = ""

    if d1_sup and abs(price - d1_sup)/price < 0.01:
        strength_adj += 1
        level_note = f"D1 поддержка: {d1_sup}"
    elif d1_res and abs(d1_res - price)/price < 0.01:
        strength_adj -= 1
        level_note = f"D1 сопротивление: {d1_res}"
    else:
        level_note = f"D1 диапазон: {d1_sup} – {d1_res}"

    s = state.setdefault(ticker, {"status": "INSIDE", "level": None})

    if price > hi and s["status"] == "INSIDE":
        s["status"] = "BROKE_UP"
        s["level"] = hi
        return

    if s["status"] == "BROKE_UP":
        level = s["level"]
        if abs(price - level)/level <= RETEST_TOLERANCE and vol_ratio >= VOLUME_CONFIRM:
            base_strength = 4
            strength = max(1, min(5, base_strength + strength_adj))

            tp1 = round(price + atr,2)
            tp2 = round(price + atr*2,2)
            tp3 = round(price + atr*3,2)

            send(
                f"✅ РЕТЕСТ УРОВНЯ УДЕРЖАН\n"
                f"{ticker}\n"
                f"Цена: {round(price,2)}\n\n"
                f"{level_note}\n"
                f"ATR(1H): {round(atr,2)}\n\n"
                f"🎯 Цели:\nTP1: {tp1}\nTP2: {tp2}\nTP3: {tp3}\n\n"
                f"Сила сценария: {'🔥'*strength} ({strength}/5)\n\n"
                f"🧠 Вывод: {'Приоритетный сценарий' if strength>=4 else 'Наблюдать'}"
            )
            s["status"] = "CONFIRMED"

# ---------- START ----------
send("🇷🇺 МОЕХ-РАДАР\nD1-уровни добавлены")

while True:
    try:
        for t in TICKERS:
            check(t)
        time.sleep(CHECK_INTERVAL)
    except Exception as e:
        send(f"❌ ERROR: {e}")
        time.sleep(60)
