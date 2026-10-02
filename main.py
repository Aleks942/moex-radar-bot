import os
import time
import json
import tempfile
from html import escape
import requests
from datetime import datetime, timedelta, timezone
from statistics import mean
from open_interest import get_open_interest_signal   # 🔹 импорт OI (как у тебя)
from signal_journal import SignalJournal

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
