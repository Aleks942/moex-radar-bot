import requests
import time
import os
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MOEX_TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN",
    "NVTK", "TATN", "MTSS", "ALRS", "CHMF",
    "MAGN", "PLZL"
]

STATE_FILE = "state.json"

STAGE_UP = "📈 ИМПУЛЬС ВВЕРХ"
STAGE_DOWN = "📉 ИМПУЛЬС ВНИЗ"
STAGE_FLAT = "⏸ ФЛЕТ"

def send(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": msg
    })

def get_price(ticker):
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
    r = requests.get(url, timeout=10).json()

    marketdata = r.get("marketdata", {}).get("data", [])
    if not marketdata:
        return None

    last = marketdata[0][12]  # LAST price
    return last

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    import json
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    import json
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def detect_stage(prev, curr):
    if curr > prev * 1.01:
        return STAGE_UP
    if curr < prev * 0.99:
        return STAGE_DOWN
    return STAGE_FLAT

def main():
    send("🇷🇺 МОЕХ-РАДАР ЗАПУЩЕН\nСигналы только при смене стадии.")

    state = load_state()
    first_run = not state

    while True:
        try:
            for ticker in MOEX_TICKERS:
                price = get_price(ticker)
                if price is None:
                    continue

                prev_price = state.get(ticker, {}).get("price")
                prev_stage = state.get(ticker, {}).get("stage")

                if prev_price is None:
                    state[ticker] = {
                        "price": price,
                        "stage": STAGE_FLAT
                    }
                    continue

                stage = detect_stage(prev_price, price)

                if stage != prev_stage:
                    change = round((price - prev_price) / prev_price * 100, 2)

                    msg = (
                        f"{ticker}\n"
                        f"Цена: {price}\n"
                        f"Изм: {change}%\n"
                        f"Стадия: {stage}\n\n"
                        f"🧠 Вывод: {'СМОТРЕТЬ' if stage != STAGE_FLAT else 'ОЖИДАТЬ'}"
                    )
                    send(msg)

                state[ticker] = {
                    "price": price,
                    "stage": stage
                }

            save_state(state)
            time.sleep(300)

        except Exception as e:
            send(f"❌ MOEX BOT ERROR: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
