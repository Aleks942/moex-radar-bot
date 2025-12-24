import os
import time
import json
import requests
from datetime import datetime, timedelta
from statistics import mean

print("=== MOEX RADAR (AGG + SAFE + CONFIRM + STATS + REPORTS) ===", flush=True)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Время МСК (UTC+3)
MSK_OFFSET_HOURS = 3

# =========================
# SETTINGS
# =========================
CHECK_INTERVAL_SEC = 60 * 5   # 5 минут
LOOKBACK_H1_BARS = 24         # 24 часа для H1 логики
EMA_PERIOD = 20

# Антиспам
COOLDOWN_MIN = 90             # минимум минут между сигналами на тикер

# Сигнальная логика
# AGGRESSIVE: ранний выход из диапазона + объём
AGG_VOL_MULT_MIN = 1.5
AGG_BREAK_PCT_MIN = 0.35      # % выхода за диапазон (H1)

# SAFE: подтверждение на D1 + индекс не против
SAFE_MIN_STRENGTH = 4
CONFIRM_WINDOW_HOURS = 48     # окно подтверждения AGG -> SAFE (для акций шире)

# Перегрев
OVERHEAT_D1_PCT = 8.0         # дневной % за последние 5 D1 баров

# Отчёты
DAILY_REPORT_HOUR = 19        # 19:00 МСК
DAILY_REPORT_MINUTE = 0

WEEKLY_REPORT_WEEKDAY = 0     # Понедельник
WEEKLY_REPORT_HOUR = 10       # 10:00 МСК
WEEKLY_REPORT_MINUTE = 0

# STATE (поддержка /data на Railway)
STATE_DIR = os.getenv("STATE_DIR", ".")
STATE_FILE = os.path.join(STATE_DIR, "moex_radar_state.json")

# =========================
# TICKERS
# =========================
BASE_TICKERS = [
    "SBER", "GAZP", "LKOH", "ROSN", "GMKN",
    "NVTK", "TATN", "MTSS", "ALRS", "CHMF",
    "MAGN", "PLZL"
]
PRIORITY_TICKERS = [
    "YNDX", "OZON", "AFKS", "SMLT", "PIKK",
    "MOEX", "RUAL", "FLOT", "POLY", "SBERP"
]
ALL_TICKERS = list(dict.fromkeys(BASE_TICKERS + PRIORITY_TICKERS))

# Индекс фильтр
INDEX_TICKER = "IMOEX"

# MOEX ISS
MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/securities"

# =========================
# TELEGRAM
# =========================
def send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15
        )
    except:
        pass

# =========================
# TIME
# =========================
def msk_now():
    return datetime.utcnow() + timedelta(hours=MSK_OFFSET_HOURS)

def should_fire_at(now_dt, hour, minute):
    return now_dt.hour == hour and now_dt.minute == minute

# =========================
# STATE
# =========================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(state: dict):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except:
        pass

# =========================
# DATA (candles)
# =========================
def get_candles(ticker: str, interval_min: int, days: int):
    """
    Возвращает candles.data
    Для /candles.json:
    interval=60 (H1), 24? (D1 в minutes не всегда), поэтому используем:
    - H1: interval=60
    - D1: interval=24 (в ISS это 24 = 1 day) — так работает в candles
    - W1: interval=7 (неделя)
    """
    try:
        r = requests.get(
            f"{MOEX}/{ticker}/candles.json",
            params={
                "interval": interval_min,
                "from": (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            },
            timeout=20
        ).json()
        return r.get("candles", {}).get("data", [])
    except:
        return []

def last_close(candles):
    if not candles:
        return None
    # candles columns in MOEX: begin, open, close, high, low, value, volume, end (varies)
    # В твоём коде close был c[1], но это риск.
    # Берём по индексу 2 (close) — в ISS обычно open=1, close=2, high=3, low=4.
    try:
        return float(candles[-1][2])
    except:
        # fallback
        try:
            return float(candles[-1][1])
        except:
            return None

def extract_hlc(candles, n):
    """Возвращает списки high/low/close последних n баров."""
    tail = candles[-n:] if len(candles) >= n else candles
    highs, lows, closes, vols = [], [], [], []
    for c in tail:
        try:
            # предполагаем: open=1 close=2 high=3 low=4 volume=6 (часто так)
            close = float(c[2])
            high = float(c[3])
            low = float(c[4])
            vol = float(c[6]) if len(c) > 6 and c[6] is not None else 0.0
        except:
            continue
        highs.append(high)
        lows.append(low)
        closes.append(close)
        vols.append(vol)
    return highs, lows, closes, vols

def ema_simple(values, period):
    if len(values) < period:
        return None
    return mean(values[-period:])

def pct(a, b):
    if a is None or b is None or b == 0:
        return 0.0
    return (a - b) / b * 100.0

# =========================
# MARKET FILTER (INDEX)
# =========================
def index_trend():
    # D1 = interval 24, W1 = interval 7
    d1 = get_candles(INDEX_TICKER, 24, 200)
    w1 = get_candles(INDEX_TICKER, 7, 800)

    _, _, d1_closes, _ = extract_hlc(d1, 40)
    _, _, w1_closes, _ = extract_hlc(w1, 40)

    d1_ema = ema_simple(d1_closes, EMA_PERIOD)
    w1_ema = ema_simple(w1_closes, EMA_PERIOD)

    d1_last = d1_closes[-1] if d1_closes else None
    w1_last = w1_closes[-1] if w1_closes else None

    score = 0
    if d1_ema and d1_last:
        if d1_last > d1_ema * 1.01:
            score += 1
        elif d1_last < d1_ema * 0.99:
            score -= 1

    if w1_ema and w1_last:
        if w1_last > w1_ema * 1.01:
            score += 1
        elif w1_last < w1_ema * 0.99:
            score -= 1

    if score >= 2:
        return "UP"
    if score <= -2:
        return "DOWN"
    return "FLAT"

def market_mode_text(tr):
    if tr == "UP":
        return "🟢 РЫНОК СИЛЬНЫЙ (IMOEX UP)"
    if tr == "DOWN":
        return "🔴 РЫНОК СЛАБЫЙ (IMOEX DOWN)"
    return "🟡 РЫНОК НЕЙТРАЛЬНЫЙ (IMOEX FLAT)"

# =========================
# STAGES + SIGNALS
# =========================
def stage_and_signal(ticker: str, idx_trend: str):
    """
    Возвращает:
    stage, direction, strength(1-5), vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, is_overheat
    """
    # H1 candles
    h1 = get_candles(ticker, 60, 15)    # 15 дней H1
    if len(h1) < LOOKBACK_H1_BARS:
        return None

    highs, lows, closes, vols = extract_hlc(h1, LOOKBACK_H1_BARS)
    if len(closes) < LOOKBACK_H1_BARS:
        return None

    price = closes[-1]
    hi = max(highs) if highs else price
    lo = min(lows) if lows else price
    rng = (hi - lo) / price * 100.0 if price else 0.0

    # H1 change (примерно 1 час назад)
    h1_prev = closes[-2] if len(closes) >= 2 else price
    h1_chg = pct(price, h1_prev)

    # D1 candles
    d1 = get_candles(ticker, 24, 400)
    _, _, d1_closes, d1_vols = extract_hlc(d1, 30)

    d1_last = d1_closes[-1] if d1_closes else None
    d1_prev = d1_closes[-2] if len(d1_closes) >= 2 else d1_last
    d1_chg = pct(d1_last, d1_prev)

    # Перегрев по D1 за 5 баров
    is_overheat = False
    if len(d1_closes) >= 6:
        d1_5 = pct(d1_closes[-1], d1_closes[-6])
        if abs(d1_5) >= OVERHEAT_D1_PCT:
            is_overheat = True

    # Объём: сравнение последнего H1 объёма с средним H1 объёмом
    vol_now = vols[-1] if vols else 0.0
    vol_avg = mean(vols[:-1]) if len(vols) > 5 else (mean(vols) if vols else 0.0)
    vol_mult = (vol_now / vol_avg) if vol_avg and vol_avg > 0 else 0.0

    # Направление — по H1
    direction = "UP" if h1_chg >= 0 else "DOWN"

    # стадия
    stage = "ACCUM"
    reasons = []
    strength = 0

    # пробой диапазона (грубая оценка)
    break_up = price > hi * (1 + AGG_BREAK_PCT_MIN / 100.0)
    break_dn = price < lo * (1 - AGG_BREAK_PCT_MIN / 100.0)

    if break_up:
        stage = "IMPULSE_UP"
        reasons.append("Выход вверх из диапазона H1")
        strength += 1
    elif break_dn:
        stage = "IMPULSE_DOWN"
        reasons.append("Выход вниз из диапазона H1")
        strength += 1
    else:
        # если диапазон узкий и объём растёт — накопление
        if rng <= 2.0 and vol_mult >= 1.3:
            stage = "ACCUM"
            reasons.append("Сжатие диапазона + рост объёма")
            strength += 1

    # объём как сила
    if vol_mult >= 1.5:
        strength += 1
        reasons.append(f"Объём x{vol_mult:.2f}")
    if vol_mult >= 2.2:
        strength += 1
    if vol_mult >= 3.0:
        strength += 1

    # согласие H1 и D1
    if d1_chg * h1_chg > 0 and abs(d1_chg) > 0.2:
        strength += 1
        reasons.append("H1 + D1 в одну сторону")

    # индекс-фильтр
    if idx_trend == "UP" and direction == "UP":
        strength += 1
        reasons.append("IMOEX поддерживает вверх")
    elif idx_trend == "DOWN" and direction == "DOWN":
        strength += 1
        reasons.append("IMOEX поддерживает вниз")
    elif idx_trend == "DOWN" and direction == "UP":
        reasons.append("IMOEX против направления")
    elif idx_trend == "UP" and direction == "DOWN":
        reasons.append("IMOEX против направления")

    # приоритет
    if ticker in PRIORITY_TICKERS:
        strength += 1
        reasons.append("Приоритетная бумага")

    # перегрев
    if is_overheat:
        reasons.append("Перегрев по D1")
        # перегрев не добавляет силу, а режет сигнал
        stage = "OVERHEAT"

    # нормализация силы
    strength = max(1, min(strength, 5))

    # AGG
    is_agg = (vol_mult >= AGG_VOL_MULT_MIN and stage in ("IMPULSE_UP", "IMPULSE_DOWN") and not is_overheat)

    # SAFE (строже): нужен импульс + согласие D1 + индекс не против + сила
    idx_ok = (idx_trend == "FLAT") or (idx_trend == "UP" and direction == "UP") or (idx_trend == "DOWN" and direction == "DOWN")
    tf_ok = (d1_chg * h1_chg > 0) and (abs(d1_chg) > 0.2)

    is_safe = (is_agg and tf_ok and idx_ok and strength >= SAFE_MIN_STRENGTH)

    return stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, is_overheat

def stage_emoji(stage):
    if stage.startswith("IMPULSE"):
        return "🟡"
    if stage == "OVERHEAT":
        return "🔴"
    return "🟢"

def memo_intraday():
    return (
        "🕒 <b>Чек</b>\n"
        "1) вход только после паузы/ретеста\n"
        "2) стоп за локальный экстремум\n"
        "⛔ если нет структуры — SKIP"
    )

# =========================
# MAIN
# =========================
def run():
    state = load_state()
    coins_state = state.get("coins", {})
    stats = state.get("stats", {})

    now = msk_now()
    day_key = now.strftime("%Y-%m-%d")
    week_key = now.strftime("%G-%V")

    if not stats:
        stats = {
            "day": day_key, "agg": 0, "safe": 0, "confirmed": 0,
            "week": week_key, "w_agg": 0, "w_safe": 0, "w_confirmed": 0
        }

    # стартовое сообщение 1 раз в сутки
    if state.get("start_day") != day_key:
        send("🇷🇺 <b>MOEX-радар активен</b>\nАкции РФ • H1 + D1 • AGG + SAFE • подтверждение • статистика")
        state["start_day"] = day_key
        save_state({**state, "coins": coins_state, "stats": stats})

    while True:
        try:
            now = msk_now()
            day_key = now.strftime("%Y-%m-%d")
            week_key = now.strftime("%G-%V")

            # rollover day/week
            if stats.get("day") != day_key:
                stats["day"] = day_key
                stats["agg"] = 0
                stats["safe"] = 0
                stats["confirmed"] = 0

            if stats.get("week") != week_key:
                stats["week"] = week_key
                stats["w_agg"] = 0
                stats["w_safe"] = 0
                stats["w_confirmed"] = 0

            idx_tr = index_trend()
            mode_text = market_mode_text(idx_tr)

            # ===== DAILY REPORT (19:00 MSK) =====
            if should_fire_at(now, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE) and state.get("last_daily_day") != day_key:
                agg = stats.get("agg", 0)
                safe = stats.get("safe", 0)
                conf = stats.get("confirmed", 0)
                rate = (conf / agg * 100.0) if agg > 0 else 0.0

                quality = "🟡 НЕЙТРАЛЬНОЕ"
                if agg >= 6 and rate >= 25:
                    quality = "🟢 ХОРОШЕЕ"
                elif agg >= 6 and rate < 12:
                    quality = "🔴 ШУМНОЕ"

                send(
                    "🇷🇺 <b>ОБЗОР МОЕХ — СЕГОДНЯ</b>\n\n"
                    f"🧠 Режим рынка:\n{mode_text}\n\n"
                    f"AGGRESSIVE: {agg}\n"
                    f"SAFE: {safe}\n"
                    f"Подтверждений: {conf}\n"
                    f"Качество: <b>{quality}</b>\n"
                )
                state["last_daily_day"] = day_key

            # ===== WEEKLY REPORT =====
            if (now.weekday() == WEEKLY_REPORT_WEEKDAY and
                should_fire_at(now, WEEKLY_REPORT_HOUR, WEEKLY_REPORT_MINUTE) and
                state.get("last_weekly_week") != week_key):

                send(
                    "🇷🇺 <b>НЕДЕЛЬНЫЙ ОБЗОР МОЕХ</b>\n\n"
                    f"{mode_text}\n\n"
                    f"AGGRESSIVE: {stats.get('w_agg', 0)}\n"
                    f"SAFE: {stats.get('w_safe', 0)}\n"
                    f"Подтверждений: {stats.get('w_confirmed', 0)}\n"
                )
                state["last_weekly_week"] = week_key

            # ===== RADAR LOOP =====
            now_ts = datetime.utcnow().timestamp()

            for t in ALL_TICKERS:
                cs = coins_state.get(t, {})
                last_sent_ts = cs.get("last_sent_ts", 0)
                if last_sent_ts and (now_ts - last_sent_ts) < (COOLDOWN_MIN * 60):
                    continue

                pack = stage_and_signal(t, idx_tr)
                if pack is None:
                    continue

                stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, is_overheat = pack

                if not is_agg and not is_safe:
                    continue

                sig_type = "SAFE" if is_safe else "AGG"

                # анти-дубликат
                if cs.get("last_type") == sig_type and cs.get("last_stage") == stage and cs.get("last_strength") == strength:
                    continue

                # подтверждение AGG -> SAFE
                confirmed = False
                confirmed_tag = ""
                if sig_type == "SAFE":
                    last_agg_ts = cs.get("last_agg_ts", 0)
                    last_agg_dir = cs.get("last_agg_dir")
                    if last_agg_ts and (now_ts - last_agg_ts) <= (CONFIRM_WINDOW_HOURS * 3600) and last_agg_dir == direction:
                        confirmed = True
                        confirmed_tag = "\n<b>AGGRESSIVE → SAFE подтверждён</b>"

                fire = "🔥" * strength
                emoji = stage_emoji(stage)

                if sig_type == "AGG":
                    title = "⚠️ <b>AGGRESSIVE</b> — ранний радар"
                    conclusion = "🔴 <b>НЕ ВХОД</b>\n(наблюдать и ждать структуру)"
                else:
                    title = f"✅ <b>SAFE</b>{confirmed_tag}"
                    conclusion = "🟢 <b>МОЖНО ПЛАНИРОВАТЬ</b>\n(вход только по структуре)"

                msg = (
                    f"{title}\n"
                    f"{emoji} <b>{t}</b>\n"
                    f"Стадия: <b>{stage}</b>\n"
                    f"Сила: {fire} ({strength}/5)\n\n"
                    f"H1: {h1_chg:.2f}% | D1: {d1_chg:.2f}%\n"
                    f"Объём: x{vol_mult:.2f}\n\n"
                    f"Причины:\n• " + "\n• ".join(reasons[:8]) +
                    f"\n\n{memo_intraday()}\n\n"
                    f"🧠 <b>ВЫВОД</b>:\n{conclusion}"
                )

                send(msg)

                # update coin state
                cs["last_sent_ts"] = now_ts
                cs["last_type"] = sig_type
                cs["last_stage"] = stage
                cs["last_strength"] = strength

                if sig_type == "AGG":
                    cs["last_agg_ts"] = now_ts
                    cs["last_agg_dir"] = direction

                coins_state[t] = cs

                # stats
                if sig_type == "AGG":
                    stats["agg"] = stats.get("agg", 0) + 1
                    stats["w_agg"] = stats.get("w_agg", 0) + 1
                else:
                    stats["safe"] = stats.get("safe", 0) + 1
                    stats["w_safe"] = stats.get("w_safe", 0) + 1
                    if confirmed:
                        stats["confirmed"] = stats.get("confirmed", 0) + 1
                        stats["w_confirmed"] = stats.get("w_confirmed", 0) + 1

            # save
            state["coins"] = coins_state
            state["stats"] = stats
            save_state(state)

        except Exception as e:
            send(f"❌ <b>BOT ERROR</b>: {e}")

        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    run()
