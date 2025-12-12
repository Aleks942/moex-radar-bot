import os
import time
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300  # 5 минут
STATE_FILE = "state.json"
STATS_FILE = "stats.json"

TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN",
    "NVTK", "TATN", "MTSS", "ALRS", "CHMF",
    "MAGN", "PLZL"
]

IMOEX = "IMOEX"

# ---------- TELEGRAM ----------
def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except:
        pass

# ---------- FILES ----------
def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

# ---------- MOEX ----------
def get_price(ticker):
    try:
        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
        r = requests.get(url, timeout=10).json()
        marketdata = r.get("marketdata", {})
        data = marketdata.get("data", [])
        columns = marketdata.get("columns", [])
        if not data or "LAST" not in columns:
            return None
        price = data[0][columns.index("LAST")]
        return float(price) if price else None
    except:
        return None

# ---------- STAGES ----------
STAGE_ACCUM = "🟢 НАКОПЛЕНИЕ"
STAGE_UP = "🟡 ИМПУЛЬС ВВЕРХ"
STAGE_DOWN = "🔴 ИМПУЛЬС ВНИЗ"
STAGE_FLAT = "⚪ ФЛЕТ"

def detect_stage(prev, curr, history):
    if len(history) >= 3:
        spread = max(history) - min(history)
        if spread / curr < 0.006:
            return STAGE_ACCUM
    if curr >= prev * 1.01:
        return STAGE_UP
    if curr <= prev * 0.99:
        return STAGE_DOWN
    return STAGE_FLAT

# ---------- STRENGTH ----------
def calc_strength(prev, curr, stage, imoex_trend):
    strength = 1
    move = abs(curr - prev) / prev * 100

    if move > 1:
        strength += 1
    if move > 2:
        strength += 1

    if stage in [STAGE_UP, STAGE_DOWN]:
        strength += 1

    if imoex_trend == "FLAT":
        strength += 1
    if imoex_trend == "DOWN" and stage == STAGE_UP:
        strength += 1
    if imoex_trend == "DOWN":
        strength -= 1

    return max(1, min(strength, 5))

# ---------- IMOEX ----------
def get_imoex_trend(state):
    price = get_price(IMOEX)
    prev = state.get("IMOEX")
    if price is None or prev is None:
        state["IMOEX"] = price
        return "FLAT"
    change = (price - prev) / prev * 100
    state["IMOEX"] = price
    if change > 0.3:
        return "UP"
    if change < -0.3:
        return "DOWN"
    return "FLAT"

# ---------- STATS ----------
def update_stats(stats, ticker, stage):
    s = stats.setdefault(ticker, {"signals": 0})
    if stage in [STAGE_UP, STAGE_DOWN]:
        s["signals"] += 1

# ---------- MAIN ----------
def main():
    send_telegram("🇷🇺 МОЕХ-РАДАР ЗАПУЩЕН\nЭтап 2 активен: сила • IMOEX • накопление • статистика")

    state = load_json(STATE_FILE)
    stats = load_json(STATS_FILE)

    history = {}

    while True:
        try:
            imoex_trend = get_imoex_trend(state)

            for ticker in TICKERS:
                price = get_price(ticker)
                if price is None:
                    continue

                hist = history.setdefault(ticker, [])
                hist.append(price)
                if len(hist) > 5:
                    hist.pop(0)

                prev = state.get(ticker, {}).get("price")
                prev_stage = state.get(ticker, {}).get("stage")

                if prev is None:
                    state[ticker] = {"price": price, "stage": STAGE_FLAT}
                    continue

                stage = detect_stage(prev, price, hist)

                if stage != prev_stage:
                    strength = calc_strength(prev, price, stage, imoex_trend)
                    update_stats(stats, ticker, stage)

                    change = round((price - prev) / prev * 100, 2)
                    fire = "🔥" * strength

                    msg = (
                        f"{ticker}\n"
                        f"Цена: {price}\n"
                        f"Изм: {change}%\n\n"
                        f"Стадия: {stage}\n"
                        f"Сила: {fire} ({strength}/5)\n"
                        f"IMOEX: {imoex_trend}\n\n"
                        f"📊 Статистика:\n"
                        f"Сигналов: {stats.get(ticker, {}).get('signals', 0)}\n\n"
                        f"🧠 Вывод: {'ВХОД ВОЗМОЖЕН' if strength >= 4 and stage == STAGE_UP else 'НАБЛЮДАТЬ'}"
                    )

                    send_telegram(msg)

                state[ticker] = {"price": price, "stage": stage}

            save_json(STATE_FILE, state)
            save_json(STATS_FILE, stats)
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            send_telegram(f"❌ MOEX BOT ERROR: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
