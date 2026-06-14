import os
import time
import json
import requests
from datetime import datetime, timedelta, timezone
from statistics import mean
from open_interest import get_open_interest_signal   # 🔹 импорт OI (как у тебя)

print("=== MOEX RADAR (FAST + AGG + SAFE + CONFIRM + FLOW PRO + STATS + REPORTS) ===", flush=True)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# MSK = UTC+3
MSK_OFFSET_HOURS = 3

# =========================
# SETTINGS
# =========================
CHECK_INTERVAL_SEC = 60 * 5

LOOKBACK_H1_BARS = 24
EMA_PERIOD = 20

COOLDOWN_MIN = 90  # общий анти-спам для AGG/SAFE

AGG_VOL_MULT_MIN = 1.5
AGG_BREAK_PCT_MIN = 0.35

SAFE_MIN_STRENGTH = 4

# =========================
# PULLBACK
# =========================
PULLBACK_RETRACE_MIN = 30
PULLBACK_RETRACE_MAX = 60

PULLBACK_VOL_MAX = 0.80

PULLBACK_COOLDOWN_MIN = 180

CONFIRM_WINDOW_HOURS = 48

OVERHEAT_D1_PCT = 8.0

DAILY_REPORT_HOUR = 19
DAILY_REPORT_MINUTE = 0

WEEKLY_REPORT_WEEKDAY = 0
WEEKLY_REPORT_HOUR = 10
WEEKLY_REPORT_MINUTE = 0

# --- FAST (интрадей M15) — ДОБАВЛЕНО, но ничего старого не трогаем
FAST_INTERVAL_MIN = 10
FAST_DAYS = 7
FAST_LOOKBACK_BARS = 30        # флет-окно ≈ 5 часов (30 * 10m)
FAST_BREAK_BARS = 18           # "последние 3 часа" (18 свечей по 10m)
FAST_RANGE_MAX_PCT = 2.5       # диапазон флета ≤ 2.5%
FAST_MOVE_MIN_PCT = 0.6        # импульс одной 10m свечи ≥ 0.6%
FAST_VOL_MULT_MIN = 1.3        # объём ≥ x1.3
FAST_COOLDOWN_MIN = 120        # анти-спам FAST на тикер (2 часа)

# =========================
# FLOW PRO (M5) — НОВЫЙ СЛОЙ, ПОВЕРХ
# =========================
FLOW_INTERVAL_MIN = 10
FLOW_DAYS = 10
FLOW_LOOKBACK_BARS = 30        # окно для средней ≈ 5 часов (30 * 10m)
FLOW_TREND_BARS = 3            # 3 свечи в одну сторону
FLOW_BREAK_BARS = 12           # локальный уровень ≈ 2 часа (12 * 10m)

FLOW_PUBLISH_SCORE_MIN = 8     # проф. порог публикации
FLOW_PUBLISH_DELTA_MIN = 3     # публикуем если скачок score >= 3
FLOW_COOLDOWN_SEC = 60 * 20    # анти-спам на FLOW (если надо, но мы итак шлём только по изменениям)

EVENING_START_HOUR = 19        # MSK
EVENING_THIN_VOL_RATIO = 0.60  # "тонкий рынок" если vol_now < 60% от локальной средней
EVENING_SCORE_PENALTY = 2      # штраф score в вечерке

STATE_DIR = os.getenv("STATE_DIR", ".")
STATE_FILE = os.path.join(STATE_DIR, "moex_radar_state.json")

# =========================
# TICKERS
# =========================
BASE_TICKERS = [
    "SBER","GAZP","LKOH","ROSN","GMKN",
    "NVTK","TATN","MTSS","ALRS","CHMF",
    "MAGN","PLZL"
]

PRIORITY_TICKERS = [
    "YNDX","OZON","AFKS","SMLT","PIKK",
    "MOEX","RUAL","FLOT","POLY","SBERP"
]

ALL_TICKERS = list(dict.fromkeys(BASE_TICKERS + PRIORITY_TICKERS))
INDEX_TICKER = "IMOEX"

# =========================
# MARKET REGIME TICKERS
# =========================
BR_TICKER = "BR"
SI_TICKER = "Si"

# =========================
# SECTORS (для синхронности/перетока)
# можно расширять — это не ломает логику
# =========================
SECTOR_MAP = {
    # Банки / финансы
    "SBER": "BANKS",
    "SBERP": "BANKS",
    "VTBR": "BANKS",
    "MOEX": "FIN",

    # Нефть/газ
    "GAZP": "OILGAS",
    "LKOH": "OILGAS",
    "ROSN": "OILGAS",
    "NVTK": "OILGAS",
    "TATN": "OILGAS",
    "SNGS": "OILGAS",
    "SNGSP": "OILGAS",

    # Металлы/майнинг
    "GMKN": "METALS",
    "CHMF": "METALS",
    "MAGN": "METALS",

    "RUAL": "METALS",
    "ALRS": "METALS",
    "PLZL": "METALS",
    "POLY": "METALS",

    # Телеком
    "MTSS": "TELCO",

    # Девелоперы
    "PIKK": "DEV",
    "SMLT": "DEV",

    # Тех/ритейл/прочее
    "YNDX": "TECH",
    "OZON": "RETAIL",
    "AFKS": "HOLD",
    "FLOT": "TRANSPORT",
}

def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker, "OTHER")

# =========================
# MOEX ISS
# =========================
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
    return datetime.now(timezone.utc) + timedelta(hours=MSK_OFFSET_HOURS)

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
# DATA (SAFE PARSE)
# =========================
def get_candles(ticker: str, interval: int, days: int):
    try:
        r = requests.get(
            f"{MOEX}/{ticker}/candles.json",
            params={
                "interval": interval,
                "from": (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            },
            timeout=20
        ).json()

        candles = r.get("candles", {})
        cols = candles.get("columns", [])
        data = candles.get("data", [])
        if not cols or not data:
            return [], []
        return cols, data
    except:
        return [], []

def col_idx(cols, name):
    try:
        return cols.index(name)
    except:
        return None

def extract_series(cols, data, n):
    if not cols or not data:
        return [], [], [], []

    tail = data[-n:] if len(data) >= n else data

    i_close = col_idx(cols, "close")
    i_high  = col_idx(cols, "high")
    i_low   = col_idx(cols, "low")
    i_vol   = col_idx(cols, "volume")

    highs, lows, closes, vols = [], [], [], []

    for row in tail:
        try:
            close = float(row[i_close]) if i_close is not None and i_close < len(row) and row[i_close] is not None else None
            high  = float(row[i_high])  if i_high  is not None and i_high  < len(row) and row[i_high]  is not None else None
            low   = float(row[i_low])   if i_low   is not None and i_low   < len(row) and row[i_low]   is not None else None
            vol   = float(row[i_vol])   if i_vol   is not None and i_vol   < len(row) and row[i_vol]   is not None else 0.0
        except:
            continue

        if close is None or high is None or low is None:
            continue

        closes.append(close)
        highs.append(high)
        lows.append(low)
        vols.append(vol)

    return highs, lows, closes, vols

def pct(a, b):
    if a is None or b is None or b == 0:
        return 0.0
    return (a - b) / b * 100.0

def ema_simple(values, period):
    if len(values) < period:
        return None
    return mean(values[-period:])

# =========================
# INDEX TREND (IMOEX)
# =========================
def index_trend():
    cols, data = get_candles(INDEX_TICKER, 24, 220)
    _, _, closes, _ = extract_series(cols, data, 60)
    if len(closes) < EMA_PERIOD:
        return "FLAT"

    ema = ema_simple(closes, EMA_PERIOD)
    last = closes[-1]
    if ema is None:
        return "FLAT"

    if last > ema * 1.01:
        return "UP"
    if last < ema * 0.99:
        return "DOWN"
    return "FLAT"

def market_mode_text(tr):
    if tr == "UP":
        return "🟢 РЫНОК СИЛЬНЫЙ (IMOEX UP)"
    if tr == "DOWN":
        return "🔴 РЫНОК СЛАБЫЙ (IMOEX DOWN)"
    return "🟡 РЫНОК НЕЙТРАЛЬНЫЙ (IMOEX FLAT)"

# =========================
# MARKET REGIME — BR + Si + IMOEX
# =========================
def get_last_change_pct(ticker: str, interval: int = 10, days: int = 5):
    cols, data = get_candles(ticker, interval, days)
    _, _, closes, _ = extract_series(cols, data, 5)

    if len(closes) < 2:
        return None

    return pct(closes[-1], closes[-2])


def detect_market_regime():

    print("[MARKET_REGIME_START]", flush=True)

    print("[BR_START]", flush=True)
    br_change = get_last_change_pct(BR_TICKER, 10, 5)
    print("[BR_END]", br_change, flush=True)

    print("[SI_START]", flush=True)
    si_change = get_last_change_pct(SI_TICKER, 10, 5)
    print("[SI_END]", si_change, flush=True)

    print("[IMOEX_START]", flush=True)
    imoex_change = get_last_change_pct(INDEX_TICKER, 10, 5)
    print("[IMOEX_END]", imoex_change, flush=True)

    score = 0
    reasons = []

    if br_change is not None and br_change > 0:
        score += 1
        reasons.append(f"BR растёт {br_change:.2f}%")
    elif br_change is not None:
        reasons.append(f"BR падает {br_change:.2f}%")
    else:
        reasons.append("BR недоступен")

    if si_change is not None and si_change < 0:
        score += 1
        reasons.append(f"Si падает {si_change:.2f}%")
    elif si_change is not None:
        reasons.append(f"Si растёт {si_change:.2f}%")
    else:
        reasons.append("Si недоступен")

    if imoex_change is not None and imoex_change > 0:
        score += 1
        reasons.append(f"IMOEX растёт {imoex_change:.2f}%")
    elif imoex_change is not None:
        reasons.append(f"IMOEX падает {imoex_change:.2f}%")
    else:
        reasons.append("IMOEX недоступен")

    if score == 3:
        regime = "LONG_REGIME"
    elif score == 2:
        regime = "SOFT_LONG"
    elif score == 1:
        regime = "MIXED"
    else:
        regime = "RISK_OFF"

    print("[MARKET_REGIME_END]", flush=True)

    return regime, score, reasons, br_change, si_change, imoex_change


def market_regime_text(regime: str, score: int, reasons: list):
    if regime == "LONG_REGIME":
        title = "🟢 LONG режим"
    elif regime == "SOFT_LONG":
        title = "🟡 Мягкий LONG режим"
    elif regime == "MIXED":
        title = "⚪ Смешанный рынок"
    else:
        title = "🔴 RISK OFF"

    return (
        f"{title} ({score}/3)\n"
        "Причины:\n• " + "\n• ".join(reasons)
    )

# =========================
# STAGES + SIGNALS (ТВОЯ ЛОГИКА — НЕ ТРОГАЮ)
# =========================
def stage_and_signal(ticker: str, idx_tr: str):
    cols_h1, data_h1 = get_candles(ticker, 60, 20)
    highs, lows, closes, vols = extract_series(cols_h1, data_h1, LOOKBACK_H1_BARS)
    if len(closes) < LOOKBACK_H1_BARS:
        return None

    price = closes[-1]

    # диапазон считаем БЕЗ текущей свечи, иначе пробой сам себя душит
    if len(highs) < 2 or len(lows) < 2:
        return None

    hi = max(highs[:-1])
    lo = min(lows[:-1])

    h1_prev = closes[-2] if len(closes) >= 2 else closes[-1]
    h1_chg = pct(price, h1_prev)
    direction = "UP" if h1_chg >= 0 else "DOWN"

    vol_now = vols[-1] if vols else 0.0
    vol_avg = mean(vols[:-1]) if len(vols) > 6 else (mean(vols) if vols else 0.0)
    vol_mult = (vol_now / vol_avg) if vol_avg and vol_avg > 0 else 0.0

    cols_d1, data_d1 = get_candles(ticker, 24, 450)
    _, _, d1_closes, _ = extract_series(cols_d1, data_d1, 60)
    d1_last = d1_closes[-1] if d1_closes else None
    d1_prev = d1_closes[-2] if len(d1_closes) >= 2 else d1_last
    d1_chg = pct(d1_last, d1_prev)

    is_overheat = False
    if len(d1_closes) >= 6:
        d1_5 = pct(d1_closes[-1], d1_closes[-6])
        if abs(d1_5) >= OVERHEAT_D1_PCT:
            is_overheat = True

    stage = "ACCUM"
    reasons = []
    strength = 0

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
        rng = (hi - lo) / price * 100.0 if price else 0.0
        if rng <= 2.0 and vol_mult >= 1.3:
            reasons.append("Сжатие диапазона + рост объёма")
            strength += 1

    if vol_mult >= 1.5:
        strength += 1
        reasons.append(f"Объём x{vol_mult:.2f}")
    if vol_mult >= 2.2:
        strength += 1
    if vol_mult >= 3.0:
        strength += 1

    if d1_chg * h1_chg > 0 and abs(d1_chg) > 0.2:
        strength += 1
        reasons.append("H1 + D1 в одну сторону")

    if idx_tr == "UP" and direction == "UP":
        strength += 1
        reasons.append("IMOEX поддерживает вверх")
    elif idx_tr == "DOWN" and direction == "DOWN":
        strength += 1
        reasons.append("IMOEX поддерживает вниз")
    elif idx_tr == "DOWN" and direction == "UP":
        reasons.append("IMOEX против направления")
    elif idx_tr == "UP" and direction == "DOWN":
        reasons.append("IMOEX против направления")

    if ticker in PRIORITY_TICKERS:
        strength += 1
        reasons.append("Приоритетная бумага")

    if is_overheat:
        stage = "OVERHEAT"
        reasons.append("Перегрев по D1")

    strength = max(1, min(strength, 5))

    is_agg = (vol_mult >= AGG_VOL_MULT_MIN and stage in ("IMPULSE_UP", "IMPULSE_DOWN") and not is_overheat)

    idx_ok = (idx_tr == "FLAT") or (idx_tr == "UP" and direction == "UP") or (idx_tr == "DOWN" and direction == "DOWN")
    tf_ok = (d1_chg * h1_chg > 0) and (abs(d1_chg) > 0.2)
    is_safe = (is_agg and tf_ok and idx_ok and strength >= SAFE_MIN_STRENGTH)

    return stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, is_overheat, price

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
# FAST (M10) — ДОБАВЛЕНО, НЕ ЛОМАЕТ AGG/SAFE
# =========================
def fast_signal_m15(ticker: str):
    cols, data = get_candles(ticker, FAST_INTERVAL_MIN, FAST_DAYS)
    highs, lows, closes, vols = extract_series(cols, data, FAST_LOOKBACK_BARS + FAST_BREAK_BARS + 5)
    if len(closes) < FAST_LOOKBACK_BARS + 2 or len(highs) < FAST_LOOKBACK_BARS + 2 or len(vols) < FAST_LOOKBACK_BARS + 2:
        return None

    price = closes[-1]
    prev = closes[-2]

    # 1) флет-диапазон
    hi = max(highs[-FAST_LOOKBACK_BARS:])
    lo = min(lows[-FAST_LOOKBACK_BARS:])
    rng = (hi - lo) / price * 100.0 if price else 0.0
    if rng > FAST_RANGE_MAX_PCT:
        return None

    # 2) импульс последней свечи
    move = pct(price, prev)
    if abs(move) < FAST_MOVE_MIN_PCT:
        return None

    # 3) объём
    vol_now = vols[-1]
    vol_avg = mean(vols[-FAST_LOOKBACK_BARS:-1]) if len(vols) >= FAST_LOOKBACK_BARS + 1 else 0.0
    vol_mult = (vol_now / vol_avg) if vol_avg and vol_avg > 0 else 0.0
    if vol_mult < FAST_VOL_MULT_MIN:
        return None

    # 4) пробой последних 3 часов
    if len(highs) < FAST_BREAK_BARS + 2:
        return None

    br_hi = max(highs[-FAST_BREAK_BARS-1:-1])
    br_lo = min(lows[-FAST_BREAK_BARS-1:-1])

    direction = None
    if price > br_hi:
        direction = "UP"
    elif price < br_lo:
        direction = "DOWN"
    else:
        return None

    reasons = [
        f"Флет M10: {rng:.2f}%",
        f"Импульс M10: {move:.2f}%",
        f"Объём x{vol_mult:.2f}",
        "Пробой диапазона 3ч"
    ]

    return direction, move, vol_mult, rng, reasons

# =========================
# PULLBACK ENGINE
# =========================
def detect_pullback(ticker: str):

    cols, data = get_candles(ticker, 60, 20)

    highs, lows, closes, vols = extract_series(cols, data, 50)

    if len(closes) < 20:
        return None

    swing_high = max(highs[-20:])
    swing_low = min(lows[-20:])

    impulse_size = swing_high - swing_low

    if impulse_size <= 0:
        return None

    current_price = closes[-1]

    retrace_pct = (
        (swing_high - current_price)
        / impulse_size
    ) * 100

    vol_now = vols[-1]
    vol_avg = mean(vols[-10:-1])

    vol_ratio = (
        vol_now / vol_avg
        if vol_avg > 0 else 1
    )

    if (
        PULLBACK_RETRACE_MIN <= retrace_pct <= PULLBACK_RETRACE_MAX
        and vol_ratio <= PULLBACK_VOL_MAX
    ):
        return (
            retrace_pct,
            vol_ratio
        )

    return None
# =========================
# FLOW PRO (M5) — НОВЫЙ СЛОЙ
# =========================
def flow_score_m5(ticker: str, idx_tr: str, now_dt: datetime):
    """
    FLOW PRO score 0-10:
      +2 vol > 1.8x
      +3 vol > 2.5x
      +2 3 свечи в одну сторону (close-close)
      +1 range_expand (last range > avg range)
      +1 breakout локального уровня (2 часа)
      +2 сектор синхронен (это в агрегаторе, не тут)
    Возвращает: (score, direction, vol_mult, move_last, reasons[])
    """
    cols, data = get_candles(ticker, FLOW_INTERVAL_MIN, FLOW_DAYS)
    highs, lows, closes, vols = extract_series(cols, data, FLOW_LOOKBACK_BARS + FLOW_BREAK_BARS + 5)
    if len(closes) < max(FLOW_LOOKBACK_BARS + 5, FLOW_BREAK_BARS + 5):
        return None

    price = closes[-1]
    prev = closes[-2]
    move_last = pct(price, prev)

    vol_now = vols[-1]
    vol_base = mean(vols[-FLOW_LOOKBACK_BARS:-1]) if len(vols) >= FLOW_LOOKBACK_BARS + 1 else (mean(vols[:-1]) if len(vols) > 3 else 0.0)
    vol_mult = (vol_now / vol_base) if vol_base and vol_base > 0 else 0.0

    # range expand
    last_range = highs[-1] - lows[-1]
    ranges = [(highs[i] - lows[i]) for i in range(max(0, len(highs) - FLOW_LOOKBACK_BARS), len(highs) - 1)]
    avg_range = mean(ranges) if ranges else 0.0
    range_expand = (avg_range > 0 and last_range > avg_range * 1.2)

    # 3-bar trend
    if len(closes) >= 4:
        c1, c2, c3 = closes[-1], closes[-2], closes[-3]
        up3 = (c1 > c2 > c3)
        dn3 = (c1 < c2 < c3)
    else:
        up3 = dn3 = False

    direction = "UP" if move_last >= 0 else "DOWN"
    if up3:
        direction = "UP"
    if dn3:
        direction = "DOWN"

    # breakout (локальный уровень за 2 часа)
    br_hi = max(highs[-FLOW_BREAK_BARS-1:-1])
    br_lo = min(lows[-FLOW_BREAK_BARS-1:-1])
    breakout = (price > br_hi) or (price < br_lo)

    score = 0
    reasons = []

    # volume scoring
    if vol_mult > 2.5:
        score += 3
        reasons.append(f"Объём x{vol_mult:.2f} (очень высокий)")
    elif vol_mult > 1.8:
        score += 2
        reasons.append(f"Объём x{vol_mult:.2f}")

    if up3 or dn3:
        score += 2
        reasons.append("3 свечи подряд в одну сторону")

    if range_expand:
        score += 1
        reasons.append("Расширение диапазона")

    if breakout:
        score += 1
        reasons.append("Пробой локального уровня (≈2ч)")

    # H1 контекст через IMOEX (проф. фильтр направления)
    # если IMOEX против — не запрещаем, но уменьшаем качество
    if idx_tr == "UP" and direction == "DOWN":
        reasons.append("IMOEX против движения")
    if idx_tr == "DOWN" and direction == "UP":
        reasons.append("IMOEX против движения")

    # вечерний тонкий рынок (штраф)
    if now_dt.hour >= EVENING_START_HOUR:
        # сравним текущий объём с локальной средней — если слабый, штраф
        local_avg = mean(vols[-12:-1]) if len(vols) >= 13 else vol_base  # ~55 минут
        ratio = (vol_now / local_avg) if local_avg and local_avg > 0 else 1.0
        if ratio < EVENING_THIN_VOL_RATIO:
            score = max(0, score - EVENING_SCORE_PENALTY)
            reasons.append(f"Вечерка тонкая (vol {ratio:.2f}×) → -{EVENING_SCORE_PENALTY}")

    # легкий бонус за приоритетные тикеры (как у тебя)
    if ticker in PRIORITY_TICKERS:
        score = min(10, score + 1)
        reasons.append("Приоритетная бумага (+1)")

    score = max(0, min(score, 10))
    return score, direction, vol_mult, move_last, reasons

def sector_name(sector: str) -> str:
    return {
        "BANKS": "Банки",
        "FIN": "Финансы",
        "OILGAS": "Нефть/Газ",
        "METALS": "Металлы",
        "TELCO": "Телеком",
        "DEV": "Девелоперы",
        "TECH": "Тех",
        "RETAIL": "Ритейл",
        "HOLD": "Холдинги",
        "TRANSPORT": "Транспорт",
        "OTHER": "Другое",
    }.get(sector, sector)

def flow_dir_emoji(d: str) -> str:
    return "📈" if d == "UP" else "📉"

def flow_score_emoji(score: int) -> str:
    if score >= 9:
        return "🔴"
    if score >= 8:
        return "🟢"
    if score >= 6:
        return "🟡"
    return "⚪"

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

    # --- ИНИЦИАЛИЗАЦИЯ СТАТЫ (добавил fast + flow, но старое не ломаю)
    if not stats:
        stats = {
            "day": day_key,
            "fast": 0,
            "agg": 0,
            "safe": 0,
            "confirmed": 0,
            "week": week_key,
            "w_fast": 0,
            "w_agg": 0,
            "w_safe": 0,
            "w_confirmed": 0,

            # FLOW PRO stats
            "flow": 0,
            "w_flow": 0,
            "flow_shift": 0,
            "w_flow_shift": 0,
            "market_woke": 0,
            "w_market_woke": 0,
        }
    else:
        # защита на случай старого state без новых полей
        stats.setdefault("fast", 0)
        stats.setdefault("w_fast", 0)

        stats.setdefault("flow", 0)
        stats.setdefault("w_flow", 0)
        stats.setdefault("flow_shift", 0)
        stats.setdefault("w_flow_shift", 0)
        stats.setdefault("market_woke", 0)
        stats.setdefault("w_market_woke", 0)

    # стартовое сообщение раз в сутки (как у тебя)
    if state.get("start_day") != day_key:
        send("🇷🇺 <b>MOEX-радар активен</b>\nАкции РФ • M10 + H1 + D1 • FAST + AGG + SAFE • FLOW PRO • подтверждение • статистика")
        state["start_day"] = day_key
        state["coins"] = coins_state
        state["stats"] = stats
        save_state(state)

    while True:
        try:
            print("[NEW_CYCLE]", flush=True)
            now = msk_now()
            day_key = now.strftime("%Y-%m-%d")
            week_key = now.strftime("%G-%V")

            # rollover day/week
            if stats.get("day") != day_key:
                stats["day"] = day_key
                stats["fast"] = 0
                stats["agg"] = 0
                stats["safe"] = 0
                stats["confirmed"] = 0

                stats["flow"] = 0
                stats["flow_shift"] = 0
                stats["market_woke"] = 0

            if stats.get("week") != week_key:
                stats["week"] = week_key
                stats["w_fast"] = 0
                stats["w_agg"] = 0
                stats["w_safe"] = 0
                stats["w_confirmed"] = 0

                stats["w_flow"] = 0
                stats["w_flow_shift"] = 0
                stats["w_market_woke"] = 0

            idx_tr = index_trend()
            mode_text = market_mode_text(idx_tr)
            
            # market_regime, regime_score, regime_reasons, br_chg, si_chg, imoex_chg = detect_market_regime()
            # regime_text = market_regime_text(market_regime, regime_score, regime_reasons)
                        

            # DAILY REPORT
            if should_fire_at(now, DAILY_REPORT_HOUR, DAILY_REPORT_MINUTE) and state.get("last_daily_day") != day_key:
                fast = stats.get("fast", 0)
                agg = stats.get("agg", 0)
                safe = stats.get("safe", 0)
                conf = stats.get("confirmed", 0)
                flow = stats.get("flow", 0)
                shift = stats.get("flow_shift", 0)
                woke = stats.get("market_woke", 0)

                rate = (conf / agg * 100.0) if agg > 0 else 0.0

                quality = "🟡 НЕЙТРАЛЬНОЕ"
                if agg >= 6 and rate >= 25:
                    quality = "🟢 ХОРОШЕЕ"
                elif agg >= 6 and rate < 12:
                    quality = "🔴 ШУМНОЕ"

                # OI BLOCK (как у тебя)
                try:
                    oi = get_open_interest_signal()
                    oi_text = f"\n{oi['text']}\n"
                except Exception as e:
                    oi_text = f"\n⚠️ Open Interest недоступен ({e})\n"

                send(
                    "🇷🇺 <b>ОБЗОР МОЕХ — СЕГОДНЯ</b>\n\n"
                    f"🧠 Режим рынка:\n{mode_text}\n"
                    f"{oi_text}\n"
                    f"FAST: {fast}\n"
                    f"AGGRESSIVE: {agg}\n"
                    f"SAFE: {safe}\n"
                    f"Подтверждений: {conf}\n"
                    f"FLOW PRO (score≥{FLOW_PUBLISH_SCORE_MIN}): {flow}\n"
                    f"Перетоков: {shift}\n"
                    f"Рынок проснулся: {woke}\n"
                    f"Качество: <b>{quality}</b>\n"
                )
                state["last_daily_day"] = day_key

            # WEEKLY REPORT
            if (now.weekday() == WEEKLY_REPORT_WEEKDAY and
                should_fire_at(now, WEEKLY_REPORT_HOUR, WEEKLY_REPORT_MINUTE) and
                state.get("last_weekly_week") != week_key):

                send(
                    "🇷🇺 <b>НЕДЕЛЬНЫЙ ОБЗОР МОЕХ</b>\n\n"
                    f"{mode_text}\n\n"
                    f"FAST: {stats.get('w_fast', 0)}\n"
                    f"AGGRESSIVE: {stats.get('w_agg', 0)}\n"
                    f"SAFE: {stats.get('w_safe', 0)}\n"
                    f"Подтверждений: {stats.get('w_confirmed', 0)}\n"
                    f"FLOW PRO (score≥{FLOW_PUBLISH_SCORE_MIN}): {stats.get('w_flow', 0)}\n"
                    f"Перетоков: {stats.get('w_flow_shift', 0)}\n"
                    f"Рынок проснулся: {stats.get('w_market_woke', 0)}\n"
                )
                state["last_weekly_week"] = week_key

            now_ts = datetime.now(timezone.utc).timestamp()

            # =========================
            # FLOW PRO — СЧИТАЕМ СНАЧАЛА ВСЁ, ПОТОМ ПУБЛИКУЕМ ЛУЧШЕЕ
            # =========================
            flow_rows = []  # (ticker, sector, score, dir, vol_mult, move, reasons)
            sector_buckets = {}  # sector -> list of rows with score>=FLOW_PUBLISH_SCORE_MIN

            for t in ALL_TICKERS:
                fr = flow_score_m5(t, idx_tr, now)
                if not fr:
                    continue
                score, fdir, vol_mult, move_last, reasons = fr
                sector = get_sector(t)
                row = (t, sector, score, fdir, vol_mult, move_last, reasons)
                flow_rows.append(row)

                if score >= FLOW_PUBLISH_SCORE_MIN:
                    sector_buckets.setdefault(sector, []).append(row)

            # секторная синхронность: если в секторе 2+ тикера score>=8 и в одну сторону → +2 к каждому (кап, не выше 10)
            boosted = {}
            for sector, rows in sector_buckets.items():
                if len(rows) < 2:
                    continue
                up = [r for r in rows if r[3] == "UP"]
                dn = [r for r in rows if r[3] == "DOWN"]

                dominant = None
                if len(up) >= 2:
                    dominant = "UP"
                elif len(dn) >= 2:
                    dominant = "DOWN"

                if dominant:
                    for r in rows:
                        if r[3] != dominant:
                            continue
                        t, sec, sc, d, vm, mv, rs = r
                        sc2 = min(10, sc + 2)
                        rs2 = rs + [f"Сектор синхронен (+2)"]
                        boosted[t] = (t, sec, sc2, d, vm, mv, rs2)

            # применяем буст
            final_flow = []
            for r in flow_rows:
                t = r[0]
                if t in boosted:
                    final_flow.append(boosted[t])
                else:
                    final_flow.append(r)

            # обновим sector_buckets после буста
            sector_buckets2 = {}
            for r in final_flow:
                t, sector, score, fdir, vol_mult, move_last, reasons = r
                if score >= FLOW_PUBLISH_SCORE_MIN:
                    sector_buckets2.setdefault(sector, []).append(r)

            # РЫНОК ПРОСНУЛСЯ: 3 сектора активны (имеют score>=8) + не FLAT по IMOEX (чтобы не ловить боковик)
            woke = False
            active_sectors = [s for s, rows in sector_buckets2.items() if len(rows) >= 1]
            if len(active_sectors) >= 3 and idx_tr != "FLAT":
                # анти-спам: 1 раз в 2 часа
                last_woke_ts = state.get("last_market_woke_ts", 0)
                if (not last_woke_ts) or (now_ts - last_woke_ts) >= (2 * 3600):
                    woke = True
                    state["last_market_woke_ts"] = now_ts

            if woke:
                send(
                    "🌪 <b>РЫНОК ПРОСНУЛСЯ</b>\n"
                    f"{mode_text}\n"
                    f"Активные сектора: " + ", ".join([sector_name(s) for s in active_sectors[:6]]) + "\n"
                    "Ожидается волатильная сессия — работаем по потоку.\n"
                )
                stats["market_woke"] = stats.get("market_woke", 0) + 1
                stats["w_market_woke"] = stats.get("w_market_woke", 0) + 1

            # =========================
            # ДАЛЬШЕ — ТВОЙ ЦИКЛ ПО ТИКЕРАМ (FAST + AGG/SAFE), НО ДОБАВЛЯЕМ ПУБЛИКАЦИЮ FLOW
            # =========================
            # Для FLOW публикуем:
            #  - score >= 8
            #  - и (изменился score) или (delta>=3) или (переток начался)
            #  - и анти-спам FLOW_COOLDOWN_SEC (страховка)
            # Публикуем ТОЛЬКО лучшие (до 1-2 в цикл), чтобы не шуметь.
            published_flow = 0

            # кандидаты — отсортируем по score desc, потом по vol_mult desc
            flow_candidates = sorted(
                [r for r in final_flow if r[2] >= FLOW_PUBLISH_SCORE_MIN],
                key=lambda x: (x[2], x[4]),
                reverse=True
            )

            # вычислим переток: было <4, стало >=8, vol_mult>=2, и в секторе 2 тикера >=8 в одну сторону
            def is_flow_shift(ticker: str, sector: str, score: int, fdir: str, vol_mult: float, cs: dict):
                prev = cs.get("flow_score_prev", None)
                if prev is None:
                    return False
                if prev >= 4:
                    return False
                if score < FLOW_PUBLISH_SCORE_MIN:
                    return False
                if vol_mult < 2.0:
                    return False

                # секторное подтверждение
                rows = sector_buckets2.get(sector, [])
                same_dir = [r for r in rows if r[3] == fdir and r[2] >= FLOW_PUBLISH_SCORE_MIN]
                return len(same_dir) >= 2

            for r in flow_candidates:
                if published_flow >= 2:  # жёсткий лимит на цикл
                    break

                t, sector, score, fdir, vol_mult, move_last, reasons = r
                cs = coins_state.get(t, {})

                last_flow_pub_ts = cs.get("last_flow_pub_ts", 0)
                if last_flow_pub_ts and (now_ts - last_flow_pub_ts) < FLOW_COOLDOWN_SEC:
                    # если слишком часто — не шлём
                    continue

                prev_score = cs.get("flow_score_prev", None)
                last_pub_score = cs.get("flow_last_pub_score", None)

                delta = 0
                if prev_score is not None:
                    delta = score - prev_score

                # обновляем prev_score каждый цикл (даже если не публикуем)
                cs["flow_score_prev"] = score

                shift = is_flow_shift(t, sector, score, fdir, vol_mult, cs)

                should_publish = False
                if last_pub_score is None:
                    # первый раз — только если сильный (>=8)
                    should_publish = True
                else:
                    if score != last_pub_score:
                        should_publish = True
                    if abs(delta) >= FLOW_PUBLISH_DELTA_MIN:
                        should_publish = True
                    if shift:
                        should_publish = True

                if not should_publish:
                    coins_state[t] = cs
                    continue

                # собираем секторный блок (топ-3 в секторе, same direction)
                sector_rows = sector_buckets2.get(sector, [])
                same_dir = [x for x in sector_rows if x[3] == fdir]
                same_dir_sorted = sorted(same_dir, key=lambda x: x[2], reverse=True)[:3]

                lines = []
                for x in same_dir_sorted:
                    tt, _, sc, dd, _, _, _ = x
                    lines.append(f"{flow_score_emoji(sc)} <b>{tt}</b> — {sc}/10")

                shift_tag = "\n⚡ <b>ПЕРЕТОК НАЧАЛСЯ</b>" if shift else ""
                d_emoji = flow_dir_emoji(fdir)

                msg = (
                    f"🚨 <b>MARKET FLOW — MOEX</b>\n\n"
                    f"🔥 Сектор: <b>{sector_name(sector)}</b>\n"
                    + "\n".join(lines) + "\n\n"
                    f"{d_emoji} M10 ход: {move_last:.2f}%\n"
                    f"📈 Объём: x{vol_mult:.2f}\n"
                    f"🎯 Score: <b>{score}/10</b>\n"
                    f"{shift_tag}\n\n"
                    "Причины:\n• " + "\n• ".join(reasons[:7])
                )

                send(msg)

                cs["last_flow_pub_ts"] = now_ts
                cs["flow_last_pub_score"] = score

                stats["flow"] = stats.get("flow", 0) + 1
                stats["w_flow"] = stats.get("w_flow", 0) + 1
                if shift:
                    stats["flow_shift"] = stats.get("flow_shift", 0) + 1
                    stats["w_flow_shift"] = stats.get("w_flow_shift", 0) + 1

                coins_state[t] = cs
                published_flow += 1

            # =========================
            # ТВОЯ ЛОГИКА ПО ТИКЕРАМ: FAST + AGG/SAFE (НЕ ТРОГАЮ)
            # =========================
            for t in ALL_TICKERS:
                cs = coins_state.get(t, {})

                # =========================
                # FAST (M15) — отдельный cooldown
                # =========================
                last_fast_ts = cs.get("last_fast_ts", 0)
                if (not last_fast_ts) or (now_ts - last_fast_ts) >= (FAST_COOLDOWN_MIN * 60):
                    fast_pack = fast_signal_m15(t)
                    if fast_pack:
                        f_dir, f_move, f_vol_mult, f_rng, f_reasons = fast_pack
                        dir_emoji = "📈" if f_dir == "UP" else "📉"
                        star = " ⭐" if t in PRIORITY_TICKERS else ""

                        send(
                            f"⚡ <b>MOEX FAST</b> — {t}{star}\n"
                            f"{dir_emoji} M15 импульс: {f_move:.2f}%\n"
                            f"Объём: x{f_vol_mult:.2f}\n"
                            f"Флет-диапазон: {f_rng:.2f}%\n\n"
                            "Причины:\n• " + "\n• ".join(f_reasons) + "\n\n"
                            "🕒 <b>Интрадей</b>\n"
                            "1) вход только после ретеста/паузы\n"
                            "2) стоп за экстремум M15\n"
                            "3) цель 0.8–1.5% (частями)\n"
                        )

                        cs["last_fast_ts"] = now_ts
                        stats["fast"] = stats.get("fast", 0) + 1
                        stats["w_fast"] = stats.get("w_fast", 0) + 1

                # =========================
                # AGG/SAFE — твой общий cooldown (как было)
                # =========================
                last_sent_ts = cs.get("last_sent_ts", 0)
                if last_sent_ts and (now_ts - last_sent_ts) < (COOLDOWN_MIN * 60):
                    coins_state[t] = cs
                    continue

                pack = stage_and_signal(t, idx_tr)
                if pack is None:
                    coins_state[t] = cs
                    continue

                stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, _, signal_price = pack
                if not is_agg and not is_safe:
                    coins_state[t] = cs
                    continue

                sig_type = "SAFE" if is_safe else "AGG"

                # анти-дубликат (как у тебя)
                if cs.get("last_type") == sig_type and cs.get("last_stage") == stage and cs.get("last_strength") == strength:
                    coins_state[t] = cs
                    continue

                # confirm (как у тебя)
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
                star = " ⭐" if t in PRIORITY_TICKERS else ""

                if sig_type == "AGG":
                    title = "⚠️ <b>AGGRESSIVE</b> — ранний радар"
                    conclusion = "🔴 <b>НЕ ВХОД</b>\n(наблюдать и ждать структуру)"
                else:
                    title = f"✅ <b>SAFE</b>{confirmed_tag}"
                    conclusion = "🟢 <b>МОЖНО ПЛАНИРОВАТЬ</b>\n(вход только по структуре)"

                msg = (
                    f"{title}\n"
                    f"{emoji} <b>{t}{star}</b>\n"
                    f"Стадия: <b>{stage}</b>\n"
                    f"Сила: {fire} ({strength}/5)\n\n"
                    f"H1: {h1_chg:.2f}% | D1: {d1_chg:.2f}%\n"
                    f"Объём: x{vol_mult:.2f}\n\n"
                    "Причины:\n• " + "\n• ".join(reasons) +
                    f"\n\n{memo_intraday()}\n\n"
                    f"🧠 <b>ВЫВОД</b>:\n{conclusion}"
                )

                send(msg)

                # state update (как у тебя + flow отдельно выше)
                cs["last_sent_ts"] = now_ts
                cs["last_type"] = sig_type
                cs["last_stage"] = stage
                cs["last_strength"] = strength
                
                cs["last_signal_price"] = signal_price
                cs["last_signal_direction"] = direction
                cs["last_signal_type"] = sig_type
                cs["last_signal_time"] = now_ts
               

                if sig_type == "AGG":
                    cs["last_agg_ts"] = now_ts
                    cs["last_agg_dir"] = direction
                    stats["agg"] = stats.get("agg", 0) + 1
                    stats["w_agg"] = stats.get("w_agg", 0) + 1
                else:
                    stats["safe"] = stats.get("safe", 0) + 1
                    stats["w_safe"] = stats.get("w_safe", 0) + 1
                    if confirmed:
                        stats["confirmed"] = stats.get("confirmed", 0) + 1
                        stats["w_confirmed"] = stats.get("w_confirmed", 0) + 1

                coins_state[t] = cs

            # save
            state["coins"] = coins_state
            state["stats"] = stats
            save_state(state)

        except Exception as e:
            send(f"❌ <b>BOT ERROR</b>: {e}")

        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    run()
