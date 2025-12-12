import os
import time
import requests
from datetime import datetime

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

CHECK_INTERVAL = 60 * 15  # 15 минут
STATE_FILE = "moex_state.txt"

# ===== НАСТРОЙКИ =====
IMOEX_SYMBOL = "IMOEX"
STOCKS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN", "NVTK", "TATN", "MTSS",
    "ALRS", "CHMF", "MAGN", "PLZL", "POLY", "SNGS", "VTBR",
    "YNDX", "OZON", "FIVE", "MOEX", "RUAL",
    "AFLT", "IRAO", "PIKK", "PHOR", "RTKM",
    "TRNFP", "BSPB", "CBOM", "SBERP", "UPRO",
    "RASP", "ENPG", "LSRG", "FEES", "AKRN",
    "NMTP", "HYDR", "MTLR", "TCSG", "POSI"
]

MOEX_API = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities"

# ===== TELEGRAM =====
def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

# ===== МОЕХ ДАННЫЕ =====
def get_price(symbol):
    url = f"{MOEX_API}/{symbol}.json"
    r = requests.get(url, timeout=10).json()
    market = r["marketdata"]["data"][0]
    last = market[12]
    open_p = market[7]
    return last, open_p

def stage_from_change(change):
    if change >= 2:
        return "📈 ИМПУЛЬС ВВЕРХ", 5
    if change >= 1:
        return "⬆️ РОСТ", 4
    if change > -1:
        return "⏸ ФЛЕТ", 2
    if change <= -2:
        return "📉 ИМПУЛЬС ВНИЗ", 5
    return "⬇️ ПАДЕНИЕ", 4

# ===== СОСТОЯНИЕ =====
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        lines = f.read().splitlines()
    return dict(line.split("|") for line in lines if "|" in line)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        for k, v in state.items():
            f.write(f"{k}|{v}\n")

# ===== ОСНОВНОЙ ЦИКЛ =====
def run():
    send_telegram("🇷🇺 <b>МОЕХ-РАДАР ЗАПУЩЕН</b>\nСигналы только при смене стадии.")
    state = load_state()

    while True:
        try:
            for symbol in STOCKS:
                price, open_p = get_price(symbol)
                if not price or not open_p:
                    continue

                change = round((price - open_p) / open_p * 100, 2)
                stage, power = stage_from_change(change)

                prev = state.get(symbol)
                now = stage

                if prev != now:
                    state[symbol] = now
                    send_telegram(
                        f"<b>{symbol}</b>\n"
                        f"Цена: {price}\n"
                        f"Изм: {change}%\n"
                        f"Стадия: <b>{stage}</b>\n"
                        f"Сила: {power}/5\n\n"
                        f"🧠 Вывод: {'СМОТРЕТЬ' if power >= 4 else 'НЕ ВХОДИТЬ'}"
                    )

            save_state(state)

        except Exception as e:
            send_telegram(f"❌ MOEX BOT ERROR: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()

