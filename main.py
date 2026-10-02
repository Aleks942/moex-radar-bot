import os
import time
import json
import tempfile
from html import escape
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
    "YDEX","OZON","AFKS","SMLT","PIKK",
    "MOEX","RUAL","FLOT","SBERP"
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

    # Телеком
    "MTSS": "TELCO",

    # Девелоперы
    "PIKK": "DEV",
    "SMLT": "DEV",

    # Тех/ритейл/прочее
    "YDEX": "TECH",
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
        print("[TELEGRAM_DISABLED] BOT_TOKEN/CHAT_ID отсутствуют", flush=True)
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error_code", "unknown") if isinstance(result, dict) else "invalid_json"
            print(f"[TELEGRAM_ERROR] code={code}", flush=True)
            return False
        return True
    except Exception as exc:
        # Не выводим URL Telegram: он содержит BOT_TOKEN.
        print(f"[TELEGRAM_ERROR] {type(exc).__name__}", flush=True)
        return False

# =========================
# TIME
# =========================
def msk_now():
    return datetime.now(timezone(timedelta(hours=MSK_OFFSET_HOURS)))

def should_fire_at(now_dt, hour, minute):
    # Отчёт запускается первым циклом после нужного времени.
    return (now_dt.hour, now_dt.minute) >= (hour, minute)

# =========================
# STATE
# =========================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict):
            raise ValueError("Корень state должен быть словарём")
        return state
    except Exception as exc:
        print(f"[STATE_LOAD_ERROR] {type(exc).__name__}", flush=True)
        return {}

def save_state(state: dict):
    temp_path = None
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=STATE_DIR,
            prefix="moex_state_", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(state, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, STATE_FILE)
        return True
    except Exception as exc:
        print(f"[STATE_SAVE_ERROR] {type(exc).__name__}", flush=True)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

# =========================
# DATA (SAFE PARSE)
# =========================
def candles_are_recent(ticker, columns, rows, interval):
    """Не публикуем интрадей-сигнал по давно остановившейся истории.

    M10: последний begin не старше 45 минут; H1: не старше 3 часов.
    Текущая формирующаяся свеча по-прежнему допустима для раннего радара.
    """
    if not columns or not rows or "begin" not in columns:
        return False
    try:
        begin = rows[-1][columns.index("begin")]
        last_time = datetime.fromisoformat(begin)
        if last_time.tzinfo is None:
            last_time = last_time.replace(
                tzinfo=timezone(timedelta(hours=MSK_OFFSET_HOURS))
            )
        age = (msk_now() - last_time).total_seconds()
        max_age = max(45, interval * 3) * 60
        valid = -60 <= age <= max_age
        if not valid and os.getenv("CANDLES_DEBUG", "0").strip().lower() in ("1", "true", "yes"):
            print(f"[CANDLES_STALE] {ticker} interval={interval} last_begin={begin}", flush=True)
        return valid
    except (ValueError, TypeError, IndexError):
        return False

_CANDLES_CACHE = None  # Только на время текущего цикла

def get_candles(ticker: str, interval: int, days: int):
    """MOEX ISS: все страницы свечей; прежний интерфейс (cols, data).

    Внутри одного цикла повторно используется уже загруженная история.
    Для проверки дат установи в Railway CANDLES_DEBUG=1.
    """
    try:
        cache_key = (ticker, interval)
        from_date = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")
        if _CANDLES_CACHE is not None:
            cached = _CANDLES_CACHE.get(cache_key)
            if cached and cached[0] >= days:
                _, cached_columns, cached_rows = cached
                begin_idx = cached_columns.index("begin")
                return cached_columns, [
                    row for row in cached_rows if row[begin_idx] >= from_date
                ]

        # IMOEX находится на рынке индексов, акции — на рынке shares.
        base_url = (
            "https://iss.moex.com/iss/engines/stock/markets/index/securities"
            if ticker == INDEX_TICKER else MOEX
        )
        params = {
            "interval": interval,
            "from": from_date,
            "iss.meta": "off",
            "iss.only": "candles",
            "start": 0,
        }

        columns = None
        rows = []
        seen_times = set()
        pages = 0

        # Лимит защищает от бесконечного цикла при ошибке сервера.
        for _ in range(100):
            response = requests.get(
                f"{base_url}/{ticker}/candles.json",
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            block = payload.get("candles")
            if not isinstance(block, dict):
                raise ValueError("ISS не вернул блок candles")

            page_columns = block.get("columns", [])
            page_rows = block.get("data")
            if not isinstance(page_rows, list):
                raise ValueError("Некорректный блок candles.data")

            if not page_rows:
                # Пустая страница завершает загрузку. При ошибке запроса
                # частичный набор свечей не возвращается.
                break

            if columns is None:
                required = {"begin", "end", "close", "high", "low"}
                if not required.issubset(page_columns):
                    raise ValueError("В candles отсутствуют обязательные поля")
                columns = page_columns
            elif page_columns != columns:
                raise ValueError("Состав колонок изменился между страницами")

            begin_idx = columns.index("begin")
            for row in page_rows:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise ValueError("Некорректная строка candles")
                begin = row[begin_idx]
                if not isinstance(begin, str) or not begin:
                    raise ValueError("В свече отсутствует время begin")
                if begin in seen_times:
                    raise ValueError(
                        "Повтор свечи между страницами; частичные данные отброшены"
                    )
                seen_times.add(begin)

            rows.extend(page_rows)
            pages += 1
            # Размер страницы берём из ответа, не предполагаем 100/500.
            params["start"] += len(page_rows)
        else:
            raise ValueError("Превышен лимит страниц ISS")

        if not columns or not rows:
            if os.getenv("CANDLES_DEBUG", "0").strip().lower() in (
                "1", "true", "yes"
            ):
                print(
                    f"[CANDLES_EMPTY] {ticker} interval={interval}",
                    flush=True,
                )
            return [], []

        begin_idx = columns.index("begin")
        rows.sort(key=lambda row: row[begin_idx])

        if os.getenv("CANDLES_DEBUG", "0").strip().lower() in (
            "1", "true", "yes"
        ):
            end_idx = columns.index("end")
            print(
                f"[CANDLES_OK] {ticker} interval={interval} "
                f"rows={len(rows)} pages={pages} "
                f"first={rows[0][begin_idx]} "
                f"last_begin={rows[-1][begin_idx]} "
                f"last_end={rows[-1][end_idx]}",
                flush=True,
            )

        if _CANDLES_CACHE is not None:
            _CANDLES_CACHE[cache_key] = (days, columns, rows)
        return columns, rows

    except Exception as exc:
        # При ошибке не выдаём старую первую страницу за свежие данные.
        print(
            f"[CANDLES_ERROR] {ticker} interval={interval} "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
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
        return "UNKNOWN"

    ema = ema_simple(closes, EMA_PERIOD)
    last = closes[-1]
    if ema is None:
        return "UNKNOWN"

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
    if tr == "UNKNOWN":
        return "⚠️ IMOEX: данные недоступны, направление не определено"
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
    if not candles_are_recent(ticker, cols_h1, data_h1, 60):
        return None
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
    if not candles_are_recent(ticker, cols, data, FAST_INTERVAL_MIN):
        return None
    highs, lows, closes, vols = extract_series(cols, data, FAST_LOOKBACK_BARS + FAST_BREAK_BARS + 5)
    if len(closes) < FAST_LOOKBACK_BARS + 2 or len(highs) < FAST_LOOKBACK_BARS + 2 or len(vols) < FAST_LOOKBACK_BARS + 2:
        return None

    price = closes[-1]
    prev = closes[-2]

    # 1) флет-диапазон
    hi = max(highs[-FAST_LOOKBACK_BARS-1:-1])
    lo = min(lows[-FAST_LOOKBACK_BARS-1:-1])
    rng = (hi - lo) / price * 100.0 if price else 0.0
    if rng > FAST_RANGE_MAX_PCT:
        return None

    # 2) импульс последней свечи
    move = pct(price, prev)
    if abs(move) < FAST_MOVE_MIN_PCT:
        return None

    # 3) объём
    vol_now = vols[-1]
    vol_avg = mean(vols[-FAST_LOOKBACK_BARS-1:-1]) if len(vols) >= FAST_LOOKBACK_BARS + 1 else 0.0
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
    if not candles_are_recent(ticker, cols, data, FLOW_INTERVAL_MIN):
        return None
    highs, lows, closes, vols = extract_series(cols, data, FLOW_LOOKBACK_BARS + FLOW_BREAK_BARS + 5)
    if len(closes) < max(FLOW_LOOKBACK_BARS + 5, FLOW_BREAK_BARS + 5):
        return None

    price = closes[-1]
    prev = closes[-2]
    move_last = pct(price, prev)

    vol_now = vols[-1]
    vol_base = mean(vols[-FLOW_LOOKBACK_BARS-1:-1]) if len(vols) >= FLOW_LOOKBACK_BARS + 1 else (mean(vols[:-1]) if len(vols) > 3 else 0.0)
    vol_mult = (vol_now / vol_base) if vol_base and vol_base > 0 else 0.0

    # range expand
    last_range = highs[-1] - lows[-1]
    ranges = [(highs[i] - lows[i]) for i in range(max(0, len(highs) - FLOW_LOOKBACK_BARS - 1), len(highs) - 1)]
    avg_range = mean(ranges) if ranges else 0.0
    range_expand = (avg_range > 0 and last_range > avg_range * 1.2)

    # 3-bar trend
    if len(closes) >= FLOW_TREND_BARS + 1:
        trend_closes = closes[-FLOW_TREND_BARS-1:]
        up3 = all(b > a for a, b in zip(trend_closes, trend_closes[1:]))
        dn3 = all(b < a for a, b in zip(trend_closes, trend_closes[1:]))
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
    global _CANDLES_CACHE
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

    state["coins"] = coins_state
    state["stats"] = stats

    # Старт отмечаем только после подтверждённой доставки Telegram.
    if state.get("start_day") != day_key:
        if send("🇷🇺 <b>MOEX-радар активен</b>\nАкции РФ • M10 + H1 + D1 • FAST + AGG + SAFE • FLOW PRO • подтверждение • статистика"):
            state["start_day"] = day_key
            save_state(state)

    while True:
        cycle_started = time.monotonic()
        try:
            _CANDLES_CACHE = {}
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
                # Сохраняем закончившуюся неделю до сброса счётчиков.
                if stats.get("week"):
                    state["previous_week_stats"] = dict(stats)
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
                    oi_text = f"\n⚠️ Open Interest недоступен ({escape(str(e))})\n"

                daily_sent = send(
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
                if daily_sent:
                    state["last_daily_day"] = day_key

            # WEEKLY REPORT
            if (now.weekday() == WEEKLY_REPORT_WEEKDAY and
                should_fire_at(now, WEEKLY_REPORT_HOUR, WEEKLY_REPORT_MINUTE) and
                state.get("last_weekly_week") != week_key):

                report_stats = state.get("previous_week_stats") or stats
                weekly_sent = send(
                    "🇷🇺 <b>НЕДЕЛЬНЫЙ ОБЗОР МОЕХ</b>\n"
                    f"Неделя: {report_stats.get('week', week_key)}\n\n"
                    f"{mode_text}\n\n"
                    f"FAST: {report_stats.get('w_fast', 0)}\n"
                    f"AGGRESSIVE: {report_stats.get('w_agg', 0)}\n"
                    f"SAFE: {report_stats.get('w_safe', 0)}\n"
                    f"Подтверждений: {report_stats.get('w_confirmed', 0)}\n"
                    f"FLOW PRO (score≥{FLOW_PUBLISH_SCORE_MIN}): {report_stats.get('w_flow', 0)}\n"
                    f"Перетоков: {report_stats.get('w_flow_shift', 0)}\n"
                    f"Рынок проснулся: {report_stats.get('w_market_woke', 0)}\n"
                )
                if weekly_sent:
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

                # Бонус +2 должен быть доступен ДО порога публикации 8.
                if score >= max(0, FLOW_PUBLISH_SCORE_MIN - 2):
                    sector_buckets.setdefault(sector, []).append(row)

            # Подтверждение: 2+ сильные бумаги одного сектора и направления.
            boosted = {}
            for sector, rows in sector_buckets.items():
                for sector_direction in ("UP", "DOWN"):
                    same_direction = [r for r in rows if r[3] == sector_direction]
                    if len(same_direction) < 2:
                        continue
                    for row in same_direction:
                        t, sec, sc, direction, vm, mv, reasons = row
                        boosted[t] = (
                            t, sec, min(10, sc + 2), direction, vm, mv,
                            reasons + ["Сектор синхронен (+2)"],
                        )

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
            if len(active_sectors) >= 3 and idx_tr in ("UP", "DOWN"):
                # анти-спам: 1 раз в 2 часа
                last_woke_ts = state.get("last_market_woke_ts", 0)
                if (not last_woke_ts) or (now_ts - last_woke_ts) >= (2 * 3600):
                    woke = True

            if woke:
                woke_sent = send(
                    "🌪 <b>РЫНОК ПРОСНУЛСЯ</b>\n"
                    f"{mode_text}\n"
                    f"Активные сектора: " + ", ".join([sector_name(s) for s in active_sectors[:6]]) + "\n"
                    "Ожидается волатильная сессия — работаем по потоку.\n"
                )
                if woke_sent:
                    state["last_market_woke_ts"] = now_ts
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

                # Предыдущий score сохраняем до проверки FLOW shift.
                shift = is_flow_shift(t, sector, score, fdir, vol_mult, cs)

                should_publish = False
                if last_pub_score is None:
                    # первый раз — только если сильный (>=8)
                    should_publish = True
                else:
                    if score != last_pub_score or cs.get("flow_last_pub_dir") != fdir:
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

                if not send(msg):
                    coins_state[t] = cs
                    continue

                cs["last_flow_pub_ts"] = now_ts
                cs["flow_last_pub_score"] = score
                cs["flow_last_pub_dir"] = fdir

                stats["flow"] = stats.get("flow", 0) + 1
                stats["w_flow"] = stats.get("w_flow", 0) + 1
                if shift:
                    print(f"[FLOW_SHIFT] {t} prev={prev_score} score={score} dir={fdir}", flush=True)
                    stats["flow_shift"] = stats.get("flow_shift", 0) + 1
                    stats["w_flow_shift"] = stats.get("w_flow_shift", 0) + 1

                coins_state[t] = cs
                published_flow += 1

            # Обновляем память ВСЕХ тикеров, включая слабые и пропущенные
            # из-за cooldown/лимита публикации. Пробел данных не считаем слабостью.
            observed_flow = {row[0]: row for row in final_flow}
            for t in ALL_TICKERS:
                cs = coins_state.get(t, {})
                row = observed_flow.get(t)
                if row is None:
                    cs.pop("flow_score_prev", None)
                    cs.pop("flow_dir_prev", None)
                else:
                    cs["flow_score_prev"] = row[2]
                    cs["flow_dir_prev"] = row[3]
                coins_state[t] = cs

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

                        fast_sent = send(
                            f"⚡ <b>MOEX FAST</b> — {t}{star}\n"
                            f"{dir_emoji} M10 импульс: {f_move:.2f}%\n"
                            f"Объём: x{f_vol_mult:.2f}\n"
                            f"Флет-диапазон: {f_rng:.2f}%\n\n"
                            "Причины:\n• " + "\n• ".join(f_reasons) + "\n\n"
                            "🕒 <b>Интрадей</b>\n"
                            "1) вход только после ретеста/паузы\n"
                            "2) стоп за экстремум M10\n"
                            "3) цель 0.8–1.5% (частями)\n"
                        )

                        if fast_sent:
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

                # Одинаковый тип/score на НОВОЙ H1-свече не является дублем.
                signal_cols, signal_rows = get_candles(t, 60, 20)
                begin_index = col_idx(signal_cols, "begin")
                signal_bar = (
                    signal_rows[-1][begin_index]
                    if signal_rows and begin_index is not None else None
                )
                if (signal_bar is not None and cs.get("last_signal_bar") == signal_bar
                    and cs.get("last_type") == sig_type
                    and cs.get("last_stage") == stage
                    and cs.get("last_strength") == strength):
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

                if not send(msg):
                    coins_state[t] = cs
                    continue

                # state update (как у тебя + flow отдельно выше)
                cs["last_sent_ts"] = now_ts
                cs["last_type"] = sig_type
                cs["last_stage"] = stage
                cs["last_strength"] = strength
                
                cs["last_signal_price"] = signal_price
                cs["last_signal_direction"] = direction
                cs["last_signal_type"] = sig_type
                cs["last_signal_stage"] = stage
                cs["last_signal_time"] = now_ts
                cs["last_signal_bar"] = signal_bar

                print(
                    f"[SAVE_SIGNAL] {t} "
                    f"{sig_type} "
                    f"{direction} "
                    f"{signal_price}",
                    flush=True
                )
                               

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

        except Exception as exc:
            print(f"[BOT_ERROR] {type(exc).__name__}: {exc}", flush=True)
            send(f"❌ <b>BOT ERROR</b>: {escape(str(exc))}")
        finally:
            _CANDLES_CACHE = None
            # Успешные отправки сохраняются и при ошибке позже в цикле.
            state["coins"] = coins_state
            state["stats"] = stats
            save_state(state)

        elapsed = time.monotonic() - cycle_started
        delay = max(0.0, CHECK_INTERVAL_SEC - elapsed)
        print(f"[CYCLE_DONE] seconds={elapsed:.1f} next_in={delay:.1f}", flush=True)
        time.sleep(delay)

if __name__ == "__main__":
    run()
