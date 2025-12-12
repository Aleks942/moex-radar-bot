import os
import time
import json
import requests
from datetime import datetime

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_INTERVAL = 300
STATE_FILE = "state.json"
STATS_FILE = "stats.json"
LAST_OVERVIEW_FILE = "last_overview.json"

TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN","NVTK",
    "TATN","MTSS","ALRS","CHMF","MAGN","PLZL"
]

IMOEX = "IMOEX"

STAGE_ACCUM = "🟢 НАКОПЛЕНИЕ"
STAGE_UP = "🟡 ИМПУЛЬС ВВЕРХ"
STAGE_DOWN = "🔴 ИМПУЛЬС ВНИЗ"
STAGE_FLAT = "⚪ ФЛЕТ"


# ---------------- TELEGRAM ----------------
def send(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except:
        pass


# ---------------- FILES ----------------
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


# ---------------- MOEX ----------------
def get_price(ticker):
    try:
        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
        r = requests.get(url, timeout=10).json()
        md = r.get("marketdata", {})
        data = md.get("data", [])
        cols = md.get("columns", [])
        if not data or "LAST" not in cols:
            return None
        price = data[0][cols.index("LAST")]
        return float(price) if price else None
    except:
        return None


# ---------------- LOGIC ----------------
def detect_stage(prev, curr, history):
    if len(history) >= 4:
        spread = max(history) - min(history)
        if spread / curr < 0.006:
            return STAGE_ACCUM
    if curr >= prev * 1.01:
        return STAGE_UP
    if curr <= prev * 0.99:
        return STAGE_DOWN
    return STAGE_FLAT


def imoex_trend(state):
    price = get_price(IMOEX)
    prev = state.get("IMOEX")
    state["IMOEX"] = price
    if not price or not prev:
        return "FLAT"
    ch = (price - prev) / prev * 100
    if ch > 0.3:
        return "UP"
    if ch < -0.3:
        return "DOWN"
    return "FLAT"


def relative_strength(stock_ch, imoex_ch):
    if imoex_ch == 0:
        return "НА УРОВНЕ РЫНКА"
    if stock_ch > imoex_ch:
        return "ВЫШЕ РЫНКА"
    if stock_ch < imoex_ch:
        return "СЛАБЕЕ РЫНКА"
    return "НА УРОВНЕ РЫНКА"


def calc_strength(move, stage, imoex_dir, rel):
    s = 1
    if move > 1: s += 1
    if move > 2: s += 1
    if stage in [STAGE_UP, STAGE_DOWN]: s += 1
    if rel == "ВЫШЕ РЫНКА": s += 1
    if imoex_dir == "DOWN": s -= 1
    return max(1, min(5, s))


# ---------------- OVERVIEW ----------------
def send_overview(state, stats):
    now = datetime.now()
    last = load_json(LAST_OVERVIEW_FILE)

    key = f"{now.date()}_{'AM' if now.hour < 15 else 'PM'}"
    if last.get("sent") == key:
        return

    up = down = accum = 0
    ranked = []

    for t, s in state.items():
        if t == "IMOEX": continue
        stage = s.get("stage")
        strength = stats.get(t, {}).get("last_strength", 0)
        if stage == STAGE_UP: up += 1
        elif stage == STAGE_DOWN: down += 1
        elif stage == STAGE_ACCUM: accum += 1
        ranked.append((t, strength))

    ranked = sorted(ranked, key=lambda x: x[1], reverse=True)[:3]

    msg = (
        f"🇷🇺 ОБЗОР МОЕХ ({'УТРО' if now.hour < 15 else 'ВЕЧЕР'})\n\n"
        f"📈 Импульс вверх: {up}\n"
        f"🟢 Накопление: {accum}\n"
        f"📉 Импульс вниз: {down}\n\n"
        f"🔥 ТОП СИЛА:\n" +
        "\n".join([f"{i+1}) {t} ({s}/5)" for i,(t,s) in enumerate(ranked)])
    )

    send(msg)
    save_json(LAST_OVERVIEW_FILE, {"sent": key})


# ---------------- MAIN ----------------
def main():
    send("🇷🇺 МОЕХ-РАДАР ЗАПУЩЕН\nЭтап 3: усиленные сигналы + обзор рынка")

    state = load_json(STATE_FILE)
    stats = load_json(STATS_FILE)
    history = {}

    while True:
        try:
            imoex_dir = imoex_trend(state)

            for t in TICKERS:
                price = get_price(t)
                if not price:
                    continue

                h = history.setdefault(t, [])
                h.append(price)
                if len(h) > 5: h.pop(0)

                prev = state.get(t, {}).get("price")
                prev_stage = state.get(t, {}).get("stage")

                if not prev:
                    state[t] = {"price": price, "stage": STAGE_FLAT}
                    continue

                stage = detect_stage(prev, price, h)
                move = abs(price - prev) / prev * 100
                rel = "НА УРОВНЕ РЫНКА"

                strength = calc_strength(move, stage, imoex_dir, rel)
                stats.setdefault(t, {})["last_strength"] = strength

                send_signal = False
                if stage == STAGE_ACCUM:
                    send_signal = True
                elif strength >= 3 and (imoex_dir != "DOWN" or strength >= 4):
                    send_signal = True

                if stage != prev_stage and send_signal:
                    msg = (
                        f"{t}\n"
                        f"Цена: {price}\n"
                        f"Изм: {round((price-prev)/prev*100,2)}%\n\n"
                        f"Стадия: {stage}\n"
                        f"Сила: {'🔥'*strength} ({strength}/5)\n"
                        f"IMOEX: {imoex_dir}\n\n"
                        f"🧠 Вывод: {'ПРИОРИТЕТ' if strength>=4 else 'НАБЛЮДАТЬ'}"
                    )
                    send(msg)

                state[t] = {"price": price, "stage": stage}

            save_json(STATE_FILE, state)
            save_json(STATS_FILE, stats)
            send_overview(state, stats)
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            send(f"❌ MOEX BOT ERROR: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
