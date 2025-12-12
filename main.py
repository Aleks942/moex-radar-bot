import os
import time
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300  # 5 минут
STATE_FILE = "state.json"

TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN",
    "NVTK", "TATN", "MTSS", "ALRS", "CHMF",
    "MAGN", "PLZL"
]

STAGE_UP = "📈 ИМПУЛЬС ВВЕРХ"
STAGE_DOWN = "📉 ИМПУЛЬС ВНИЗ"
STAGE_FLAT = "⏸ ФЛЕТ"


# ---------- TELEGRAM ----------
def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except:
        pass


# ---------- STATE ----------
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ---------- MOEX PRICE ----------
def get_price(ticker):
    try:
        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
        r = requests.get(url, timeout=10).json()

        marketdata = r.get("marketdata", {})
        data = marketdata.get("data", [])
        columns = marketdata.get("columns", [])

        if not data or not columns:
            return None

        if "LAST" not in columns:
            return None

        idx = columns.index("LAST")
        price = data[0][idx]

        if price is None:
            return None

        return float(price)

    except:
        return None


# ---------- LOGIC ----------
def detect_stage(prev_price, current_price):
    if current_price >= prev_price * 1.01:
        return STAGE_UP
    if current_price <= prev_price * 0.99:
        return STAGE_DOWN
    return STAGE_FLAT


def logical_view(stage):
    if stage == STAGE_FLAT:
        return "ОЖИДАТЬ"
    return "СМОТРЕТЬ"


# ---------- MAIN ----------
def main():
    send_telegram(
        "🇷🇺 МОЕХ-РАДАР ЗАПУЩЕН\n"
        "Сигналы только при смене стадии."
    )

    state = load_state()

    while True:
        try:
            for ticker in TICKERS:
                price = get_price(ticker)
                if price is None:
                    continue

                prev = state.get(ticker)

                # первый запуск — просто сохраняем цену
                if prev is None:
                    state[ticker] = {
                        "price": price,
                        "stage": STAGE_FLAT
                    }
                    continue

                prev_price = prev["price"]
                prev_stage = prev["stage"]

                stage = detect_stage(prev_price, price)

                if stage != prev_stage:
                    change = round((price - prev_price) / prev_price * 100, 2)

                    msg = (
                        f"{ticker}\n"
                        f"Цена: {price}\n"
                        f"Изм: {change}%\n"
                        f"Стадия: {stage}\n\n"
                        f"🧠 Вывод: {logical_view(stage)}"
                    )

                    send_telegram(msg)

                state[ticker] = {
                    "price": price,
                    "stage": stage
                }

            save_state(state)
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            send_telegram(f"❌ MOEX BOT ERROR: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
