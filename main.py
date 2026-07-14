import os
import time
import json
import math
import requests
import traceback
import pandas as pd
from dotenv import load_dotenv
from priority_engine import find_global_priority, should_send_priority
from wall_detector import WallTracker
from continuation_engine import continuation_engine
from signal_tier import get_signal_tier
from sniper_engine import sniper_signal
from signal_analyst import init_db, save_signal, get_open_signals, close_signal
from ai_scoring import get_ai_multiplier
from market_context import apply_market_context
from ta_sniper import analyze_ta_sniper
from retest_filter import retest_ok
from pathlib import Path

# =========================
# LOG SYSTEM (вставить в начало файла, после импортов)
# =========================
from datetime import datetime

def log(msg, symbol=None, level="INFO"):
    now = datetime.now().strftime("%d.%m %H:%M")
    prefix = f"[{now}] [{level}]"
    if symbol:
        prefix += f" [{symbol}]"
    print(f"{prefix} {msg}", flush=True)

# =========================
# SIGNAL MODE CLASSIFIER
# =========================
def classify_signal_mode(sig):

    try:

        flags = set(sig.get("flags", []))
        score = float(sig.get("score") or 0)
        ep_score = float(sig.get("early_pressure_score") or 0)
        stage = str(sig.get("stage") or "")

        # =====================
        # COMMON FLAGS
        # =====================

        compression_present = (
            "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
            or "COMP_5M" in flags
            or "COMP_15M" in flags
        )

        absorption_present = (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        )

        launch_present = (
            "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
        )

        shift_present = (
            "BULLISH_SHIFT" in flags
            or "BEARISH_SHIFT" in flags
        )

        # =====================
        # ACCUMULATION = EARLY PREMOVE
        # =====================

        if "ACCUMULATION" in stage:
            return "PREMOVE"

        # =====================
        # TRUE EXPANSION FIRST
        # =====================
        
        if (
            "ACCELERATION_UP" in flags
            or "ACCELERATION_DOWN" in flags
            or "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
        ):
            return "EXPANSION" 

        # =====================
        # PREMOVE
        # =====================
        
        if (
            compression_present
            and absorption_present
            and ep_score >= 6
        ):
            return "PREMOVE"
        
        if (
            compression_present
            and shift_present
            and ep_score >= 8
        ):
            return "PREMOVE"
        
        if (
            compression_present
            and "ENERGY_BUILDUP" in flags
            and ep_score >= 8
        ):
            return "PREMOVE"

        # =====================
        # EXPANSION
        # =====================

        if "EXPANSION" in stage:
            return "EXPANSION"

        # =====================
        # TRANSITION
        # =====================

        if "TRANSITION" in stage:
            return "TRANSITION"

        # =====================
        # CONFIRMED
        # =====================

        if (
            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "BOS_UP" in flags
            or "BOS_DOWN" in flags
        ):
            return "CONFIRMED"

        # =====================
        # CONTINUATION
        # =====================

        if (
            "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
            or "STRONG_CONTINUATION_UP" in flags
            or "STRONG_CONTINUATION_DOWN" in flags
        ):
            return "CONTINUATION"

        # =====================
        # WATCH REVERSAL
        # =====================

        if (
            compression_present
            and absorption_present
            and score >= 10
            and not launch_present
            and not shift_present
        ):
            return "WATCH_REVERSAL"

        # =====================
        # WATCH
        # =====================

        if score >= 6:
            return "WATCH"

        return "NO_MODE"

    except Exception as e:

        print(
            f"[SIGNAL_MODE_ERROR] {e}",
            flush=True
        )

        return "NO_MODE"
        # =====================
        # ACCUMULATION
        # =====================

        if "ACCUMULATION" in stage:
            return "PREMOVE"

        # =====================
        # CONFIRMED
        # =====================

        if (

            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "BOS_UP" in flags
            or "BOS_DOWN" in flags

        ):

            return "CONFIRMED"

        # =====================
        # CONTINUATION
        # =====================

        if (

            "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
            or "STRONG_CONTINUATION_UP" in flags
            or "STRONG_CONTINUATION_DOWN" in flags

        ):

            return "CONTINUATION"

        



# =========================
# BTC MARKET REGIME (V2 FIXED + LOG)
# =========================
def detect_market_phase(df_h1):

    log("detect_market_phase START")

    try:
        # 🔴 ПРОВЕРКА ДАННЫХ
        if df_h1 is None or len(df_h1) < 50:
            log("not enough data for phase", level="WARN")
            return {"phase": "UNKNOWN", "score": 0}

        close = df_h1["close"]

        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()

        last_price = float(close.iloc[-1])
        ema20_last = float(ema20.iloc[-1])
        ema50_last = float(ema50.iloc[-1])

        log(f"price={last_price} ema20={ema20_last} ema50={ema50_last}")

        # 📊 СПРЕД И НАКЛОН
        spread = abs(ema20_last - ema50_last) / last_price * 100
        slope = ema20.iloc[-1] - ema20.iloc[-5]

        log(f"spread={spread:.3f} slope={slope:.6f}")

        trend_score = 0

        # 📈 НАПРАВЛЕНИЕ
        if ema20_last > ema50_last:
            trend_score += 1
        elif ema20_last < ema50_last:
            trend_score += 1

        # 📈 НАКЛОН
        if abs(slope) > last_price * 0.001:
            trend_score += 1

        # 📈 РАСШИРЕНИЕ
        if spread > 0.2:
            trend_score += 1

        # 📊 КЛАССИФИКАЦИЯ
        if trend_score >= 3:
            phase = "TREND"
        elif trend_score == 2:
            phase = "TRANSITION"
        else:
            phase = "FLAT"

        log(f"phase={phase} score={trend_score}")

        return {
            "phase": phase,
            "score": trend_score,
            "spread": round(spread, 3)
        }

    except Exception as e:
        log(f"PHASE_ERROR {e}", level="ERROR")
        return {"phase": "UNKNOWN", "score": 0}

# =====================
# MONEY FLOW
# =====================
def money_flow_ok(candles, oi_change, direction):
    try:
        if candles is None:
            return {"ok": False}

        # если pandas → в список
        if hasattr(candles, "iloc"):
            if candles.empty:
                return {"ok": False}
            data = candles.values.tolist()
        else:
            data = candles

        if len(data) < 3:
            return {"ok": False}

        last = data[-1]
        prev = data[-2]

        close_now = float(last[4])
        close_prev = float(prev[4])

        move_pct = (close_now - close_prev) / close_prev * 100 if close_prev else 0

        vol_now = float(last[5]) if len(last) > 5 else 0
        vol_prev = float(prev[5]) if len(prev) > 5 else 1

        vol_ok = vol_now > vol_prev
        impulse_ok = abs(move_pct) > 0.3

        oi_ok = False
        if oi_change is not None:
            try:
                oi_value = float(oi_change)

                if direction in ("LONG", "BUY"):

                    oi_ok = (
                        oi_value >= 0.3
                        and move_pct > 0.3
                    )
                
                elif direction in ("SHORT", "SELL"):
                
                    oi_ok = (
                        oi_value >= 0.3
                        and move_pct < -0.3
                    )
            except:
                pass

        return {
            "ok": vol_ok and impulse_ok and oi_ok
        }

    except Exception as e:
        print(f"[MF_ERROR] {e}", flush=True)
        return {"ok": False}

# =========================
# SCALP CANDIDATE DETECTOR
# =========================
def is_scalp_candidate(signal):

    try:

        if not signal or not isinstance(signal, dict):

            print(
                "[SCALP_SIGNAL_NONE]",
                flush=True
            )

            return False, "signal_none"

        flags = set(signal.get("flags", []))

        score = float(signal.get("score") or 0)
        acc = float(signal.get("acc_score") or 0)
        ep_score = float(signal.get("early_pressure_score") or 0)

        entry = (
            signal.get("entry")
            or signal.get("entry_type")
            or "NO_ENTRY"
        )

        mode = classify_signal_mode(signal)
        if mode is None:

            print(
                "[MODE_NONE]",
                flush=True
            )

            return False, "mode_none"

        # =========================
        # HARD FILTER
        # =========================

        if score < 12:
            return False, "scalp_low_score"

        if entry in ("NO_ENTRY", "PREMOVE_CONFLICT", None):
            return False, "scalp_no_entry"

        # =========================
        # PREMOVE
        # =========================

        if (
            mode == "PREMOVE"
            and score >= 20
            and ep_score >= 7
            and (
                "LAUNCH_PROXIMITY_UP" in flags
                or "LAUNCH_PROXIMITY_DOWN" in flags
                or "EXPLOSION_READY_UP" in flags
                or "EXPLOSION_READY_DOWN" in flags
            )
        ):

            return True, "scalp_premove"

        # =========================
        # TRANSITION
        # =========================

        if (
            mode == "TRANSITION"
            and score >= 18
            and ep_score >= 7
            and (
                "BULLISH_SHIFT" in flags
                or "BEARISH_SHIFT" in flags
            )
            and (
                "ACCELERATION_UP" in flags
                or "ACCELERATION_DOWN" in flags
            )
        ):

            return True, "scalp_transition"

        # =========================
        # ACCUMULATION
        # =========================

        if (
            (
                "ACCUMULATION_LONG" in str(entry)
                or "ACCUMULATION_SHORT" in str(entry)
            )
            and acc >= 3
            and score >= 18
        ):

            return True, "scalp_accumulation"

        return False, "scalp_no_match"

    except Exception as e:

        print(
            f"[SCALP_CANDIDATE_ERROR] {e}",
            flush=True
        )

        return False, "scalp_error"

# =========================
# CENTRAL SIGNAL ROUTER
# =========================
def route_signal(sig):

    try:

        if not sig or not isinstance(sig, dict):

            print(
                "[ROUTE_SIGNAL_NONE]",
                flush=True
            )

            return None, "signal_none"

        flags = set(sig.get("flags", []))

        score = float(sig.get("score") or 0)
        ep = float(sig.get("early_pressure_score") or 0)
        acc = float(sig.get("acc_score") or 0)

        entry = str(
            sig.get("entry")
            or sig.get("entry_type")
            or ""
        )

        stage = str(sig.get("stage") or "")

        # =====================
        # INVALID
        # =====================

        if entry in (
            "",
            "NO_ENTRY",
            "PREMOVE_CONFLICT",
            "None",
        ):
            return None, "invalid_entry"

        # =====================
        # SWING
        # =====================

        has_structure = (
            "STRUCTURE_HH_HL" in flags
            or "STRUCTURE_LH_LL" in flags
        )

        has_mtf = (
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
        )

        has_acceleration = (
            "ACCELERATION_UP" in flags
            or "ACCELERATION_DOWN" in flags
        )

        if (
            score >= 18
            and ep >= 8
            and has_structure
            and has_mtf
            and has_acceleration
        ):

            return "SWING", "swing_confirmed"

        # =====================
        # PRE_SWING
        # =====================

        if (
            (
                "TRANSITION" in stage
                or "ACCUMULATION" in stage
            )
            and score >= 16
            and ep >= 7
            and (
                has_structure
                or has_mtf
                or has_acceleration
            )
        ):

            return "PRE_SWING", "pre_swing"

        # =====================
        # SCALP
        # =====================

        scalp_ok, scalp_reason = is_scalp_candidate(sig)

        if scalp_ok:

            return "SCALP", scalp_reason

        # =====================
        # NO ROUTE
        # =====================

        return None, "no_route"

    except Exception as e:

        print(
            f"[ROUTE_SIGNAL_ERROR] {e}",
            flush=True
        )

        return None, "route_error"

# =========================
# ELITE SCALP FILTER
# =========================
def is_elite_scalp(sig):

    try:

        flags = set(sig.get("flags", []))

        score = float(sig.get("score") or 0)
        acc = float(sig.get("acc_score") or 0)
        ep = float(sig.get("early_pressure_score") or 0)

        entry = (
            str(sig.get("entry") or sig.get("entry_type") or "")
        )

        has_launch = (
            "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
        )

        has_acceleration = (
            "ACCELERATION_UP" in flags
            or "ACCELERATION_DOWN" in flags
        )

        has_shift = (
            "BULLISH_SHIFT" in flags
            or "BEARISH_SHIFT" in flags
        )

        has_mtf = (
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
        )

        has_absorption = (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        )

        # =========================
        # ELITE PREMOVE
        # =========================

        has_imbalance = (
            "EARLY_IMBALANCE_UP" in flags
            or "EARLY_IMBALANCE_DOWN" in flags
        )

        if (
            "PREMOVE" in entry
            and score >= 30
            and ep >= 10
            and has_launch
            and has_acceleration
            and has_shift
            and has_mtf
            and (
                has_absorption
                or has_imbalance
            )
        ):

            return True, "elite_premove"

        # =========================
        # ELITE ACCUMULATION
        # =========================

        if (
            "ACCUMULATION" in entry
            and score >= 24
            and acc >= 3
            and ep >= 10
            and has_absorption
        ):

            return True, "elite_accumulation"

        # =========================
        # ELITE CONFIRM
        # =========================

        if (
            "CONFIRM" in entry
            and score >= 26
            and has_acceleration
            and has_mtf
        ):

            return True, "elite_confirm"

        return False, "not_elite_scalp"

    except Exception as e:

        print(
            f"[ELITE_SCALP_ERROR] {e}",
            flush=True
        )

        return False, "elite_error"

# =========================
# ELITE SWING FILTER
# =========================
def is_elite_swing(sig):

    try:

        flags = set(sig.get("flags", []))

        score = float(sig.get("score") or 0)
        acc = float(sig.get("acc_score") or 0)
        ep = float(sig.get("early_pressure_score") or 0)

        stage = str(sig.get("stage") or "")
        entry = str(sig.get("entry") or "")

        has_absorption = (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        )

        has_structure = (
            "STRUCTURE_HH_HL" in flags
            or "STRUCTURE_LH_LL" in flags
        )

        has_mtf = (
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
        )

        has_pressure = (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )

        # =========================
        # ELITE ACCUMULATION SWING
        # =========================

        if (
            "ACCUMULATION" in entry
            and acc >= 3
            and ep >= 10
            and has_absorption
            and (
                has_structure
                or has_pressure
                or has_mtf
            )
        ):

            return True, "elite_swing_accumulation"

        # =========================
        # ELITE TREND SWING
        # =========================

        if (
            score >= 18
            and has_structure
            and has_mtf
            and has_pressure
            and (
                "EMA_BULL_STRONG" in flags
                or "EMA_BEAR_STRONG" in flags
            )
        ):

            return True, "elite_swing_trend"

        # =========================
        # ELITE TRANSITION SWING
        # =========================

        if (
            "TRANSITION" in stage
            and acc >= 3
            and ep >= 10
            and has_structure
            and has_absorption
        ):

            return True, "elite_swing_transition"

        return False, "not_elite_swing"

    except Exception as e:

        print(
            f"[ELITE_SWING_ERROR] {e}",
            flush=True
        )

        return False, "elite_swing_error"


# =========================
# SIGNAL STRENGTH ANALYZER
# =========================
def analyze_signal_strength(sig):
    if not sig or not isinstance(sig, dict):

        print(
            "[SIGNAL_STRENGTH_NONE]",
            flush=True
        )

        return "D", [], 0

    flags = set(sig.get("flags", []))
    score = sig.get("score", 0)
    oi = sig.get("oi_change", 0)

    reasons = []
    strength = 0

    # =====================
    # 🟠 EARLY PRESSURE — SMART FILTER
    # =====================

    ep_score = float(sig.get("early_pressure_score") or 0)

    real_impulse = (

        "VOL_SPIKE" in flags
        or "ATR_EXPANSION" in flags
        or "BREAKOUT_CONFIRM_UP" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
        or "CONTINUATION_UP" in flags
        or "CONTINUATION_DOWN" in flags
    )

    compression_context = (

        (
            "COMP_5M" in flags
            or "COMP_15M" in flags
        )

        and (

            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    )

    trend_only = (

        (
            "PRESSURE_UP" in flags

            and (

                "EMA_BULL" in flags
                or "EMA_BULL_STRONG" in flags
            )
        )

        or

        (
            "PRESSURE_DOWN" in flags

            and (

                "EMA_BEAR" in flags
                or "EMA_BEAR_STRONG" in flags
            )
        )
    )

    # =========================
    # BREAKOUT
    # =========================

    if "BREAKOUT_UP" in flags or "BREAKOUT_DOWN" in flags:
        reasons.append("Пробой уровня")
        strength += 2

    if (
        "BREAKOUT_CONFIRM_UP" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
    ):
        reasons.append("Подтверждённый пробой")
        strength += 3

    # =========================
    # CONTINUATION
    # =========================

    if "CONTINUATION_UP" in flags:
        reasons.append("Продолжение роста")
        strength += 3

    if "CONTINUATION_DOWN" in flags:
        reasons.append("Продолжение падения")
        strength += 3

    # =========================
    # PRESSURE
    # =========================

    if "PRESSURE_UP" in flags:
        reasons.append("Давление покупателей")
        strength += 1

    if "PRESSURE_DOWN" in flags:
        reasons.append("Давление продавцов")
        strength += 1

    # =========================
    # EMA TREND
    # =========================

    if "EMA_BULL" in flags:
        reasons.append("Тренд вверх")
        strength += 1

    if "EMA_BEAR" in flags:
        reasons.append("Тренд вниз")
        strength += 1

    if "EMA_BULL_STRONG" in flags:
        reasons.append("Сильный бычий тренд")
        strength += 2

    if "EMA_BEAR_STRONG" in flags:
        reasons.append("Сильный медвежий тренд")
        strength += 2

    # =========================
    # VOLUME
    # =========================

    if "VOL_SPIKE" in flags:
        reasons.append("Всплеск объёма")
        strength += 2

    # =========================
    # MTF ALIGNMENT
    # =========================

    if "MTF_LONG_ALIGN" in flags:
        reasons.append("MTF long alignment")
        strength += 2

    if "MTF_SHORT_ALIGN" in flags:
        reasons.append("MTF short alignment")
        strength += 2

    # =========================
    # ACCUMULATION
    # =========================

    if "COMP_5M" in flags:
        reasons.append("Сжатие 5M")
        strength += 1

    if "COMP_15M" in flags:
        reasons.append("Сжатие 15M")
        strength += 1

    # =========================
    # ORDERBOOK
    # =========================

    if "OB_WALL_BID" in flags:
        reasons.append("Покупатель в стакане")
        strength += 1

    if "OB_WALL_ASK" in flags:
        reasons.append("Продавец в стакане")
        strength += 1

    # =========================
    # OPEN INTEREST ANALYSIS
    # =========================
    
    if oi is not None:
    
        try:
    
            oi = float(oi)
    
            flags_set = set(flags)
            # =====================
            # OI ACCELERATION CHECK
            # =====================
            
            oi_accel = analyze_oi_acceleration(instId, oi)
            
            sig["oi_trend"] = oi_accel.get("oi_trend")
            sig["oi_acceleration"] = oi_accel.get("oi_acceleration")
            sig["oi_persistence"] = oi_accel.get("oi_persistence")
            sig["oi_power"] = oi_accel.get("oi_power")
            
            for f in oi_accel.get("flags", []):
                flags_set.add(f)
            
            print(
                f"[OI_ACCEL] {instId} "
                f"trend={oi_accel.get('oi_trend')} "
                f"power={oi_accel.get('oi_power')} "
                f"accel={oi_accel.get('oi_acceleration')} "
                f"persist={oi_accel.get('oi_persistence')}",
                flush=True
            )

            # =====================
            # REAL OI FLOW
            # =====================

            try:

                price_change_pct = float(
                    signal.get("price_change_pct") or 0
                )

            except:

                price_change_pct = 0

            oi_data = analyze_oi_flow(

                instId,

                price_change_pct,

                oi

            )

            signal["oi_score"] = (
                oi_data["oi_score"]
            )

            signal["oi_label"] = (
                oi_data["oi_label"]
            )

            signal["oi_reason"] = (
                oi_data["oi_reason"]
            )

            signal["oi_side"] = (
                oi_data["oi_side"]
            )

            print(
                f"[REAL_OI] "
                f"{instId} "
                f"label={oi_data['oi_label']} "
                f"score={oi_data['oi_score']} "
                f"side={oi_data['oi_side']}",
                flush=True
            )
    
            # =====================
            # STRONG OI BUILDUP
            # =====================
    
            if (

                oi >= 0.15
            
                and (
                    "COMP_PRO_5M" in flags_set
                    or "COMP_PRO_15M" in flags_set
                )
            
            ):
    
                reasons.append("Сильный рост OI")
                strength += 4
    
                flags_set.add("OI_STRONG_BUILDUP")
    
            elif (

                oi >= 0.4
            
                and (
                    "PRESSURE_UP" in flags_set
                    or "PRESSURE_DOWN" in flags_set
                )
            
            ):
    
                reasons.append("Рост OI")
                strength += 2
    
                flags_set.add("OI_BUILDUP")
    
            # =====================
            # OI DROP
            # =====================
    
            elif oi <= -3:
    
                reasons.append("Сильное падение OI")
                strength -= 3
    
                flags_set.add("OI_STRONG_DROP")
    
            elif oi <= -1:
    
                reasons.append("Падение OI")
                strength -= 1
    
                flags_set.add("OI_DROP")
    
            # =====================
            # LONG CONFIRMATION
            # =====================
    
            if (
                oi > 0
                and (
                    "PRESSURE_UP" in flags_set
                    or "BREAKOUT_CONFIRM_UP" in flags_set
                )
            ):
    
                strength += 2
    
                reasons.append("LONG подтверждается OI")
    
                flags_set.add("OI_LONG_CONFIRM")
    
            # =====================
            # SHORT CONFIRMATION
            # =====================
    
            if (
                oi > 0
                and (
                    "PRESSURE_DOWN" in flags_set
                    or "BREAKOUT_CONFIRM_DOWN" in flags_set
                )
            ):
    
                strength += 2
    
                reasons.append("SHORT подтверждается OI")
    
                flags_set.add("OI_SHORT_CONFIRM")
    
            # =====================
            # EXHAUSTION
            # =====================
    
            if (
                oi < 0
                and (
                    "BREAKOUT_CONFIRM_UP" in flags_set
                    or "BREAKOUT_CONFIRM_DOWN" in flags_set
                )
            ):
    
                reasons.append("Движение без поддержки OI")
    
                strength -= 2
    
                flags_set.add("OI_EXHAUSTION")
    
            flags = list(flags_set)
    
        except Exception as e:
    
            print(
                f"[OI_ANALYSIS_ERROR] {e}",
                flush=True
            )

    # =========================
    # RAW SCORE BONUS
    # =========================

    if score >= 6:
        strength += 3

    elif score >= 4:
        strength += 2

    elif score >= 2:
        strength += 1

    # =========================
    # FINAL RATING
    # =========================

    if strength >= 12:
        rating = "A+"

    elif strength >= 9:
        rating = "A"

    elif strength >= 6:
        rating = "B"

    elif strength >= 3:
        rating = "C"

    else:
        rating = "D"

    return rating, reasons, strength


# =========================
# ANTI-SPAM TELEGRAM
# =========================

LAST_SENT = {}

LAST_ALERT_SIGNATURE = {}

def telegram_firewall(sig, group="GENERAL"):
    """
    Центральный фильтр перед Telegram.
    Режет:
    - None сигналы
    - PREMOVE_CONFLICT
    - NO_ENTRY
    - одинаковые повторы
    - слабые score/ep
    - повтор без изменения цены
    """

    try:
        if not sig:
            return False, "empty_signal"

        symbol = (
            sig.get("instId")
            or sig.get("symbol")
            or sig.get("sym")
            or ""
        )

        if not symbol:
            return False, "no_symbol"

        entry = str(
            sig.get("entry")
            or sig.get("entry_type")
            or ""
        )

        if entry in ("", "None", "NO_ENTRY", "PREMOVE_CONFLICT"):
            return False, f"bad_entry_{entry}"

        direction = str(
            sig.get("direction_code")
            or sig.get("direction")
            or sig.get("side")
            or ""
        )

        if direction in ("", "None", "NEUTRAL"):
            return False, "bad_direction"

        score = float(sig.get("score") or 0)
        ep = float(sig.get("early_pressure_score") or 0)
        price = float(sig.get("price") or sig.get("entry_price") or 0)

        # =====================
        # MINIMUM QUALITY
        # =====================
        if group == "SCALP":
            if score < 12:
                return False, f"scalp_score_low_{score}"
            if ep < 7:
                return False, f"scalp_ep_low_{ep}"

        elif group == "PREMOVE":
            if score < 20:
                return False, f"premove_score_low_{score}"
            if ep < 8:
                return False, f"premove_ep_low_{ep}"

        elif group == "SWING":
            if score < 14:
                return False, f"swing_score_low_{score}"

        else:
            if score < 14:
                return False, f"general_score_low_{score}"

        # =====================
        # SAME SIGNAL ANTI-REPEAT
        # =====================
        key = f"{symbol}_{group}"
        now = time.time()

        prev = LAST_ALERT_SIGNATURE.get(key)

        signature = {
            "entry": entry,
            "direction": direction,
            "score": round(score, 1),
            "price": price,
            "time": now,
        }

        if prev:
            last_time = float(prev.get("time") or 0)
            last_price = float(prev.get("price") or 0)

            # cooldown 15 минут
            if now - last_time < 900:
                return False, "cooldown_15m"

            # если почти тот же сигнал и цена почти не изменилась
            if last_price > 0 and price > 0:
                price_change_pct = abs(price - last_price) / last_price * 100

                if (
                    prev.get("entry") == entry
                    and prev.get("direction") == direction
                    and prev.get("score") == round(score, 1)
                    and price_change_pct < 0.35
                ):
                    return False, f"same_signal_price_change_{price_change_pct:.2f}%"

        LAST_ALERT_SIGNATURE[key] = signature

        return True, "ok"

    except Exception as e:
        print(f"[TELEGRAM_FIREWALL_ERROR] {e}", flush=True)
        return False, "firewall_error"
# =========================
# PRE-SWING MEMORY
# =========================

PRE_SWING_STATE = {}

# =========================
# OI MEMORY
# =========================

OI_MEMORY = {}

# =========================
# REAL OI MEMORY
# =========================

REAL_OI_MEMORY = {}

# =========================
# OI TREND MEMORY
# =========================

OI_TREND_MEMORY = {}

# =========================
# PRESSURE MEMORY
# =========================

PRESSURE_MEMORY = {}

# =========================
# PRICE MEMORY
# =========================

PRICE_MEMORY = {}

# =========================
# LIQUIDATION MEMORY
# =========================

LIQUIDATION_MEMORY = {}

# =========================
# LIQUIDITY MAP MEMORY
# =========================

LIQUIDITY_ZONES = {}

# =========================
# LIQUIDATION CASCADE MEMORY
# =========================

LIQUIDATION_CASCADE_MEMORY = {}

# =========================
# IMPULSE CONFIRMATION MEMORY
# =========================

IMPULSE_CONFIRM_MEMORY = {}

# =========================
# MARKET REGIME MEMORY
# =========================

MARKET_REGIME_MEMORY = {}

# =========================
# PRICE MEMORY
# =========================

PRICE_MEMORY = {}


def analyze_pressure_memory(symbol, flags):
    try:
        symbol = str(symbol)
        flags = set(flags or [])

        has_up = "PRESSURE_UP" in flags
        has_down = "PRESSURE_DOWN" in flags

        prev = PRESSURE_MEMORY.get(symbol, {
            "side": None,
            "count": 0,
            "last_seen": 0
        })

        side = None

        if has_up and not has_down:
            side = "LONG"

        elif has_down and not has_up:
            side = "SHORT"

        else:
            PRESSURE_MEMORY[symbol] = {
                "side": None,
                "count": 0,
                "last_seen": time.time()
            }

            return {
                "pressure_side": None,
                "pressure_count": 0,
                "pressure_power": 0,
                "flags": []
            }

        if prev.get("side") == side:
            count = int(prev.get("count") or 0) + 1
        else:
            count = 1

        PRESSURE_MEMORY[symbol] = {
            "side": side,
            "count": count,
            "last_seen": time.time()
        }

        extra_flags = []
        power = 0

        if count >= 2:
            extra_flags.append(f"PRESSURE_{side}_PERSIST_2")
            power += 1

        if count >= 3:
            extra_flags.append(f"PRESSURE_{side}_PERSIST_3")
            power += 2

        if count >= 5:
            extra_flags.append(f"PRESSURE_{side}_PERSIST_5")
            power += 3

        return {
            "pressure_side": side,
            "pressure_count": count,
            "pressure_power": power,
            "flags": extra_flags
        }

    except Exception as e:
        print(f"[PRESSURE_MEMORY_ERROR] {symbol} {e}", flush=True)
        return {
            "pressure_side": None,
            "pressure_count": 0,
            "pressure_power": 0,
            "flags": []
        }

# =========================
# OI TREND ENGINE
# =========================

def analyze_oi_trend(symbol, oi_value):

    try:

        symbol = str(symbol)
        oi_value = float(oi_value or 0)

        prev = OI_TREND_MEMORY.get(
            symbol,
            {
                "values": []
            }
        )

        values = list(
            prev.get("values", [])
        )

        values.append(oi_value)

        # храним последние 5 значений
        values = values[-5:]

        OI_TREND_MEMORY[symbol] = {
            "values": values
        }

        flags = []
        trend_score = 0

        # =====================
        # BASIC TREND
        # =====================

        if len(values) >= 3:

            growing = (

                values[-1] > values[-2]
                and values[-2] > values[-3]

            )

            falling = (

                values[-1] < values[-2]
                and values[-2] < values[-3]

            )

            # =====================
            # OI BUILDUP
            # =====================

            if growing:

                trend_score += 2

                flags.append(
                    "OI_BUILDUP"
                )

                if values[-1] >= 0:

                    flags.append(
                        "OI_BUILDUP_LONG"
                    )

                else:

                    flags.append(
                        "OI_BUILDUP_SHORT"
                    )

            # =====================
            # OI FADE
            # =====================

            if falling:

                trend_score -= 1

                flags.append(
                    "OI_FADE"
                )

        # =====================
        # OI REVERSAL
        # =====================

        if len(values) >= 5:

            reversal_up = (

                values[-5] < values[-4]
                and values[-4] < values[-3]
                and values[-2] > values[-3]
                and values[-1] > values[-2]

            )

            reversal_down = (

                values[-5] > values[-4]
                and values[-4] > values[-3]
                and values[-2] < values[-3]
                and values[-1] < values[-2]

            )

            # =====================
            # REVERSAL UP
            # =====================

            if reversal_up:

                trend_score += 3

                flags.append(
                    "OI_REVERSAL_UP"
                )

            # =====================
            # REVERSAL DOWN
            # =====================

            if reversal_down:

                trend_score += 3

                flags.append(
                    "OI_REVERSAL_DOWN"
                )

        print(
            f"[OI_TREND] "
            f"{symbol} "
            f"values={values} "
            f"score={trend_score} "
            f"flags={flags}",
            flush=True
        )

        return {

            "oi_trend_score": trend_score,
            "oi_trend_flags": flags

        }

    except Exception as e:

        print(
            f"[OI_TREND_ERROR] "
            f"{symbol} "
            f"{e}",
            flush=True
        )

        return {

            "oi_trend_score": 0,
            "oi_trend_flags": []

        }

# =========================
# LIQUIDATION PRESSURE
# =========================

def analyze_liquidation_pressure(
    symbol,
    liq_side=None
):

    try:

        symbol = str(symbol)

        prev = LIQUIDATION_MEMORY.get(symbol, {
            "side": None,
            "count": 0,
            "last_seen": 0
        })

        if not liq_side:

            return {
                "liq_side": None,
                "liq_count": 0,
                "liq_power": 0,
                "flags": []
            }

        side = str(liq_side)

        if prev.get("side") == side:

            count = int(
                prev.get("count") or 0
            ) + 1

        else:

            count = 1

        LIQUIDATION_MEMORY[symbol] = {
            "side": side,
            "count": count,
            "last_seen": time.time()
        }

        flags = []
        power = 0

        # =====================
        # CASCADE MEMORY
        # =====================

        cascade_prev = LIQUIDATION_CASCADE_MEMORY.get(
            symbol,
            {
                "side": None,
                "count": 0
            }
        )

        cascade_count = 1

        if cascade_prev.get("side") == side:

            cascade_count = int(
                cascade_prev.get("count") or 0
            ) + 1

        LIQUIDATION_CASCADE_MEMORY[symbol] = {

            "side": side,
            "count": cascade_count

        }

        print(
            f"[LIQ_CASCADE] "
            f"{symbol} "
            f"side={side} "
            f"cascade={cascade_count}",
            flush=True
        )

        # =====================
        # SHORT SQUEEZE
        # =====================

        if side == "SHORT":

            if count >= 2:

                flags.append(
                    "SHORT_SQUEEZE"
                )

                power += 1

            if count >= 4:

                flags.append(
                    "CASCADE_SHORTS"
                )

                power += 2

            if cascade_count >= 3:

                flags.append(
                    "LIQUIDATION_CASCADE_ACTIVE"
                )

                power += 3

        # =====================
        # LONG FLUSH
        # =====================

        if side == "LONG":

            if count >= 2:

                flags.append(
                    "LONG_FLUSH"
                )

                power += 1

            if count >= 4:

                flags.append(
                    "CASCADE_LONGS"
                )

                power += 2

            if cascade_count >= 3:

                flags.append(
                    "LIQUIDATION_CASCADE_ACTIVE"
                )

                power += 3

        print(
            f"[LIQ_PRESSURE] "
            f"{symbol} "
            f"side={side} "
            f"count={count} "
            f"power={power}",
            flush=True
        )

        return {
            "liq_side": side,
            "liq_count": count,
            "liq_power": power,
            "flags": flags
        }

    except Exception as e:

        print(
            f"[LIQ_ERROR] "
            f"{symbol} "
            f"{e}",
            flush=True
        )

        return {
            "liq_side": None,
            "liq_count": 0,
            "liq_power": 0,
            "flags": []
        }

# =========================
# FLAT CONTROL ENGINE
# =========================

def analyze_flat_control(flags):

    try:

        flags = set(flags or [])

        long_control = 0
        short_control = 0

        reasons_long = []
        reasons_short = []

        # =====================
        # LONG CONTROL
        # =====================

        if "PRESSURE_UP" in flags:

            long_control += 2
            reasons_long.append(
                "покупатели давят вверх"
            )

        if "BUYER_ABSORPTION" in flags:

            long_control += 2
            reasons_long.append(
                "покупатели удерживают проливы"
            )

        if "RANGE_HOLD_HIGH" in flags:

            long_control += 2
            reasons_long.append(
                "цена держится в верхней части диапазона"
            )

        if "EMA_BULL" in flags:

            long_control += 1

        if "ACCELERATION_UP" in flags:

            long_control += 2
            reasons_long.append(
                "движение вверх ускоряется"
            )

        if "SHORT_SQUEEZE" in flags:

            long_control += 2
            reasons_long.append(
                "шортистов начинают выбивать"
            )
            
        # =====================
        # SHORT CONTROL
        # =====================

        if "PRESSURE_DOWN" in flags:

            short_control += 2
            reasons_short.append(
                "продавцы давят вниз"
            )

        if "SELLER_ABSORPTION" in flags:

            short_control += 2
            reasons_short.append(
                "продавцы удерживают рост"
            )

        if "RANGE_HOLD_LOW" in flags:

            short_control += 2
            reasons_short.append(
                "цена держится в нижней части диапазона"
            )

        if "EMA_BEAR" in flags:

            short_control += 1

        if "ACCELERATION_DOWN" in flags:

            short_control += 2
            reasons_short.append(
                "движение вниз ускоряется"
            )

        if "LONG_FLUSH" in flags:

            short_control += 2
            reasons_short.append(
                "лонгистов начинают выбивать"
            )

        dominance = "NEUTRAL"

        if long_control > short_control:

            dominance = "LONG"

        elif short_control > long_control:

            dominance = "SHORT"

        return {

            "long_control": long_control,
            "short_control": short_control,
            "dominance": dominance,

            "long_reasons": reasons_long,
            "short_reasons": reasons_short
        }

    except Exception as e:

        print(
            f"[FLAT_CONTROL_ERROR] {e}",
            flush=True
        )

        return {

            "long_control": 0,
            "short_control": 0,
            "dominance": "NEUTRAL",

            "long_reasons": [],
            "short_reasons": []
        }


# =========================
# CAPITAL FLOW ENGINE
# =========================

def analyze_capital_flow(signal):

    try:
        flags = set(signal.get("flags", []))

        oi = float(signal.get("oi") or 0)
        ep = float(signal.get("early_pressure_score") or 0)
        acc = float(signal.get("acc_score") or 0)
        score = float(signal.get("score") or 0)

        capital_score = 0
        reasons = []

        # =====================
        # EARLY OI BUILDUP
        # =====================

        if (
            oi >= 0.05
            and ep >= 10
            and acc >= 2
            and "RANGE_COMPRESSION" in flags
        ):

            capital_score += 2

            reasons.append(
                "появляется ранний приток капитала внутри сжатия"
            )

        if oi >= 0.30:
            capital_score += 3
            reasons.append("в рынок заметно заходят новые позиции")

        elif oi >= 0.10:

            capital_score += 2
        
            flow_reasons.append(
                "появляется приток капитала"
            )
        
            flow_flags.append(
                "FLOW_OI_BUILDUP"
            )
        elif oi <= -0.30:

            capital_score -= 2
        
            flow_reasons.append(
                "капитал выходит из рынка"
            )
        
            flow_flags.append(
                "FLOW_OI_EXIT"
            )

        if ep >= 15:
            capital_score += 2
            reasons.append("раннее давление сильное")

        if acc >= 3:
            capital_score += 2
            reasons.append("накопление позиции сильное")

        if "RANGE_COMPRESSION" in flags:
            capital_score += 1
            reasons.append("рынок сжат — возможен набор позиции")

        if (
            "PRESSURE_LONG_PERSIST_2" in flags
            or "PRESSURE_SHORT_PERSIST_2" in flags
        ):
            capital_score += 2
            reasons.append("давление удерживается несколько циклов")

        if (
            "SHORT_SQUEEZE" in flags
            or "LONG_FLUSH" in flags
        ):
            capital_score += 2
            reasons.append("ликвидации начинают усиливать движение")

        if score >= 25:
            capital_score += 1
            reasons.append("общая структура сигнала сильная")

        if capital_score >= 7:
            capital_state = "STRONG_CAPITAL_FLOW"
        elif capital_score >= 4:
            capital_state = "BUILDING_CAPITAL_FLOW"
        elif capital_score <= 0:
            capital_state = "WEAK_CAPITAL_FLOW"
        else:
            capital_state = "NEUTRAL_CAPITAL_FLOW"

        return {
            "capital_score": capital_score,
            "capital_state": capital_state,
            "capital_reasons": reasons
        }

    except Exception as e:
        print(
            f"[CAPITAL_FLOW_ERROR] {e}",
            flush=True
        )

        return {
            "capital_score": 0,
            "capital_state": "ERROR",
            "capital_reasons": []
        }


# =========================
# FLOW SNAPSHOT ENGINE — SWING MONEY FLOW
# =========================

def analyze_flow_snapshot(sig):
    """
    Главный снимок движения денег для SWING.
    Пока работает безопасно на тех данных, которые уже есть в signal.
    Позже сюда подключим CVD, Funding, Long/Short Ratio и Coinglass liquidity.
    """

    try:
        flags = set(sig.get("flags", []))

        price_change_pct = float(sig.get("price_change_pct") or 0)
        oi = float(sig.get("oi_change") or 0)
        volume_score = float(sig.get("volume_score") or 0)
        ep = float(sig.get("early_pressure_score") or 0)
        acc = float(sig.get("acc_score") or 0)

        flow_score = 0
        flow_reasons = []
        flow_flags = []
        capital_score = 0

        # =====================
        # BUILDUP CONTEXT
        # =====================
        
        has_buildup = (
        
            "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
            or "RANGE_COMPRESSION" in flags
            or "TIGHT_RANGE" in flags
        
        )
        
        if has_buildup:
        
            flow_score += 2
        
            flow_reasons.append(
                "рынок находится в фазе накопления"
            )
        
            flow_flags.append("FLOW_BUILDUP")
        
            # SHORT BUILDUP
        
            if (
                "EMA_BEAR" in flags
                or "STRUCTURE_LH_LL" in flags
            ):
        
                flow_score += 1
        
                flags.add(
                    "BEARISH_BUILDUP"
                )

        # =====================
        # OI BUILDUP
        # =====================
        
        if oi >= 0.10:
        
            flow_score += 2
        
            flow_reasons.append(
                "появляется приток капитала"
            )
        
            flow_flags.append(
                "FLOW_OI_BUILDUP"
            )
        
        elif oi <= -0.30:
        
            flow_score -= 2
        
            flow_reasons.append(
                "капитал выходит из рынка"
            )
        
            flow_flags.append(
                "FLOW_OI_EXIT"
            )
        # =====================
        # ACCUMULATION CONFIRM
        # =====================
        
        if acc >= 3 and "FLOW_BUILDUP" in flow_flags:
        
            flow_score += 1
        
            flow_reasons.append(
                "накопление подтверждает buildup"
            )
        
            flow_flags.append(
                "FLOW_ACCUMULATION_CONFIRM"
            )

        # 3) Раннее давление
        if ep >= 10:
            flow_score += 2
            flow_reasons.append("сильное раннее давление")
            flow_flags.append("FLOW_EARLY_PRESSURE")

        elif ep >= 7:
            flow_score += 1
            flow_reasons.append("есть раннее давление")
            flow_flags.append("FLOW_PRESSURE_START")

        # 4) Сжатие рынка
        if (
            "RANGE_COMPRESSION" in flags
            or "TIGHT_RANGE" in flags
            or "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
        ):
            flow_score += 2
            flow_reasons.append("рынок сжат — возможен набор позиции перед движением")
            flow_flags.append("FLOW_COMPRESSION")

        # 5) Абсорбция
        if (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        ):
            flow_score += 2
            flow_reasons.append("есть абсорбция — крупный участник удерживает цену")
            flow_flags.append("FLOW_ABSORPTION")

        # 6) Ликвидации
        if (
            "SHORT_SQUEEZE" in flags
            or "LONG_FLUSH" in flags
            or "LIQUIDATION_CASCADE_ACTIVE" in flags
        ):
            flow_score += 2
            flow_reasons.append("ликвидации начинают усиливать движение")
            flow_flags.append("FLOW_LIQUIDATION_PRESSURE")

        # =====================
        # LIQUIDITY MAP
        # =====================
        
        try:
        
            candles = sig.get("candles")
        
            if candles:
        
                liq_data = detect_liquidity_zones(candles)
        
                liq_flags = liq_data.get("liq_flags", [])
        
                for f in liq_flags:
                    flags.add(f)
        
                sig["liquidity_above"] = (
                    liq_data.get("liquidity_above")
                )
        
                sig["liquidity_below"] = (
                    liq_data.get("liquidity_below")
                )
        
                sig["dist_above_pct"] = (
                    liq_data.get("dist_above_pct")
                )
        
                sig["dist_below_pct"] = (
                    liq_data.get("dist_below_pct")
                )
        
                sig["liq_score"] = (
                    liq_data.get("liq_score")
                )
        
                sig["liq_state"] = (
                    liq_data.get("liq_state")
                )
        
                print(
                    f"[LIQUIDITY_MAP] "
                    f"{sig.get('instId')} "
                    f"state={liq_data.get('liq_state')} "
                    f"flags={liq_flags} "
                    f"above={liq_data.get('dist_above_pct')}% "
                    f"below={liq_data.get('dist_below_pct')}%",
                    flush=True
                )
        
        except Exception as e:
        
            print(
                f"[LIQUIDITY_MAP_PIPELINE_ERROR] "
                f"{sig.get('instId')} "
                f"{e}",
                flush=True
            )
        # 7) Подозрение на поздний вход
        if (
            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "ATR_EXPANSION" in flags
        ) and acc < 2:
            flow_score -= 2
            flow_reasons.append("движение может быть уже поздним — нет накопления")
            flow_flags.append("FLOW_LATE_RISK")

        # =====================
        # FINAL FLOW STATE
        # =====================
        
        total_flow = flow_score + capital_score
        
        if (
            total_flow >= 8
            and capital_score >= 3
            and (
                "FLOW_BUILDUP" in flow_flags
                or "FLOW_COMPRESSION" in flow_flags
                or "FLOW_ABSORPTION" in flow_flags
            )
        ):

            flow_state = "STRONG_MONEY_FLOW"

        elif (
            total_flow >= 5
            and capital_score >= 1
            and (
                "FLOW_BUILDUP" in flow_flags
                or "FLOW_COMPRESSION" in flow_flags
                or "FLOW_ABSORPTION" in flow_flags
            )
        ):

            flow_state = "BUILDING_MONEY_FLOW"

        elif (
            flow_score >= 2
            and (
                "FLOW_BUILDUP" in flow_flags
                or "FLOW_COMPRESSION" in flow_flags
                or "FLOW_ABSORPTION" in flow_flags
            )
        ):

            flow_state = "EARLY_MONEY_FLOW"

        else:

            flow_state = "WEAK_OR_NO_FLOW"

        # =====================================
        # СОХРАНЯЕМ РЕЗУЛЬТАТ ДЛЯ ЛЮБОГО FLOW_STATE
        # =====================================

        sig["flow_score"] = flow_score
        sig["capital_flow_score"] = capital_score
        sig["flow_total_score"] = total_flow
        sig["flow_state"] = flow_state
        sig["flow_reasons"] = flow_reasons

        all_flags = set(
            sig.get("flags") or []
        )

        all_flags.update(flow_flags)
        all_flags.update(flags)

        sig["flags"] = list(all_flags)

        return sig

    except Exception as e:
        print(f"[FLOW_SNAPSHOT_ERROR] {e}", flush=True)
        sig["flow_score"] = 0
        sig["flow_state"] = "FLOW_ERROR"
        sig["flow_reasons"] = []
        return sig

# =========================
# STOP HUNT ENGINE V2
# =========================

def analyze_stop_hunt(signal):

    try:

        flags = set(signal.get("flags", []))

        stop_hunt_score = 0
        stop_hunt_state = "NO_STOP_HUNT"
        stop_hunt_side = "NEUTRAL"
        stop_hunt_reasons = []

        oi_state = str(signal.get("oi_state") or "")

        has_compression = (
            "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
            or "RANGE_COMPRESSION" in flags
            or "TIGHT_RANGE" in flags
        )

        # =====================
        # PROBABLE SWEEP ABOVE
        # =====================

        if (
            "EQUAL_HIGHS" in flags
            and "LIQUIDITY_ABOVE" in flags
            and "PRESSURE_UP" in flags
            and has_compression
        ):

            stop_hunt_score += 3
            stop_hunt_side = "UP"

            stop_hunt_reasons.append(
                "сверху есть пул ликвидности"
            )

            flags.add("PROBABLE_STOP_HUNT_UP")

            if "NEAR_LIQUIDITY_ABOVE" in flags:

                stop_hunt_score += 2

                stop_hunt_reasons.append(
                    "ликвидность сверху очень близко"
                )

                flags.add("NEAR_STOP_POOL_ABOVE")

            if oi_state == "NEW_LONGS":

                stop_hunt_score += 3

                stop_hunt_reasons.append(
                    "заходят новые LONG"
                )

                flags.add(
                    "SHORT_STOP_HUNT_SETUP"
                )

        # =====================
        # PROBABLE SWEEP BELOW
        # =====================

        if (
            "EQUAL_LOWS" in flags
            and "LIQUIDITY_BELOW" in flags
            and has_compression
        ):

            stop_hunt_score += 3
            stop_hunt_side = "DOWN"

            stop_hunt_reasons.append(
                "снизу есть пул ликвидности"
            )

            flags.add("PROBABLE_STOP_HUNT_DOWN")

            # EARLY PRESSURE BONUS

            if (
                "PRESSURE_DOWN" in flags
                or "ACCELERATION_DOWN" in flags
            ):

                stop_hunt_score += 2

                stop_hunt_reasons.append(
                    "давление вниз усиливает вероятность sweep"
                )

            if "NEAR_LIQUIDITY_BELOW" in flags:

                stop_hunt_score += 2

                stop_hunt_reasons.append(
                    "ликвидность снизу очень близко"
                )

                flags.add("NEAR_STOP_POOL_BELOW")

            if oi_state == "NEW_SHORTS":

                stop_hunt_score += 3

                stop_hunt_reasons.append(
                    "заходят новые SHORT"
                )

                flags.add(
                    "LONG_STOP_HUNT_SETUP"
                )

        # =====================
        # FINAL STATE
        # =====================

        if stop_hunt_score >= 6:

            stop_hunt_state = "ACTIVE_STOP_HUNT"

        elif stop_hunt_score >= 3:

            stop_hunt_state = "PROBABLE_STOP_HUNT"

        signal["stop_hunt_score"] = (
            stop_hunt_score
        )

        signal["stop_hunt_state"] = (
            stop_hunt_state
        )

        signal["stop_hunt_side"] = (
            stop_hunt_side
        )

        signal["stop_hunt_reasons"] = (
            stop_hunt_reasons
        )

        signal["flags"] = list(flags)

        print(
            f"[STOP_HUNT] "
            f"{signal.get('instId')} "
            f"state={stop_hunt_state} "
            f"side={stop_hunt_side} "
            f"score={stop_hunt_score} "
            f"reasons={stop_hunt_reasons}",
            flush=True
        )

        return signal

    except Exception as e:

        print(
            f"[STOP_HUNT_ERROR] "
            f"{signal.get('instId')} "
            f"{e}",
            flush=True
        )

        signal["stop_hunt_score"] = 0
        signal["stop_hunt_state"] = "STOP_HUNT_ERROR"
        signal["stop_hunt_side"] = "NEUTRAL"
        signal["stop_hunt_reasons"] = []

        return signal

# =========================
# SWEEP DETECTION ENGINE
# =========================

def analyze_liquidity_sweep(signal):

    try:

        flags = set(signal.get("flags", []))

        candles = signal.get("candles") or []

        if len(candles) < 5:
            return signal

        last = candles[-1]

        try:
            high = float(last[2])
            low = float(last[3])
            close = float(last[4])

        except:
            return signal

        liquidity_above = signal.get("liquidity_above")
        liquidity_below = signal.get("liquidity_below")

        sweep_score = 0
        sweep_state = "NO_SWEEP"
        sweep_side = "NEUTRAL"
        sweep_reasons = []

        # =====================
        # SWEEP ABOVE
        # =====================

        if (
            liquidity_above
            and high > liquidity_above
            and close < liquidity_above
        ):

            sweep_score += 4
            sweep_side = "UP"

            sweep_reasons.append(
                "цена забрала ликвидность сверху и вернулась обратно"
            )

            flags.add("SWEEP_ABOVE")
            flags.add("FAILED_BREAKOUT")

        # =====================
        # SWEEP BELOW
        # =====================

        if (
            liquidity_below
            and low < liquidity_below
            and close > liquidity_below
        ):

            sweep_score += 4
            sweep_side = "DOWN"

            sweep_reasons.append(
                "цена забрала ликвидность снизу и вернулась обратно"
            )

            flags.add("SWEEP_BELOW")
            flags.add("FAILED_BREAKDOWN")

        # =====================
        # SWEEP RECLAIM BONUS
        # =====================

        if (
            sweep_side == "DOWN"
            and "BUYER_ABSORPTION" in flags
        ):

            sweep_score += 2

            sweep_reasons.append(
                "покупатели удержали sweep снизу"
            )

            flags.add("BULLISH_RECLAIM")

        if (
            sweep_side == "UP"
            and "SELLER_ABSORPTION" in flags
        ):

            sweep_score += 2

            sweep_reasons.append(
                "продавцы удержали sweep сверху"
            )

            flags.add("BEARISH_RECLAIM")

        # =====================
        # RECLAIM TRAP FILTER
        # =====================

        if (

            "BULLISH_RECLAIM" in flags

            and (
                "BUYER_ABSORPTION" in flags
                or "BULLISH_SHIFT" in flags
            )

        ):

            flags.add("SHORT_TRAP_RISK")

            sweep_score += 2

            sweep_reasons.append(
                "рынок вернулся после выноса вниз — риск trap для SHORT"
            )

        if (

            "BEARISH_RECLAIM" in flags

            and (
                "SELLER_ABSORPTION" in flags
                or "BEARISH_SHIFT" in flags
            )

        ):

            flags.add("LONG_TRAP_RISK")

            sweep_score += 2

            sweep_reasons.append(
                "рынок вернулся после выноса вверх — риск trap для LONG"
            )

        # =====================
        # LIQUIDITY MAGNET
        # =====================

        if (
            "PRESSURE_UP" in flags
            and "ACCELERATION_UP" in flags
            and "NEAR_LIQUIDITY_ABOVE" in flags
        ):

            sweep_score += 2

            sweep_reasons.append(
                "цена ускоряется к ликвидности сверху"
            )

            flags.add(
                "LIQUIDITY_MAGNET_UP"
            )

        if (
            "PRESSURE_DOWN" in flags
            and "ACCELERATION_DOWN" in flags
            and "NEAR_LIQUIDITY_BELOW" in flags
        ):

            sweep_score += 2

            sweep_reasons.append(
                "цена ускоряется к ликвидности снизу"
            )

            flags.add(
                "LIQUIDITY_MAGNET_DOWN"
            )

        # =====================
        # FINAL STATE
        # =====================

        if sweep_score >= 5:

            sweep_state = "ACTIVE_SWEEP"

        elif sweep_score >= 3:

            sweep_state = "PROBABLE_SWEEP"

        signal["sweep_score"] = sweep_score
        signal["sweep_state"] = sweep_state
        signal["sweep_side"] = sweep_side
        signal["sweep_reasons"] = sweep_reasons

        signal["flags"] = list(flags)

        print(
            f"[SWEEP_ENGINE] "
            f"{signal.get('instId')} "
            f"state={sweep_state} "
            f"side={sweep_side} "
            f"score={sweep_score} "
            f"reasons={sweep_reasons}",
            flush=True
        )

        return signal

    except Exception as e:

        print(
            f"[SWEEP_ENGINE_ERROR] "
            f"{signal.get('instId')} "
            f"{e}",
            flush=True
        )

        signal["sweep_score"] = 0
        signal["sweep_state"] = "SWEEP_ERROR"
        signal["sweep_side"] = "NEUTRAL"
        signal["sweep_reasons"] = []

        return signal
# =========================
# OI BEHAVIOR CLASSIFIER
# =========================

def analyze_oi_behavior(sig):

    try:

        price_change = float(
            sig.get("price_change_pct") or 0
        )

        oi_change = float(
            sig.get("oi_change") or 0
        )

        flags = []
        reasons = []

        oi_state = "NEUTRAL"

        # =====================
        # NEW LONGS
        # =====================

        if (
            price_change > 0.25
            and oi_change > 0.10
        ):

            oi_state = "NEW_LONGS"

            flags.append(
                "OI_NEW_LONGS"
            )

            reasons.append(
                "в рынок заходят новые LONG позиции"
            )

        # =====================
        # NEW SHORTS
        # =====================

        elif (
            price_change < -0.25
            and oi_change > 0.10
        ):

            oi_state = "NEW_SHORTS"

            flags.append(
                "OI_NEW_SHORTS"
            )

            reasons.append(
                "в рынок заходят новые SHORT позиции"
            )

        # =====================
        # SHORT COVERING
        # =====================

        elif (
            price_change > 0.25
            and oi_change < -0.10
        ):

            oi_state = "SHORT_COVERING"

            flags.append(
                "OI_SHORT_COVERING"
            )

            reasons.append(
                "рост идет в основном за счет закрытия SHORT"
            )

        # =====================
        # LONG EXIT
        # =====================

        elif (
            price_change < -0.25
            and oi_change < -0.10
        ):

            oi_state = "LONG_EXIT"

            flags.append(
                "OI_LONG_EXIT"
            )

            reasons.append(
                "падение идет из-за выхода LONG позиций"
            )

        sig["oi_state"] = oi_state
        sig["oi_reasons"] = reasons

        old_flags = list(sig.get("flags") or [])

        sig["flags"] = list(
            set(old_flags + flags)
        )

        print(
            f"[OI_CLASSIFIER] "
            f"{sig.get('instId')} "
            f"state={oi_state} "
            f"price_change={price_change} "
            f"oi={oi_change}",
            flush=True
        )

        return sig

    except Exception as e:

        print(
            f"[OI_CLASSIFIER_ERROR] {e}",
            flush=True
        )

        sig["oi_state"] = "ERROR"
        sig["oi_reasons"] = []

        return sig


# =========================
# REAL OI CLASSIFIER V2
# =========================

def analyze_real_oi_flow(instId, price, oi):

    try:

        global PRICE_MEMORY
        global REAL_OI_MEMORY

        prev_price = PRICE_MEMORY.get(instId)
        prev_oi = REAL_OI_MEMORY.get(instId)

        PRICE_MEMORY[instId] = price
        REAL_OI_MEMORY[instId] = oi

        if prev_price is None or prev_oi is None:

            return {
                "oi_state": "NO_OI_HISTORY",
                "oi_score": 0,
                "oi_reason": "нет истории OI",
                "oi_side": "NEUTRAL"
            }

        price_delta = price - prev_price
        oi_delta = oi - prev_oi

        oi_state = "NEUTRAL"
        oi_score = 0
        oi_reason = ""
        oi_side = "NEUTRAL"

        # =====================
        # NEW LONGS
        # =====================

        if (
            price_delta > 0
            and oi_delta > 0
        ):

            oi_state = "NEW_LONGS"
            oi_score = 4
            oi_reason = "в рынок заходят новые LONG позиции"
            oi_side = "LONG"

        # =====================
        # NEW SHORTS
        # =====================

        elif (
            price_delta < 0
            and oi_delta > 0
        ):

            oi_state = "NEW_SHORTS"
            oi_score = 4
            oi_reason = "в рынок заходят новые SHORT позиции"
            oi_side = "SHORT"

        # =====================
        # SHORT COVERING
        # =====================

        elif (
            price_delta > 0
            and oi_delta < 0
        ):

            oi_state = "SHORT_COVERING"
            oi_score = -2
            oi_reason = "рост идет за счет закрытия SHORT"
            oi_side = "LONG"

        # =====================
        # LONG EXITS
        # =====================

        elif (
            price_delta < 0
            and oi_delta < 0
        ):

            oi_state = "LONG_EXIT"
            oi_score = -2
            oi_reason = "падение идет за счет выхода LONG"
            oi_side = "SHORT"

        print(
            f"[REAL_OI_CLASSIFIER] "
            f"{instId} "
            f"state={oi_state} "
            f"price_delta={round(price_delta, 6)} "
            f"oi_delta={round(oi_delta, 6)}",
            flush=True
        )

        return {
            "oi_state": oi_state,
            "oi_score": oi_score,
            "oi_reason": oi_reason,
            "oi_side": oi_side
        }

    except Exception as e:

        print(
            f"[REAL_OI_CLASSIFIER_ERROR] {e}",
            flush=True
        )

        return {
            "oi_state": "OI_ERROR",
            "oi_score": 0,
            "oi_reason": str(e),
            "oi_side": "NEUTRAL"
        }
# =========================
# FLOW + OI MERGE ENGINE
# =========================

def merge_flow_with_oi(sig):
    """
    SMART MONEY V3.
    Проверяет: подтверждает ли OI поток капитала, найденный FLOW.
    """

    try:
        flags = set(sig.get("flags", []))

        flow_state = str(sig.get("flow_state") or "")
        oi_state = str(sig.get("oi_state") or "NEUTRAL")

        direction = str(
            sig.get("direction_code")
            or sig.get("direction")
            or sig.get("side")
            or ""
        ).upper()

        # =====================
        # FLOW CONFIRM
        # =====================

        flow_confirm = flow_state in (
            "EARLY_MONEY_FLOW",
            "BUILDING_MONEY_FLOW",
            "STRONG_MONEY_FLOW"
        )

        sig["flow_confirm"] = flow_confirm

        smart_money_score = 0
        smart_money_state = "NEUTRAL_SMART_MONEY"
        real_money_confirm = False
        reasons = []

        # =====================
        # NO FLOW = NO SMART MONEY
        # =====================

        if not flow_confirm:

            smart_money_score = 0
            smart_money_state = "WEAK_SMART_MONEY"

            reasons.append(
                "FLOW не подтвердил начало движения капитала"
            )

            flags.add("SMART_MONEY_NO_FLOW")

        else:

            smart_money_score += 1

            reasons.append(
                "FLOW подтвердил начало движения капитала"
            )

            flags.add("SMART_MONEY_FLOW_CONFIRMED")

            # =====================
            # LONG CONFIRM
            # =====================

            if (
                ("LONG" in direction or "UP" in direction or "BUY" in direction)
                and oi_state == "NEW_LONGS"
            ):

                smart_money_score += 5
                real_money_confirm = True

                reasons.append(
                    "LONG подтверждён новыми позициями"
                )

                flags.add("SMART_MONEY_LONG_CONFIRM")

            # =====================
            # SHORT CONFIRM
            # =====================

            elif (
                ("SHORT" in direction or "DOWN" in direction or "SELL" in direction)
                and oi_state == "NEW_SHORTS"
            ):

                smart_money_score += 5
                real_money_confirm = True

                reasons.append(
                    "SHORT подтверждён новыми позициями"
                )

                flags.add("SMART_MONEY_SHORT_CONFIRM")

            # =====================
            # WEAK LONG
            # =====================

            elif (
                ("LONG" in direction or "UP" in direction or "BUY" in direction)
                and oi_state == "SHORT_COVERING"
            ):

                smart_money_score += 1

                reasons.append(
                    "рост больше похож на закрытие SHORT, чем на новый LONG"
                )

                flags.add("SMART_MONEY_LONG_WEAK_COVERING")

            # =====================
            # WEAK SHORT
            # =====================

            elif (
                ("SHORT" in direction or "DOWN" in direction or "SELL" in direction)
                and oi_state == "LONG_EXIT"
            ):

                smart_money_score += 1

                reasons.append(
                    "падение больше похоже на выход LONG, чем на новый SHORT"
                )

                flags.add("SMART_MONEY_SHORT_WEAK_EXIT")


            else:

                reasons.append(
                    "FLOW есть, но OI пока не подтвердил движение"
                )
            
                flags.add("SMART_MONEY_WAIT_OI_CONFIRM")

            # =====================
            # FINAL STATE
            # =====================
            
            if not flow_confirm:
            
                smart_money_state = "WEAK_SMART_MONEY"
            
            elif real_money_confirm and smart_money_score >= 5:

                smart_money_state = "STRONG_SMART_MONEY"
            
            elif oi_state in (
                "SHORT_COVERING",
                "LONG_EXIT"
            ):
            
                smart_money_state = "WEAK_SMART_MONEY"
            
            else:
            
                smart_money_state = "EARLY_SMART_MONEY"

        sig["real_money_confirm"] = real_money_confirm
        sig["smart_money_score"] = smart_money_score
        sig["smart_money_state"] = smart_money_state
        sig["smart_money_reasons"] = reasons
        sig["flags"] = list(flags)

        print(
            f"[SMART_MONEY_MERGE] "
            f"{sig.get('instId')} "
            f"state={smart_money_state} "
            f"score={smart_money_score} "
            f"real_money={real_money_confirm} "
            f"oi_state={oi_state} "
            f"flow={flow_state}",
            flush=True
        )

        return sig

    except Exception as e:
        print(
            f"[SMART_MONEY_MERGE_ERROR] "
            f"{sig.get('instId')} "
            f"{e}",
            flush=True
        )

        sig["real_money_confirm"] = False
        sig["smart_money_score"] = 0
        sig["smart_money_state"] = "SMART_MONEY_ERROR"
        sig["smart_money_reasons"] = [str(e)]

        return sig

    

# =========================
# CVD ENGINE V1
# =========================

def analyze_cvd(signal):

    try:

        flags = set(
            signal.get("flags", [])
        )

        cvd_score = 0
        cvd_state = "NEUTRAL_CVD"
        cvd_reasons = []

        # =====================
        # BUY PRESSURE
        # =====================

        if "PRESSURE_UP" in flags:

            cvd_score += 2

            cvd_reasons.append(
                "покупатели агрессивно давят market-ордерами"
            )

        # =====================
        # SELL PRESSURE
        # =====================

        if "PRESSURE_DOWN" in flags:

            cvd_score -= 2

            cvd_reasons.append(
                "продавцы агрессивно давят market-ордерами"
            )

        # =====================
        # ACCELERATION
        # =====================

        if "ACCELERATION_UP" in flags:

            cvd_score += 2

            cvd_reasons.append(
                "покупатели начинают ускорять движение"
            )

        if "ACCELERATION_DOWN" in flags:

            cvd_score -= 2

            cvd_reasons.append(
                "продавцы начинают ускорять движение"
            )

        # =====================
        # ABSORPTION
        # =====================

        if "BUYER_ABSORPTION" in flags:

            cvd_score += 1

            cvd_reasons.append(
                "покупатели удерживают цену"
            )

        if "SELLER_ABSORPTION" in flags:

            cvd_score -= 1

            cvd_reasons.append(
                "продавцы удерживают цену"
            )

        # =====================
        # BREAKOUT CONFIRM
        # =====================

        if "BREAKOUT_CONFIRM_UP" in flags:

            cvd_score += 2

        if "BREAKOUT_CONFIRM_DOWN" in flags:

            cvd_score -= 2

        # =====================
        # CVD STATE
        # =====================

        if cvd_score >= 5:

            cvd_state = "STRONG_BUY_CVD"

        elif cvd_score >= 2:

            cvd_state = "BUY_CVD"

        elif cvd_score <= -5:

            cvd_state = "STRONG_SELL_CVD"

        elif cvd_score <= -2:

            cvd_state = "SELL_CVD"

        signal["cvd_score"] = cvd_score
        signal["cvd_state"] = cvd_state
        signal["cvd_reasons"] = cvd_reasons

        print(
            f"[CVD] "
            f"{signal.get('instId')} "
            f"state={cvd_state} "
            f"score={cvd_score}",
            flush=True
        )

        return signal

    except Exception as e:

        print(
            f"[CVD_ERROR] {e}",
            flush=True
        )

        signal["cvd_score"] = 0
        signal["cvd_state"] = "CVD_ERROR"
        signal["cvd_reasons"] = []

        return signal


# =========================
# LATE MOVE FILTER V2
# =========================

def analyze_late_move(signal):

    try:
        flags = set(signal.get("flags", []))

        score_penalty = 0
        late_reasons = []

        price = float(signal.get("price") or 0)
        ema20 = float(signal.get("ema20") or 0)
        exp_move_max = float(signal.get("exp_move_max") or 0)

        entry = str(signal.get("entry") or "")
        stage = str(signal.get("stage") or "")
        cvd_state = str(signal.get("cvd_state") or "")

        ema_distance_pct = 0

        if ema20 > 0:
            ema_distance_pct = abs((price - ema20) / ema20) * 100

        signal["ema_distance_pct"] = ema_distance_pct

        has_compression = (
            "RANGE_COMPRESSION" in flags
            or "TIGHT_RANGE" in flags
            or "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
        )

        has_retest_context = (
            ema_distance_pct <= 1.2
            and has_compression
        )

        # =====================
        # REAL LATE MOVE
        # =====================

        if ema_distance_pct >= 4:
            score_penalty += 7
            late_reasons.append(
                "цена критически далеко ушла от EMA20"
            )

        elif ema_distance_pct >= 3:
            score_penalty += 5
            late_reasons.append(
                "движение уже сильно растянуто"
            )

        elif ema_distance_pct >= 2:
            score_penalty += 2
            late_reasons.append(
                "движение начинает растягиваться"
            )

        # =====================
        # EXPANSION PENALTY ONLY IF NO RETEST
        # =====================

        if (
            "EXPANSION" in entry
            and exp_move_max <= 2
            and not has_retest_context
        ):
            score_penalty += 2
            late_reasons.append("часть импульса уже могла реализоваться")

        # =====================
        # RETEST PROTECTION
        # =====================

        if has_retest_context:
            score_penalty = max(score_penalty - 2, 0)
            late_reasons.append("есть retest/compression context — не считаем движение поздним")

        # =====================
        # STRONG CVD PROTECTION
        # =====================

        if cvd_state in ("STRONG_BUY_CVD", "STRONG_SELL_CVD"):
            score_penalty = max(score_penalty - 1, 0)

        signal["late_move_penalty"] = score_penalty
        signal["late_move_reasons"] = late_reasons

        if score_penalty > 0:
            before = signal.get("score", 0)
            signal["score"] -= score_penalty

            print(
                f"[LATE_MOVE_FILTER] "
                f"{signal.get('instId')} "
                f"penalty={score_penalty} "
                f"score_before={before} "
                f"score_after={signal.get('score')} "
                f"ema_distance={round(ema_distance_pct, 2)}%",
                flush=True
            )

        else:
            print(
                f"[LATE_MOVE_OK] "
                f"{signal.get('instId')} "
                f"ema_distance={round(ema_distance_pct, 2)}% "
                f"retest_context={has_retest_context}",
                flush=True
            )

        return signal

    except Exception as e:
        print(f"[LATE_MOVE_ERROR] {e}", flush=True)

        signal["late_move_penalty"] = 0
        signal["late_move_reasons"] = []

        return signal
# =========================
# RETEST + RECLAIM ENGINE
# =========================

def analyze_retest_reclaim(signal):

    try:

        flags = set(
            signal.get("flags", [])
        )

        retest_score = 0
        retest_state = "NO_RETEST"
        retest_reasons = []

        entry = str(
            signal.get("entry") or ""
        )

        stage = str(
            signal.get("stage") or ""
        )

        ema_distance = float(
            signal.get("ema_distance_pct") or 0
        )

        # =====================
        # SHORT RETEST
        # =====================

        if (
            "SHORT" in entry
            and ema_distance <= 1.2
            and "PRESSURE_DOWN" in flags
        ):

            retest_score += 2

            retest_reasons.append(
                "цена близко к EMA — возможен short retest"
            )

        if (
            "SHORT" in entry
            and "SELLER_ABSORPTION" in flags
        ):

            retest_score += 2

            retest_reasons.append(
                "продавцы продолжают удерживать цену"
            )

        if (
            "SHORT" in entry
            and "MTF_SHORT_ALIGN" in flags
        ):

            retest_score += 2

            retest_reasons.append(
                "таймфреймы поддерживают continuation вниз"
            )

        # =====================
        # LONG RETEST
        # =====================

        if (
            "LONG" in entry
            and ema_distance <= 1.2
            and "PRESSURE_UP" in flags
        ):

            retest_score += 2

            retest_reasons.append(
                "цена близко к EMA — возможен long retest"
            )

        if (
            "LONG" in entry
            and "BUYER_ABSORPTION" in flags
        ):

            retest_score += 2

            retest_reasons.append(
                "покупатели продолжают удерживать цену"
            )

        if (
            "LONG" in entry
            and "MTF_LONG_ALIGN" in flags
        ):

            retest_score += 2

            retest_reasons.append(
                "таймфреймы поддерживают continuation вверх"
            )

        # =====================
        # RETEST RECLAIM BONUS
        # =====================

        if (
            "TRANSITION" in stage
            and ema_distance <= 0.8
        ):

            retest_score += 1

            retest_reasons.append(
                "рынок близок к reclaim-фазе"
            )

        # =====================
        # FINAL STATE
        # =====================

        if retest_score >= 5:

            retest_state = "STRONG_RETEST"

        elif retest_score >= 3:

            retest_state = "RETEST_BUILDUP"

        signal["retest_score"] = retest_score
        signal["retest_state"] = retest_state
        signal["retest_reasons"] = retest_reasons

        print(
            f"[RETEST_ENGINE] "
            f"{signal.get('instId')} "
            f"state={retest_state} "
            f"score={retest_score}",
            flush=True
        )

        return signal

    except Exception as e:

        print(
            f"[RETEST_ENGINE_ERROR] {e}",
            flush=True
        )

        signal["retest_score"] = 0
        signal["retest_state"] = "RETEST_ERROR"
        signal["retest_reasons"] = []

        return signal

# =========================
# ENTRY QUALITY ENGINE V2
# =========================

def analyze_entry_quality_v2(signal):

    try:
        flags = set(signal.get("flags", []))

        entry_quality_score = 0
        entry_quality_state = "LOW_QUALITY_ENTRY"
        entry_quality_reasons = []

        entry = str(signal.get("entry") or "")
        direction = str(
            signal.get("direction_code")
            or signal.get("direction")
            or signal.get("side")
            or ""
        ).upper()

        ema_distance = float(signal.get("ema_distance_pct") or 0)
        late_penalty = float(signal.get("late_move_penalty") or 0)

        retest_state = str(signal.get("retest_state") or "")
        cvd_state = str(signal.get("cvd_state") or "")
        smart_money_state = str(signal.get("smart_money_state") or "")
        flow_state = str(signal.get("flow_state") or "")

        # =====================
        # PRICE NOT EXTENDED
        # =====================

        if ema_distance <= 0.8:
            entry_quality_score += 3
            entry_quality_reasons.append("цена близко к EMA — вход не растянут")

        elif ema_distance <= 1.5:
            entry_quality_score += 2
            entry_quality_reasons.append("дистанция до EMA допустимая")

        elif ema_distance >= 3:
            entry_quality_score -= 3
            entry_quality_reasons.append("цена далеко от EMA — вход может быть поздним")

        # =====================
        # RETEST QUALITY
        # =====================

        if retest_state == "STRONG_RETEST":
            entry_quality_score += 4
            entry_quality_reasons.append("есть сильный retest/reclaim контекст")

        elif retest_state == "RETEST_BUILDUP":
            entry_quality_score += 2
            entry_quality_reasons.append("есть retest buildup")

        # =====================
        # SMART MONEY QUALITY
        # =====================
        
        if smart_money_state == "STRONG_SMART_MONEY":
            entry_quality_score += 6
            entry_quality_reasons.append(
                "smart money сильно подтверждает сетап"
            )
        
        elif smart_money_state == "BUILDING_SMART_MONEY":
            entry_quality_score += 4
            entry_quality_reasons.append(
                "smart money начинает подтверждать сетап"
            )
        
        elif smart_money_state == "EARLY_SMART_MONEY":
            entry_quality_score += 2
            entry_quality_reasons.append(
                "появляются первые признаки smart money"
            )

        elif smart_money_state == "WEAK_SMART_MONEY":
            entry_quality_score -= 4
            entry_quality_reasons.append(
                "smart money слабый"
            )

        # =====================
        # FLOW QUALITY
        # =====================

        if flow_state == "STRONG_MONEY_FLOW":
            entry_quality_score += 3
            entry_quality_reasons.append("сильный поток денег")

        elif flow_state == "BUILDING_MONEY_FLOW":
            entry_quality_score += 2
            entry_quality_reasons.append("поток денег формируется")


        # =====================
        # STOP HUNT QUALITY
        # =====================
        
        if signal.get("stop_hunt_state") == "ACTIVE_STOP_HUNT":
        
            entry_quality_score += 4
        
            entry_quality_reasons.append(
                "обнаружен активный stop hunt"
            )
        
        elif signal.get("stop_hunt_state") == "PROBABLE_STOP_HUNT":
        
            entry_quality_score += 2
        
            entry_quality_reasons.append(
                "возможен stop hunt"
            )
        
        # =====================
        # LIQUIDITY QUALITY
        # =====================
        
        if "NEAR_LIQUIDITY_ABOVE" in flags:
        
            entry_quality_score += 3
        
            entry_quality_reasons.append(
                "ликвидность сверху очень близко"
            )
        
        if "NEAR_LIQUIDITY_BELOW" in flags:
        
            entry_quality_score += 3
        
            entry_quality_reasons.append(
                "ликвидность снизу очень близко"
            )
        # =====================
        # CVD ALIGNMENT
        # =====================

        if (
            ("SHORT" in direction or "DOWN" in direction or "SELL" in direction)
            and cvd_state in ("SELL_CVD", "STRONG_SELL_CVD")
        ):
            entry_quality_score += 2
            entry_quality_reasons.append("CVD подтверждает SHORT")

        elif (
            ("LONG" in direction or "UP" in direction or "BUY" in direction)
            and cvd_state in ("BUY_CVD", "STRONG_BUY_CVD")
        ):
            entry_quality_score += 2
            entry_quality_reasons.append("CVD подтверждает LONG")

        elif (
            ("SHORT" in direction or "DOWN" in direction or "SELL" in direction)
            and cvd_state in ("BUY_CVD", "STRONG_BUY_CVD")
        ):
            entry_quality_score -= 2
            entry_quality_reasons.append("CVD против SHORT")

        elif (
            ("LONG" in direction or "UP" in direction or "BUY" in direction)
            and cvd_state in ("SELL_CVD", "STRONG_SELL_CVD")
        ):
            entry_quality_score -= 2
            entry_quality_reasons.append("CVD против LONG")

        # =====================
        # LATE MOVE PENALTY
        # =====================

        if late_penalty > 0:
            entry_quality_score -= late_penalty
            entry_quality_reasons.append("есть штраф за позднее движение")

        # =====================
        # FINAL ENTRY QUALITY
        # =====================

        if entry_quality_score >= 10:
            entry_quality_state = "ELITE_ENTRY"

        elif entry_quality_score >= 7:
            entry_quality_state = "HIGH_QUALITY_ENTRY"

        elif entry_quality_score >= 4:
            entry_quality_state = "GOOD_ENTRY"

        elif entry_quality_score >= 2:
            entry_quality_state = "WATCH_ENTRY"

        else:
            entry_quality_state = "LOW_QUALITY_ENTRY"

        signal["entry_quality_score_v2"] = entry_quality_score
        signal["entry_quality_state_v2"] = entry_quality_state
        signal["entry_quality_reasons_v2"] = entry_quality_reasons

        print(
            f"[ENTRY_QUALITY_V2] "
            f"{signal.get('instId')} "
            f"state={entry_quality_state} "
            f"score={entry_quality_score} "
            f"ema_dist={round(ema_distance, 2)}%",
            flush=True
        )

        return signal

    except Exception as e:
        print(
            f"[ENTRY_QUALITY_V2_ERROR] {e}",
            flush=True
        )

        signal["entry_quality_score_v2"] = 0
        signal["entry_quality_state_v2"] = "ENTRY_QUALITY_ERROR"
        signal["entry_quality_reasons_v2"] = []

        return signal
# =========================
# MARKET STORY ENGINE
# =========================

def generate_market_story(signal):

    try:

        flags = set(
            signal.get("flags", [])
        )

        story = []

        # =====================
        # COMPRESSION
        # =====================

        if "RANGE_COMPRESSION" in flags:

            story.append(
                "рынок сильно сжался — готовится движение"
            )

        if "TIGHT_RANGE" in flags:

            story.append(
                "цена зажата в очень узком диапазоне"
            )

        # =====================
        # LONG CONTROL
        # =====================

        if "PRESSURE_UP" in flags:

            story.append(
                "покупатели начинают усиливать давление"
            )

        if "PRESSURE_LONG_PERSIST_2" in flags:

            story.append(
                "покупатели удерживают контроль уже некоторое время"
            )

        if "RANGE_HOLD_HIGH" in flags:

            story.append(
                "цена удерживается в верхней части диапазона"
            )

        if (
            "BUYER_ABSORPTION" in flags
            and not (
                "PRESSURE_DOWN" in flags
                and "CONTINUATION_STRONG_SHORT" in flags
            )
        ):
        
            story.append(
                "покупатели удерживают проливы"
            )

        # =====================
        # SHORT CONTROL
        # =====================

        if "PRESSURE_DOWN" in flags:

            story.append(
                "продавцы начинают усиливать давление"
            )

        if "PRESSURE_SHORT_PERSIST_2" in flags:

            story.append(
                "продавцы удерживают контроль уже некоторое время"
            )

        if "RANGE_HOLD_LOW" in flags:

            story.append(
                "цена удерживается в нижней части диапазона"
            )

        if (
            "SELLER_ABSORPTION" in flags
            and not (
                "PRESSURE_UP" in flags
                and "CONTINUATION_STRONG_LONG" in flags
            )
        ):
        
            story.append(
                "продавцы удерживают рост"
            )

        # =====================
        # ACCELERATION
        # =====================

        if "ACCELERATION_UP" in flags:

            story.append(
                "движение вверх начинает ускоряться"
            )

        if "ACCELERATION_DOWN" in flags:

            story.append(
                "движение вниз начинает ускоряться"
            )
        # =====================
        # IMPULSE CONFIRMATION
        # =====================

        if "IMPULSE_CONFIRMED_LONG" in flags:

            story.append(
                "начинается подтвержденный импульс вверх"
            )

        if "IMPULSE_CONFIRMED_SHORT" in flags:

            story.append(
                "начинается подтвержденный импульс вниз"
            )
        # =====================
        # LIQUIDATIONS
        # =====================

        if "SHORT_SQUEEZE" in flags:

            story.append(
                "шортистов начинают выбивать — это усиливает рост"
            )

        if "LONG_FLUSH" in flags:

            story.append(
                "лонгистов начинают выбивать — падение усиливается"
            )

        if "CASCADE_SHORTS" in flags:

            story.append(
                "начался каскад ликвидаций шортов"
            )

        if "CASCADE_LONGS" in flags:

            story.append(
                "начался каскад ликвидаций лонгов"
            )

        # =====================
        # ENERGY
        # =====================

        if "ENERGY_BUILDUP" in flags:

            story.append(
                "рынок выглядит напряжённым перед импульсом"
            )

        if "EXPLOSION_READY_UP" in flags:

            story.append(
                "рынок готовится к сильному движению вверх"
            )

        if "EXPLOSION_READY_DOWN" in flags:

            story.append(
                "рынок готовится к сильному движению вниз"
            )

        # =====================
        # SMART NARRATIVE
        # =====================

        smart_phase = signal.get(
            "smart_phase",
            "NEUTRAL"
        )

        smart_intent = signal.get(
            "smart_intent",
            "NEUTRAL"
        )

        flow_quality = signal.get(
            "flow_quality",
            "WEAK_FLOW"
        )

        context_grade = signal.get(
            "context_grade",
            "LOW_CONTEXT"
        )

        # =====================
        # PHASE NARRATIVE
        # =====================

        if smart_phase == "ACCUMULATION":

            story.append(
                "рынок находится в фазе накопления"
            )

        elif smart_phase == "EXPANSION":

            story.append(
                "рынок перешел в фазу активного импульса"
            )

        elif smart_phase == "DISTRIBUTION":

            story.append(
                "наблюдается возможная разгрузка позиций"
            )

        elif smart_phase == "MANIPULATION":

            story.append(
                "рынок выглядит манипулятивным и нестабильным"
            )

        elif smart_phase == "COLLAPSE":

            story.append(
                "рынок находится в фазе агрессивного слива"
            )

        # =====================
        # INTENT NARRATIVE
        # =====================

        if smart_intent == "ACCUMULATING_LONG":

            story.append(
                "крупный капитал вероятно набирает LONG позиции"
            )

        elif smart_intent == "DISTRIBUTING_LONGS":

            story.append(
                "крупный капитал вероятно фиксирует LONG позиции"
            )

        elif smart_intent == "AGGRESSIVE_EXPANSION":

            story.append(
                "движение активно поддерживается потоком капитала"
            )

        elif smart_intent == "MANIPULATING_LIQUIDITY":

            story.append(
                "рынок может собирать ликвидность через ловушки"
            )

        # =====================
        # FLOW NARRATIVE
        # =====================

        if flow_quality == "STRONG_FLOW":

            story.append(
                "движение выглядит поддержанным капиталом"
            )

        elif flow_quality == "WEAK_FLOW":

            story.append(
                "движение пока выглядит слабым по потоку денег"
            )

        # =====================
        # CONTEXT NARRATIVE
        # =====================

        if context_grade == "ELITE_CONTEXT":

            story.append(
                "контекст движения выглядит очень сильным"
            )

        elif context_grade == "HIGH_CONTEXT":

            story.append(
                "контекст движения выглядит качественным"
            )

        # =====================
        # EMPTY
        # =====================

        if not story:

            story.append(
                "рынок пока не показывает сильного преимущества одной из сторон"
            )

        return story

    except Exception as e:

        print(
            f"[MARKET_STORY_ERROR] {e}",
            flush=True
        )

        return [
            "не удалось построить описание рынка"
        ]
        


# =========================
# RANGE DETECTOR
# =========================

RANGE_MEMORY = {}

def analyze_range_behavior(symbol, candles):

    try:

        if not candles or len(candles) < 12:
            return None

        highs = []
        lows = []
        closes = []

        for c in candles[-12:]:

            try:
                h = float(c[2])
                l = float(c[3])
                cl = float(c[4])

                highs.append(h)
                lows.append(l)
                closes.append(cl)

            except:
                continue

        if len(highs) < 8:
            return None

        high_range = max(highs)
        low_range = min(lows)

        mid = (high_range + low_range) / 2

        current = closes[-1]

        range_pct = (
            (high_range - low_range)
            / max(low_range, 0.0001)
        ) * 100

        compression = False

        if range_pct <= 3.5:
            compression = True

        position = "MID"

        if current >= mid:
            position = "UPPER"

        if current <= mid:
            position = "LOWER"

        flags = []

        if compression:
            flags.append("RANGE_COMPRESSION")

        if range_pct <= 2:
            flags.append("TIGHT_RANGE")

        if position == "UPPER":
            flags.append("RANGE_HOLD_HIGH")

        if position == "LOWER":
            flags.append("RANGE_HOLD_LOW")

        return {
            "range_pct": round(range_pct, 2),
            "compression": compression,
            "position": position,
            "flags": flags
        }

    except Exception as e:

        print(
            f"[RANGE_DETECTOR_ERROR] {symbol} {e}",
            flush=True
        )

        return None
# =========================
# OI ACCELERATION ENGINE
# =========================

def analyze_oi_acceleration(symbol, oi_change):
    try:
        symbol = str(symbol)

        try:
            oi = float(oi_change or 0)
        except:
            oi = 0.0

        hist = OI_MEMORY.get(symbol, [])
        hist.append(oi)

        if len(hist) > 6:
            hist = hist[-6:]

        OI_MEMORY[symbol] = hist

        if len(hist) < 3:
            return {
                "oi_trend": "UNKNOWN",
                "oi_acceleration": 0,
                "oi_persistence": 0,
                "oi_power": 0,
                "flags": []
            }

        positive = [x for x in hist if x > 0]
        negative = [x for x in hist if x < 0]

        oi_persistence = len(positive)

        last = hist[-1]
        prev = hist[-2]
        first = hist[0]

        acceleration = last - prev
        total_growth = last - first

        flags = []
        oi_power = 0

        if oi_persistence >= 3:
            flags.append("OI_PERSISTENT_BUILDUP")
            oi_power += 2

        if acceleration > 0.08:
            flags.append("OI_ACCELERATION_UP")
            oi_power += 2

        if total_growth > 0.20:
            flags.append("OI_TREND_UP")
            oi_power += 2

        if last >= 0.30:
            flags.append("OI_STRONG_NOW")
            oi_power += 1

        if len(negative) >= 3:
            flags.append("OI_PERSISTENT_DROP")
            oi_power -= 2

        if last < 0 and prev < 0:
            flags.append("OI_CAPITAL_EXIT")
            oi_power -= 1

        if oi_power >= 5:
            oi_trend = "STRONG_BUILDUP"
        elif oi_power >= 3:
            oi_trend = "BUILDUP"
        elif oi_power <= -2:
            oi_trend = "DROPPING"
        else:
            oi_trend = "NEUTRAL"

        return {
            "oi_trend": oi_trend,
            "oi_acceleration": round(acceleration, 4),
            "oi_persistence": oi_persistence,
            "oi_power": oi_power,
            "flags": flags
        }

    except Exception as e:
        print(f"[OI_ACCEL_ERROR] {symbol} {e}", flush=True)
        return {
            "oi_trend": "ERROR",
            "oi_acceleration": 0,
            "oi_persistence": 0,
            "oi_power": 0,
            "flags": []
        }
# =========================
# ALERT MEMORY
# =========================

LAST_ALERTS = {}

# =========================
# MISSED SETUP TRACKING
# =========================

MISSED_SETUPS_FILE = "missed_setups.jsonl"

# =========================
# MISSED SETUP TRACKING
# =========================

MISSED_SETUPS_FILE = "missed_setups.jsonl"

def track_missed_setup(
    symbol,
    reason,
    sig=None,
):

    try:

        sig = sig or {}

        row = {
            "time": int(time.time()),
            "symbol": str(symbol),
            "reason": str(reason),

            "score": float(sig.get("score") or 0),
            "acc_score": float(sig.get("acc_score") or 0),
            "ep_score": float(sig.get("early_pressure_score") or 0),

            "stage": str(sig.get("stage") or ""),
            "entry": str(sig.get("entry") or ""),

            "price": float(sig.get("price") or 0),

            "flags": list(sig.get("flags") or []),
        }

        with open(
            MISSED_SETUPS_FILE,
            "a",
            encoding="utf-8"
        ) as f:

            f.write(
                json.dumps(row, ensure_ascii=False)
                + "\n"
            )

    except Exception as e:

        print(
            f"[TRACK_MISSED_ERROR] {e}",
            flush=True
        )


# =========================
# SIGNAL COOLDOWN
# =========================

SIGNAL_COOLDOWN = 900   # 15 минут

LAST_SENT = {}

def can_send(symbol, sec=SIGNAL_COOLDOWN):

    now = time.time()

    last = LAST_SENT.get(symbol, 0)

    if now - last < sec:
        return False

    LAST_SENT[symbol] = now

    return True


load_dotenv()

wall_tracker = WallTracker()

# =========================
# ENV
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

MESSAGE_MODE = (os.getenv("MESSAGE_MODE") or "AUTO").upper()   # AUTO / SHORT / MEDIUM / FULL

EDGE_MID_SCORE = int(os.getenv("EDGE_MID_SCORE") or "4")
EDGE_HIGH_SCORE = int(os.getenv("EDGE_HIGH_SCORE") or "7")

POLL_SECONDS = int(os.getenv("POLL_SECONDS") or "60")
TIMEOUT = int(os.getenv("TIMEOUT") or "12")
STATE_FILE = os.getenv("STATE_FILE") or "state.json"
RESULT_CHECK_SEC = int(os.getenv("RESULT_CHECK_SEC") or "1200")

SCAN_TOP_N = int(os.getenv("SCAN_TOP_N") or "300")
SCAN_MIN_VOL_USDT = float(os.getenv("SCAN_MIN_VOL_USDT") or "3000000")
SCAN_MIN_PCT_24H = float(os.getenv("SCAN_MIN_PCT_24H") or "2")
PREBREAK_SCAN_MAX_PCT_24H = float(os.getenv("PREBREAK_SCAN_MAX_PCT_24H") or "1.2")
PREBREAK_SCAN_MIN_PCT_24H = float(os.getenv("PREBREAK_SCAN_MIN_PCT_24H") or "0.2")

print(
    f"[CONFIG] "
    f"VOL={SCAN_MIN_VOL_USDT} "
    f"PCT={SCAN_MIN_PCT_24H} "
    f"PRE_MIN={PREBREAK_SCAN_MIN_PCT_24H} "
    f"PRE_MAX={PREBREAK_SCAN_MAX_PCT_24H} "
    f"TOP={SCAN_TOP_N}",
    flush=True
)

ALERT_MIN_SCORE = int(os.getenv("ALERT_MIN_SCORE") or "1")
ALERT_TOP_M = int(os.getenv("ALERT_TOP_M") or "8")
DETAIL_TOP_K = int(os.getenv("DETAIL_TOP_K") or "1")
MIN_SCORE = int(os.getenv("MIN_SCORE") or "1")
ONE_OPEN_SIGNAL_PER_SYMBOL = (os.getenv("ONE_OPEN_SIGNAL_PER_SYMBOL") or "1").strip() != "0"
MIN_STOP_PCT = float(os.getenv("MIN_STOP_PCT") or "0.25")
SWING_MODE = (os.getenv("SWING_MODE") or "AUTO").upper()
ENABLE_SCALP_ALERTS = (os.getenv("ENABLE_SCALP_ALERTS") or "0") == "1"   
OI_GOOD = float(os.getenv("OI_GOOD") or "0.15")
OI_STRONG = float(os.getenv("OI_STRONG") or "0.30")
OI_BAD = float(os.getenv("OI_BAD") or "-0.10")
DEBUG_VERBOSE = (
    os.getenv("DEBUG_VERBOSE") or "0"
) == "1"


# =========================
# SWING MODE (H4 / H1 / M15)
# =========================

SWING_USE_H4 = (os.getenv("SWING_USE_H4") or "1").strip() != "0"
SWING_USE_H1 = (os.getenv("SWING_USE_H1") or "1").strip() != "0"
SWING_USE_M15 = (os.getenv("SWING_USE_M15") or "1").strip() != "0"

SWING_ALERT_COOLDOWN_SEC = int(os.getenv("SWING_ALERT_COOLDOWN_SEC") or "14400")
SWING_ONE_IDEA_PER_SYMBOL = (os.getenv("SWING_ONE_IDEA_PER_SYMBOL") or "1").strip() != "0"
PREMOVE_COOLDOWN_SEC = int(os.getenv("PREMOVE_COOLDOWN_SEC") or "2700")

H4_EMA_FAST = int(os.getenv("H4_EMA_FAST") or "20")
H4_EMA_SLOW = int(os.getenv("H4_EMA_SLOW") or "50")
H4_EMA_TREND = int(os.getenv("H4_EMA_TREND") or "200")

H1_EMA_FAST = int(os.getenv("H1_EMA_FAST") or "20")
H1_EMA_SLOW = int(os.getenv("H1_EMA_SLOW") or "50")

SWING_MIN_H4_SCORE = int(os.getenv("SWING_MIN_H4_SCORE") or "3")
SWING_MIN_H1_SCORE = int(os.getenv("SWING_MIN_H1_SCORE") or "3")
SWING_MIN_TRIGGER_SCORE = int(os.getenv("SWING_MIN_TRIGGER_SCORE") or "3")

mode_now = SWING_MODE

if SWING_MODE == "AUTO":
    mode_now = "AGGRESSIVE" if ALERT_MIN_SCORE <= 3 else "SAFE"

if mode_now == "AGGRESSIVE":
    SWING_MIN_ROOM_TO_TARGET_PCT = 1.4
    SWING_MAX_STOP_PCT = 7.0
    SWING_MIN_RR = 1.8
else:
    SWING_MIN_ROOM_TO_TARGET_PCT = 1.6
    SWING_MAX_STOP_PCT = 7.0
    SWING_MIN_RR = 1.8

SWING_LATE_FROM_EMA_PCT = float(os.getenv("SWING_LATE_FROM_EMA_PCT") or "8.0")
SWING_MAX_ENTRY_ZONE_PCT = float(os.getenv("SWING_MAX_ENTRY_ZONE_PCT") or "8")
SWING_REQUIRE_STOP_OUTSIDE_ZONE = (os.getenv("SWING_REQUIRE_STOP_OUTSIDE_ZONE") or "1").strip() != "0"
SWING_BUILD_MIN_SCORE = int(os.getenv("SWING_BUILD_MIN_SCORE") or "2")


# =========================
# V2 ENV
# =========================
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC") or "1800")
EARLY_ALERT_COOLDOWN_SEC = int(os.getenv("EARLY_ALERT_COOLDOWN_SEC") or "2700")
ANTI_PUMP_PCT_5M = float(os.getenv("ANTI_PUMP_PCT_5M") or "9.0")

MANIP_ALERT_ENABLED = (os.getenv("MANIP_ALERT_ENABLED") or "1").strip() != "0"
MANIP_TOP_N = int(os.getenv("MANIP_TOP_N") or "6")
MANIP_DETAIL_TOP_K = int(os.getenv("MANIP_DETAIL_TOP_K") or "1")
MANIP_MIN_ACC_SCORE = int(os.getenv("MANIP_MIN_ACC_SCORE") or "3")
MANIP_COOLDOWN_SEC = int(os.getenv("MANIP_COOLDOWN_SEC") or "1800")

ACCUMULATION_MODE = (os.getenv("ACCUMULATION_MODE") or "0").strip() == "1"

# =========================
# V3 ENV (NEW — лучше профи)
# =========================
# Стакан (order book) — включить/выключить
ORDERBOOK_ENABLED = (os.getenv("ORDERBOOK_ENABLED") or "1").strip() != "0"
ORDERBOOK_SZ = int(os.getenv("ORDERBOOK_SZ") or "25")  # глубина стакана
ORDERBOOK_WALL_MULT = float(os.getenv("ORDERBOOK_WALL_MULT") or "2.2")  # "стена" в X раз больше среднего
ORDERBOOK_IMB_MIN = float(os.getenv("ORDERBOOK_IMB_MIN") or "0.18")  # минимальный дисбаланс (0..1)

# Sweep detector — снятие стопов (вверх/вниз) + возврат
SWEEP_LOOKBACK = int(os.getenv("SWEEP_LOOKBACK") or "20")
SWEEP_PIERCE_PCT = float(os.getenv("SWEEP_PIERCE_PCT") or "0.15")  # насколько прокол (в % от цены)
SWEEP_RECLAIM_ZONE = float(os.getenv("SWEEP_RECLAIM_ZONE") or "0.35")  # насколько закрылись обратно в диапазон

# Анти-шум пробоя: минимальная дистанция от уровня (в %)
MIN_BREAKOUT_DIST_PCT = float(os.getenv("MIN_BREAKOUT_DIST_PCT") or "0.03")

NEAR_BREAKOUT_PCT = float(os.getenv("NEAR_BREAKOUT_PCT") or "0.40")

START_MAX_DIST_PCT = float(os.getenv("START_MAX_DIST_PCT") or "1.5")

PRE_MIN_EXPECTED_MOVE_PCT = float(os.getenv("PRE_MIN_EXPECTED_MOVE_PCT") or "1.2")

USE_RSI_FILTER = int(os.getenv("USE_RSI_FILTER", "0"))

# ==============================
# 🧨 LIQUIDITY VACUUM SETTINGS
# ==============================

VAC_LOOKBACK = 12
VAC_VOL_MULT = 2.2
VAC_RANGE_COMPRESSION = 0.9
# ==============================
# PRE-BREAKOUT SETTINGS
# ==============================
PREBREAK_LOOKBACK = int(os.getenv("PREBREAK_LOOKBACK", "15"))
PREBREAK_RECENT_BARS = int(os.getenv("PREBREAK_RECENT_BARS", "3"))
PREBREAK_VOL_MULT = float(os.getenv("PREBREAK_VOL_MULT", "1.25"))
PREBREAK_RANGE_BUILD_MULT = float(os.getenv("PREBREAK_RANGE_BUILD_MULT", "1.15"))
PREBREAK_RANGE_MAX_PCT = float(os.getenv("PREBREAK_RANGE_MAX_PCT", "1.80"))
PREBREAK_EDGE_POS = float(os.getenv("PREBREAK_EDGE_POS", "0.25"))

RSI_FAST_LEN = int(os.getenv("RSI_FAST_LEN", "7"))
RSI_SLOW_LEN = int(os.getenv("RSI_SLOW_LEN", "14"))

RSI_OB_WARN = float(os.getenv("RSI_OB_WARN", "74"))
RSI_OB_BLOCK = float(os.getenv("RSI_OB_BLOCK", "80"))

RSI_OS_WARN = float(os.getenv("RSI_OS_WARN", "26"))
RSI_OS_BLOCK = float(os.getenv("RSI_OS_BLOCK", "20"))

BLOCK_AGGRESSIVE_ON_RSI_EXTREME = int(os.getenv("BLOCK_AGGRESSIVE_ON_RSI_EXTREME", "1"))

# 3-уровневый триггер
TRIGGER_PRE_ACC = int(os.getenv("TRIGGER_PRE_ACC") or "3")
TRIGGER_PRE_COOLDOWN = int(os.getenv("TRIGGER_PRE_COOLDOWN") or "1800")

TRIGGER_START_COOLDOWN = int(os.getenv("TRIGGER_START_COOLDOWN") or "1800")
START_AFTERGLOW_SEC = int(os.getenv("START_AFTERGLOW_SEC") or "3600")
TRIGGER_CONFIRM_COOLDOWN = int(os.getenv("TRIGGER_CONFIRM_COOLDOWN") or "1800")
SAFE_ENTRY_SUPPRESS_SEC = int(os.getenv("SAFE_ENTRY_SUPPRESS_SEC") or "3600")

# =========================
# PRIORITY ALERT SYSTEM (ADDON — ничего не ломает)
# =========================
PRIORITY_ENABLED = (os.getenv("PRIORITY_ENABLED") or "1").strip() != "0"
PRIORITY_SCORE_MIN = int(os.getenv("PRIORITY_SCORE_MIN") or "7")
PRIORITY_ACC_MIN = int(os.getenv("PRIORITY_ACC_MIN") or "3")
PRIORITY_COOLDOWN_SEC = int(os.getenv("PRIORITY_COOLDOWN_SEC") or "2400")

# =========================
# PRO EDGE (NEW, умеренное усиление, без удаления логики)
# =========================
PRO_EDGE_ENABLED = (os.getenv("PRO_EDGE_ENABLED") or "1").strip() != "0"
PRO_EDGE_MIN_SCORE = int(os.getenv("PRO_EDGE_MIN_SCORE") or "5")           # сильнее чем ALERT_MIN_SCORE
PRO_EDGE_MAX_ALERTS_PER_CYCLE = int(os.getenv("PRO_EDGE_MAX_ALERTS") or "4")
PRO_EDGE_MIN_RANGE_PCT = float(os.getenv("PRO_EDGE_MIN_RANGE_PCT") or "0.40")  # отсекаем супер-флет
PRO_EDGE_REQUIRE_IMPULSE = (os.getenv("PRO_EDGE_REQUIRE_IMPULSE") or "0").strip() != "0"
PRO_EDGE_REJECT_BALANCE = (os.getenv("PRO_EDGE_REJECT_BALANCE") or "1").strip() != "0"

# OKX
OKX_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OKX_BOOKS_URL = "https://www.okx.com/api/v5/market/books"  # NEW
# =========================
# EXCHANGE SWITCH (NEW)
# =========================
EXCHANGE = "BYBIT"
BYBIT_CATEGORY = "linear"

# BYBIT
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_ORDERBOOK_URL = "https://api.bybit.com/v5/market/orderbook"

# =========================
# MARKET CAP FILTER
# =========================
MARKET_CAP_MIN_USD = int(os.getenv("MARKET_CAP_MIN_USD", "150000000"))
MARKET_CAP_CACHE_TTL_SEC = int(os.getenv("MARKET_CAP_CACHE_TTL_SEC", "3600"))
COINGECKO_API_KEY = (os.getenv("COINGECKO_API_KEY") or "").strip()

_market_cap_cache = {
    "ts": 0,
    "data": {},
    "last_fail_ts": 0,
}

# =========================
# SWING TF CANDLES (H4 / H1 / M15)
# =========================
def _swing_tf_to_bybit(tf: str) -> str:
    mp = {
        "15m": "15",
        "1h": "60",
        "4h": "240",
    }
    return mp.get(tf, "60")


def _swing_tf_to_okx(tf: str) -> str:
    mp = {
        "15m": "15m",
        "1h": "1H",
        "4h": "4H",
    }
    return mp.get(tf, "1H")


def _okx_swap_symbol(instId: str) -> str:
    # BTCUSDT -> BTC-USDT-SWAP
    s = str(instId).replace("-", "").upper()
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}-USDT-SWAP"
    return instId


def _df_from_ohlcv_rows(rows, source="bybit"):
    if not rows:
        return pd.DataFrame()

    try:
        if source == "bybit":
            # Bybit v5 kline:
            # [startTime, open, high, low, close, volume, turnover]
            cols = ["ts", "open", "high", "low", "close", "volume", "turnover"]
            out = pd.DataFrame(rows, columns=cols[:len(rows[0])]).copy()
            out["ts"] = pd.to_datetime(out["ts"].astype("int64"), unit="ms", utc=True)
        else:
            # OKX candles:
            # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            cols = ["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]
            out = pd.DataFrame(rows, columns=cols[:len(rows[0])]).copy()
            out["ts"] = pd.to_datetime(out["ts"].astype("int64"), unit="ms", utc=True)

        for c in ["open", "high", "low", "close", "volume"]:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")

        out = out.sort_values("ts").reset_index(drop=True)
        return out[["ts", "open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        return pd.DataFrame()


def get_tf_candles_bybit(instId: str, tf: str = "1h", limit: int = 200) -> pd.DataFrame:
    try:
        interval = _swing_tf_to_bybit(tf)
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": str(instId).upper(),
            "interval": interval,
            "limit": int(limit),
        }
        r = requests.get(url, params=params, timeout=TIMEOUT)
        data = r.json()
        rows = (((data or {}).get("result") or {}).get("list")) or []
        return _df_from_ohlcv_rows(rows, source="bybit")
    except Exception:
        return pd.DataFrame()


def get_tf_candles_okx(instId: str, tf: str = "1h", limit: int = 200) -> pd.DataFrame:
    try:
        bar = _swing_tf_to_okx(tf)
        symbol = _okx_swap_symbol(instId)
        url = "https://www.okx.com/api/v5/market/candles"
        params = {
            "instId": symbol,
            "bar": bar,
            "limit": str(int(limit)),
        }
        r = requests.get(url, params=params, timeout=TIMEOUT)
        data = r.json()
        rows = (data or {}).get("data") or []
        return _df_from_ohlcv_rows(rows, source="okx")
    except Exception:
        return pd.DataFrame()


def get_tf_candles(instId: str, tf: str = "1h", limit: int = 200) -> pd.DataFrame:
    """
    Универсальный слой для swing-анализа.
    Сначала Bybit linear, если пусто — fallback на OKX swap.
    """
    df = get_tf_candles_bybit(instId, tf=tf, limit=limit)
    if not df.empty:
        return df
    return get_tf_candles_okx(instId, tf=tf, limit=limit)

# =========================
# SWING H4 ANALYSIS
# =========================
def _swing_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _swing_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    if df.empty or len(df) < length + 2:
        return pd.Series(dtype="float64")

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def _pct(a, b) -> float:
    try:
        a = float(a)
        b = float(b)
        if b == 0:
            return 0.0
        return (a - b) / b * 100.0
    except Exception:
        return 0.0


def analyze_h4_context(df_h4: pd.DataFrame) -> dict:
    """
    Возвращает контекст старшего ТФ:
    - bias: LONG / SHORT / NEUTRAL
    - support_zone
    - resistance_zone
    - room_to_target_pct
    - atr
    - bias_score
    """
    empty = {
        "ok": False,
        "bias": "NEUTRAL",
        "bias_score": 0,
        "support_zone": None,
        "resistance_zone": None,
        "room_to_target_pct": 0.0,
        "atr": 0.0,
        "ema20": None,
        "ema50": None,
        "ema200": None,
        "higher_low": False,
        "lower_high": False,
        "close": None,
    }

    try:
        if df_h4 is None or df_h4.empty or len(df_h4) < 60:
            return empty

        df = df_h4.copy().reset_index(drop=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema20 = _swing_ema(close, H4_EMA_FAST)
        ema50 = _swing_ema(close, H4_EMA_SLOW)
        ema200 = _swing_ema(close, H4_EMA_TREND)
        atr_s = _swing_atr(df, 14)

        last_close = float(close.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])
        last_ema200 = float(ema200.iloc[-1])
        last_atr = float(atr_s.iloc[-1]) if not atr_s.empty and pd.notna(atr_s.iloc[-1]) else 0.0

        # Структура последних двух блоков
        recent8 = df.tail(8)
        prev8 = df.tail(16).head(8) if len(df) >= 16 else df.head(0)

        higher_low = False
        lower_high = False
        if not prev8.empty and not recent8.empty:
            higher_low = float(recent8["low"].min()) > float(prev8["low"].min())
            lower_high = float(recent8["high"].max()) < float(prev8["high"].max())

        # Берём рабочие зоны не по последней свече, а по предыдущему участку
        base = df.iloc[:-2] if len(df) > 10 else df.copy()
        tail_zone = base.tail(20) if len(base) >= 20 else base

        support_raw = float(tail_zone["low"].min())
        resistance_raw = float(tail_zone["high"].max())

        zone_buf = last_atr * 0.35 if last_atr > 0 else last_close * 0.005

        support_zone = (
            round(support_raw, 6),
            round(support_raw + zone_buf, 6),
        )
        resistance_zone = (
            round(max(resistance_raw - zone_buf, 0), 6),
            round(resistance_raw, 6),
        )

        room_to_target_pct = _pct(resistance_raw, last_close) if resistance_raw > last_close else 0.0

        long_score = 0
        short_score = 0

        if last_close > last_ema50:
            long_score += 1
        else:
            short_score += 1

        if last_ema20 >= last_ema50:
            long_score += 1
        else:
            short_score += 1

        if last_ema50 >= last_ema200:
            long_score += 1
        else:
            short_score += 1

        if higher_low:
            long_score += 1

        if lower_high:
            short_score += 1

        if room_to_target_pct >= SWING_MIN_ROOM_TO_TARGET_PCT:
            long_score += 1

        bias = "NEUTRAL"
        bias_score = 0

        if long_score >= SWING_MIN_H4_SCORE and long_score >= short_score + 1:
            bias = "LONG"
            bias_score = long_score
        elif short_score >= SWING_MIN_H4_SCORE and short_score >= long_score + 1:
            bias = "SHORT"
            bias_score = short_score
        else:
            bias = "NEUTRAL"
            bias_score = max(long_score, short_score)

        return {
            "ok": True,
            "bias": bias,
            "bias_score": int(bias_score),
            "support_zone": support_zone,
            "resistance_zone": resistance_zone,
            "room_to_target_pct": round(room_to_target_pct, 2),
            "atr": round(last_atr, 6),
            "ema20": round(last_ema20, 6),
            "ema50": round(last_ema50, 6),
            "ema200": round(last_ema200, 6),
            "higher_low": higher_low,
            "lower_high": lower_high,
            "close": round(last_close, 6),
        }

    except Exception as e:
        print(f"[SWING_BUILD_ERROR H4] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return empty

# =========================
# SWING H1 SETUP ANALYSIS
# =========================
def _swing_vwap(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype="float64")

    try:
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].fillna(0.0)
        cum_vol = vol.cumsum()
        cum_tpv = (tp * vol).cumsum()
        out = cum_tpv / cum_vol.replace(0, pd.NA)
        return out.astype("float64")
    except Exception:
        return pd.Series(dtype="float64")


def analyze_h1_setup(df_h1: pd.DataFrame, h4_ctx: dict) -> dict:
    """
    Возвращает:
    - setup_type: pullback_hold / breakout_retest / late / none
    - side: LONG / SHORT / NEUTRAL
    - entry_zone
    - invalidation_level
    - setup_score
    """
    empty = {
        "ok": False,
        "side": "NEUTRAL",
        "setup_type": "none",
        "setup_score": 0,
        "entry_zone": None,
        "invalidation_level": None,
        "late": False,
        "close": None,
        "ema20": None,
        "ema50": None,
        "vwap": None,
        "atr": 0.0,
        "reason": "no_setup",
    }

    try:
        if df_h1 is None or df_h1.empty or len(df_h1) < 40:
            return empty

        if not h4_ctx or not h4_ctx.get("ok"):
            return empty

        df = df_h1.copy().reset_index(drop=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema20 = _swing_ema(close, H1_EMA_FAST)
        ema50 = _swing_ema(close, H1_EMA_SLOW)
        atr_s = _swing_atr(df, 14)
        vwap_s = _swing_vwap(df)

        last_close = float(close.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])
        last_atr = float(atr_s.iloc[-1]) if not atr_s.empty and pd.notna(atr_s.iloc[-1]) else 0.0
        last_vwap = float(vwap_s.iloc[-1]) if not vwap_s.empty and pd.notna(vwap_s.iloc[-1]) else last_close

        prev20 = df.iloc[-21:-1] if len(df) >= 21 else df.iloc[:-1]
        recent6 = df.tail(6)
        recent3 = df.tail(3)

        local_hi = float(prev20["high"].max()) if not prev20.empty else float(high.iloc[-2])
        local_lo = float(prev20["low"].min()) if not prev20.empty else float(low.iloc[-2])

        h4_bias = h4_ctx.get("bias", "NEUTRAL")
        support_zone = h4_ctx.get("support_zone")
        resistance_zone = h4_ctx.get("resistance_zone")

        # Буфер
        zone_buf = last_atr * 0.25 if last_atr > 0 else last_close * 0.004

        # Для оценки "поздно/не поздно"
        dist_from_ema20_pct = _pct(last_close, last_ema20)
        late_long = dist_from_ema20_pct > SWING_LATE_FROM_EMA_PCT
        late_short = dist_from_ema20_pct < -SWING_LATE_FROM_EMA_PCT

        # -------------------------
        # LONG setup
        # -------------------------
        if h4_bias == "LONG":
            long_score = 0
            setup_type = "none"
            entry_zone = None
            invalidation = None
            reason = "h4_long_but_no_h1_setup"

            # Pullback hold:
            # цена выше EMA50, рядом с EMA20/VWAP, структура не сломана
            pullback_hold = (
                last_close > last_ema50
                and last_close >= last_vwap * 0.995
                and float(recent6["low"].min()) >= float(low.iloc[-12:-6].min()) if len(df) >= 12 else True
            )

            # Breakout retest:
            # цена уже выше локального хая, но не улетела слишком далеко
            breakout_up = last_close > local_hi
            breakout_retest = (
                breakout_up
                and last_close > last_ema20
                and last_close > last_vwap
            )

            if last_close > last_ema50:
                long_score += 1
            if last_ema20 >= last_ema50:
                long_score += 1
            if last_close >= last_vwap:
                long_score += 1

            if pullback_hold:
                long_score += 1
                setup_type = "pullback_hold"
                base_low = float(recent6["low"].min())
                zone_low = max(min(last_ema20, last_vwap) - zone_buf, 0)
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(base_low - zone_buf, 6)
                reason = "pullback_hold"

            if breakout_retest and last_close <= local_hi * 1.03:
                long_score += 1
                setup_type = "breakout_retest"
                zone_low = max(local_hi - zone_buf, 0)
                zone_high = local_hi + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(zone_low - zone_buf, 6)
                reason = "breakout_retest"

            continuation_pullback = (
                last_close > last_ema50
                and last_ema20 >= last_ema50
                and last_close >= last_vwap * 0.997
            )

            if setup_type == "none" and continuation_pullback and long_score >= 3:
                setup_type = "pullback_hold"
                swing_low = float(recent6["low"].min())
                zone_low = max(min(last_ema20, last_vwap) - zone_buf, 0)
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(swing_low - zone_buf, 6)
                reason = "continuation_pullback"

            if late_long and setup_type == "none":
                setup_type = "late"
                reason = "late_long"

            if support_zone and setup_type == "pullback_hold":
                # дополнительно усиливаем, если зона рядом с H4 support
                sz_low, sz_high = support_zone
                if last_close >= sz_low and last_close <= sz_high * 1.03:
                    long_score += 1

            if entry_zone is not None and invalidation is not None:
                zone_low, zone_high = entry_zone
                invalidation = min(float(invalidation), float(zone_low) - zone_buf)

            return {
                "ok": True,
                "side": "LONG",
                "setup_type": setup_type if long_score >= SWING_MIN_H1_SCORE else "none",
                "setup_score": int(long_score),
                "entry_zone": entry_zone if long_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "invalidation_level": invalidation if long_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "late": bool(late_long),
                "close": round(last_close, 6),
                "ema20": round(last_ema20, 6),
                "ema50": round(last_ema50, 6),
                "vwap": round(last_vwap, 6),
                "atr": round(last_atr, 6),
                "reason": reason,
            }

        # -------------------------
        # SHORT setup
        # -------------------------
        if h4_bias == "SHORT":
            short_score = 0
            setup_type = "none"
            entry_zone = None
            invalidation = None
            reason = "h4_short_but_no_h1_setup"

            pullback_hold = (
                last_close < last_ema50
                and last_close <= last_vwap * 1.005
                and float(recent6["high"].max()) <= float(high.iloc[-12:-6].max()) if len(df) >= 12 else True
            )

            breakout_down = last_close < local_lo
            breakout_retest = (
                breakout_down
                and last_close < last_ema20
                and last_close < last_vwap
            )

            if last_close < last_ema50:
                short_score += 1
            if last_ema20 <= last_ema50:
                short_score += 1
            if last_close <= last_vwap:
                short_score += 1

            if pullback_hold:
                short_score += 1
                setup_type = "pullback_hold"
                base_high = float(recent6["high"].max())
                zone_low = min(last_ema20, last_vwap) - zone_buf
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(max(zone_low, 0), 6), round(zone_high, 6))
                invalidation = round(base_high + zone_buf, 6)
                reason = "pullback_hold"

            if breakout_retest and last_close >= local_lo * 0.97:
                short_score += 1
                setup_type = "breakout_retest"
                zone_low = max(local_lo - zone_buf, 0)
                zone_high = local_lo + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(zone_high + zone_buf, 6)
                reason = "breakout_retest"

                continuation_pullback = (
                last_close < last_ema50
                and last_ema20 <= last_ema50
                and last_close <= last_vwap * 1.003
            )

            if setup_type == "none" and continuation_pullback and short_score >= 3:
                setup_type = "pullback_hold"
                swing_high = float(recent6["high"].max())
                zone_low = min(last_ema20, last_vwap) - zone_buf
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(max(zone_low, 0), 6), round(zone_high, 6))
                invalidation = round(swing_high + zone_buf, 6)
                reason = "continuation_pullback"

            if late_short and setup_type == "none":
                setup_type = "late"
                reason = "late_short"

            if resistance_zone and setup_type == "pullback_hold":
                rz_low, rz_high = resistance_zone
                if last_close <= rz_high and last_close >= rz_low * 0.97:
                    short_score += 1

            if entry_zone is not None and invalidation is not None:
                zone_low, zone_high = entry_zone
                invalidation = max(float(invalidation), float(zone_high) + zone_buf)

            return {
                "ok": True,
                "side": "SHORT",
                "setup_type": setup_type if short_score >= SWING_MIN_H1_SCORE else "none",
                "setup_score": int(short_score),
                "entry_zone": entry_zone if short_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "invalidation_level": invalidation if short_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "late": bool(late_short),
                "close": round(last_close, 6),
                "ema20": round(last_ema20, 6),
                "ema50": round(last_ema50, 6),
                "vwap": round(last_vwap, 6),
                "atr": round(last_atr, 6),
                "reason": reason,
            }

        return empty

    except Exception:
        return empty

# =========================
# SWING M15 TRIGGER
# =========================
def analyze_m15_trigger(df_m15: pd.DataFrame, h1_setup: dict, h4_ctx: dict) -> dict:

    empty = {
        "ok": False,
        "trigger_ok": False,
        "entry_now": False,
        "trigger_type": "none",
        "trigger_score": 0,
        "micro_stop": None,
        "close": None,
        "ema20": None,
        "vwap": None,
        "atr": 0.0,
        "reason": "no_trigger",
    }

    try:
        if df_m15 is None or df_m15.empty or len(df_m15) < 30:
            return empty

        if not h1_setup or not h1_setup.get("ok"):
            return empty

        if h1_setup.get("setup_type") in ("none", "late"):
            return empty

        side = h1_setup.get("side", "NEUTRAL")
        entry_zone = h1_setup.get("entry_zone")
        invalidation = h1_setup.get("invalidation_level")

        if not entry_zone or invalidation is None or side not in ("LONG", "SHORT"):
            return empty

        df = df_m15.copy().reset_index(drop=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        ema20 = _swing_ema(close, 20)
        atr_s = _swing_atr(df, 14)
        vwap_s = _swing_vwap(df)

        last_close = float(close.iloc[-1])
        last_high = float(high.iloc[-1])
        last_low = float(low.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_vwap = float(vwap_s.iloc[-1]) if not vwap_s.empty else last_close
        last_atr = float(atr_s.iloc[-1]) if not atr_s.empty else 0.0

        prev6 = df.iloc[-7:-1] if len(df) >= 7 else df.iloc[:-1]
        recent3 = df.tail(3)

        vol_avg = float(volume.iloc[-21:-1].mean()) if len(df) >= 21 else float(volume.mean())
        vol_now = float(volume.iloc[-1])
        vol_mult = (vol_now / vol_avg) if vol_avg > 0 else 0.0

        zone_low, zone_high = entry_zone
        zone_buf = last_atr * 0.20 if last_atr > 0 else last_close * 0.003

        # =====================
        # PRO МЕТРИКИ
        # =====================
        rng = max(last_high - last_low, 1e-9)
        last_open = float(df["open"].iloc[-1])

        body = abs(last_close - last_open)
        body_ratio = body / rng

        strong_candle = body_ratio >= 0.55

        micro_range = float(high.tail(5).max()) - float(low.tail(5).min())
        compression_ready = (micro_range / last_close) <= 0.012 if last_close > 0 else False

        trigger_score = 0
        trigger_type = "none"
        trigger_ok = False
        entry_now = False
        micro_stop = None
        reason = "no_trigger"

        # =====================
        # LONG
        # =====================
        if side in ["LONG", "UP", "BUY"]:
            in_zone = (last_close >= zone_low - zone_buf) and (last_close <= zone_high + zone_buf)
            above_ema = last_close >= last_ema20
            above_vwap = last_close >= last_vwap

            local_break = not prev6.empty and last_close > float(prev6["high"].max())

            retest_hold = (
                in_zone and above_ema and above_vwap
                and float(recent3["low"].min()) > float(invalidation)
            )

            if above_ema: trigger_score += 1
            if above_vwap: trigger_score += 1
            if vol_mult >= 1.10: trigger_score += 1
            if strong_candle: trigger_score += 1
            if compression_ready: trigger_score += 1

            if retest_hold:
                trigger_score += 1
                trigger_type = "retest_hold"
                reason = "m15_retest_hold_long"

            if local_break:
                trigger_score += 1

                if compression_ready and strong_candle:
                    trigger_type = "compression_break"
                    reason = "m15_compression_break_long"
                elif trigger_type == "none":
                    trigger_type = "breakout_push"
                    reason = "m15_breakout_long"

            if trigger_score >= SWING_MIN_TRIGGER_SCORE:
                trigger_ok = True
                entry_now = True
                micro_stop = round(max(invalidation, last_low - zone_buf), 6)

        # =====================
        # SHORT
        # =====================
        if side == "SHORT":
            in_zone = (last_close >= zone_low - zone_buf) and (last_close <= zone_high + zone_buf)
            below_ema = last_close <= last_ema20
            below_vwap = last_close <= last_vwap

            local_break = not prev6.empty and last_close < float(prev6["low"].min())

            retest_hold = (
                in_zone and below_ema and below_vwap
                and float(recent3["high"].max()) < float(invalidation)
            )

            if below_ema: trigger_score += 1
            if below_vwap: trigger_score += 1
            if vol_mult >= 1.10: trigger_score += 1
            if strong_candle: trigger_score += 1
            if compression_ready: trigger_score += 1

            if retest_hold:
                trigger_score += 1
                trigger_type = "retest_hold"
                reason = "m15_retest_hold_short"

            if local_break:
                trigger_score += 1
                if trigger_type == "none":
                    trigger_type = "breakout_push"
                    reason = "m15_breakout_short"

            if trigger_score >= SWING_MIN_TRIGGER_SCORE:
                trigger_ok = True
                entry_now = True
                micro_stop = round(min(invalidation, last_high + zone_buf), 6)

        # =====================
        # FALLBACK
        # =====================
        if trigger_score >= SWING_MIN_TRIGGER_SCORE and trigger_type == "none":
            trigger_type = "momentum_ready"
            trigger_ok = True
            entry_now = True

        return {
            "ok": True,
            "trigger_ok": trigger_ok,
            "entry_now": entry_now,
            "trigger_type": trigger_type,
            "trigger_score": trigger_score,
            "micro_stop": micro_stop,
            "close": round(last_close, 6),
            "ema20": round(last_ema20, 6),
            "vwap": round(last_vwap, 6),
            "atr": round(last_atr, 6),
            "reason": reason,
        }

    except Exception:
        return empty


            
# =========================
# SWING SIGNAL BUILDER + TELEGRAM FORMAT
# =========================
def _swing_mid(zone):
    try:
        if not zone:
            return None
        a, b = zone
        return (float(a) + float(b)) / 2.0
    except Exception:
        return None


def _swing_rr(entry: float, stop: float, target: float, side: str) -> float:
    try:
        entry = float(entry)
        stop = float(stop)
        target = float(target)

        if side == "LONG":
            risk = entry - stop
            reward = target - entry
        else:
            risk = stop - entry
            reward = entry - target

        if risk <= 0:
            return 0.0
        return reward / risk
    except Exception:
        return 0.0


def _fmt_px(x):
    try:
        x = float(x)
        if x >= 1000:
            return f"{x:.2f}"
        if x >= 100:
            return f"{x:.3f}"
        if x >= 1:
            return f"{x:.4f}"
        return f"{x:.6f}"
    except Exception:
        return str(x)


def build_swing_signal(instId: str, h4_ctx: dict, h1_setup: dict, m15_trigger: dict, sig: dict = None) -> dict:
    empty = {
        "ok": False,
        "symbol": instId,
        "status": "NONE",
        "side": "NEUTRAL",
        "entry_zone": None,
        "entry_price": None,
        "stop": None,
        "tp1": None,
        "tp2": None,
        "rr1": 0.0,
        "late": False,
        "sendable": False,
        "verdict": "no_signal",
        "oi_change": None,
        "flags": [],
        "price": None,
        "instId": instId,
    }

    try:
        h4_ctx = h4_ctx or {}
        h1_setup = h1_setup or {}
        m15_trigger = m15_trigger or {}
        sig = sig or {}

        if not h4_ctx or not h4_ctx.get("ok"):
            return empty

        # =====================
        # SIDE
        # =====================
        side = h1_setup.get("side", "NEUTRAL")
        if side not in ("LONG", "SHORT"):
            side = "LONG" if h4_ctx.get("bias") == "LONG" else (
                "SHORT" if h4_ctx.get("bias") == "SHORT" else "NEUTRAL"
            )

        if side == "NEUTRAL":
            return empty

        # =====================
        # M15 CHECK
        # =====================
        m15_ready = bool(
            m15_trigger and (
                m15_trigger.get("trigger_ok") is True
                or m15_trigger.get("ok") is True
                or str(m15_trigger.get("trigger_type", "none")) not in ("none", "", "None")
            )
        )

        print(f"[M15_READY] {instId} ready={m15_ready} raw={m15_trigger}")

        if not m15_ready:
            return empty

        def detect_market_mode(m15_trigger: dict) -> str:
            atr = float(m15_trigger.get("atr") or 0)
            price = float(m15_trigger.get("close") or 0)
            ema20 = float(m15_trigger.get("ema20") or 0)
            vwap = float(m15_trigger.get("vwap") or 0)
        
            if price <= 0:
                return "НЕИЗВЕСТНО"
        
            # нормализуем
            atr_pct = atr / price * 100
            ema_dist = abs(ema20 - vwap) / price * 100
        
            # =====================
            # ЛОГИКА
            # =====================
        
            # 💤 ФЛЕТ
            if atr_pct < 0.15 and ema_dist < 0.1:
                return "ФЛЕТ"
        
            # 💣 ХАОС
            if atr_pct > 0.8:
                return "ХАОС"
        
            # 🚀 ТРЕНД
            if ema_dist > 0.2:
                return "ТРЕНД"
        
            return "НЕЯСНО"

        # =====================
        # RETEST OVERRIDE (важно)
        # =====================
        if not rt.get("ok"):

            flags = set(sig.get("flags", []))
            direction = sig.get("direction", "")

            ep = float(
                sig.get("early_pressure_score") or 0
            )

            acc = float(
                sig.get("acc_score") or 0
            )

            strong_momentum = (

                "BREAKOUT_UP" in flags
                or "CONTINUATION_UP" in flags
                or "BREAKOUT_DOWN" in flags
                or "CONTINUATION_DOWN" in flags

                or "BREAKOUT_CONFIRM_UP" in flags
                or "BREAKOUT_CONFIRM_DOWN" in flags

                or "EXPLOSION_READY_UP" in flags
                or "EXPLOSION_READY_DOWN" in flags

                or "ACCELERATION_UP" in flags
                or "ACCELERATION_DOWN" in flags

                or "LAUNCH_PROXIMITY_UP" in flags
                or "LAUNCH_PROXIMITY_DOWN" in flags

                or (
                    ep >= 10
                    and (
                        "MTF_LONG_ALIGN" in flags
                        or "MTF_SHORT_ALIGN" in flags
                    )
                )

                # =====================
                # ACCUMULATION OVERRIDE
                # =====================

                or (
                    "COMP_5M" in flags
                    and acc >= 3
                    and ep >= 10
                )

                or (
                    "COMP_PRO_5M" in flags
                    and acc >= 3
                    and ep >= 10
                )
            )

            if strong_momentum:

                print(
                    f"[RETEST_OVERRIDE] {instId} strong momentum → allow",
                    flush=True
                )

                entry = sig.get("price")

                if "ВВЕРХ" in direction:
                    stop = entry * 0.985

                elif "ВНИЗ" in direction:
                    stop = entry * 1.015

                else:
                    return empty

            else:

                print(
                    f"[RETEST_SKIP] {instId} {rt.get('reason')}",
                    flush=True
                )

                return empty

        else:

            print(
                f"[RETEST_OK] {instId}",
                flush=True
            )

            entry = rt["entry"]
            stop = rt["stop"]

        # =====================
        # RR (ОБЩИЙ ДЛЯ ВСЕХ)
        # =====================
        tp1 = sig.get("tp1")

        if tp1 is None or entry is None or stop is None:
            rr = 0
        else:
            rr = abs(tp1 - entry) / max(abs(entry - stop), 1e-9)
        
        # 🔥 ВАЖНО — СЮДА
        if rr == 0 and entry and stop:
            rr = 2.0
            print(f"[RR_FIX] {instId} fallback rr=2.0", flush=True)
        
        print(f"[RR] {instId} rr={round(rr,2)}", flush=True)


        # =====================
        # RR FILTER
        # =====================
        score = sig.get("score", 0)
        
        if rr < 1 and score < 6:
            print(f"[RR_SKIP] {instId} rr={round(rr,2)} score={score}", flush=True)
            return empty
        
        
        # =====================
        # RSI FILTER (TEMP DISABLED)
        # =====================
        
        # rsi = sig.get("rsi") or sig.get("rsi14")
        #
        # try:
        #     rsi = float(rsi)
        # except:
        #     rsi = None
        #
        # side = str(sig.get("side") or "").upper()
        #
        # if rsi is not None:
        #
        #     if side in ("LONG", "BUY") and rsi > 80:
        #         print(f"[RSI_SKIP] {instId} перегрев LONG rsi={rsi}", flush=True)
        #         return empty
        #
        #     if side in ("SHORT", "SELL") and rsi < 20:
        #         print(f"[RSI_SKIP] {instId} перепроданность SHORT rsi={rsi}", flush=True)
        #         return empty
                
        
        
                
        
        # =====================
        # OI INTELLIGENCE
        # =====================
        oi = sig.get("oi_change")

        a_plus = sig.get("smart_money_a_plus")
        
        try:
            oi = float(oi)
        except:
            oi = None
        
        score = sig.get("score", 0)
        
        oi_confirm = False
        oi_weak = False
        
        if oi is not None:
        
            # 🔥 деньги заходят
            if oi > 0.05:
                oi_confirm = True
                print(f"[OI_CONFIRM] {instId} oi={oi}", flush=True)
        
            # ⚠️ деньги выходят
            elif oi < -0.05:
                oi_weak = True
                print(f"[OI_WEAK] {instId} oi={oi}", flush=True)


        # =====================
        # OI FILTER
        # =====================
        if oi_weak and score < 6:
            print(f"[OI_SKIP] {instId} weak OI + low score", flush=True)
            return empty
        
        
        # =====================
        # OI BOOST
        # =====================
        if oi_confirm:
            sig["score"] = sig.get("score", 0) + 1
            print(f"[OI_BOOST] {instId} +1 score", flush=True)

        # =====================
        # SCORE FILTER
        # =====================
        score = float(sig.get("score") or 0)
        if score < 5:
            print(f"[SCORE_SKIP] {instId} score={score}", flush=True)
            return empty

        
        # =====================
        # RSI DIVERGENCE FILTER
        # =====================
        rsi = sig.get("rsi") or sig.get("rsi14")
        
        try:
            rsi = float(rsi)
        except:
            rsi = None
        
        prev_price = sig.get("prev_price")
        prev_rsi = sig.get("prev_rsi")
        
        try:
            prev_price = float(prev_price)
            prev_rsi = float(prev_rsi)
        except:
            prev_price = None
            prev_rsi = None
        
        price_now = float(m15_trigger.get("close") or 0)

        # =====================
        # SAVE PREVIOUS VALUES
        # =====================
        if "prev_price" not in sig:
            sig["prev_price"] = price_now
        
        if "prev_rsi" not in sig:
            sig["prev_rsi"] = rsi

        # =====================
        # LOAD PREVIOUS VALUES
        # =====================
        prev_price = sig.get("prev_price")
        prev_rsi = sig.get("prev_rsi")
        
        try:
            prev_price = float(prev_price)
            prev_rsi = float(prev_rsi)
        except:
            prev_price = None
            prev_rsi = None

        
        # LONG дивергенция (плохо для лонга)
        premove_bypass = bool(sig.get("premove_bypass"))
        side = str(
            sig.get("direction_code")
            or sig.get("side")
            or ""
        ).upper()
        if (
            not premove_bypass
            and side in ("LONG", "BUY", "UP")
        ):
            if prev_price and prev_rsi and rsi:
                if price_now > prev_price and rsi < prev_rsi:
                    print(f"[DIV_SKIP] {instId} bearish divergence", flush=True)
                    return empty
        
    
        # SHORT дивергенция (плохо для шорта)
        if side in ("SHORT", "DOWN", "SELL"):
            if prev_price and prev_rsi and rsi:
                if price_now < prev_price and rsi > prev_rsi:
                    print(f"[DIV_SKIP] {instId} bullish divergence", flush=True)
                    return empty
        
        
        # =====================
        # 👉 ВСТАВИТЬ СЮДА (DOUBLE DIVERGENCE)
        # =====================
        oi = sig.get("oi_change")
        
        try:
            oi = float(oi)
        except:
            oi = None
        
        # LONG — ослабление
        if side == "LONG":
            if prev_price and prev_rsi and rsi and oi is not None:
                if price_now > prev_price and rsi < prev_rsi and oi < 0:
                    print(f"[DOUBLE_DIV_SKIP] {instId} LONG weak", flush=True)
                    return empty
        
        # SHORT — ослабление
        if side == "SHORT":
            if prev_price and prev_rsi and rsi and oi is not None:
                if price_now < prev_price and rsi > prev_rsi and oi < 0:
                    print(f"[DOUBLE_DIV_SKIP] {instId} SHORT weak", flush=True)
                    return empty


        # =====================
        # H4 FILTER
        # =====================
        support_zone = h4_ctx.get("support_zone")
        resistance_zone = h4_ctx.get("resistance_zone")

        if side == "LONG" and resistance_zone:
            resistance = float(resistance_zone[1])
            dist = abs(resistance - entry) / entry * 100
            if dist < 0.8:
                print(f"[H4_SKIP] {instId} near resistance {round(dist,2)}%", flush=True)
                return empty

        if side == "SHORT" and support_zone:
            support = float(support_zone[0])
            dist = abs(entry - support) / entry * 100
            if dist < 0.8:
                print(f"[H4_SKIP] {instId} near support {round(dist,2)}%", flush=True)
                return empty

        

        # =====================
        # FALLBACK
        # =====================
        if m15_trigger.get("close") is not None:
            entry = float(m15_trigger.get("close"))

        if m15_trigger.get("micro_stop") is not None:
            stop = m15_trigger.get("micro_stop")

        # =====================
        # FLAT FILTER (БОКОВИК)
        # =====================
        
        ema20 = float(m15_trigger.get("ema20") or 0)
        vwap = float(m15_trigger.get("vwap") or 0)
        atr = float(m15_trigger.get("atr") or 0)
        price = float(m15_trigger.get("close") or 0)
        
        # диапазон через ATR
        range_pct = (atr / price * 100) if price > 0 else 0
        
        # расстояние между EMA и VWAP
        ema_dist = abs(ema20 - vwap) / price * 100 if price > 0 else 0
        
        # условия флета
        is_flat = (
            range_pct < 0.2   # слабое движение
            and ema_dist < 0.1  # нет тренда
        )
        
        if is_flat:
            print(f"[FLAT_SKIP] {instId} range={round(range_pct,3)} ema_dist={round(ema_dist,3)}", flush=True)
            return empty


        # =====================
        # PREMOVE BYPASS
        # =====================
        signal_mode = classify_signal_mode(sig)
        
        sig["signal_mode"] = signal_mode

        premove_bypass = (
            signal_mode == "PREMOVE"
        )

        if premove_bypass:
            print(
                f"[PREMOVE_BYPASS] {instId}",
                flush=True
            )
        # =====================
        # CONTINUATION FILTER
        # =====================
        
        close = float(m15_trigger.get("close") or 0)
        ema20 = float(m15_trigger.get("ema20") or 0)
        vwap = float(m15_trigger.get("vwap") or 0)
        
        # сила продолжения
        if sig.get("side") in ("LONG", "BUY"):
        
            # цена должна быть выше EMA и VWAP
            if close < ema20 or close < vwap:
                print(f"[CONT_SKIP] {instId} weak long continuation", flush=True)
                return empty 
        
        elif (
            not premove_bypass
            and sig.get("side") in ("SHORT", "SELL")
        ):
        
            # цена должна быть ниже EMA и VWAP
            if close > ema20 or close > vwap:
                print(f"[CONT_SKIP] {instId} weak short continuation", flush=True)
                return empty

        # =====================
        # FAKE BREAKOUT (ANTI-TRAP)
        # =====================
        
        support_zone = h4_ctx.get("support_zone")
        resistance_zone = h4_ctx.get("resistance_zone")
        
        close = float(m15_trigger.get("close") or 0)
        atr = float(m15_trigger.get("atr") or 0)
        
        buffer = atr * 0.5
        
        if sig.get("side") in ("LONG", "BUY") and resistance_zone:
            resistance = float(resistance_zone[1])
        
            if abs(resistance - close) < buffer:
                print(f"[TRAP_SKIP] {instId} near resistance trap zone", flush=True)
                return empty
        
            if close < resistance - buffer:
                print(f"[TRAP_SKIP] {instId} weak breakout", flush=True)
                return empty
        
        
        elif sig.get("side") in ("SHORT", "SELL") and support_zone:
            support = float(support_zone[0])
        
            if abs(close - support) < buffer:
                print(f"[TRAP_SKIP] {instId} near support trap zone", flush=True)
                return empty
        
            if close > support + buffer:
                print(f"[TRAP_SKIP] {instId} weak breakdown", flush=True)
                return empty

        # =====================
        # PREMOVE IMPULSE BYPASS
        # =====================
        signal_mode = classify_signal_mode(sig)

        premove_impulse_bypass = (
            signal_mode == "PREMOVE"
        )

        if premove_impulse_bypass:
            print(
                f"[PREMOVE_IMPULSE_BYPASS] {instId}",
                flush=True
            )

        # =====================
        # IMPULSE 2.0 (STRONG MOVE FILTER)
        # =====================
        atr = float(m15_trigger.get("atr") or 0)
        price = float(m15_trigger.get("close") or 0)
        ema20 = float(m15_trigger.get("ema20") or 0)
        
        # защита
        if price <= 0 or atr <= 0:
            print(f"[IMPULSE_SKIP] {instId} no data", flush=True)
            return empty
        
        # сила движения
        atr_pct = atr / price * 100
        
        # расстояние от EMA (перегрев)
        ema_dist = abs(price - ema20) / price * 100
        
        # объём
        vol = float(sig.get("volume") or sig.get("vol") or 0)
        avg_vol = float(sig.get("avg_volume") or sig.get("vol_avg") or 0)
        
        vol_ok = True
        if avg_vol > 0:
            vol_ok = vol > avg_vol * 1.3
        
        # =====================
        # УСЛОВИЯ
        # =====================
        
        # ❌ слабое движение
        if (
            not premove_impulse_bypass
            and atr_pct < 0.2
        ):
            print(f"[IMPULSE_SKIP] {instId} weak move {round(atr_pct,3)}%", flush=True)
            return empty
        
        # ❌ нет объёма
        if (
            not premove_impulse_bypass
            and not vol_ok
        ):
            print(f"[IMPULSE_SKIP] {instId} weak volume", flush=True)
            return empty
        
        # ❌ перегрев (вход в конец движения)
        if (
            not premove_impulse_bypass
            and ema_dist > 1.2
        ):
            print(f"[IMPULSE_SKIP] {instId} overextended {round(ema_dist,2)}%", flush=True)
            return empty

        
        # =====================
        # SNIPER PRO FILTER
        # =====================
        
        # базовая проверка
        if entry is None or stop is None or sig.get("tp1") is None:
            print(f"[PRO_SKIP] {instId} empty trade", flush=True)
            return empty
        
        tp1 = sig.get("tp1")
        
        # RR
        if entry and stop and tp1:
            rr = abs(tp1 - entry) / max(abs(entry - stop), 1e-9)
        else:
            rr = 0
        
        # fallback
        if rr == 0 and entry and stop:
            rr = 2.0
            print(f"[RR_FIX] {instId}", flush=True)
        
        print(f"[RR] {instId} rr={round(rr,2)}", flush=True)
        
        score = sig.get("score", 0)
        
        # RR фильтр
        if rr < 1 and score < 6:
            print(f"[PRO_SKIP] weak RR", flush=True)
            return empty
        
        
        # RSI
        rsi = sig.get("rsi") or sig.get("rsi14")
        
        try:
            rsi = float(rsi)
        except:
            rsi = None
        
        if rsi is not None:
            if side in ("LONG", "BUY") and rsi > 80:
                print(f"[PRO_SKIP] RSI high", flush=True)
                return empty
        
            if side in ("SHORT", "SELL") and rsi < 20:
                print(f"[PRO_SKIP] RSI low", flush=True)
                return empty

        # =====================
        # MARKET FILTER
        # =====================
        df_h1_phase = get_tf_candles(instId, tf="1h", limit=100)
        market_phase = detect_market_phase(df_h1_phase)
        
        phase = market_phase.get("phase")
        score = sig.get("score", 0)
        
        if phase == "FLAT":
            print(f"[FLAT_SKIP] {instId}", flush=True)
            return empty
        
        if phase == "TRANSITION" and score < 6:
            print(f"[TRANSITION_SKIP] {instId}", flush=True)
            return empty
        
        if phase == "TREND":
            sig["score"] = sig.get("score", 0) + 1
            print(f"[TREND_BOOST] {instId}", flush=True)
        
        # 🔥 ВОТ ЭТО ДОБАВЬ
        score = sig.get("score", 0)

        # =====================
        # SIGNAL CLASSIFICATION (УРОВЕНЬ)
        # =====================
        
        level = "C"
        
        # временно без money flow
        mf_ok = False  
        
        if rr >= 2 and score >= 6:
            if mf_ok and oi_confirm:
                level = "A"
            else:
                level = "B"
        
        elif rr >= 1.2 and score >= 4:
            level = "B"
        
        print(f"[LEVEL] {instId} level={level}", flush=True)
        
        
        # =====================
        # FILTER WEAK (C)
        # =====================
        if level == "C":
            print(f"[SKIP] {instId} weak signal (C)", flush=True)
            return empty
        
        
        
        # =====================
        # REPEAT FILTER
        # =====================
        
        last_score = LAST_PREMOVE_SCORE.get(instId)
        
        if (
            last_score is not None
            and abs(last_score - score) <= 2
        ):
        
            print(
                f"[PREMOVE_REPEAT_SKIP] "
                f"{instId}",
                flush=True
            )
        
            return empty
        
        # =====================
        # SEND TELEGRAM
        # =====================
        
        send_telegram(
            f"🎯 <b>RETEST ENTRY — {instId}</b>\n\n"
            f"🧭 Side: <b>{side}</b>\n"
            f"💵 Entry: <b>{entry}</b>\n"
            f"🛑 Stop: <b>{stop}</b>\n"
            f"🎯 TP1: <b>{tp1}</b>\n"
            f"📊 RR: <b>{round(rr,2)}</b>\n\n"
            f"📌 Причина: {rt.get('reason')}"
        )
        
        LAST_PREMOVE_SCORE[instId] = score
        print(
            f"[RETEST_SENT] "
            f"{instId} "
            f"score={score} "
            f"rr={round(rr,2)} "
            f"entry={entry} "
            f"stop={stop}",
            flush=True
        )
        # =====================
        # UPDATE PREVIOUS VALUES (ПОСЛЕ ОТПРАВКИ)
        # =====================
        signal["prev_price"] = price_now
        signal["prev_rsi"] = rsi
        
        return {
            "ok": True,
            "symbol": instId,
            "side": side,
            "entry_price": entry,
            "stop": stop,
            "tp1": sig.get("tp1"),
            "rr1": rr,
            "sendable": True
        }

    except Exception as e:
        print(f"[SWING_ERROR] {instId} {e}", flush=True)
        return empty

        
def format_swing_telegram(sig: dict) -> str:
    if not sig or not sig.get("ok"):
        return ""

    icon = "🧭"
    if sig.get("status") == "SWING TRIGGER":
        icon = "🚀"
    elif sig.get("status") == "SWING SETUP":
        icon = "📍"

    side_txt = "LONG" if sig.get("side") == "LONG" else "SHORT"

    support_zone = sig.get("h4_support_zone")
    resistance_zone = sig.get("h4_resistance_zone")
    entry_zone = sig.get("entry_zone")

    support_txt = "-"
    if support_zone:
        support_txt = f"{_fmt_px(support_zone[0])} → {_fmt_px(support_zone[1])}"

    resistance_txt = "-"
    if resistance_zone:
        resistance_txt = f"{_fmt_px(resistance_zone[0])} → {_fmt_px(resistance_zone[1])}"

    entry_txt = "-"
    if entry_zone:
        entry_txt = f"{_fmt_px(entry_zone[0])} → {_fmt_px(entry_zone[1])}"

    stop_txt = _fmt_px(sig["stop"]) if sig.get("stop") is not None else "-"
    tp1_txt = _fmt_px(sig["tp1"]) if sig.get("tp1") is not None else "-"
    tp2_txt = _fmt_px(sig["tp2"]) if sig.get("tp2") is not None else "-"

    return (
        f"{icon} <b>{sig.get('status')}</b> — {sig.get('symbol')}\n\n"
        f"Направление: <b>{side_txt}</b>\n"
        f"H4 Bias: <b>{sig.get('h4_bias')}</b> | score={sig.get('h4_bias_score')}\n"
        f"H1 Setup: <b>{sig.get('h1_setup_type')}</b> | score={sig.get('h1_setup_score')}\n"
        f"M15 Trigger: <b>{sig.get('m15_trigger_type')}</b> | score={sig.get('m15_trigger_score')}\n\n"
        f"H4 support: {support_txt}\n"
        f"H4 resistance: {resistance_txt}\n"
        f"Room to target: {sig.get('h4_room_to_target_pct')}%\n\n"
        f"Entry zone: {entry_txt}\n"
        f"Stop: {stop_txt}\n"
        f"TP1: {tp1_txt}\n"
        f"TP2: {tp2_txt}\n"
        f"RR1: {sig.get('rr1')}\n\n"
        f"🧠 <b>Вердикт</b>:\n{sig.get('verdict')}"
    )

def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def get_base_coin(symbol: str) -> str:
    symbol = str(symbol).upper().strip()

    if symbol.endswith("USDT"):
        return symbol[:-4]

    if symbol.endswith("-USDT"):
        return symbol[:-5]

    return symbol

def _get_coingecko_headers():
    headers = {"accept": "application/json"}

    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    return headers


def fetch_market_caps_usd(base_coins):
    """
    Возвращает словарь:
    {
        "BTC": 1800000000000,
        "ETH": 420000000000,
    }
    """

    base_coins = sorted({str(x).upper().strip() for x in base_coins if x})
    if not base_coins:
        return {}

    now = time.time()      # ← вернуть сюда
    
    
    
    # 🔥 быстрый фильтр — берем только новые монеты
    if _market_cap_cache["data"]:
        missing = [c for c in base_coins if c not in _market_cap_cache["data"]]
    else:
        missing = base_coins.copy()
    
    # если всё уже есть в кеше
    if not missing and (
        now - _market_cap_cache["ts"] < MARKET_CAP_CACHE_TTL_SEC
    ):
        return _market_cap_cache["data"]

    

    # cache hit
    if _market_cap_cache["data"] and (now - _market_cap_cache["ts"] < MARKET_CAP_CACHE_TTL_SEC):
        cached = _market_cap_cache["data"]
        if all(c in cached for c in base_coins):
            return cached

    fresh = {}
    chunk_size = 20
    request_sleep_sec = float(os.getenv("MARKET_CAP_REQUEST_SLEEP_SEC", "3.0"))
    retry_sleep_sec = float(os.getenv("MARKET_CAP_RETRY_SLEEP_SEC", "8.0"))
    max_retries = int(os.getenv("MARKET_CAP_MAX_RETRIES", "2"))

    for chunk in _chunked(missing, chunk_size):
        success = False

        for attempt in range(max_retries):
            try:
                r = requests.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "symbols": ",".join(x.lower() for x in chunk),
                        "include_tokens": "top",
                        "order": "market_cap_desc",
                        "per_page": 250,
                        "page": 1,
                    },
                    headers=_get_coingecko_headers(),
                    timeout=20,
                )

                if r.status_code == 429:
                    print(f"[MARKET_CAP] 429 retry {attempt+1}")
                    time.sleep(retry_sleep_sec * (attempt + 1))
                    continue

                r.raise_for_status()
                rows = r.json()

                for row in rows:
                    sym = str(row.get("symbol", "")).upper().strip()
                    mcap = row.get("market_cap")

                    if sym and mcap is not None:
                        try:
                            fresh[sym] = float(mcap)
                        except:
                            pass

                success = True
                break

            except Exception as e:
                print(f"[MARKET_CAP ERROR] {e}")
                time.sleep(retry_sleep_sec * (attempt + 1))

        if not success:
            print(f"[MARKET_CAP FAIL] {chunk}")

        time.sleep(request_sleep_sec)

    # обновляем кэш
    if fresh:
        merged = dict(_market_cap_cache["data"])
        merged.update(fresh)
        _market_cap_cache["data"] = merged
        _market_cap_cache["ts"] = now
        _market_cap_cache["last_fail_ts"] = 0
    else:
        _market_cap_cache["last_fail_ts"] = now

    # ✅ ВАЖНО — внутри функции
    return _market_cap_cache["data"]


# =========================
# MARKET CAP FILTER
# =========================
def is_market_cap_ok(symbol: str, market_caps: dict) -> bool:
    base = get_base_coin(symbol)
    mcap = market_caps.get(base)

    if mcap is None:
        return False

    return mcap >= MARKET_CAP_MIN_USD


def bybit_get(url, params, retries=5):

    for attempt in range(retries):

        try:

            r = requests.get(
                url,
                params=params,
                timeout=10
            )

            try:

                data = r.json()
            
            except Exception as e:
            
                print(
                    f"[BYBIT_JSON_ERROR] "
                    f"url={url} "
                    f"status={r.status_code} "
                    f"text={r.text[:200]}",
                    flush=True
                )
            
                time.sleep(1)
            
                continue

            # =====================
            # SUCCESS
            # =====================

            if data.get("retCode") == 0:
                return data

            # =====================
            # RATE LIMIT
            # =====================

            error_text = str(data).lower()

            if (
                data.get("retCode") == 10006
                or "too frequent" in error_text
                or "rate limit" in error_text
                or "access too frequent" in error_text
            ):

                sleep_sec = min(
                    5 + attempt * 3,
                    20
                )

                print(
                    f"⚠️ BYBIT RATE LIMIT "
                    f"sleep={sleep_sec}s "
                    f"attempt={attempt+1}",
                    flush=True
                )

                time.sleep(sleep_sec)

                continue

            raise RuntimeError(
                f"BYBIT bad response: {str(data)[:250]}"
            )

        except Exception as e:

            print(
                f"[BYBIT_GET_ERROR] {e}",
                flush=True
            )

            if attempt == retries - 1:
                raise

            time.sleep(2 + attempt)

    raise RuntimeError(
        "BYBIT failed after retries"
    )


def get_bybit_tickers_linear():

    try:
        res = bybit_get(
            BYBIT_TICKERS_URL,
            {"category": "linear"}
        )

        result = res.get("result") or {}
        lst = result.get("list") or []

        if lst:
            return lst

    except Exception as e:
        print(
            f"[BYBIT_TICKERS_ERROR] {e}",
            flush=True
        )

    # =====================
    # OKX TICKERS FALLBACK
    # =====================

    try:
        print(
            "[OKX_TICKERS_FALLBACK] loading OKX SWAP tickers",
            flush=True
        )

        url = "https://www.okx.com/api/v5/market/tickers"
        params = {
            "instType": "SWAP"
        }

        r = requests.get(
            url,
            params=params,
            timeout=TIMEOUT
        )

        data = r.json()
        rows = data.get("data") or []

        out = []

        for x in rows:
            inst = str(x.get("instId") or "")

            if not inst.endswith("-USDT-SWAP"):
                continue

            symbol = (
                inst.replace("-USDT-SWAP", "USDT")
                .replace("-", "")
            )

            out.append({
                "symbol": symbol,
                "turnover24h": str(x.get("volCcy24h") or x.get("vol24h") or "0"),
                "price24hPcnt": str(x.get("sodUtc8") or "0"),
            })

        if out:
            print(
                f"[OKX_TICKERS_OK] count={len(out)}",
                flush=True
            )

            return out

    except Exception as e:
        print(
            f"[OKX_TICKERS_FAILED] {e}",
            flush=True
        )

    print(
        "[TICKERS_EMERGENCY_FALLBACK] using fixed core symbols",
        flush=True
    )

    return [
        {"symbol": "BTCUSDT", "turnover24h": "1000000000", "price24hPcnt": "0"},
        {"symbol": "ETHUSDT", "turnover24h": "800000000", "price24hPcnt": "0"},
        {"symbol": "SOLUSDT", "turnover24h": "500000000", "price24hPcnt": "0"},
        {"symbol": "BNBUSDT", "turnover24h": "400000000", "price24hPcnt": "0"},
        {"symbol": "XRPUSDT", "turnover24h": "300000000", "price24hPcnt": "0"},
        {"symbol": "AVAXUSDT", "turnover24h": "200000000", "price24hPcnt": "0"},
        {"symbol": "LINKUSDT", "turnover24h": "200000000", "price24hPcnt": "0"},
        {"symbol": "INJUSDT", "turnover24h": "150000000", "price24hPcnt": "0"},
        {"symbol": "ARBUSDT", "turnover24h": "150000000", "price24hPcnt": "0"},
        {"symbol": "TONUSDT", "turnover24h": "150000000", "price24hPcnt": "0"},
    ]
def get_bybit_candles(symbol: str, interval: str, limit: int = 200):
    try:
        res = bybit_get(
            BYBIT_KLINE_URL,
            {
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": str(limit)
            }
        )

        result = res.get("result") or {}
        lst = result.get("list") or []

        if not lst or len(lst) < 30:
            print(f"⚠️ Not enough bybit candles for {symbol} {interval}")
            return []

        # Bybit returns newest -> oldest
        lst.reverse()

        candles = []
        for c in lst:
            try:
                candles.append([
                    int(c[0]),
                    float(c[1]),
                    float(c[2]),
                    float(c[3]),
                    float(c[4]),
                    float(c[5])
                ])
            except:
                continue

        if len(candles) < 30:
            print(f"⚠️ Candle parse failed {symbol} {interval}")
            return []

        return candles

    except Exception as e:

        print(
            f"❌ get_bybit_candles error "
            f"{symbol} {interval}: {e}",
            flush=True
        )
    
        # =====================
        # OKX FALLBACK
        # =====================
    
        print(
            f"[BYBIT_FALLBACK_OKX] "
            f"{symbol} interval={interval}",
            flush=True
        )
    
        try:
    
            tf_map = {
                "5": "5m",
                "15": "15m",
                "60": "1h",
                "240": "4h",
    
                "5m": "5m",
                "15m": "15m",
                "1H": "1h",
                "4H": "4h",
            }
    
            okx_tf = tf_map.get(
                str(interval),
                "15m"
            )
    
            df = get_tf_candles_okx(
                symbol,
                tf=okx_tf,
                limit=limit
            )
    
            if df is not None and not df.empty:
    
                candles = []
    
                for _, row in df.iterrows():
    
                    candles.append([
                        0,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"])
                    ])
    
                print(
                    f"[OKX_FALLBACK_OK] "
                    f"{symbol} "
                    f"tf={okx_tf} "
                    f"candles={len(candles)}",
                    flush=True
                )
    
                return candles
    
        except Exception as okx_error:
    
            print(
                f"[OKX_FALLBACK_FAILED] "
                f"{symbol} "
                f"{okx_error}",
                flush=True
            )
    
        return []

def get_bybit_books(symbol: str, limit: int = 25):
    res = bybit_get(BYBIT_ORDERBOOK_URL, {"category": "linear", "symbol": symbol, "limit": str(limit)})
    result = res.get("result") or {}
    bids = result.get("b") or []
    asks = result.get("a") or []
    try:
        bids_pq = [(float(x[0]), float(x[1])) for x in bids]
        asks_pq = [(float(x[0]), float(x[1])) for x in asks]
    except:
        return None
    if not bids_pq or not asks_pq:
        return None
    return {"bids": bids_pq, "asks": asks_pq}

is_bybit = lambda: (EXCHANGE or "OKX").upper() == "BYBIT"


def btc_symbol():
    return "BTCUSDT" if is_bybit() else "BTC-USDT"


def normalize_symbol(instId: str) -> str:
    if is_bybit():
        return instId.replace("-", "")
    return instId


def fetch_candles(instId: str, bar: str, limit: int = 120):

    if is_bybit():

        sym = normalize_symbol(instId)

        # NORMALIZE
        bar = str(bar).lower()

        if bar == "5m":
            return get_bybit_candles(sym, "5", max(200, limit))

        elif bar == "15m":
            return get_bybit_candles(sym, "15", max(200, limit))

        elif bar == "1h":
            return get_bybit_candles(sym, "60", max(200, limit))

        elif bar == "4h":
            return get_bybit_candles(sym, "240", max(200, limit))

        elif bar == "1d":
            return get_bybit_candles(sym, "D", max(200, limit))

        raise RuntimeError(f"Bybit bar not supported: {bar}")

    return get_okx_candles(instId, bar, limit)


def fetch_books(instId: str, sz: int = 25):
    if is_bybit():
        sym = normalize_symbol(instId)
        return get_bybit_books(sym, limit=sz)
    return get_okx_books(instId, sz)

# ==============================
# ⚡ BYBIT LIQUIDATIONS API
# ==============================

def fetch_bybit_liquidations(instId: str):

    if not is_bybit():
        return []

    try:
        sym = normalize_symbol(instId)

        url = "https://api.bybit.com/v5/market/liquidation"

        params = {
            "category": "linear",
            "symbol": sym,
            "limit": "20"
        }

        res = bybit_get(url, params)

        return res.get("list") or []

    except Exception:
        return []

# ==============================
# 💥 LIQUIDATION RADAR
# ==============================

def liquidation_radar(liqs):

    if not liqs:
        return None

    long_liq = 0
    short_liq = 0

    for l in liqs:

        try:
            side = l.get("side")
            size = float(l.get("size", 0))

            if side == "Sell":
                long_liq += size

            if side == "Buy":
                short_liq += size

        except:
            continue

    if long_liq > short_liq * 1.8:
        return "LONG_LIQUIDATIONS"

    if short_liq > long_liq * 1.8:
        return "SHORT_LIQUIDATIONS"

    return None
# ==============================
# 📊 BYBIT OPEN INTEREST API
# ==============================

def fetch_bybit_open_interest(instId: str, limit: int = 20):

    if not is_bybit():
        return []

    try:
        sym = normalize_symbol(instId)

        url = "https://api.bybit.com/v5/market/open-interest"

        params = {
            "category": "linear",
            "symbol": sym,
            "intervalTime": "5min",
            "limit": str(limit)
        }

        res = bybit_get(url, params)

        return res.get("list") or []

    except Exception:
        return []

# =========================
# HARD RULES / FILTERS
# =========================
EXCLUDE_TOKENS_CONTAINS = ["3L", "3S", "5L", "3M", "5M", "BULL", "BEAR", "UP", "DOWN"]
QUOTE = "USDT"

# =========================
# HTTP SESSION
# =========================
S = requests.Session()
S.headers.update({"User-Agent": "smart-money-radar/PRO-EDGE-4.0"})

# =========================
# TELEGRAM
# =========================
def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        # защита от слишком длинных сообщений
        text = str(text)[:4000]

        S.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            },
            timeout=10
        )

    except Exception as e:
        print(f"TELEGRAM ERROR: {e}")

# =========================
# STATE
# =========================
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"symbols": {}, "last_heartbeat": 0}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except:
        pass

def now_ts():
    return int(time.time())

def is_empty(c):
    return (
        c is None or
        (hasattr(c, "empty") and c.empty) or
        (isinstance(c, list) and len(c) == 0)
    )

def get_last_price(symbol: str) -> float:
    candles = fetch_candles(symbol, "5m", 2)
    if not candles:
        raise RuntimeError(f"Нет свечей для {symbol}")
    return float(candles[-1][4])

def get_open_interest(symbol):
    try:
        url = "https://api.bybit.com/v5/market/open-interest"
        params = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min"
        }

        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        rows = (((data or {}).get("result") or {}).get("list") or [])
        if len(rows) < 2:
            return None

        oi_now = float(rows[0]["openInterest"])
        oi_prev = float(rows[1]["openInterest"])

        if oi_prev <= 0:
            return None

        oi_change_pct = (oi_now - oi_prev) / oi_prev * 100.0
        return round(oi_change_pct, 2)

    except:
        return None

def direction_code_from_text(direction_text: str) -> str:
    txt = str(direction_text)

    if "ВВЕРХ" in txt:
        return "UP"

    if "ВНИЗ" in txt:
        return "DOWN"

    return "FLAT"

# =========================
# OKX HELPERS
# =========================
def okx_get(url, params):
    r = S.get(url, params=params, timeout=TIMEOUT)
    if r.status_code == 429:
        time.sleep(2.3)
        r = S.get(url, params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"OKX HTTP {r.status_code}")
    data = r.json()
    if str(data.get("code")) != "0":
        raise RuntimeError(f"OKX bad response: {str(data)[:250]}")
    return data.get("data", [])

def get_okx_spot_usdt_tickers():
    return okx_get(OKX_TICKERS_URL, {"instType": "SPOT"})

def get_okx_candles(instId: str, bar: str, limit: int = 120):
    arr = okx_get(OKX_CANDLES_URL, {"instId": instId, "bar": bar, "limit": str(limit)})
    if not isinstance(arr, list) or len(arr) < 30:
        raise RuntimeError(f"Not enough candles for {instId} {bar}")
    arr.reverse()  # old -> new
    candles = []
    for c in arr:
        try:
            candles.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        except:
            pass
    if len(candles) < 30:
        raise RuntimeError(f"Candle parse failed {instId} {bar}")
    return candles

def get_okx_books(instId: str, sz: int = 25):
    arr = okx_get(OKX_BOOKS_URL, {"instId": instId, "sz": str(sz)})
    if not arr:
        return None
    ob = arr[0]
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    try:
        bids_pq = [(float(x[0]), float(x[1])) for x in bids]
        asks_pq = [(float(x[0]), float(x[1])) for x in asks]
    except:
        return None
    if not bids_pq or not asks_pq:
        return None
    return {"bids": bids_pq, "asks": asks_pq}

# =========================
# CANDLES FEATURES (PRO MAX)
# =========================
COMPRESSION_MULT = 0.82
VOLUME_SPIKE_MULT = 1.8
FAKEDUMP_RECOVER = 0.55
FAKEDUMP_WICK_MULT = 1.8
ATR_EXPANSION_MULT = 1.3
PRESSURE_LOOKBACK = 20
PRESSURE_ZONE = 0.15
MIN_RANGE_PCT = 0.25
BREAKOUT_LOOKBACK = 12
BREAKOUT_CONFIRM_BARS = 2

def compression_ok(candles):
    if len(candles) < 20:
        return (False, False)
    highs = [x[2] for x in candles]
    lows = [x[3] for x in candles]
    ranges = [h - l for h, l in zip(highs, lows)]

    last_range = sum(ranges[-4:]) / 4.0
    prev_range = sum(ranges[-12:-4]) / 8.0
    comp = last_range < prev_range * COMPRESSION_MULT

    vols = [x[5] for x in candles]
    avg_prev = sum(vols[-20:-4]) / 16.0
    avg_last = sum(vols[-4:]) / 4.0
    vol_ok = avg_last >= avg_prev * 0.90
    return (comp and vol_ok, True)

def volume_spike_ok(candles):
    if len(candles) < 25:
        return False
    vols = [x[5] for x in candles]
    last = vols[-1]
    avg = sum(vols[-21:-1]) / 20.0
    return last > avg * VOLUME_SPIKE_MULT

def fake_dump_ok(candles):
    if len(candles) < 10:
        return False

    _, o, h, l, c, _v = candles[-1]

    rng = h - l
    if rng <= 0:
        return False

    body = abs(c - o)
    lower_wick = min(o, c) - l

    prev_lows = [x[3] for x in candles[-10:-1]]
    prev_min = min(prev_lows)

    pierced = l < prev_min * 0.997
    recovered = c > (l + rng * FAKEDUMP_RECOVER)

    wick_strong = (
        (body > 0 and lower_wick > body * FAKEDUMP_WICK_MULT)
        or (body == 0 and lower_wick > rng * 0.4)
    )

    return pierced and recovered and wick_strong


# =========================
# BREAKOUT DETECTOR
# =========================

def breakout_ok(candles, lookback=BREAKOUT_LOOKBACK):

    if len(candles) < lookback + 1:
        return None

    highs = [c[2] for c in candles[-lookback-1:-1]]
    lows = [c[3] for c in candles[-lookback-1:-1]]

    last_close = candles[-1][4]

    hi = max(highs)
    lo = min(lows)

    if last_close > hi * (1.0 + MIN_BREAKOUT_DIST_PCT / 100.0):
        return "UP"

    if last_close < lo * (1.0 - MIN_BREAKOUT_DIST_PCT / 100.0):
        return "DOWN"

    return None





# =========================
# EARLY EDGE DETECTOR
# =========================

def early_edge_detector(candles, flags):

    if len(candles) < 20:
        return False

    highs = [c[2] for c in candles[-20:]]
    lows = [c[3] for c in candles[-20:]]
    closes = [c[4] for c in candles[-20:]]

    hi = max(highs)
    lo = min(lows)

    price = closes[-1]

    range_pct = (hi - lo) / price * 100

    compression = range_pct < 1.2

    flow_up = closes[-1] > closes[-2] > closes[-3]
    flow_down = closes[-1] < closes[-2] < closes[-3]

    pressure = "PRESSURE_UP" in flags or "PRESSURE_DOWN" in flags

    if compression and pressure and (flow_up or flow_down):
        return True

    return False

# =========================
# LIQUIDITY SWEEP DETECTOR
# =========================

def liquidity_sweep_ok(candles, lookback=20):

    if len(candles) < lookback + 2:
        return None

    recent = candles[-lookback-1:-1]

    hi = max(c[2] for c in recent)
    lo = min(c[3] for c in recent)

    last = candles[-1]

    last_high = last[2]
    last_low = last[3]
    last_close = last[4]

    # сняли стопы сверху
    if last_high > hi and last_close < hi:
        return "SWEEP_UP"

    # сняли стопы снизу
    if last_low < lo and last_close > lo:
        return "SWEEP_DOWN"

    return None

def breakout_confirm_ok(
    candles,
    lookback=BREAKOUT_LOOKBACK,
    confirm_bars=BREAKOUT_CONFIRM_BARS
):

    base = candles[-(lookback + confirm_bars + 1):-(confirm_bars + 1)]

    hi = max(x[2] for x in base)
    lo = min(x[3] for x in base)

    confirm = candles[-confirm_bars:]

    body_ok_up = True
    body_ok_down = True
    
    up_closes = 0
    down_closes = 0

    for c in confirm:

        open_p = float(c[1])
        high_p = float(c[2])
        low_p = float(c[3])
        close_p = float(c[4])

        rng = high_p - low_p

        if rng <= 0:
            return None

        body = abs(close_p - open_p)
        body_ratio = body / rng

        # BODY FILTER
        if body_ratio < 0.28:
            body_ok_up = False
            body_ok_down = False

        # CLOSE FILTER
        if close_p > hi * (1.0 + MIN_BREAKOUT_DIST_PCT / 100.0):
            up_closes += 1
        
        if close_p < lo * (1.0 - MIN_BREAKOUT_DIST_PCT / 100.0):
            down_closes += 1

    # =========================
    # CONFIRMED UP
    # =========================

    if up_closes >= max(1, confirm_bars - 1) and body_ok_up:
        return "UP"

    # =========================
    # CONFIRMED DOWN
    # =========================

    if down_closes >= max(1, confirm_bars - 1) and body_ok_down:
        return "DOWN"

    return None
    
def trap_detector(candles, lookback=12):

    if len(candles) < lookback + 2:
        return None

    base = candles[-lookback-1:-1]

    hi = max(x[2] for x in base)
    lo = min(x[3] for x in base)

    last = candles[-1]

    o = last[1]
    h = last[2]
    l = last[3]
    c = last[4]

    rng = h - l

    if rng <= 0:
        return None

    body = abs(c - o)

    # bull trap
    if h > hi and c < hi and body < rng * 0.6:
        return "BULL_TRAP"

    # bear trap
    if l < lo and c > lo and body < rng * 0.6:
        return "BEAR_TRAP"

    return None


# ==============================
# STOP HUNT DETECTOR
# ==============================

def stop_hunt_detector(candles, lookback=15):

    if len(candles) < lookback + 2:
        return None

    segment = candles[-lookback-1:-1]

    hi = max(c[2] for c in segment)
    lo = min(c[3] for c in segment)

    last = candles[-1]

    o = last[1]
    h = last[2]
    l = last[3]
    c = last[4]

    rng = h - l

    if rng <= 0:
        return None

    body = abs(c - o)

    if h > hi and c < hi and body < rng * 0.6:
        return "STOP_HUNT_UP"

    if l < lo and c > lo and body < rng * 0.6:
        return "STOP_HUNT_DOWN"

    return None

# ==============================
# 🧨 LIQUIDITY VACUUM DETECTOR
# ==============================

def liquidity_vacuum_ok(candles, orderbook=None):

    try:

        if len(candles) < VAC_LOOKBACK + 3:
            return False

        vols = [float(c[5]) for c in candles[-VAC_LOOKBACK:]]
        highs = [float(c[2]) for c in candles[-VAC_LOOKBACK:]]
        lows = [float(c[3]) for c in candles[-VAC_LOOKBACK:]]

        ranges = [h - l for h, l in zip(highs, lows)]

        avg_vol = sum(vols[:-1]) / max(len(vols[:-1]), 1)
        last_vol = vols[-1]

        avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)
        last_range = ranges[-1]

        vol_spike = last_vol > avg_vol * VAC_VOL_MULT
        compression = last_range < avg_range * VAC_RANGE_COMPRESSION

        # NEW: проверяем ликвидность стакана
        ob_thin = False

        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            bid_sum = sum(q for _, q in bids)
            ask_sum = sum(q for _, q in asks)

            total_liq = bid_sum + ask_sum

            # если ликвидность маленькая → vacuum
            if total_liq > 0 and total_liq < 50000:
                ob_thin = True

        if vol_spike and compression:
            if ob_thin:
                return True

    except Exception:
        return False

    return False
# ==============================
# 🐋 WHALE ACCUMULATION DETECTOR
# ==============================

def whale_accumulation_ok(candles):

    if not candles or len(candles) < 6:
        return False

    try:
        vols = [float(c[5]) for c in candles[-6:]]
        highs = [float(c[2]) for c in candles[-6:]]
        lows = [float(c[3]) for c in candles[-6:]]

        avg_vol = sum(vols[:-1]) / max(len(vols[:-1]), 1)
        last_vol = vols[-1]

        ranges = [h - l for h, l in zip(highs, lows)]
        avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)
        last_range = ranges[-1]

        if last_vol > avg_vol * 2 and last_range < avg_range * 0.8:
            return True

    except Exception:
        return False

    return False

# ==============================
# 🐋 WHALE FLOW RADAR
# ==============================

def whale_flow_radar(candles):

    if len(candles) < 8:
        return False

    vols = [float(c[5]) for c in candles[-8:]]
    highs = [float(c[2]) for c in candles[-8:]]
    lows = [float(c[3]) for c in candles[-8:]]

    ranges = [h - l for h, l in zip(highs, lows)]

    avg_vol = sum(vols[:-1]) / max(len(vols[:-1]), 1)
    last_vol = vols[-1]

    avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)
    last_range = ranges[-1]

    volume_build = last_vol > avg_vol * 1.6
    price_hold = last_range < avg_range * 0.9

    if volume_build and price_hold:
        return True

    return False

# ==============================
# 📈 OPEN INTEREST BUILDUP
# ==============================

def open_interest_buildup(oi_series, price_series):

    if not oi_series or len(oi_series) < 5:
        return False

    try:
        last_oi = float(oi_series[-1])
        prev_oi = sum(float(x) for x in oi_series[-5:-1]) / 4.0

        oi_growth = last_oi > prev_oi * 1.03

        last_price = float(price_series[-1])
        prev_price = float(price_series[-5])

        price_change = abs(last_price - prev_price) / prev_price * 100.0

        price_flat = price_change < 0.4

        if oi_growth and price_flat:
            return True

    except Exception:
        return False

    return False



# ==============================
# ⚡ EARLY PUMP DETECTOR
# ==============================

def pump_warning(flags, score):

    if score < 6:
        return False

    compression = ("COMP_5M" in flags or "COMP_15M" in flags)

    pressure = (
        "PRESSURE_UP" in flags or
        "PRESSURE_DOWN" in flags
    )

    volume = "VOL_SPIKE" in flags
    vacuum = "LIQUIDITY_VACUUM" in flags
    whale = "WHALE_ACC" in flags

    if compression and pressure and (volume or vacuum or whale):
        return True

    return False



def atr_expansion_ok(candles, period=14, compare_back=5):
    trs = []
    for i in range(1, len(candles)):
        h = candles[i][2]
        l = candles[i][3]
        prev_close = candles[i-1][4]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    if len(trs) < period + compare_back + 2:
        return False
    atr_now = sum(trs[-period:]) / period
    atr_prev = sum(trs[-period-compare_back:-compare_back]) / period
    return atr_now > atr_prev * ATR_EXPANSION_MULT


def expected_move_pct(candles, pmeta, atr_period=14):
    range_pct = 0.0
    if pmeta and isinstance(pmeta, dict):
        try:
            range_pct = float(pmeta.get("range_pct") or 0.0)
        except:
            range_pct = 0.0

    try:
        trs = []
        for i in range(1, len(candles)):
            h = candles[i][2]
            l = candles[i][3]
            prev_close = candles[i-1][4]
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)

        if len(trs) >= atr_period and candles[-1][4] > 0:
            atr = sum(trs[-atr_period:]) / atr_period
            atr_pct = (atr / candles[-1][4]) * 100.0
        else:
            atr_pct = 0.0
    except:
        atr_pct = 0.0

    base = max(range_pct, atr_pct)

    min_move = max(0.5, base * 0.8)
    max_move = base * 1.6

    return round(min_move, 2), round(max_move, 2)

def liquidity_pressure(candles, lookback=PRESSURE_LOOKBACK, zone=PRESSURE_ZONE, min_range_pct=MIN_RANGE_PCT):
    segment = candles[-lookback-1:-1]
    hi = max(x[2] for x in segment)
    lo = min(x[3] for x in segment)
    close = candles[-1][4]
    rng = hi - lo
    if rng <= 0:
        return None, {}
    range_pct = (rng / close) * 100.0
    if range_pct < min_range_pct:
        return None, {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": None}
    pos = (close - lo) / rng
    if pos >= (1.0 - zone):
        return "UP", {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}
    if pos <= zone:
        return "DOWN", {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}
    return None, {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}

# =========================
# OPEN SIGNAL DUPLICATE CHECK
# =========================
def has_open_similar_signal(sig):
    try:
        open_signals = get_open_signals()
    except Exception:
        return False

    symbol = sig.get("symbol") or sig.get("instId")
    direction_code = sig.get("direction_code") or direction_code_from_text(sig.get("direction", ""))
    entry_type = sig.get("entry_type", sig.get("entry", "UNKNOWN"))

    for s in open_signals:
        s_symbol = s.get("symbol")
        s_direction = s.get("direction_code") or direction_code_from_text(s.get("direction", ""))
        s_entry = s.get("entry_type", s.get("entry", "UNKNOWN"))

        if s_symbol == symbol and s_direction == direction_code and s_entry == entry_type:
            return True

    return False

def has_any_open_signal_for_symbol(symbol: str) -> bool:
    try:
        open_signals = get_open_signals()
    except Exception:
        return False

    for s in open_signals:
        s_symbol = s.get("symbol") or s.get("instId")
        if s_symbol == symbol:
            return True

    return False

# =========================
# PRE-BREAKOUT BUILD-UP
# =========================
def volume_build_inside_range(
    candles,
    lookback=PREBREAK_LOOKBACK,
    recent=PREBREAK_RECENT_BARS,
    vol_mult=PREBREAK_VOL_MULT,
    range_mult=PREBREAK_RANGE_BUILD_MULT,
):
    if not candles or len(candles) < lookback + recent + 2:
        return False

    segment = candles[-(lookback + recent):-recent]
    recent_segment = candles[-recent:]

    try:
        prev_vols = [float(c[5]) for c in segment]
        last_vols = [float(c[5]) for c in recent_segment]

        prev_ranges = [float(c[2]) - float(c[3]) for c in segment]
        last_ranges = [float(c[2]) - float(c[3]) for c in recent_segment]
    except Exception:
        return False

    if not prev_vols or not last_vols or not prev_ranges or not last_ranges:
        return False

    avg_prev_vol = sum(prev_vols) / len(prev_vols)
    avg_last_vol = sum(last_vols) / len(last_vols)

    avg_prev_range = sum(prev_ranges) / len(prev_ranges)
    avg_last_range = sum(last_ranges) / len(last_ranges)

    if avg_prev_vol <= 0 or avg_prev_range <= 0:
        return False

    vol_build = avg_last_vol >= avg_prev_vol * vol_mult
    range_not_expanded = avg_last_range <= avg_prev_range * range_mult

    return vol_build and range_not_expanded


# =========================
# PRE-BREAKOUT PRESSURE FLAG
# =========================
def detect_pre_breakout_pressure(candles, flags, pmeta, ema_state):
    flags = set(flags)

    if not candles or not pmeta:
        return None

    pos = pmeta.get("pos")
    range_pct = pmeta.get("range_pct")

    if pos is None or range_pct is None:
        return None

    try:
        pos = float(pos)
        range_pct = float(range_pct)
    except Exception:
        return None

    # диапазон уже слишком широкий — это уже не тот флет
    if range_pct > PREBREAK_RANGE_MAX_PCT:
        return None

    # если уже есть подтверждённый пробой/расширение — это поздно
    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        return None

    if "ATR_EXPANSION" in flags:
        return None

    build_ok = volume_build_inside_range(candles)

    if not build_ok:
        return None

    comp_ok = ("COMP_5M" in flags) or ("COMP_15M" in flags)

    # PRE-BREAKOUT SELL
    if (
        pos <= PREBREAK_EDGE_POS
        and comp_ok
        and "PRESSURE_DOWN" in flags
        and "CONTINUATION_DOWN" in flags
        and ema_state == "EMA_BEAR"
        and "SWEEP_DOWN" not in flags
        and "FAKE_DUMP" not in flags
    ):
        return "PRE_BREAKOUT_SELL"

    # PRE-BREAKOUT BUY
    if (
        pos >= (1.0 - PREBREAK_EDGE_POS)
        and comp_ok
        and "PRESSURE_UP" in flags
        and "CONTINUATION_UP" in flags
        and ema_state == "EMA_BULL"
        and "SWEEP_UP" not in flags
    ):
        return "PRE_BREAKOUT_BUY"

    return None


# =========================
# ENTRY SIGNAL FILTER
# =========================
def is_entry_signal(s):
    if s["score"] < 6:
        return False

    if "WAIT" in str(s.get("entry", "")):
        return False

    if "SAFE ENTRY" not in str(s.get("entry", "")):
        return False

    if "ACCUMULATION" in str(s.get("stage", "")):
        return False

    if abs(s.get("up_w", 0) - s.get("down_w", 0)) < 2:
        return False

    if s.get("exp_move_max", 0) < 0.8:
        return False

    rsi_state = s.get("rsi_state")
    direction = str(s.get("direction") or "")

    if rsi_state == "EXTREME_OVERBOUGHT" and "⬆️" in direction:
        return False

    if rsi_state == "EXTREME_OVERSOLD" and "⬇️" in direction:
        return False

    oi = s.get("oi_change", None)

    if oi is not None:
        if oi <= OI_BAD:
            return False

    return True
    
def update_stats(result, move_pct, signal):

    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        stats = {
            "total": 0,
            "resolved": 0,
            "hit": 0,
            "fail": 0,
            "neutral": 0,
            "sum_move": 0,
            "by_entry": {},
            "by_stage": {}
        }

    # защита для старого stats.json
    stats.setdefault("total", 0)
    stats.setdefault("resolved", 0)
    stats.setdefault("hit", 0)
    stats.setdefault("fail", 0)
    stats.setdefault("neutral", 0)
    stats.setdefault("sum_move", 0)
    stats.setdefault("by_entry", {})
    stats.setdefault("by_stage", {})

    stats["total"] += 1
    stats["sum_move"] += move_pct

    if result == "HIT":
        stats["hit"] += 1
        stats["resolved"] += 1
    elif result == "FAIL":
        stats["fail"] += 1
        stats["resolved"] += 1
    else:
        stats["neutral"] += 1

    # 📊 по ENTRY
    entry = signal.get("entry_type", signal.get("entry", "UNKNOWN"))
    stats["by_entry"].setdefault(entry, {"total": 0, "resolved": 0, "hit": 0, "fail": 0, "neutral": 0})

    stats["by_entry"][entry]["total"] += 1

    if result == "HIT":
        stats["by_entry"][entry]["hit"] += 1
        stats["by_entry"][entry]["resolved"] += 1
    elif result == "FAIL":
        stats["by_entry"][entry]["fail"] += 1
        stats["by_entry"][entry]["resolved"] += 1
    else:
        stats["by_entry"][entry]["neutral"] += 1

    # 📊 по STAGE
    stage = signal.get("stage", "UNKNOWN")
    stats["by_stage"].setdefault(stage, {"total": 0, "resolved": 0, "hit": 0, "fail": 0, "neutral": 0})

    stats["by_stage"][stage]["total"] += 1

    if result == "HIT":
        stats["by_stage"][stage]["hit"] += 1
        stats["by_stage"][stage]["resolved"] += 1
    elif result == "FAIL":
        stats["by_stage"][stage]["fail"] += 1
        stats["by_stage"][stage]["resolved"] += 1
    else:
        stats["by_stage"][stage]["neutral"] += 1

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)
def show_stats():

    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        return "Нет данных"

    total = stats.get("total", 0)
    hit = stats.get("hit", 0)
    fail = stats.get("fail", 0)
    neutral = stats.get("neutral", max(total - hit - fail, 0))
    resolved = stats.get("resolved", hit + fail)
    avg_move = stats.get("sum_move", 0) / resolved if resolved > 0 else 0

    winrate = (hit / resolved * 100) if resolved > 0 else 0

    text = f"📊 STATS\n"
    text += f"Всего проверено: {total}\n"
    text += f"Resolved: {resolved} | Neutral: {neutral}\n"
    text += f"HIT: {hit} | FAIL: {fail}\n"
    text += f"Winrate: {round(winrate,1)}%\n"
    text += f"Avg move: {round(avg_move,2)}%\n\n"

    text += "📊 ENTRY:\n"
    for k, v in stats.get("by_entry", {}).items():
        resolved_e = v.get("resolved", v.get("hit", 0) + v.get("fail", 0))
        wr = (v.get("hit", 0) / resolved_e * 100) if resolved_e > 0 else 0
        text += f"{k}: total={v.get('total',0)} | resolved={resolved_e} | neutral={v.get('neutral',0)} | WR={round(wr,1)}%\n"

    text += "\n📊 STAGE:\n"
    for k, v in stats.get("by_stage", {}).items():
        resolved_s = v.get("resolved", v.get("hit", 0) + v.get("fail", 0))
        wr = (v.get("hit", 0) / resolved_s * 100) if resolved_s > 0 else 0
        text += f"{k}: total={v.get('total',0)} | resolved={resolved_s} | neutral={v.get('neutral',0)} | WR={round(wr,1)}%\n"

    return text

def is_profitable(signal):

    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        return True  # если нет данных — не блокируем

    entry = signal.get("entry_type", signal.get("entry", "UNKNOWN"))
    stage = signal.get("stage", "UNKNOWN")

    # проверка ENTRY
    if entry in stats.get("by_entry", {}):
        data = stats["by_entry"][entry]
        resolved = data.get("resolved", data.get("hit", 0) + data.get("fail", 0))

        if resolved >= 5:
            wr = data.get("hit", 0) / resolved
            if wr < 0.7:
                return False

    # проверка STAGE
    if stage in stats.get("by_stage", {}):
        data = stats["by_stage"][stage]
        resolved = data.get("resolved", data.get("hit", 0) + data.get("fail", 0))

        if resolved >= 5:
            wr = data.get("hit", 0) / resolved
            if wr < 0.4:
                return False

    return True

# ==============================
# NEAR BREAKOUT DETECTOR
# ==============================

def near_breakout(pmeta, price, near_pct):

    if not pmeta or not isinstance(pmeta, dict):
        return None

    hi = (pmeta or {}).get("range_hi")
    lo = (pmeta or {}).get("range_lo")

    if hi is None or lo is None:
        return None

    # расстояние до верхней границы
    dist_hi = abs(price - hi) / hi * 100

    # расстояние до нижней границы
    dist_lo = abs(price - lo) / lo * 100

    # почти пробой вверх
    if price < hi and dist_hi <= near_pct:
        return "NEAR_BREAKOUT_UP"

    # почти пробой вниз
    if price > lo and dist_lo <= near_pct:
        return "NEAR_BREAKOUT_DOWN"

    return None

# ==============================
# 📈 CVD (Cumulative Volume Delta)
# ==============================

def cvd_detector(candles, lookback=12):

    if not candles or len(candles) < lookback + 2:
        return None

    delta = 0.0
    deltas = []

    segment = candles[-lookback:]

    for c in segment:

        try:
            o = float(c[1])
            cl = float(c[4])
            vol = float(c[5])
        except:
            continue

        if cl > o:
            d = vol
        elif cl < o:
            d = -vol
        else:
            d = 0.0

        delta += d
        deltas.append(delta)

    if len(deltas) < 4:
        return None

    first = deltas[0]
    last = deltas[-1]

    change = last - first

    if abs(first) < 1e-9:
        return None

    change_pct = change / abs(first) * 100.0

    if change_pct > 25:
        return "CVD_ACCUMULATION"

    if change_pct < -25:
        return "CVD_DISTRIBUTION"

    return None

    

# ==============================
# 🎯 LIQUIDITY MAP
# ==============================

def liquidity_map(candles, lookback=40):

    if len(candles) < lookback:
        return None

    highs = [c[2] for c in candles[-lookback:]]
    lows = [c[3] for c in candles[-lookback:]]

    hi = max(highs)
    lo = min(lows)

    last = candles[-1]
    price = last[4]

    dist_up = abs(hi - price) / price * 100
    dist_down = abs(price - lo) / price * 100

    if dist_up < 0.35:
        return "STOP_CLUSTER_UP"

    if dist_down < 0.35:
        return "STOP_CLUSTER_DOWN"

    return None


def _close_from_candle(c):
    try:
        return float(c[4])
    except Exception:
        return None


def extract_closes(candles):
    closes = []
    for c in candles or []:
        v = _close_from_candle(c)
        if v is not None:
            closes.append(v)
    return closes


def calc_rsi(closes, period=14):
    if not closes or len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(abs(min(ch, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = max(ch, 0.0)
        loss = abs(min(ch, 0.0))

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_ema(values, period):
    if not values or len(values) < period:
        return None

    try:
        values = [float(x) for x in values]
    except Exception:
        return None

    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period

    for v in values[period:]:
        ema = (v * k) + (ema * (1.0 - k))

    return ema


def get_ema_trend(candles):
    closes = extract_closes(candles)

    if not closes or len(closes) < 200:
        return {
            "ema20": None,
            "ema50": None,
            "ema200": None,
            "price": None,
            "state": "EMA_UNKNOWN",
        }

    try:
        price = float(closes[-1])
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        ema200 = calc_ema(closes, 200)

        ema20_prev = calc_ema(closes[:-1], 20)
        ema50_prev = calc_ema(closes[:-1], 50)

        if ema20_prev is None or ema50_prev is None:
            raise ValueError("EMA prev is None")

        ema20_slope = ema20 - ema20_prev
        ema50_slope = ema50 - ema50_prev

        spread = abs(ema20 - ema50) / ema50 * 100

    except Exception:
        return {
            "ema20": None,
            "ema50": None,
            "ema200": None,
            "price": None,
            "state": "EMA_UNKNOWN",
        }

    if ema20 is None or ema50 is None or ema200 is None:
        return {
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "price": price,
            "state": "EMA_UNKNOWN",
        }

    # =========================
    # EMA CLASSIFICATION
    # =========================
    
    bull_stack = ema20 > ema50
    bear_stack = ema20 < ema50
    
    bull_full = ema20 > ema50 > ema200
    bear_full = ema20 < ema50 < ema200
    
    # =========================
    # STRONG BULL
    # =========================
    
    if bull_full and ema20_slope > 0:
    
        if spread > 0.15:
            state = "EMA_BULL_STRONG"
    
        else:
            state = "EMA_BULL"
    
    # =========================
    # STRONG BEAR
    # =========================
    
    elif bear_full and ema20_slope < 0:
    
        if spread > 0.08:
            state = "EMA_BEAR_STRONG"
    
        else:
            state = "EMA_BEAR"
    
    # =========================
    # EARLY BULL
    # =========================
    
    elif bull_stack and ema20_slope > 0:
    
        state = "EMA_BULL"
    
    # =========================
    # EARLY BEAR
    # =========================
    
    elif bear_stack and ema20_slope < 0:
    
        state = "EMA_BEAR"
    
    # =========================
    # FLAT
    # =========================
    
    elif spread < 0.04:
    
        state = "EMA_FLAT"
    

    
    # =========================
    # MIXED
    # =========================
    
    else:
    
        state = "EMA_MIXED"

    return {
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "price": price,
        "ema20_slope": ema20_slope,
        "ema50_slope": ema50_slope,
        "ema_spread": spread,
        "state": state,
    }


def get_rsi_state(candles):
    closes = extract_closes(candles)

    if not closes or len(closes) < max(RSI_FAST_LEN, RSI_SLOW_LEN) + 5:
        return {
            "rsi7": None,
            "rsi14": None,
            "state": "UNKNOWN",
        }

    try:
        rsi7 = calc_rsi(closes, RSI_FAST_LEN)
        rsi14 = calc_rsi(closes, RSI_SLOW_LEN)
    except Exception:
        return {
            "rsi7": None,
            "rsi14": None,
            "state": "UNKNOWN",
        }

    state = "NORMAL"

    if rsi7 is not None and rsi14 is not None:
        if rsi7 >= RSI_OB_BLOCK and rsi14 >= RSI_OB_WARN:
            state = "EXTREME_OVERBOUGHT"
        elif rsi7 <= RSI_OS_BLOCK and rsi14 <= RSI_OS_WARN:
            state = "EXTREME_OVERSOLD"
        elif rsi7 >= RSI_OB_WARN:
            state = "OVERBOUGHT"
        elif rsi7 <= RSI_OS_WARN:
            state = "OVERSOLD"

    return {
        "rsi7": rsi7,
        "rsi14": rsi14,
        "state": state,
    }


def rsi_blocks_aggressive_entry(direction, rsi_state):

    if not rsi_state:
        return False

    direction = str(direction).upper()

    state = rsi_state.get("state", "UNKNOWN")

    if direction == "UP" and state == "EXTREME_OVERBOUGHT":
        return True

    if direction == "DOWN" and state == "EXTREME_OVERSOLD":
        return True

    return False


def rsi_warns_direction(direction, rsi_state):

    if not rsi_state:
        return False

    direction = str(direction).upper()

    state = rsi_state.get("state", "UNKNOWN")

    if direction == "UP" and state in ("OVERBOUGHT", "EXTREME_OVERBOUGHT"):
        return True

    if direction == "DOWN" and state in ("OVERSOLD", "EXTREME_OVERSOLD"):
        return True

    return False

def too_late_from_range(price, pmeta, max_dist_pct=0.8):

    if not pmeta or price <= 0:
        return False

    low = (pmeta or {}).get("range_low")
    high = (pmeta or {}).get("range_high")

    if low is None or high is None:
        return False

    try:
        dist_pct = abs(price - high) / price * 100
    except Exception:
        return False

    if dist_pct > max_dist_pct:
        return True

    return False

    
def too_close_to_target(price, target, min_room_pct=0.35):
    if target is None or price <= 0:
        return False
    try:
        dist_pct = abs(float(target) - float(price)) / float(price) * 100.0
        return dist_pct < float(min_room_pct)
    except Exception:
        return False


def has_counter_book_or_trap(direction_text, flags):
    fs = set(flags)

    if "ВВЕРХ" in direction_text:
        if "OB_ASKS" in fs or "OB_WALL_ASK" in fs:
            return True
        if "SWEEP_UP" in fs:
            return True

    if "ВНИЗ" in direction_text:
        if "OB_BIDS" in fs or "OB_WALL_BID" in fs:
            return True
        if "FAKE_DUMP" in fs or "SWEEP_DOWN" in fs:
            return True

    return False


# =========================
# V3: ORDERBOOK EDGE (NEW)
# =========================
def orderbook_edge(instId: str):
    if not ORDERBOOK_ENABLED:
        return None

    ob = None
    try:
        ob = fetch_books(instId, ORDERBOOK_SZ)
    except:
        return None

    if not ob:
        return None

    bids = ob["bids"]
    asks = ob["asks"]

    bid_sum = sum(q for _p, q in bids)
    ask_sum = sum(q for _p, q in asks)
    total = bid_sum + ask_sum
    if total <= 0:
        return None

    imb = (bid_sum - ask_sum) / total
    imb_abs = abs(imb)

    bid_sizes = [q for _p, q in bids]
    ask_sizes = [q for _p, q in asks]

    bid_avg = sum(bid_sizes) / max(len(bid_sizes), 1)
    ask_avg = sum(ask_sizes) / max(len(ask_sizes), 1)

    ask_wall_size = max(ask_sizes) if ask_sizes else 0
    wall_state = wall_tracker.check_wall(ask_wall_size)

    wall_removed = wall_state.get("wall_removed", False)

    bid_wall = max(bid_sizes) > bid_avg * ORDERBOOK_WALL_MULT if bid_avg > 0 else False
    ask_wall = max(ask_sizes) > ask_avg * ORDERBOOK_WALL_MULT if ask_avg > 0 else False

    if imb_abs < ORDERBOOK_IMB_MIN and not (bid_wall or ask_wall):

        return {
            "ob_bias": "NEUTRAL",
            "imb": imb,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_removed": wall_removed
        }

    if imb > ORDERBOOK_IMB_MIN or bid_wall:

        return {
            "ob_bias": "BIDS",
            "imb": imb,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_removed": wall_removed
        }

    if imb < -ORDERBOOK_IMB_MIN or ask_wall:

        return {
            "ob_bias": "ASKS",
            "imb": imb,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_removed": wall_removed
        }

    return {
        "ob_bias": "NEUTRAL",
        "imb": imb,
        "bid_wall": bid_wall,
        "ask_wall": ask_wall,
        "wall_removed": wall_removed
    }
# =========================
# V3: SWEEP DETECTOR (NEW)
# =========================
def liquidity_sweep(candles, lookback=SWEEP_LOOKBACK):
    if len(candles) < lookback + 2:
        return None, {}

    seg = candles[-lookback-1:-1]
    hi = max(x[2] for x in seg)
    lo = min(x[3] for x in seg)

    _ts, o, h, l, c, _v = candles[-1]
    pierce_up = h > hi * (1.0 + SWEEP_PIERCE_PCT / 100.0)
    pierce_dn = l < lo * (1.0 - SWEEP_PIERCE_PCT / 100.0)

    rng = hi - lo
    if rng <= 0:
        return None, {"hi": hi, "lo": lo}

    reclaim_up = c < (hi - rng * SWEEP_RECLAIM_ZONE)
    reclaim_dn = c > (lo + rng * SWEEP_RECLAIM_ZONE)

    if pierce_up and reclaim_up:
        return "SWEEP_UP", {"hi": hi, "lo": lo, "close": c}
    if pierce_dn and reclaim_dn:
        return "SWEEP_DOWN", {"hi": hi, "lo": lo, "close": c}

    return None, {"hi": hi, "lo": lo, "close": c}

# =========================
# V2: Anti-pump + Accumulation score
# =========================
def anti_pump_penalty(candles, threshold_pct):
    if len(candles) < 2:
        return 0
    prev_close = candles[-2][4]
    last_close = candles[-1][4]
    if prev_close <= 0:
        return 0
    pct = (last_close - prev_close) / prev_close * 100.0
    if abs(pct) >= threshold_pct:
        return -1
    return 0

def accumulation_bias(flags):
    s = 0
    if "COMP_5M" in flags:
        s += 1
    if "COMP_15M" in flags:
        s += 1
    if "PRESSURE_UP" in flags or "PRESSURE_DOWN" in flags:
        s += 1
    if "ATR_EXPANSION" not in flags:
        s += 1
    return s

# =========================
# DIRECTION / ENTRY / STAGE / TARGET
# =========================
def direction_hint(flags):
    up = 0
    down = 0
    reasons = []


    if "BREAKOUT_UP" in flags:
        up += 2; reasons.append("Пробой ВВЕРХ (+2)")
    if "BREAKOUT_DOWN" in flags:
        down += 2; reasons.append("Пробой ВНИЗ (+2)")

    if "PRESSURE_UP" in flags:
        up += 1; reasons.append("Давление к верху (+1)")
        
    # SMART MONEY ABSORPTION
    if "BUYER_ABSORPTION" in flags:
        up += 4
        reasons.append("Поглощение продавца (+4)")

    if "SELLER_ABSORPTION" in flags:
        down += 4
        reasons.append("Поглощение покупателя (+4)")

    # SHIFT
    if "BULLISH_SHIFT" in flags:
        up += 4
        reasons.append("Смена контроля в LONG (+4)")

    if "BEARISH_SHIFT" in flags:
        down += 4
        reasons.append("Смена контроля в SHORT (+4)")

    # ACCELERATION
    if "ACCELERATION_UP" in flags:
        up += 4
        reasons.append("Ускорение вверх (+4)")

    if "ACCELERATION_DOWN" in flags:
        down += 4
        reasons.append("Ускорение вниз (+4)")

    # LAUNCH PROXIMITY
    if "LAUNCH_PROXIMITY_UP" in flags:
        up += 5
        reasons.append("Близость запуска вверх (+5)")

    if "LAUNCH_PROXIMITY_DOWN" in flags:
        down += 5
        reasons.append("Близость запуска вниз (+5)")

    # EXPLOSION READY
    if "EXPLOSION_READY_UP" in flags:
        up += 5
        reasons.append("Готовность к импульсу вверх (+5)")

    if "EXPLOSION_READY_DOWN" in flags:
        down += 5
        reasons.append("Готовность к импульсу вниз (+5)")
    if "CONTINUATION_UP" in flags:
        up += 2; reasons.append("Продолжение ВВЕРХ (+2)")
    if "CONTINUATION_DOWN" in flags:
        down += 2; reasons.append("Продолжение ВНИЗ (+2)")
    if "PRESSURE_DOWN" in flags:
        down += 1; reasons.append("Давление к низу (+1)")

    if "FAKE_DUMP" in flags:
        up += 1; reasons.append("Снятие стопов вниз (+1)")

    if "SWEEP_UP" in flags:
        down += 1; reasons.append("Снятие стопов вверх (часто разворот вниз) (+1)")
    if "SWEEP_DOWN" in flags:
        up += 1; reasons.append("Снятие стопов вниз (часто разворот вверх) (+1)")

    if "VOL_SPIKE" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags or "PRESSURE_UP" in flags):
        up += 1; reasons.append("Объём поддержал ВВЕРХ (+1)")
    if "VOL_SPIKE" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags or "PRESSURE_DOWN" in flags):
        down += 1; reasons.append("Объём поддержал ВНИЗ (+1)")

    if "ATR_EXPANSION" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags):
        up += 1; reasons.append("ATR ускорил ВВЕРХ (+1)")
    if "ATR_EXPANSION" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags):
        down += 1; reasons.append("ATR ускорил ВНИЗ (+1)")

    if "OB_BIDS" in flags:
        up += 0.5; reasons.append("Стакан: перевес BID (+0.5)")
    if "OB_ASKS" in flags:
        down += 0.5; reasons.append("Стакан: перевес ASK (+0.5)")
    if "OB_WALL_BID" in flags:
        up += 0.5; reasons.append("Стена BID (+0.5)")
    if "OB_WALL_ASK" in flags:
        down += 0.5; reasons.append("Стена ASK (+0.5)")

    # STRONG LONG
    if up >= down + 2:
        return "⬆️ ВВЕРХ", reasons, up, down
    
    # STRONG SHORT
    if down >= up + 2:
        return "⬇️ ВНИЗ", reasons, up, down
    
    # LIGHT LONG BIAS
    if up > down:
        return "🟢 LONG BIAS", reasons, up, down
    
    # LIGHT SHORT BIAS
    if down > up:
        return "🔴 SHORT BIAS", reasons, up, down
    
    # BALANCE
    return "⚖️ БАЛАНС", reasons, up, down

def decision_engine(sig):

    score = sig.get("score", 0)
    acc = sig.get("acc_score", 0)
    oi = sig.get("oi_change")
    ema = sig.get("ema_state")
    rsi = sig.get("rsi_state")
    stage = sig.get("stage", "")
    flags = set(sig.get("flags", []))

    confidence = 0

    confidence += score * 1.2
    confidence += acc * 0.8

    if oi is not None:
        if oi >= OI_STRONG:
            confidence += 2
        elif oi >= OI_GOOD:
            confidence += 1
        elif oi <= OI_BAD:
            confidence -= 2

    if "ВВЕРХ" in sig.get("direction", "") and ema == "EMA_BULL":
        confidence += 1
    if "ВНИЗ" in sig.get("direction", "") and ema == "EMA_BEAR":
        confidence += 1

    if "ATR_EXPANSION" in flags and "VOL_SPIKE" in flags:
        confidence += 2

    if "SWEEP_UP" in flags or "SWEEP_DOWN" in flags:
        confidence += 1

    if rsi in ("EXTREME_OVERBOUGHT", "EXTREME_OVERSOLD"):
        confidence -= 1

    if confidence >= 10:
        return "ELITE"
    elif confidence >= 7:
        return "STRONG"
    elif confidence >= 5:
        return "NORMAL"
    else:
        return "WEAK"

# =========================
# 🔥 PRO QUALITY FILTER V2
# =========================
def signal_quality_filter(sig):

    flags = set(sig.get("flags", []))
    score = float(sig.get("score", 0))
    acc = int(sig.get("acc_score", 0))
    stage = str(sig.get("stage", ""))
    entry = str(sig.get("entry", ""))
    ep_score = float(sig.get("early_pressure_score") or 0)
    ep_label = sig.get("early_pressure_label")
    pre_move = sig.get("pre_move")

    # =====================
    # ЖЁСТКИЙ МУСОР
    # =====================
    if score <= 0 and acc == 0:
        return False, "trash_signal"

    

    # =====================
    # 🟢 SAFE / ENTRY — пропускаем
    # =====================
    if "SAFE" in entry:
        return True, "safe_entry"

    # =====================
    # 🟠 PRE-MOVE — ДО ДВИЖЕНИЯ
    # =====================
    
    if pre_move:
        return True, "pre_move_entry"
    
    # =====================
    # 🟠 EARLY PRESSURE — SMART FILTER
    # =====================
    
    real_impulse = (
    
        "VOL_SPIKE" in flags
        or "ATR_EXPANSION" in flags
        or "BREAKOUT_CONFIRM_UP" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
        or "CONTINUATION_UP" in flags
        or "CONTINUATION_DOWN" in flags
    )
    
    compression_context = (
    
        (
            "COMP_5M" in flags
            or "COMP_15M" in flags
        )
    
        and (
    
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    )
    
    # =====================
    # 🟠 EARLY PRESSURE PASS
    # =====================
    
    if (
    
        ep_score >= 7
    
        and (
    
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
    
            or "EMA_BULL_STRONG" in flags
            or "EMA_BEAR_STRONG" in flags
    
            or real_impulse
            or compression_context
    
            or "BULLISH_SHIFT" in flags
            or "BEARISH_SHIFT" in flags
        )
    ):
    
        return True, "early_pressure"
    
    # =====================
    # 🟠 EARLY PRESSURE — SMART FILTER V2
    # =====================
    
    weak_trend_only = (
    
        (
            "PRESSURE_UP" in flags
            and "EMA_BULL" in flags
            and "EMA_BULL_STRONG" not in flags
            and "MTF_LONG_ALIGN" not in flags
            and not real_impulse
            and not compression_context
        )
    
        or
    
        (
            "PRESSURE_DOWN" in flags
            and "EMA_BEAR" in flags
            and "EMA_BEAR_STRONG" not in flags
            and "MTF_SHORT_ALIGN" not in flags
            and not real_impulse
            and not compression_context
        )
    )
    
    if weak_trend_only:
    
        allow_context = (
    
            real_impulse
            or compression_context
    
            or "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
    
            or acc >= 3
        )
    
        if not allow_context:
    
            return False, "weak_trend_only"
    
    # =====================
    # 🌍 MARKET REGIME FILTER
    # =====================
    
    market_mode = str(sig.get("market_mode", "NEUTRAL"))
    
    direction_long = (
        "PRESSURE_UP" in flags
        or "BREAKOUT_UP" in flags
        or "BREAKOUT_CONFIRM_UP" in flags
    )
    
    direction_short = (
        "PRESSURE_DOWN" in flags
        or "BREAKOUT_DOWN" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
    )
    
    # BULL MARKET
    if market_mode == "BULL":
    
        if direction_short and score < 6:
            return False, "blocked_by_bull_market"
    
    # BEAR MARKET
    elif market_mode == "BEAR":
    
        if direction_long and score < 6:
            return False, "blocked_by_bear_market"

    # =====================
    # 🟣 ACCUMULATION / TRANSITION
    # =====================
    if acc >= 2 and (
        "ACCUMULATION" in stage or
        "TRANSITION" in stage
    ):
    
        if (
            "COMP_5M" in flags
            or "COMP_15M" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
        ):
    
            return True, "accumulation_context"

    # =====================
    # 📊 STRUCTURE PASS
    # =====================
    
    real_structure = (
    
        # breakout
        "BREAKOUT_UP" in flags
        
       
    
        # compression + pressure
        or (
            (
                "COMP_5M" in flags
                or "COMP_15M" in flags
            )
            and (
                "PRESSURE_UP" in flags
                or "PRESSURE_DOWN" in flags
            )
        )
        # continuation
        or "CONTINUATION_UP" in flags
        or "CONTINUATION_DOWN" in flags
    )
    # =====================
    # 🔥 ELITE PREMOVE PASS
    # =====================
    
    if (
    
        ep_score >= 12
        and acc >= 3
    
        and (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    
        and (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        )
    
        and (
            "COMP_5M" in flags
            or "COMP_15M" in flags
            or "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
        )
    
    ):
    
        return True, "elite_premove_pass"
        
    if score >= 4 and real_structure:
        return True, "structure_pass"

    # =====================
    # 🟡 WATCH MODE
    # =====================
    
    watch_structure = (
    
        "COMP_5M" in flags
        or "COMP_15M" in flags
        or "PRE_BREAKOUT_BUY" in flags
        or "PRE_BREAKOUT_SELL" in flags
        or "BREAKOUT_UP" in flags
        or "BREAKOUT_DOWN" in flags
    )
    
    if (
        score >= 3
        and acc >= 2
        and watch_structure
    ):
        return True, "watchlist"

    # =====================
    # 🔥 СИЛЬНЫЙ СИГНАЛ — ПРОПУСКАЕМ ВСЕГДА
    # =====================
    if score >= 7:
        return True, "strong_signal"

    # =====================
    # ❌ BLOCK
    # =====================
    return False, "low_score"

def entry_engine(score, flags, direction_text, up_w, down_w, rsi7, ema_state, price, target):

    if "БАЛАНС" in direction_text:
        return "🔴 WAIT", "Нет явного направления"
    
    strong_confirmed_impulse = (
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags)
        and ("ATR_EXPANSION" in flags or "VOL_SPIKE" in flags)
    )

    # =========================
    # PRE-MOVE EARLY STRUCTURE
    # =========================

    early_pressure_up = (
        "PRESSURE_UP" in flags
        and up_w >= 2
    )

    early_pressure_down = (
        "PRESSURE_DOWN" in flags
        and down_w >= 2
    )

    accumulation_context = (
        "COMP_5M" in flags
        or "COMP_15M" in flags
    )

    early_transition_long = (
        early_pressure_up
        and accumulation_context
    )

    early_transition_short = (
        early_pressure_down
        and accumulation_context
    )

    if early_transition_long:
        return (
            "🟡 EARLY LONG",
            "PRE-MOVE accumulation + pressure buildup"
        )

    if early_transition_short:
        return (
            "🟡 EARLY SHORT",
            "PRE-MOVE accumulation + pressure buildup"
        )
    
    # =========================
    # RSI SAFETY FILTER
    # =========================
    
    if direction_text == "⬆️ ВВЕРХ" and rsi7 is not None and rsi7 >= RSI_OB_BLOCK and not strong_confirmed_impulse:
        return "🔴 WAIT", "RSI перегрет — возможен ложный пробой"
    
    if direction_text == "⬇️ ВНИЗ" and rsi7 is not None and rsi7 <= RSI_OS_BLOCK and not strong_confirmed_impulse:
        return "🔴 WAIT", "RSI перепродан — возможен ложный пролив"

    # =========================
    # EMA TREND FILTER
    # =========================
    if direction_text == "⬆️ ВВЕРХ" and ema_state == "EMA_BEAR":
        return "🔴 WAIT", "Сигнал против EMA-тренда вниз"

    if direction_text == "⬇️ ВНИЗ" and ema_state == "EMA_BULL":
        return "🔴 WAIT", "Сигнал против EMA-тренда вверх"

    # =========================
    # SAFE ENTRY
    # =========================
    
    confirmed = (
        "BREAKOUT_CONFIRM_UP" in flags or
        "BREAKOUT_CONFIRM_DOWN" in flags
    )
    
    early_breakout = (
        "BREAKOUT_UP" in flags or
        "BREAKOUT_DOWN" in flags
    )
    
    impulse_ok = (
        "VOL_SPIKE" in flags or
        "ATR_EXPANSION" in flags
    )
    
    direction_code = direction_code_from_text(direction_text)
    
    # =========================
    # FALLBACK DIRECTION
    # =========================
    
    if not direction_code:
    
        if "PRESSURE_UP" in flags:
            direction_code = "UP"
    
        elif "PRESSURE_DOWN" in flags:
            direction_code = "DOWN"
    
        elif "EMA_BULL" in flags:
            direction_code = "UP"
    
        elif "EMA_BEAR" in flags:
            direction_code = "DOWN"
    
    # =========================
    # FLOW
    # =========================
    
    flow_ok = False
    
    if direction_code == "UP":
    
        if (
            "PRESSURE_UP" in flags or
            "CONTINUATION_UP" in flags
        ):
            flow_ok = True
    
    elif direction_code == "DOWN":
    
        if (
            "PRESSURE_DOWN" in flags or
            "CONTINUATION_DOWN" in flags
        ):
            flow_ok = True
    
    safe_cond = (
        score >= 5.75 and
        impulse_ok and
        flow_ok and
        (up_w >= 3 or down_w >= 3) and
        (
            confirmed or
            (early_breakout and (up_w >= 4 or down_w >= 4))
        )
    )
    flags = sig.get("flags", [])
    
    if safe_cond:

            # 🚫 КОНФЛИКТ НАПРАВЛЕНИЯ (САМЫЙ ВАЖНЫЙ ФИЛЬТР)
            long_signals = {"CONFLUENCE_LONG", "PRESSURE_UP", "EMA_BULL", "BREAKOUT_UP"}
            short_signals = {"PRESSURE_DOWN", "BREAKOUT_DOWN", "BREAKOUT_CONFIRM_DOWN"}
    
            long_score = sum(1 for f in flags if f in long_signals)
            short_score = sum(1 for f in flags if f in short_signals)
    
            if long_score > 0 and short_score > 0:
                return "🔴 WAIT", "Конфликт сигналов (лонг/шорт одновременно)"
    
            # 🚫 СЛАБЫЙ ПЕРЕВЕС (опционально, но советую)
            if abs(long_score - short_score) <= 1:
                return "🔴 WAIT", "Нет явного перевеса стороны"
    
    
            # 🚫 ЛОВУШКИ ЛИКВИДНОСТИ
            trap_flags = {
                "SWEEP_UP", "SWEEP_DOWN",
                "FAKE_DUMP",
                "BULL_TRAP", "BEAR_TRAP",
                "STOP_HUNT_UP", "STOP_HUNT_DOWN"
            }
    
            # 🚫 ПЕРЕГРЕТОЕ ДВИЖЕНИЕ
            if target is not None:
                try:
                    move_pct = abs(target - price) / price * 100
                    if move_pct > 12:
                        return "🔴 WAIT", "Движение уже слишком растянуто"
                except:
                    pass
    
            if any(f in flags for f in trap_flags):
                return "🔴 WAIT", "Ловушка ликвидности — пропуск"
    
            if too_close_to_target(price, target, min_room_pct=0.6):
                return "🔴 WAIT", "Поздний вход — нет запаса хода"
    
            if target is not None:
                try:
                    dist_pct = abs(target - price) / price * 100
                    if dist_pct < 0.8:
                        return "🔴 WAIT", "Слишком маленький потенциал движения"
                except:
                    pass
    
            return "🟢 SAFE ENTRY", "Подтверждение + импульс по направлению"

    
    # =========================
    # AGGRESSIVE ENTRY
    # =========================
    aggressive_setup = (
        score >= EDGE_MID_SCORE and any(
            f.startswith("BREAKOUT") or f.startswith("PRESSURE")
            for f in flags
        )
    )

    if aggressive_setup:
        if direction_text == "⬆️ ВВЕРХ" and ema_state == "EMA_BEAR":
            return "🔴 WAIT", "Ранний вход против EMA-тренда вниз"

        if direction_text == "⬇️ ВНИЗ" and ema_state == "EMA_BULL":
            return "🔴 WAIT", "Ранний вход против EMA-тренда вверх"

        return "🟡 AGGRESSIVE", "Ранний вход по структуре"

    return "🔴 WAIT", "Недостаточно факторов"

# =========================
# STAGE
# =========================
def smart_money_stage(score, flags):

    flags = set(flags or [])

    # =========================
    # EARLY
    # =========================
    if score < 1:
        return "⚪ EARLY", "Очень ранний интерес"

   # =========================
    # CONFIRMED EXPANSION
    # =========================
    if (
        (
            "BREAKOUT_CONFIRM_UP" in flags
            and score >= 5
        )
        or
        (
            "BREAKOUT_CONFIRM_DOWN" in flags
            and score >= 5
        )
    ):
        return "🟢 EXPANSION", "Подтверждённый импульс"

    # =========================
    # EARLY EXPANSION
    # =========================
    strong_bull_expansion = (
        "PRESSURE_UP" in flags
        and "EMA_BULL" in flags
        and (
            "BREAKOUT_UP" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
            or "CONTINUATION_UP" in flags
            or "VOL_SPIKE" in flags
            or "ATR_EXPANSION" in flags
        )
        and score >= 5
    )

    strong_bear_expansion = (
        "PRESSURE_DOWN" in flags
        and "EMA_BEAR" in flags
        and (
            "BREAKOUT_DOWN" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "CONTINUATION_DOWN" in flags
            or "VOL_SPIKE" in flags
            or "ATR_EXPANSION" in flags
        )
        and score >= 5
    )

    bull_impulse_confirm = (
        "VOL_SPIKE" in flags
        or "ATR_EXPANSION" in flags
        or "CONTINUATION_UP" in flags
        or "BREAKOUT_UP" in flags
        or "BREAKOUT_CONFIRM_UP" in flags
    )
    
    bear_impulse_confirm = (
        "VOL_SPIKE" in flags
        or "ATR_EXPANSION" in flags
        or "CONTINUATION_DOWN" in flags
        or "BREAKOUT_DOWN" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
    )
    
    if (
        strong_bull_expansion
        and bull_impulse_confirm
        and "OVERHEAT_UP" not in flags
    ):
        return "🟢 EXPANSION", "Сильный bullish expansion"
    
    if (
        strong_bear_expansion
        and bear_impulse_confirm
        and "OVERHEAT_DOWN" not in flags
    ):
        return "🟢 EXPANSION", "Сильный bearish expansion"

    # =========================
    # SMART EXPANSION
    # =========================

    smart_bull_expansion = (

        (
            "EXPLOSION_READY_UP" in flags
            or "LAUNCH_PROXIMITY_UP" in flags
            or "ACCELERATION_UP" in flags
        )

        and (
            "PRESSURE_UP" in flags
            or "BULLISH_SHIFT" in flags
        )

        and (
            "EMA_BULL_STRONG" in flags
            or "MTF_LONG_ALIGN" in flags
        )

        and score >= 8
    )

    smart_bear_expansion = (

        (
            "EXPLOSION_READY_DOWN" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
            or "ACCELERATION_DOWN" in flags
        )

        and (
            "PRESSURE_DOWN" in flags
            or "BEARISH_SHIFT" in flags
        )

        and (
            "EMA_BEAR_STRONG" in flags
            or "MTF_SHORT_ALIGN" in flags
        )

        and score >= 8
    )

    if smart_bull_expansion:
        return "🟢 EXPANSION", "Smart-money bullish expansion"

    if smart_bear_expansion:
        return "🟢 EXPANSION", "Smart-money bearish expansion"

    # =========================
    # ACCUMULATION
    # =========================
    if (
        "COMP_5M" in flags
        or "COMP_15M" in flags
    ):
        return "🟣 ACCUMULATION", "Сжатие перед движением"

   
    # =========================
    # TRANSITION
    # =========================
    
    if (
    
        (
            "PRESSURE_UP" in flags
            or "BREAKOUT_UP" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
            or "CONTINUATION_UP" in flags
        )
    
        and "STRUCTURE_CONFLICT" not in flags
    
        and (
    
            "EMA_BULL" in flags
            or "EMA_BULL_STRONG" in flags
            or "MTF_LONG_ALIGN" in flags
            or "EMA_MIXED" in flags
        )
    ):
        return "🟠 TRANSITION", "Bullish transition"
    
    
    if (
    
        (
            "PRESSURE_DOWN" in flags
            or "BREAKOUT_DOWN" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "CONTINUATION_DOWN" in flags
        )
    
        and "STRUCTURE_CONFLICT" not in flags
    
        and (
    
            "EMA_BEAR" in flags
            or "EMA_BEAR_STRONG" in flags
            or "MTF_SHORT_ALIGN" in flags
            or "EMA_MIXED" in flags
        )
    ):
        return "🟠 TRANSITION", "Bearish transition"
    
    # =========================
    # MANIPULATION
    # =========================
    if (
        "FAKE_DUMP" in flags
        or "FAKE_PUMP" in flags
        or "SWEEP_DOWN" in flags
        or "SWEEP_UP" in flags
    ):
        return "🟡 MANIPULATION", "Манипуляция ликвидностью"

    # =========================
    # DEFAULT
    # =========================
    return "⚪ NEUTRAL", "Нейтральная структура"
       

# =========================
# ENTRY
# =========================
def decide_entry(stage, flags, price, c5, sig=None):

    last = price

    highs = [c[2] for c in c5[-10:]] if c5 else []
    lows = [c[3] for c in c5[-10:]] if c5 else []

    recent_high = max(highs) if highs else last
    recent_low = min(lows) if lows else last

    entry = "NO_ENTRY"
    entry_price = last
    stop = None
    reason = "NO_ENTRY"

    # =========================
    # EXPANSION
    # =========================
    if stage == "🟢 EXPANSION":
        # =====================
        # SMART EXPANSION LONG
        # =====================

        if (
            "PRESSURE_UP" in flags
            and (
                "ACCELERATION_UP" in flags
                or "EXPLOSION_READY_UP" in flags
                or "LAUNCH_PROXIMITY_UP" in flags
            )
        ):

            entry = "EXPANSION_LONG"

            stop = recent_low

            reason = "SMART_EXPANSION_LONG"

        # =====================
        # SMART EXPANSION SHORT
        # =====================

        elif (
            "PRESSURE_DOWN" in flags
            and (
                "ACCELERATION_DOWN" in flags
                or "EXPLOSION_READY_DOWN" in flags
                or "LAUNCH_PROXIMITY_DOWN" in flags
            )
        ):

            entry = "EXPANSION_SHORT"

            stop = recent_high

            reason = "SMART_EXPANSION_SHORT"

        elif "BREAKOUT_CONFIRM_UP" in flags:
            entry = "LONG_CONFIRM"
            stop = recent_low
            reason = "LONG_CONFIRM"
    
        elif "BREAKOUT_CONFIRM_DOWN" in flags:
            entry = "SHORT_CONFIRM"
            stop = recent_high
            reason = "SHORT_CONFIRM"
    
        elif "CONTINUATION_UP" in flags:
            entry = "CONTINUATION_LONG"
            stop = recent_low
            reason = "CONTINUATION_LONG"
    
        elif "CONTINUATION_DOWN" in flags:
            entry = "CONTINUATION_SHORT"
            stop = recent_high
            reason = "CONTINUATION_SHORT"

        elif (
            "PRESSURE_UP" in flags
            and (
                "ATR_EXPANSION" in flags
                or "VOL_SPIKE" in flags
            )
        ):
    
            entry = "EXPANSION_LONG"
            stop = recent_low
            reason = "EXPANSION_LONG"
    
        elif (
            "PRESSURE_DOWN" in flags
            and (
                "ATR_EXPANSION" in flags
                or "VOL_SPIKE" in flags
            )
        ):
    
            entry = "EXPANSION_SHORT"
            stop = recent_high
            reason = "EXPANSION_SHORT"

    # =========================
    # EARLY
    # =========================
    elif stage == "⚪ EARLY":

        if "PRESSURE_UP" in flags:

            entry = "EARLY_SCOUT_LONG"
            stop = recent_low
            reason = "EARLY_SCOUT_LONG"

        elif "PRESSURE_DOWN" in flags:

            entry = "EARLY_SCOUT_SHORT"
            stop = recent_high
            reason = "EARLY_SCOUT_SHORT"

    # =========================
    # PREMOVE BUILDUP
    # =========================
    elif (
        "ENERGY_BUILDUP" in flags
        and (
            "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
            or "COMP_5M" in flags
            or "COMP_15M" in flags
        )
    ):
    
        long_premove = (
            "BULLISH_SHIFT" in flags
            or (
                "PRESSURE_UP" in flags
                and (
                    "EMA_BULL" in flags
                    or "EMA_BULL_STRONG" in flags
                    or "MTF_LONG_ALIGN" in flags
                )
            )
        )
    
        short_premove = (
            "BEARISH_SHIFT" in flags
            or (
                "PRESSURE_DOWN" in flags
                and (
                    "EMA_BEAR" in flags
                    or "EMA_BEAR_STRONG" in flags
                    or "MTF_SHORT_ALIGN" in flags
                )
            )
        )
        
        
        # =====================
        # LONG PREMOVE
        # =====================
        if long_premove:
        
            long_strength = 0
        
            if "PRESSURE_UP" in flags:
                long_strength += 2
        
            if "BULLISH_SHIFT" in flags:
                long_strength += 2
        
            if "ACCELERATION_UP" in flags:
                long_strength += 2
        
            if "MTF_LONG_ALIGN" in flags:
                long_strength += 2
        
            if "EMA_BULL_STRONG" in flags:
                long_strength += 1
        
            short_strength = 0
        
            if "PRESSURE_DOWN" in flags:
                short_strength += 2
        
            if "BEARISH_SHIFT" in flags:
                short_strength += 2
        
            if "ACCELERATION_DOWN" in flags:
                short_strength += 2
        
            if "MTF_SHORT_ALIGN" in flags:
                short_strength += 2
        
            if "EMA_BEAR_STRONG" in flags:
                short_strength += 1
        
            if long_strength >= short_strength:
                print(
                    f"[LONG_DOMINANCE] "
                    f"long={long_strength} "
                    f"short={short_strength}",
                    flush=True
                )
        
                entry = "PREMOVE_LONG"
        
                stop = recent_low
        
                reason = "PREMOVE_LONG"
            
    
        # =====================
        # SHORT PREMOVE
        # =====================
        elif short_premove:
        
            short_strength = 0
        
            if "PRESSURE_DOWN" in flags:
                short_strength += 2
        
            if "BEARISH_SHIFT" in flags:
                short_strength += 2
        
            if "ACCELERATION_DOWN" in flags:
                short_strength += 2
        
            if "MTF_SHORT_ALIGN" in flags:
                short_strength += 2
        
            if "EMA_BEAR_STRONG" in flags:
                short_strength += 1
        
            long_strength = 0
        
            if "PRESSURE_UP" in flags:
                long_strength += 2
        
            if "BULLISH_SHIFT" in flags:
                long_strength += 2
        
            if "ACCELERATION_UP" in flags:
                long_strength += 2
        
            if "MTF_LONG_ALIGN" in flags:
                long_strength += 2
        
            if "EMA_BULL_STRONG" in flags:
                long_strength += 1
        
            if short_strength >= long_strength:
                print(
                    f"[SHORT_DOMINANCE] "
                    f"short={short_strength} "
                    f"long={long_strength}",
                    flush=True
                )
        
                entry = "PREMOVE_SHORT"
        
                stop = recent_high
        
                reason = "PREMOVE_SHORT"

           

        # =====================
        # CONFLICT
        # =====================
        else:

            entry = "WATCH_BUILDUP"
            stop = None
            reason = "WATCH_BUILDUP"
        
            print(
                f"[WATCH_BUILDUP] {stage} flags={flags}",
                flush=True
            )

   

    # =========================
    # TRANSITION
    # =========================
    elif stage == "🟠 TRANSITION":

        if (
            "PRESSURE_UP" in flags
            and (
                "EMA_BULL" in flags
                or "EMA_BULL_STRONG" in flags
                or "MTF_LONG_ALIGN" in flags
            )
        ):
    
            entry = "TRANSITION_LONG"
            stop = recent_low
            reason = "TRANSITION_LONG"
    
        elif (
            "PRESSURE_DOWN" in flags
            and (
                "EMA_BEAR" in flags
                or "EMA_BEAR_STRONG" in flags
                or "MTF_SHORT_ALIGN" in flags
            )
        ):
    
            entry = "TRANSITION_SHORT"
            stop = recent_high
            reason = "TRANSITION_SHORT"

    # =========================
    # ACCUMULATION
    # =========================
    elif stage == "🟣 ACCUMULATION":
    
        if "PRESSURE_UP" in flags:
    
            entry = "ACCUMULATION_LONG"
            stop = recent_low
            reason = "ACCUMULATION_LONG"
    
        elif "PRESSURE_DOWN" in flags:
    
            entry = "ACCUMULATION_SHORT"
            stop = recent_high
            reason = "ACCUMULATION_SHORT"
    
        elif "COMP_5M" in flags or "COMP_15M" in flags:
    
            entry = "ACC_BUILDUP"
            stop = recent_low
            reason = "ACC_BUILDUP"


    # =========================
    # MANIPULATION
    # =========================
    elif stage == "🟡 MANIPULATION":

        if "SWEEP_DOWN" in flags:

            entry = "REVERSAL_LONG"
            stop = recent_low
            reason = "REVERSAL_LONG"

        elif "SWEEP_UP" in flags:

            entry = "REVERSAL_SHORT"
            stop = recent_high
            reason = "REVERSAL_SHORT"

    return entry_price, stop, reason
# =========================
# TRADE PLAN
# =========================
def build_trade_plan(signal):
    return {
        "entry_plan": None,
        "stop": None,
        "rr": None,
        "trade_status": "NO_TRADE",
        "plan_reason": None
    }

def detect_early_pressure(sig):

    flags = set(sig.get("flags", []))
    stage = str(sig.get("stage", ""))
    ema_state = sig.get("ema_state", "EMA_MIXED")
    acc = int(sig.get("acc_score", 0) or 0)

    up_score = 0
    down_score = 0
    up_reasons = []
    down_reasons = []

    strong_transition = False

    def add_up(points, reason):
        nonlocal up_score
        up_score += points
        up_reasons.append(reason)

    def add_down(points, reason):
        nonlocal down_score
        down_score += points
        down_reasons.append(reason)

    # CORE
    if "PRESSURE_UP" in flags:
        add_up(2, "PRESSURE_UP")
    
    if "PRESSURE_DOWN" in flags:
        add_down(2, "PRESSURE_DOWN")
    # =========================
    # ENERGY BUILDUP
    # =========================

    if (
        "ENERGY_BUILDUP" in flags
        and (
            "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
            or "COMP_5M" in flags
            or "COMP_15M" in flags
        )
    ):

        if "PRESSURE_UP" in flags:
            add_up(4, "ENERGY_BUILDUP_LONG")

        if "PRESSURE_DOWN" in flags:
            add_down(4, "ENERGY_BUILDUP_SHORT")
    # ABSORPTION
    if "BUYER_ABSORPTION" in flags:
        add_up(4, "BUYER_ABSORPTION")

    if "SELLER_ABSORPTION" in flags:
        add_down(4, "SELLER_ABSORPTION")
        
    if "PRE_BREAKOUT_BUY" in flags:
        add_up(3, "PRE_BREAKOUT_BUY")

    if "PRE_BREAKOUT_SELL" in flags:
        add_down(3, "PRE_BREAKOUT_SELL")

    if "CONTINUATION_UP" in flags:
        add_up(2, "CONTINUATION_UP")

    if "CONTINUATION_DOWN" in flags:
        add_down(2, "CONTINUATION_DOWN")

        # BREAKOUT CONFIRM

    if "BREAKOUT_CONFIRM_UP" in flags:
        add_up(4, "BREAKOUT_CONFIRM_UP")

    if "BREAKOUT_CONFIRM_DOWN" in flags:
        add_down(4, "BREAKOUT_CONFIRM_DOWN")

    # NORMAL BREAKOUT

    if "BREAKOUT_UP" in flags:
        add_up(2, "BREAKOUT_UP")

    if "BREAKOUT_DOWN" in flags:
        add_down(2, "BREAKOUT_DOWN")

    # COMPRESSION
    if "COMP_5M" in flags:
        if up_score > 0:
            add_up(1, "COMP_5M")
        if down_score > 0:
            add_down(1, "COMP_5M")

    if "COMP_15M" in flags:
        if up_score > 0:
            add_up(2, "COMP_15M")
        if down_score > 0:
            add_down(2, "COMP_15M")

    # =========================
    # ACCUMULATION CONTEXT
    # =========================
    
    if acc >= 2:
    
        acc_context_ok = (
    
            "COMP_5M" in flags
            or "COMP_15M" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "BREAKOUT_UP" in flags
            or "BREAKOUT_DOWN" in flags
        )
    
        if acc_context_ok:
    
            if up_score > 0:
                add_up(1, "ACC_SCORE")
    
            if down_score > 0:
                add_down(1, "ACC_SCORE")
    
    
    if acc >= 3 and acc_context_ok:
    
        if up_score > 0:
            add_up(2, "STRONG_ACCUMULATION")
    
        if down_score > 0:
            add_down(2, "STRONG_ACCUMULATION")

    # =========================
    # SHIFT
    # =========================

    if "BULLISH_SHIFT" in flags:
        add_up(3, "BULLISH_SHIFT")

    if "BEARISH_SHIFT" in flags:
        add_down(3, "BEARISH_SHIFT")

    # =========================
    # ACCELERATION
    # =========================

    if "ACCELERATION_UP" in flags:
        add_up(2, "ACCELERATION_UP")

    if "ACCELERATION_DOWN" in flags:
        add_down(2, "ACCELERATION_DOWN")

    # =========================
    # LAUNCH PROXIMITY
    # =========================

    if "LAUNCH_PROXIMITY_UP" in flags:
        add_up(3, "LAUNCH_PROXIMITY_UP")

    if "LAUNCH_PROXIMITY_DOWN" in flags:
        add_down(3, "LAUNCH_PROXIMITY_DOWN")

    # =========================
    # EXPLOSION READY
    # =========================

    if "EXPLOSION_READY_UP" in flags:
        add_up(4, "EXPLOSION_READY_UP")

    if "EXPLOSION_READY_DOWN" in flags:
        add_down(4, "EXPLOSION_READY_DOWN")
      
    # =========================
    # MTF CONTEXT
    # =========================

    if "MTF_LONG_ALIGN" in flags:
        add_up(2, "MTF_LONG_ALIGN")

    if "MTF_SHORT_ALIGN" in flags:
        add_down(2, "MTF_SHORT_ALIGN")

    # =========================
    # EMA MIXED CONTEXT
    # =========================

    if "EMA_MIXED" in flags:

        if up_score > 0:
            add_up(1, "EMA_MIXED_PRESSURE")

        if down_score > 0:
            add_down(1, "EMA_MIXED_PRESSURE")

    # =========================
    # STRUCTURE CONFLICT
    # =========================

    if "STRUCTURE_CONFLICT" in sig.get("flags", []):

        up_score -= 3
        down_score -= 3

        up_reasons.append("STRUCTURE_CONFLICT")
        down_reasons.append("STRUCTURE_CONFLICT") 

    # =========================
    # COMPRESSION + PRESSURE
    # =========================

    if (
        (
            "COMP_5M" in flags
            or "COMP_15M" in flags
        )
        and (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    ):

        if up_score > 0:
            add_up(2, "COMP_PLUS_PRESSURE")

        if down_score > 0:
            add_down(2, "COMP_PLUS_PRESSURE")


    # =========================
    # COMPRESSION + CONTINUATION
    # =========================

    if (
        (
            "COMP_5M" in flags
            or "COMP_15M" in flags
        )
        and (
            "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
        )
    ):

        if up_score > 0:
            add_up(3, "COMP_PLUS_CONTINUATION")

        if down_score > 0:
            add_down(3, "COMP_PLUS_CONTINUATION")

    # STAGE
    if "ACCUMULATION" in stage:
        add_up(2, "ACC_STAGE")
        add_down(2, "ACC_STAGE")
    
    strong_transition = (
        "VOL_SPIKE" in flags
        or "COMP_15M" in flags
        or "BREAKOUT_UP" in flags
        or "BREAKOUT_DOWN" in flags
        or "BREAKOUT_CONFIRM_UP" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
        or "ATR_EXPANSION" in flags
    )
    
    if "TRANSITION" in stage:

        add_up(1, "TRANSITION_STAGE")
        add_down(1, "TRANSITION_STAGE")
    
        if strong_transition:
            add_up(2, "STRONG_TRANSITION")
            add_down(2, "STRONG_TRANSITION")
        
    if "MANIPULATION" in stage:
    
        if "SWEEP_DOWN" in flags:
            add_up(3, "REVERSAL_LONG")
    
        if "SWEEP_UP" in flags:
            add_down(3, "REVERSAL_SHORT")

    # EMA

    if "EMA_BULL" in ema_state:
    
        add_up(1, "EMA_BULL")
    
        if "STRONG" in ema_state:
            add_up(2, "EMA_BULL_STRONG")
    
        elif "WEAK" in ema_state:
            add_up(1, "EMA_BULL_WEAK")
    
    elif "EMA_BEAR" in ema_state:
    
        add_down(1, "EMA_BEAR")
    
        if "STRONG" in ema_state:
            add_down(2, "EMA_BEAR_STRONG")
    
        elif "WEAK" in ema_state:
            add_down(1, "EMA_BEAR_WEAK")
    
    elif ema_state == "EMA_TRANSITION":
    
        add_up(0.5, "EMA_TRANSITION")
        add_down(0.5, "EMA_TRANSITION")
    
    elif ema_state == "EMA_FLAT":
    
        add_up(0.25, "EMA_FLAT")
        add_down(0.25, "EMA_FLAT") 

    # RESULT
    result = {
        "early_pressure_side": None,
        "early_pressure_score": 0,
        "early_pressure_reasons": [],
    }

    if up_score >= 5 and up_score >= down_score:
        result["early_pressure_side"] = "BUY"
        result["early_pressure_score"] = up_score
        result["early_pressure_reasons"] = up_reasons

    elif down_score >= 5 and down_score >= up_score:
        result["early_pressure_side"] = "SELL"
        result["early_pressure_score"] = down_score
        result["early_pressure_reasons"] = down_reasons

    return result
                
            
    
      

def liquidity_target(pmeta, flags, price=None):

    if not pmeta:
        return None

    lo = (pmeta or {}).get("range_lo")
    hi = (pmeta or {}).get("range_hi")

    if lo is None or hi is None:
        return None

    try:
        lo = float(lo)
        hi = float(hi)
        price = float(price) if price is not None else None
    except:
        return None

    rng = hi - lo

    if rng <= 0:
        return None

    if "BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags or "PRESSURE_UP" in flags:

        if price is not None and price >= hi:
            return round(hi + rng * 0.35, 6)

        return round(hi, 6)

    if "BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags or "PRESSURE_DOWN" in flags:

        if price is not None and price <= lo:
            return round(lo - rng * 0.35, 6)

        return round(lo, 6)

    return None

def calc_entry_zone(price, pmeta, flags, direction_code):
    if not pmeta:
        return None

    hi = pmeta.get("range_hi")
    lo = pmeta.get("range_lo")

    if hi is None or lo is None:
        return None

    try:
        hi = float(hi)
        lo = float(lo)
        price = float(price)
    except Exception:
        return None

    rng = hi - lo
    if rng <= 0:
        return None

    flags = set(flags)

    if direction_code == "UP":
        if "BREAKOUT_CONFIRM_UP" in flags:
            return {
                "zone_type": "RETEST_LONG",
                "low": round(hi - rng * 0.05, 6),
                "high": round(hi + rng * 0.10, 6),
                "stop": round(hi - rng * 0.10, 6),
            }

        if "SWEEP_DOWN" in flags or "FAKE_DUMP" in flags:
            return {
                "zone_type": "RECLAIM_LONG",
                "low": round(lo + rng * 0.10, 6),
                "high": round(lo + rng * 0.30, 6),
                "stop": round(lo - rng * 0.08, 6),
            }

    if direction_code == "DOWN":
        if "BREAKOUT_CONFIRM_DOWN" in flags:
            return {
                "zone_type": "RETEST_SHORT",
                "low": round(lo - rng * 0.10, 6),
                "high": round(lo + rng * 0.05, 6),
                "stop": round(lo + rng * 0.20, 6),
            }

        if "SWEEP_UP" in flags:
            return {
                "zone_type": "RECLAIM_SHORT",
                "low": round(hi - rng * 0.30, 6),
                "high": round(hi - rng * 0.10, 6),
                "stop": round(hi + rng * 0.12, 6),
            }

    return None

    # =========================
    # ORDERBOOK
    # =========================
    if ORDERBOOK_ENABLED:
        try:
            ob_meta = orderbook_edge(instId)
        except Exception as e:
            print(f"[ORDERBOOK_ERROR] {instId} {e}", flush=True)
            ob_meta = None

    if isinstance(ob_meta, dict):
        if ob_meta.get("ob_bias") == "BIDS":
            flags.add("OB_BIDS")
            score += 1
        elif ob_meta.get("ob_bias") == "ASKS":
            flags.add("OB_ASKS")
            score += 1

        if ob_meta.get("bid_wall"):
            flags.add("OB_WALL_BID")

        if ob_meta.get("ask_wall"):
            flags.add("OB_WALL_ASK")

        if ob_meta.get("wall_removed"):
            flags.add("WALL_REMOVED")
# =========================
# COMPRESSION PRO ENGINE v1
# =========================
def compression_pro(candles):

    try:

        if not candles or len(candles) < 30:
            return {
                "active": False,
                "score": 0,
                "reasons": []
            }

        closes = [float(x[4]) for x in candles[-20:]]
        highs = [float(x[2]) for x in candles[-20:]]
        lows  = [float(x[3]) for x in candles[-20:]]

        reasons = []
        score = 0

        # =====================
        # RANGE %
        # =====================

        hi = max(highs)
        lo = min(lows)

        if lo <= 0:
            return {
                "active": False,
                "score": 0,
                "reasons": []
            }

        range_pct = ((hi - lo) / lo) * 100

        # узкий диапазон
        if range_pct <= 2.5:
            score += 2
            reasons.append("tight_range")

        elif range_pct <= 4:
            score += 1
            reasons.append("mid_range")

        # =====================
        # CLOSE CLUSTERING
        # =====================

        avg_close = sum(closes) / len(closes)

        deviations = []

        for c in closes:
            dev = abs(c - avg_close) / avg_close * 100
            deviations.append(dev)

        avg_dev = sum(deviations) / len(deviations)

        if avg_dev <= 0.8:
            score += 2
            reasons.append("close_clustering")

        elif avg_dev <= 1.5:
            score += 1
            reasons.append("soft_clustering")

        # =====================
        # SHRINKING SPREAD
        # =====================

        spreads = []

        for i in range(-10, 0):

            h = float(candles[i][2])
            l = float(candles[i][3])

            if l <= 0:
                continue

            spreads.append(
                ((h - l) / l) * 100
            )

        if len(spreads) >= 6:

            first_half = sum(spreads[:5]) / 5
            second_half = sum(spreads[-5:]) / 5

            if second_half < first_half * 0.8:
                score += 2
                reasons.append("spread_contraction")

        # =====================
        # FAILED EXPANSION
        # =====================

        failed_breaks = 0

        for i in range(-8, -1):

            c = float(candles[i][4])

            if c > hi * 0.995 and c < hi:
                failed_breaks += 1

            if c < lo * 1.005 and c > lo:
                failed_breaks += 1

        if failed_breaks >= 3:
            score += 1
            reasons.append("failed_expansion")

        # =====================
        # FINAL
        # =====================

        active = score >= 4

        return {
            "active": active,
            "score": score,
            "range_pct": round(range_pct, 2),
            "reasons": reasons
        }

    except Exception as e:

        print(
            f"[COMPRESSION_PRO_ERROR] {e}",
            flush=True
        )

        return {
            "active": False,
            "score": 0,
            "reasons": []
        }

# =========================
# PRESSURE ABSORPTION ENGINE
# =========================
def detect_absorption(candles):

    try:

        if not candles or len(candles) < 20:
            return None

        highs = [float(x[2]) for x in candles[-8:]]
        lows  = [float(x[3]) for x in candles[-8:]]
        closes = [float(x[4]) for x in candles[-8:]]

        last_close = closes[-1]

        low_tests = 0
        high_tests = 0

        # =====================
        # LOW ABSORPTION
        # =====================

        zone_low = min(lows)

        for l in lows:

            dist = abs(l - zone_low) / zone_low * 100

            if dist <= 0.35:
                low_tests += 1

        # price NOT breaking lower
        low_reclaim = (
            last_close > zone_low * 1.003
        )

        if (
            low_tests >= 3
            and low_reclaim
        ):
            return "SELLER_ABSORPTION"

        # =====================
        # HIGH ABSORPTION
        # =====================

        zone_high = max(highs)

        for h in highs:

            dist = abs(h - zone_high) / zone_high * 100

            if dist <= 0.35:
                high_tests += 1

        high_reject = (
            last_close < zone_high * 0.997
        )

        if (
            high_tests >= 3
            and high_reject
        ):
            return "BUYER_ABSORPTION"

        return None

    except Exception as e:

        print(
            f"[ABSORPTION_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# PRESSURE SHIFT ENGINE
# =========================
def detect_pressure_shift(flags):

    try:

        # =====================
        # BULLISH SHIFT
        # =====================

        if (
    
            "SELLER_ABSORPTION" in flags
        
            and
        
            (
                "PRESSURE_UP" in flags
                or "BREAKOUT_UP" in flags
                or "MTF_LONG_ALIGN" in flags
            )
        
        ):

            return "BULLISH_SHIFT"

        # =====================
        # BEARISH SHIFT
        # =====================

        if (

            "BUYER_ABSORPTION" in flags
        
            and
        
            (
                "PRESSURE_DOWN" in flags
                or "BREAKOUT_DOWN" in flags
                or "MTF_SHORT_ALIGN" in flags
            )
        
        ):

            return "BEARISH_SHIFT"

        return None

    except Exception as e:

        print(
            f"[SHIFT_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# EARLY IMBALANCE ENGINE
# =========================
def detect_early_imbalance(candles, flags):

    try:

        if not candles or len(candles) < 20:
            return None

        closes = [float(x[4]) for x in candles[-10:]]
        highs  = [float(x[2]) for x in candles[-10:]]
        lows   = [float(x[3]) for x in candles[-10:]]

        last_close = closes[-1]

        range_high = max(highs[:-1])
        range_low  = min(lows[:-1])

        # =====================
        # BULLISH IMBALANCE
        # =====================

        if (

            "BULLISH_SHIFT" in flags

            and

            last_close <= range_low * 1.002

        ):

            return "EARLY_IMBALANCE_UP"

        # =====================
        # BEARISH IMBALANCE
        # =====================

        if (

            "BEARISH_SHIFT" in flags

            and

            last_close < range_low * 0.999

        ):

            return "EARLY_IMBALANCE_DOWN"

        return None

    except Exception as e:

        print(
            f"[EARLY_IMBALANCE_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# LAUNCH ENGINE
# =========================
def detect_launch_ready(flags):

    try:

        # =====================
        # LONG LAUNCH
        # =====================

        if (

            "EARLY_IMBALANCE_UP" in flags

            and

            (
                "VOL_SPIKE" in flags
                or "ATR_EXPANSION" in flags
            )

            and

            (
                "PRESSURE_UP" in flags
                or "CONTINUATION_UP" in flags
            )

        ):

            return "LAUNCH_READY_UP"

        # =====================
        # SHORT LAUNCH
        # =====================

        if (

            "EARLY_IMBALANCE_DOWN" in flags

            and

            (
                "VOL_SPIKE" in flags
                or "ATR_EXPANSION" in flags
            )

            and

            (
                "PRESSURE_DOWN" in flags
                or "CONTINUATION_DOWN" in flags
            )

        ):

            return "LAUNCH_READY_DOWN"

        return None

    except Exception as e:

        print(
            f"[LAUNCH_ENGINE_ERROR] {e}",
            flush=True
        )

        return None
        
# =========================
# EARLY LAUNCH ENGINE
# =========================
def detect_early_launch(flags):

    try:

        # =====================
        # EARLY LONG LAUNCH
        # =====================

        if (

            "EARLY_IMBALANCE_UP" in flags

            and

            "BULLISH_SHIFT" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "PRESSURE_UP" in flags
                or "BREAKOUT_UP" in flags
            )

        ):

            return "EARLY_LAUNCH_UP"

        # =====================
        # EARLY SHORT LAUNCH
        # =====================

        if (

            "EARLY_IMBALANCE_DOWN" in flags

            and

            "BEARISH_SHIFT" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "PRESSURE_DOWN" in flags
                or "BREAKOUT_DOWN" in flags
            )

        ):

            return "EARLY_LAUNCH_DOWN"

        return None

    except Exception as e:

        print(
            f"[EARLY_LAUNCH_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# SQUEEZE PRESSURE ENGINE
# =========================
def detect_squeeze_pressure(candles):

    try:

        if not candles or len(candles) < 20:
            return None

        highs = [float(x[2]) for x in candles[-10:]]
        lows  = [float(x[3]) for x in candles[-10:]]
        closes = [float(x[4]) for x in candles[-10:]]

        range_high = max(highs)
        range_low  = min(lows)

        high_hits = 0
        low_hits = 0

        # =====================
        # HIGH PRESSURE
        # =====================

        for c in closes:

            dist = abs(c - range_high) / range_high * 100

            if dist <= 0.35:
                high_hits += 1

        # =====================
        # LOW PRESSURE
        # =====================

        for c in closes:

            dist = abs(c - range_low) / range_low * 100

            if dist <= 0.35:
                low_hits += 1

        # =====================
        # DETECT
        # =====================

        if high_hits >= 4:
            return "SQUEEZE_UP"

        if low_hits >= 4:
            return "SQUEEZE_DOWN"

        return None

    except Exception as e:

        print(
            f"[SQUEEZE_ENGINE_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# SQUEEZE MATURITY ENGINE
# =========================
def detect_squeeze_maturity(flags):

    try:

        # =====================
        # BULLISH MATURE SQUEEZE
        # =====================

        if (

            "SQUEEZE_UP" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "BULLISH_SHIFT" in flags
                or "PRESSURE_UP" in flags
            )

        ):

            return "MATURE_SQUEEZE_UP"

        # =====================
        # BEARISH MATURE SQUEEZE
        # =====================

        if (

            "SQUEEZE_DOWN" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "BEARISH_SHIFT" in flags
                or "PRESSURE_DOWN" in flags
            )

        ):

            return "MATURE_SQUEEZE_DOWN"

        return None

    except Exception as e:

        print(
            f"[SQUEEZE_MATURITY_ERROR] {e}",
            flush=True
        )

        return None
# =========================
# ACCELERATION PRESSURE ENGINE
# =========================
def detect_acceleration(flags):

    try:

        # =====================
        # BULLISH ACCELERATION
        # =====================

        if (

            "PRESSURE_UP" in flags

            and

            (
                "BULLISH_SHIFT" in flags
                or "EARLY_LAUNCH_UP" in flags
            )

            and

            (
                "EMA_BULL_STRONG" in flags
                or "MTF_LONG_ALIGN" in flags
            )

        ):

            return "ACCELERATION_UP"

        # =====================
        # BEARISH ACCELERATION
        # =====================

        if (

            "PRESSURE_DOWN" in flags

            and

            (
                "BEARISH_SHIFT" in flags
                or "EARLY_LAUNCH_DOWN" in flags
            )

            and

            (
                "EMA_BEAR_STRONG" in flags
                or "MTF_SHORT_ALIGN" in flags
            )

        ):

            return "ACCELERATION_DOWN"

        return None

    except Exception as e:

        print(
            f"[ACCELERATION_ENGINE_ERROR] {e}",
            flush=True
        )

        return None
        
# =========================
# EXPLOSIVE MOVE ENGINE
# =========================
def detect_explosive_move(flags):

    try:

        # =====================
        # EXPLOSIVE LONG
        # =====================

        if (

            "ACCELERATION_UP" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "BULLISH_SHIFT" in flags
                or "EARLY_LAUNCH_UP" in flags
            )

            and

            (
                "MTF_LONG_ALIGN" in flags
                or "EMA_BULL_STRONG" in flags
            )

        ):

            return "EXPLOSIVE_UP"

        # =====================
        # EXPLOSIVE SHORT
        # =====================

        if (

            "ACCELERATION_DOWN" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "BEARISH_SHIFT" in flags
                or "EARLY_LAUNCH_DOWN" in flags
            )

            and

            (
                "MTF_SHORT_ALIGN" in flags
                or "EMA_BEAR_STRONG" in flags
            )

        ):

            return "EXPLOSIVE_DOWN"

        return None

    except Exception as e:

        print(
            f"[EXPLOSIVE_ENGINE_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# SETUP RANKING ENGINE
# =========================
def detect_setup_rank(flags, score=0, acc_score=0):

    try:

        rank_score = 0
        reasons = []

        if "ENERGY_BUILDUP" in flags:
            rank_score += 2
            reasons.append("energy")

        if "BULLISH_SHIFT" in flags or "BEARISH_SHIFT" in flags:
            rank_score += 3
            reasons.append("shift")

        if "ACCELERATION_UP" in flags or "ACCELERATION_DOWN" in flags:
            rank_score += 4
            reasons.append("acceleration")

        if "EARLY_LAUNCH_UP" in flags or "EARLY_LAUNCH_DOWN" in flags:
            rank_score += 4
            reasons.append("early_launch")

        if "LAUNCH_READY_UP" in flags or "LAUNCH_READY_DOWN" in flags:
            rank_score += 5
            reasons.append("launch_ready")

        if "EXPLOSIVE_UP" in flags or "EXPLOSIVE_DOWN" in flags:
            rank_score += 6
            reasons.append("explosive")

        if "MTF_LONG_ALIGN" in flags or "MTF_SHORT_ALIGN" in flags:
            rank_score += 2
            reasons.append("mtf_align")

        if "EMA_BULL_STRONG" in flags or "EMA_BEAR_STRONG" in flags:
            rank_score += 2
            reasons.append("strong_ema")

        if "BUYER_ABSORPTION" in flags or "SELLER_ABSORPTION" in flags:
            rank_score += 2
            reasons.append("absorption")

        if acc_score >= 3:
            rank_score += 2
            reasons.append("strong_accumulation")

        if score >= 12:
            rank_score += 2
            reasons.append("high_score")

        # =====================
        # FINAL RANK
        # =====================

        if rank_score >= 15:
            return "PRIORITY_1", rank_score, reasons
    
        if rank_score >= 11:
            return "PRIORITY_2", rank_score, reasons
        
        if rank_score >= 7:
            return "PRIORITY_3", rank_score, reasons

        return "WATCH", rank_score, reasons

    except Exception as e:

        print(
            f"[SETUP_RANK_ERROR] {e}",
            flush=True
        )

        return "WATCH", 0, []


# =========================
# LAUNCH PROXIMITY ENGINE
# =========================
def detect_launch_proximity(flags):

    try:

        # =====================
        # LONG PROXIMITY
        # =====================

        if (

            "BULLISH_SHIFT" in flags

            and

            "PRESSURE_UP" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "COMP_PRO_5M" in flags
                or "COMP_PRO_15M" in flags
            )

        ):

            return "LAUNCH_PROXIMITY_UP"

        # =====================
        # SHORT PROXIMITY
        # =====================

        if (

            "BEARISH_SHIFT" in flags

            and

            "PRESSURE_DOWN" in flags

            and

            "ENERGY_BUILDUP" in flags

            and

            (
                "COMP_PRO_5M" in flags
                or "COMP_PRO_15M" in flags
            )

        ):

            return "LAUNCH_PROXIMITY_DOWN"

        return None

    except Exception as e:

        print(
            f"[LAUNCH_PROXIMITY_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# EXPLOSION BUILDUP ENGINE
# =========================
def detect_explosion_buildup(flags):

    try:

        # =====================
        # EXPLOSION LONG
        # =====================

        if (

            "LAUNCH_PROXIMITY_UP" in flags

            and

            (
                "ACCELERATION_UP" in flags
                or "BULLISH_SHIFT" in flags
            )

            and

        (
            "VOL_SPIKE" in flags
            or "ATR_EXPANSION" in flags
            or "COMP_PRO_5M" in flags
        )

            and

            "ENERGY_BUILDUP" in flags

        ):

            return "EXPLOSION_READY_UP"

        # =====================
        # EXPLOSION SHORT
        # =====================

        if (

            "LAUNCH_PROXIMITY_DOWN" in flags

            and

            (
                "ACCELERATION_DOWN" in flags
                or "BEARISH_SHIFT" in flags
            )

            and

            (
                "VOL_SPIKE" in flags
                or "ATR_EXPANSION" in flags
                or "COMP_PRO_5M" in flags
            )

            and

            "ENERGY_BUILDUP" in flags

        ):

            return "EXPLOSION_READY_DOWN"

        return None

    except Exception as e:

        print(
            f"[EXPLOSION_BUILDUP_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# ANTI-TRAP ENGINE
# =========================
def detect_trap_risk(flags):

    try:

        # =====================
        # LONG TRAP
        # =====================

        if (

            "EXPLOSION_READY_UP" in flags

            and

            (
                "STRUCTURE_CONFLICT" in flags
                or "EMA_BEAR_STRONG" in flags
            )

        ):

            return "TRAP_RISK_UP"

        # =====================
        # SHORT TRAP
        # =====================

        if (

            "EXPLOSION_READY_DOWN" in flags

            and

            (
                "STRUCTURE_CONFLICT" in flags
                or "EMA_BULL_STRONG" in flags
            )

        ):

            return "TRAP_RISK_DOWN"

        return None

    except Exception as e:

        print(
            f"[TRAP_ENGINE_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# BASIC STRUCTURE ENGINE v1
# =========================
def detect_basic_structure(candles):

    try:

        if not candles or len(candles) < 20:
            return None

        highs = [float(x[2]) for x in candles[-12:]]
        lows = [float(x[3]) for x in candles[-12:]]

        prev_high = max(highs[:6])
        last_high = max(highs[6:])

        prev_low = min(lows[:6])
        last_low = min(lows[6:])

        # =====================
        # BULLISH STRUCTURE
        # =====================

        if (
            last_high > prev_high
            and last_low > prev_low
        ):
            return "STRUCTURE_HH_HL"

        # =====================
        # BEARISH STRUCTURE
        # =====================

        if (
            last_high < prev_high
            and last_low < prev_low
        ):
            return "STRUCTURE_LH_LL"

        # =====================
        # RANGE STRUCTURE
        # =====================

        if (
            abs(last_high - prev_high) / prev_high * 100 <= 0.4
            and abs(last_low - prev_low) / prev_low * 100 <= 0.4
        ):
            return "STRUCTURE_RANGE"

        return None

    except Exception as e:

        print(
            f"[STRUCTURE_ENGINE_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# BOS ENGINE v1
# =========================
def detect_bos(candles):

    try:

        if not candles or len(candles) < 20:
            return None

        highs = [float(x[2]) for x in candles[-10:]]
        lows = [float(x[3]) for x in candles[-10:]]

        prev_high = max(highs[:5])
        last_high = max(highs[5:])

        prev_low = min(lows[:5])
        last_low = min(lows[5:])

        # =====================
        # BOS UP
        # =====================

        if last_high > prev_high * 1.003:
            return "BOS_UP"

        # =====================
        # BOS DOWN
        # =====================

        if last_low < prev_low * 0.997:
            return "BOS_DOWN"

        return None

    except Exception as e:

        print(
            f"[BOS_ENGINE_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# RECLAIM ENGINE v1
# =========================
def detect_reclaim(flags):

    try:

        # =====================
        # RECLAIM UP
        # =====================

        if (

            "BOS_DOWN" in flags

            and

            (
                "PRESSURE_UP" in flags
                or "BULLISH_SHIFT" in flags
            )

        ):

            return "RECLAIM_UP"

        # =====================
        # RECLAIM DOWN
        # =====================

        if (

            "BOS_UP" in flags

            and

            (
                "PRESSURE_DOWN" in flags
                or "BEARISH_SHIFT" in flags
            )

        ):

            return "RECLAIM_DOWN"

        return None

    except Exception as e:

        print(
            f"[RECLAIM_ENGINE_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# CONTINUATION ENGINE v1
# =========================
def detect_continuation_quality(flags):

    try:

        # =====================
        # STRONG LONG CONTINUATION
        # =====================

        if (

            "BREAKOUT_CONFIRM_UP" in flags

            and

            "PRESSURE_UP" in flags

            and

            (
                "STRUCTURE_HH_HL" in flags
                or "BOS_UP" in flags
            )

        ):

            return "STRONG_CONTINUATION_UP"

        # =====================
        # STRONG SHORT CONTINUATION
        # =====================

        if (

            "BREAKOUT_CONFIRM_DOWN" in flags

            and

            "PRESSURE_DOWN" in flags

            and

            (
                "STRUCTURE_LH_LL" in flags
                or "BOS_DOWN" in flags
            )

        ):

            return "STRONG_CONTINUATION_DOWN"

        return None

    except Exception as e:

        print(
            f"[CONTINUATION_ENGINE_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# EXHAUSTION ENGINE v1
# =========================
def detect_exhaustion(flags):

    try:

        # =====================
        # BULL EXHAUSTION
        # =====================

        if (

            "BREAKOUT_CONFIRM_UP" in flags

            and

            "PRESSURE_UP" not in flags

            and

            "STRUCTURE_HH_HL" not in flags

        ):

            return "BULL_EXHAUSTION"

        # =====================
        # BEAR EXHAUSTION
        # =====================

        if (

            "BREAKOUT_CONFIRM_DOWN" in flags

            and

            "PRESSURE_DOWN" not in flags

            and

            "STRUCTURE_LH_LL" not in flags

        ):

            return "BEAR_EXHAUSTION"

        return None

    except Exception as e:

        print(
            f"[EXHAUSTION_ENGINE_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# LIQUIDITY SWEEP ENGINE v1
# =========================
def detect_liquidity_sweep(flags):

    try:

        # =====================
        # SWEEP LOW
        # =====================

        if (

            "BOS_DOWN" in flags

            and

            (
                "RECLAIM_UP" in flags
                or "BULLISH_SHIFT" in flags
            )

        ):

            return "SWEEP_LOW"

        # =====================
        # SWEEP HIGH
        # =====================

        if (

            "BOS_UP" in flags

            and

            (
                "RECLAIM_DOWN" in flags
                or "BEARISH_SHIFT" in flags
            )

        ):

            return "SWEEP_HIGH"

        return None

    except Exception as e:

        print(
            f"[LIQUIDITY_SWEEP_ERROR] {e}",
            flush=True
        )

        return None


# =========================
# MARKET REGIME ENGINE v1
# =========================
def detect_market_regime(flags):

    try:

        # =====================
        # TREND MARKET
        # рынок трендовый
        # =====================

        if (
            (
                "EMA_BULL_STRONG" in flags
                or "EMA_BEAR_STRONG" in flags
            )
            and
            (
                "MTF_LONG_ALIGN" in flags
                or "MTF_SHORT_ALIGN" in flags
            )
        ):

            return "TREND_MARKET"

        # =====================
        # VOLATILE MARKET
        # рынок резкий / нервный
        # =====================

        if (
            "VOL_SPIKE" in flags
            or "ATR_EXPANSION" in flags
        ):

            return "VOLATILE_MARKET"

        # =====================
        # RANGE MARKET
        # рынок в боковике
        # =====================

        if (
            "STRUCTURE_RANGE" in flags
            or "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
        ):

            return "RANGE_MARKET"

        # =====================
        # DEAD MARKET
        # рынок слабый / тухлый
        # =====================

        if (
            "EMA_FLAT" in flags
            and "PRESSURE_UP" not in flags
            and "PRESSURE_DOWN" not in flags
        ):

            return "DEAD_MARKET"

        return "UNKNOWN_MARKET"

    except Exception as e:

        print(
            f"[MARKET_REGIME_ENGINE_ERROR] {e}",
            flush=True
        )

        return "UNKNOWN_MARKET"

# =========================
# ENTRY QUALITY ENGINE v1
# =========================
def detect_entry_quality(flags):

    try:

        # =====================
        # HIGH QUALITY LONG ENTRY
        # =====================

        if (

            (
                "LAUNCH_PROXIMITY_UP" in flags
                or "BULLISH_SHIFT" in flags
            )

            and

            "EXPLOSION_READY_UP" not in flags

            and

            "BULL_EXHAUSTION" not in flags

        ):

            return "HIGH_QUALITY_LONG_ENTRY"

        # =====================
        # HIGH QUALITY SHORT ENTRY
        # =====================

        if (

            (
                "LAUNCH_PROXIMITY_DOWN" in flags
                or "BEARISH_SHIFT" in flags
            )

            and

            "EXPLOSION_READY_DOWN" not in flags

            and

            "BEAR_EXHAUSTION" not in flags

        ):

            return "HIGH_QUALITY_SHORT_ENTRY"

        return None

    except Exception as e:

        print(
            f"[ENTRY_QUALITY_ENGINE_ERROR] {e}",
            flush=True
        )

        return None

# =========================
# LATE ENTRY FILTER
# =========================
def detect_late_entry(sig):

    try:

        price = float(sig.get("price") or 0)
        ema20 = float(sig.get("ema20") or 0)
        stage = str(sig.get("stage") or "")
        entry = str(sig.get("entry") or "")

        if price <= 0 or ema20 <= 0:
            return False, "no_ema_data"

        distance_pct = abs(price - ema20) / ema20 * 100

        if (
            distance_pct >= 2.5
            and (
                "EXPANSION" in stage
                or "CONFIRM" in entry
            )
        ):

            return True, f"late_entry_distance_{round(distance_pct, 2)}%"

        return False, f"entry_ok_distance_{round(distance_pct, 2)}%"

    except Exception as e:

        print(
            f"[LATE_ENTRY_ERROR] {e}",
            flush=True
        )

        return False, "late_entry_error"

# =========================
# RETEST DETECTOR
# =========================
def detect_retest_entry(sig):

    try:

        price = float(sig.get("price") or 0)

        ema20 = float(sig.get("ema20") or 0)

        stage = str(sig.get("stage") or "")

        flags = set(sig.get("flags", []))

        if price <= 0 or ema20 <= 0:

            return False, "no_price"

        distance_pct = (
            abs(price - ema20) / ema20 * 100
        )

        # =====================
        # LONG RETEST
        # =====================

        if (
            (
                "PRESSURE_UP" in flags
                or "BULLISH_SHIFT" in flags
            )
            and distance_pct <= 1.2
            and (
                "ACCUMULATION" in stage
                or "TRANSITION" in stage
            )
        ):

            return True, "long_retest_ready"

        # =====================
        # SHORT RETEST
        # =====================

        if (
            (
                "PRESSURE_DOWN" in flags
                or "BEARISH_SHIFT" in flags
            )
            and distance_pct <= 1.2
            and (
                "ACCUMULATION" in stage
                or "TRANSITION" in stage
            )
        ):

            return True, "short_retest_ready"

        # =====================
        # EXPANSION / PREMOVE EXCEPTION
        # =====================

        if sig.get("signal_mode") in (
            "EXPANSION",
            "PREMOVE",
        ):

            print(
                f"[RETEST_BYPASS] "
                f"{sig.get('symbol')} "
                f"mode={sig.get('signal_mode')}",
                flush=True
            )

            return True, "retest_bypass"

        return False, "no_retest"

    except Exception as e:

        print(
            f"[RETEST_ERROR] {e}",
            flush=True
        )

        return False, "retest_error"

# =========================
# MARKET REGIME FILTER
# =========================

def market_regime_filter(btc_signal):
    """
    Возвращает режим рынка:
    RISK_ON  — лучше LONG
    RISK_OFF — лучше SHORT / осторожно с LONG
    NEUTRAL  — можно только сильные сетапы
    """

    try:
        if not btc_signal or not isinstance(btc_signal, dict):
            return {
                "regime": "NEUTRAL",
                "score": 0,
                "reason": "no_btc_signal"
            }

        flags = set(btc_signal.get("flags", []))
        score = 0
        reasons = []

        if "EMA_BULL" in flags or "EMA_BULL_STRONG" in flags:
            score += 2
            reasons.append("BTC EMA bull")

        if "EMA_BEAR" in flags or "EMA_BEAR_STRONG" in flags:
            score -= 2
            reasons.append("BTC EMA bear")

        if "PRESSURE_UP" in flags:
            score += 2
            reasons.append("BTC pressure up")

        if "PRESSURE_DOWN" in flags:
            score -= 2
            reasons.append("BTC pressure down")

        if "BREAKOUT_CONFIRM_UP" in flags or "ACCELERATION_UP" in flags:
            score += 2
            reasons.append("BTC impulse up")

        if "BREAKOUT_CONFIRM_DOWN" in flags or "ACCELERATION_DOWN" in flags:
            score -= 2
            reasons.append("BTC impulse down")

        if "STRUCTURE_CONFLICT" in flags:
            score -= 1
            reasons.append("BTC structure conflict")

        if score >= 3:
            regime = "RISK_ON"
        elif score <= -3:
            regime = "RISK_OFF"
        else:
            regime = "NEUTRAL"

        return {
            "regime": regime,
            "score": score,
            "reason": ", ".join(reasons) if reasons else "mixed"
        }

    except Exception as e:
        print(f"[REGIME_FILTER_ERROR] {e}", flush=True)

        return {
            "regime": "NEUTRAL",
            "score": 0,
            "reason": "error"
        }

def detect_liquidity_zones(candles, lookback=40, tolerance_pct=0.45):

    try:

        if candles is None or len(candles) < 20:
            return {
                "equal_highs": False,
                "equal_lows": False,
                "liquidity_above": None,
                "liquidity_below": None,
                "dist_above_pct": None,
                "dist_below_pct": None,
                "liq_flags": [],
                "liq_score": 0,
                "liq_state": "NO_LIQUIDITY_DATA"
            }

        highs = []
        lows = []
        closes = []

        for c in candles[-lookback:]:

            try:
                # формат dict
                if isinstance(c, dict):
                    high = float(c.get("high"))
                    low = float(c.get("low"))
                    close = float(c.get("close"))

                # формат list/tuple: [ts, open, high, low, close, volume]
                else:
                    high = float(c[2])
                    low = float(c[3])
                    close = float(c[4])

                highs.append(high)
                lows.append(low)
                closes.append(close)

            except:
                continue

        if len(highs) < 20 or len(lows) < 20 or len(closes) < 20:
            return {
                "equal_highs": False,
                "equal_lows": False,
                "liquidity_above": None,
                "liquidity_below": None,
                "dist_above_pct": None,
                "dist_below_pct": None,
                "liq_flags": [],
                "liq_score": 0,
                "liq_state": "BAD_CANDLE_FORMAT"
            }

        current_price = float(closes[-1])

        max_high = max(highs)
        min_low = min(lows)

        high_cluster = []
        low_cluster = []

        # =========================
        # EQUAL HIGHS = стопы шортистов сверху
        # =========================

        for h in highs:
            dist = abs(h - max_high) / current_price * 100

            if dist <= tolerance_pct:
                high_cluster.append(h)

        # =========================
        # EQUAL LOWS = стопы лонгистов снизу
        # =========================

        for l in lows:
            dist = abs(l - min_low) / current_price * 100

            if dist <= tolerance_pct:
                low_cluster.append(l)

        equal_highs = len(high_cluster) >= 3
        equal_lows = len(low_cluster) >= 3

        liquidity_above = max(high_cluster) if equal_highs else None
        liquidity_below = min(low_cluster) if equal_lows else None

        dist_above_pct = None
        dist_below_pct = None

        if liquidity_above:
            dist_above_pct = round(
                ((liquidity_above - current_price) / current_price) * 100,
                2
            )

        if liquidity_below:
            dist_below_pct = round(
                ((current_price - liquidity_below) / current_price) * 100,
                2
            )

        liq_flags = []
        liq_score = 0

        if equal_highs:
            liq_flags.append("EQUAL_HIGHS")
            liq_flags.append("LIQUIDITY_ABOVE")
            liq_score += 2

        if equal_lows:
            liq_flags.append("EQUAL_LOWS")
            liq_flags.append("LIQUIDITY_BELOW")
            liq_score += 2

        # Близкая ликвидность — важнее, потому что её легче забрать
        if dist_above_pct is not None and 0 <= dist_above_pct <= 1.5:
            liq_flags.append("NEAR_LIQUIDITY_ABOVE")
            liq_score += 1

        if dist_below_pct is not None and 0 <= dist_below_pct <= 1.5:
            liq_flags.append("NEAR_LIQUIDITY_BELOW")
            liq_score += 1

        if liq_score >= 4:
            liq_state = "STRONG_LIQUIDITY_MAP"
        elif liq_score >= 2:
            liq_state = "ACTIVE_LIQUIDITY_MAP"
        else:
            liq_state = "WEAK_LIQUIDITY_MAP"

        return {
            "equal_highs": equal_highs,
            "equal_lows": equal_lows,
            "liquidity_above": liquidity_above,
            "liquidity_below": liquidity_below,
            "dist_above_pct": dist_above_pct,
            "dist_below_pct": dist_below_pct,
            "liq_flags": liq_flags,
            "liq_score": liq_score,
            "liq_state": liq_state
        }

    except Exception as e:

        print(
            f"[LIQUIDITY_MAP_ERROR] {e}",
            flush=True
        )

        return {
            "equal_highs": False,
            "equal_lows": False,
            "liquidity_above": None,
            "liquidity_below": None,
            "dist_above_pct": None,
            "dist_below_pct": None,
            "liq_flags": [],
            "liq_score": 0,
            "liq_state": "LIQUIDITY_ERROR"
        }
# =========================
# MAIN SIGNAL BUILDER
# =========================

# =====================
# TREND CANDIDATE ENGINE
# =====================

def is_trend_candidate(instId, ticker):

    try:

        last = float(ticker.get("lastPrice") or 0)
        prev = float(ticker.get("prevPrice24h") or 0)

        if prev <= 0:
            return False

        pct24 = abs((last - prev) / prev * 100)

        if pct24 >= 8:
            return True

        return False

    except Exception:

        return False
def build_signal(instId):
    print(f"[BUILD_SIGNAL_ENTER] {instId}", flush=True)
    
    flags = set()
    score = 0

    # =========================
    # CANDLES
    # =========================
    c5 = fetch_candles(instId, "5m", 120)
    c15 = fetch_candles(instId, "15m", 240)
    
    c1h = fetch_candles(instId, "1H", 240)
    c4h = fetch_candles(instId, "4H", 240)
    
    if not c5 or len(c5) < 20:
        print(f"[WARN] {instId} c5 not enough", flush=True)
        c5 = []
    
    if not c15 or len(c15) < 200:
        print(f"[WARN] {instId} c15 not enough", flush=True)
        c15 = []
    
    if not c5:
        price = 0
    else:
        price = float(c5[-1][4])

    # =========================
    # EMA
    # =========================
    ema_meta = get_ema_trend(c15) if c15 else {}
    ema_state = (ema_meta or {}).get("state", "EMA_UNKNOWN")

    ema_h1 = get_ema_trend(c1h) if c1h else {}
    ema_h4 = get_ema_trend(c4h) if c4h else {}
    
    h1_state = ema_h1.get("state", "EMA_UNKNOWN")
    h4_state = ema_h4.get("state", "EMA_UNKNOWN")
        
    if "EMA_BULL" in ema_state:
    
        flags.add("EMA_BULL")
    
        if "STRONG" in ema_state:
            flags.add("EMA_BULL_STRONG")
    
        elif "WEAK" in ema_state:
            flags.add("EMA_BULL_WEAK")
    
    elif "EMA_BEAR" in ema_state:
    
        flags.add("EMA_BEAR")
    
        if "STRONG" in ema_state:
            flags.add("EMA_BEAR_STRONG")
    
        elif "WEAK" in ema_state:
            flags.add("EMA_BEAR_WEAK")
    
    elif ema_state == "EMA_TRANSITION":
    
        flags.add("EMA_TRANSITION")
    
    elif ema_state == "EMA_FLAT":
    
        flags.add("EMA_FLAT")
    
    else:
    
        flags.add("EMA_MIXED")

        # =========================
        # HTF STRETCH FILTER
        # =========================
    
        h4_ema200 = (ema_h4 or {}).get("ema200")
    
        htf_stretched_long = False
        htf_stretched_short = False
    
        try:
    
            if h4_ema200:
    
                h4_distance_pct = (
                    abs(price - h4_ema200)
                    / h4_ema200
                ) * 100
    
                # =====================
                # OVEREXTENDED UP
                # =====================
    
                if (
                    price > h4_ema200
                    and h4_distance_pct >= 12
                ):
    
                    htf_stretched_long = True
    
                    print(
                        f"[HTF_STRETCH_LONG] "
                        f"{instId} "
                        f"dist={round(h4_distance_pct, 2)}%",
                        flush=True
                    )
    
                # =====================
                # OVEREXTENDED DOWN
                # =====================
    
                elif (
                    price < h4_ema200
                    and h4_distance_pct >= 12
                ):
    
                    htf_stretched_short = True
    
                    print(
                        f"[HTF_STRETCH_SHORT] "
                        f"{instId} "
                        f"dist={round(h4_distance_pct, 2)}%",
                        flush=True
                    )
    
        except Exception as e:
    
            print(
                f"[HTF_STRETCH_ERROR] "
                f"{instId} {e}",
                flush=True
            )

    # =========================
    # EMA FLOW SEED
    # =========================
    
    if (
        "EMA_BULL_STRONG" in flags
        and "PRESSURE_UP" not in flags
        and "PRESSURE_DOWN" not in flags
    ):
        flags.add("PRESSURE_UP")
        score += 1
    
    if (
        "EMA_BEAR_STRONG" in flags
        and "PRESSURE_UP" not in flags
        and "PRESSURE_DOWN" not in flags
    ):
        flags.add("PRESSURE_DOWN")
        score += 1

    # =========================
    # PRESSURE (пример базовый)
    # =========================
    try:
        pmeta = detect_pressure(c5)
    except:
        pmeta = {}

    pressure_detect = (pmeta or {}).get("pressure")
   


    # =========================
    # MOMENTUM / VOL
    # =========================
    try:
        vol_spike = detect_volume_spike(c5)
        atr_expansion = detect_atr_expansion(c5)
    except:
        vol_spike = False
        atr_expansion = False
    
    if vol_spike:
        flags.add("VOL_SPIKE")
        score += 1
    
    if atr_expansion:
        flags.add("ATR_EXPANSION")
        score += 1

    # =========================
    # OVERHEAT FILTER
    # =========================
    
    try:
    
        last_close = float(c5[-1][4])
        prev_close = float(c5[-4][4])
    
        move_pct_3 = (
            (last_close - prev_close)
            / prev_close
        ) * 100
    
    except:
        move_pct_3 = 0
    
    overheat_up = (
        move_pct_3 >= 4.5
    )
    
    overheat_down = (
        move_pct_3 <= -4.5
    )
    
    if overheat_up:
        flags.add("OVERHEAT_UP")
    
    if overheat_down:
        flags.add("OVERHEAT_DOWN")
     
    pressure, pmeta = liquidity_pressure(c5)

    # =========================
    # FINAL PRESSURE
    # =========================
    
    final_pressure = None
    
    if pressure == "UP":
        final_pressure = "UP"
    
    elif pressure == "DOWN":
        final_pressure = "DOWN"
    
    elif pressure_detect == "UP":
        final_pressure = "UP"
    
    elif pressure_detect == "DOWN":
        final_pressure = "DOWN"
    
    if (
        "EMA_BULL_STRONG" in flags
        and final_pressure is None
    ):
        final_pressure = "UP"
    
    elif (
        "EMA_BEAR_STRONG" in flags
        and final_pressure is None
    ):
        final_pressure = "DOWN"
    
    # =========================
    # APPLY FINAL PRESSURE
    # =========================
    
    if final_pressure == "UP":
    
        # block fake bearish context
        if (
            "EMA_BEAR_STRONG" not in flags
        ):
    
            flags.discard("PRESSURE_DOWN")
            flags.add("PRESSURE_UP")
            score += 1
    
    
    elif final_pressure == "DOWN":
    
        # block fake bearish pressure
        # against strong bullish HTF
    
        if not (
            h4_state in ["EMA_BULL", "EMA_BULL_STRONG"]
            and h1_state in ["EMA_BULL", "EMA_BULL_STRONG"]
        ):
    
            flags.discard("PRESSURE_UP")
            flags.add("PRESSURE_DOWN")
            score += 1
    
    
    # =========================
    # STRUCTURE CONFLICT
    # =========================
    
    if (
        (
            "PRESSURE_UP" in flags
            and (
                "EMA_BEAR" in flags
                or "EMA_BEAR_STRONG" in flags
            )
        )
    
        or
    
        (
            "PRESSURE_DOWN" in flags
            and (
                "EMA_BULL" in flags
                or "EMA_BULL_STRONG" in flags
            )
        )
    ):
        flags.add("STRUCTURE_CONFLICT")
    
    
    # =========================
    # AUTO CONTINUATION PRO
    # =========================
    
    long_cont_quality = (
    
        "PRESSURE_UP" in flags
    
        and (
    
            "EMA_BULL" in flags
            or "EMA_BULL_STRONG" in flags
        )
    
        and (
    
            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_UP" in flags
            or "VOL_SPIKE" in flags
            or "ATR_EXPANSION" in flags
            or "COMP_5M" in flags
            or "COMP_15M" in flags
        )
    
        and "OVERHEAT_UP" not in flags
        and "STRUCTURE_CONFLICT" not in flags
    )
    
    
    short_cont_quality = (
    
        "PRESSURE_DOWN" in flags
    
        and (
    
            "EMA_BEAR" in flags
            or "EMA_BEAR_STRONG" in flags
        )
    
        and (
    
            "BREAKOUT_CONFIRM_DOWN" in flags
            or "BREAKOUT_DOWN" in flags
            or "VOL_SPIKE" in flags
            or "ATR_EXPANSION" in flags
            or "COMP_5M" in flags
            or "COMP_15M" in flags
        )
    
        and "OVERHEAT_DOWN" not in flags
        and "STRUCTURE_CONFLICT" not in flags
    )
    
    
    if long_cont_quality:
        flags.add("CONTINUATION_UP")
        score += 2
    
    
    if short_cont_quality:
        flags.add("CONTINUATION_DOWN")
        score += 2
    
    
    # =========================
    # MTF DIRECTION BIAS
    # =========================
    
    mtf_long_bias = False
    mtf_short_bias = False
    
    mtf_score = 0
    
    
    # =========================
    # LONG BIAS
    # =========================
    
    if (
        h4_state in ["EMA_BULL_STRONG", "EMA_BULL"]
        and h1_state in [
            "EMA_BULL_STRONG",
            "EMA_BULL",
            "EMA_BULL_WEAK"
        ]
    ):
        mtf_long_bias = True
    
    
    # =========================
    # SHORT BIAS
    # =========================
    
    if (
        h4_state in ["EMA_BEAR_STRONG", "EMA_BEAR"]
        and h1_state in [
            "EMA_BEAR_STRONG",
            "EMA_BEAR",
            "EMA_BEAR_WEAK"
        ]
    ):
        mtf_short_bias = True
    
    
        # =========================
        # APPLY LONG ALIGN
        # =========================
        
        if mtf_long_bias and (
        
            "PRESSURE_UP" in flags
            or "CONTINUATION_UP" in flags
            or "BREAKOUT_UP" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
        ):
        
            flags.add("MTF_LONG_ALIGN")
        
            # STRONG ALIGN
            if (
                h4_state == "EMA_BULL_STRONG"
                and h1_state == "EMA_BULL_STRONG"
            ):
                mtf_score += 2
        
            # NORMAL ALIGN
            else:
                mtf_score += 1

        # =========================
        # APPLY SHORT ALIGN
        # =========================
        
        if mtf_short_bias and (
        
            "PRESSURE_DOWN" in flags
            or "CONTINUATION_DOWN" in flags
            or "BREAKOUT_DOWN" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
        ):
    
            flags.add("MTF_SHORT_ALIGN")
    
            # STRONG ALIGN
            if (
                h4_state == "EMA_BEAR_STRONG"
                and h1_state == "EMA_BEAR_STRONG"
            ):
                mtf_score += 1
        
    
    # =========================
    # PULLBACK ENGINE
    # =========================
    
    pullback_long = (
        "EMA_BULL" in flags
        and "PRESSURE_UP" in flags
        and "VOL_SPIKE" not in flags
        and "BREAKOUT_CONFIRM_UP" not in flags
        and "OVERHEAT_UP" not in flags
        and score >= 4
    )
    
    pullback_short = (
        "EMA_BEAR" in flags
        and "PRESSURE_DOWN" in flags
        and "VOL_SPIKE" not in flags
        and "BREAKOUT_CONFIRM_DOWN" not in flags
        and "OVERHEAT_DOWN" not in flags
        and score >= 4
    )
    
    if pullback_long:
        flags.add("PULLBACK_LONG")
        score += 1
    
    if pullback_short:
        flags.add("PULLBACK_SHORT")
        score += 1

    # =========================
    # COMPRESSION
    # =========================
    
    comp5, _ = compression_ok(c5)
    
    if comp5:
        flags.add("COMP_5M")
        score += 1
    
    comp15, _ = compression_ok(c15)
    
    if comp15:
        flags.add("COMP_15M")
        score += 1


    # =========================
    # COMPRESSION PRO ENGINE
    # =========================
    
    comp_pro_5m = compression_pro(c5)
    comp_pro_15m = compression_pro(c15)
    
    compression_score = 0
    
    # 5M
    if comp_pro_5m.get("active"):
    
        flags.add("COMP_PRO_5M")
    
        cs = comp_pro_5m.get("score", 0)
    
        compression_score += cs
    
        score += min(cs * 0.5, 2)
    
        if DEBUG_VERBOSE:

            print(
                f"[COMP_PRO_5M] {instId} "
                f"score={cs} "
                f"reasons={comp_pro_5m.get('reasons')}"
            )
    
    # 15M
    if comp_pro_15m.get("active"):
    
        flags.add("COMP_PRO_15M")
    
        cs = comp_pro_15m.get("score", 0)
    
        compression_score += cs
    
        score += min(cs * 0.5, 2)
    
        if DEBUG_VERBOSE:

            print(
                f"[COMP_PRO_15M] {instId} "
                f"score={cs} "
                f"reasons={comp_pro_15m.get('reasons')}"
            )
    
    # =========================
    # ENERGY BUILDUP
    # =========================
    
    if compression_score >= 6:
    
        flags.add("ENERGY_BUILDUP")
    
        score += 1
    
        if DEBUG_VERBOSE:

            print(
                f"[ENERGY_BUILDUP] {instId} "
                f"compression_score={compression_score}"
            )

    # =========================
    # BASIC STRUCTURE
    # =========================
    
    structure = detect_basic_structure(c5)

    # =========================
    # BOS
    # =========================
    
    bos = detect_bos(c5)

    # =========================
    # RECLAIM
    # =========================
    
    reclaim = detect_reclaim(flags)
    
    if reclaim:
    
        flags.add(reclaim)
    
        score += 3
    
        print(
            f"[RECLAIM] {instId} {reclaim}",
            flush=True
        )
        
        if bos:
        
            flags.add(bos)

    # =========================
    # CONTINUATION QUALITY
    # =========================
    
    continuation = detect_continuation_quality(flags)

    # =========================
    # EXHAUSTION
    # =========================
    
    exhaustion = detect_exhaustion(flags)

    # =========================
    # ENTRY QUALITY
    # =========================
    
    entry_quality = detect_entry_quality(flags)
    
    if entry_quality:
    
        flags.add(entry_quality)
    
        score += 2
    
        print(
            f"[ENTRY_QUALITY] "
            f"{instId} {entry_quality}",
            flush=True
        )
    
    if exhaustion:
    
        flags.add(exhaustion)
    
        score -= 3
    
        print(
            f"[EXHAUSTION] "
            f"{instId} {exhaustion}",
            flush=True
        )
    
    if continuation:
    
        flags.add(continuation)
    
        score += 3
    
        print(
            f"[CONTINUATION_QUALITY] "
            f"{instId} {continuation}",
            flush=True
        )


    # =========================
    # BOS BOOST
    # =========================
    
    if (
        bos == "BOS_UP"
        and (
            "PRESSURE_UP" in flags
            or "BULLISH_SHIFT" in flags
        )
    ):
    
        score += 2
    
        print(
            f"[BOS_BOOST_UP] {instId}",
            flush=True
        )
    
    if (
        bos == "BOS_DOWN"
        and (
            "PRESSURE_DOWN" in flags
            or "BEARISH_SHIFT" in flags
        )
    ):
    
        score += 2
    
        print(
            f"[BOS_BOOST_DOWN] {instId}",
            flush=True
        )
        
        score += 3
    
        print(
            f"[BOS] {instId} {bos}",
            flush=True
        )
    
    if structure:
    
        flags.add(structure)
    
        score += 2
    
        print(
            f"[STRUCTURE] {instId} {structure}",
            flush=True
        )

    # =========================
    # ABSORPTION
    # =========================
    
    absorption = detect_absorption(c5)
    
    if absorption:
    
        flags.add(absorption)
    
        score += 2
    
        print(
            f"[ABSORPTION] {instId} {absorption}",
            flush=True
        )

    # =========================
    # PRESSURE SHIFT
    # =========================
    
    shift = detect_pressure_shift(flags)
    
    if shift:
    
        flags.add(shift)
    
        score += 3
    
        print(
            f"[PRESSURE_SHIFT] {instId} {shift}",
            flush=True
        )

    # =========================
    # LAUNCH READY
    # =========================
    
    launch = detect_launch_ready(flags)
    
    if launch:
    
        flags.add(launch)
    
        score += 4
    
        print(
            f"[LAUNCH_READY] {instId} {launch}",
            flush=True
        )

    # =========================
    # EARLY IMBALANCE
    # =========================
    
    imbalance = detect_early_imbalance(c5, flags)
    
    if imbalance:
    
        flags.add(imbalance)
    
        score += 3
    
        print(
            f"[EARLY_IMBALANCE] {instId} {imbalance}",
            flush=True
        )

    # =========================
    # EARLY LAUNCH
    # =========================
    
    early_launch = detect_early_launch(flags)
    
    if early_launch:
    
        flags.add(early_launch)
    
        score += 4
    
        print(
            f"[EARLY_LAUNCH] {instId} {early_launch}",
            flush=True
        )
    # =========================
    # ACCELERATION
    # =========================
    
    acceleration = detect_acceleration(flags)
    
    if acceleration:
    
        flags.add(acceleration)
    
        score += 4
    
        print(
            f"[ACCELERATION] {instId} {acceleration}",
            flush=True
        )
    
        
    # =====================
    # IMPULSE CONFIRMATION
    # =====================

    impulse_long = (

        "ACCELERATION_UP" in flags
        and "BREAKOUT_CONFIRM_UP" in flags
        and (
            "PRESSURE_LONG_PERSIST_2"
            in flags
            or
            "PRESSURE_LONG_PERSIST_3"
            in flags
        )
        and (
            "LAUNCH_PROXIMITY_UP"
            in flags
            or
            "EXPLOSION_READY_UP"
            in flags
        )

    )

    impulse_short = (

        "ACCELERATION_DOWN" in flags
        and "BREAKOUT_CONFIRM_DOWN" in flags
        and (
            "PRESSURE_SHORT_PERSIST_2"
            in flags
            or
            "PRESSURE_SHORT_PERSIST_3"
            in flags
        )
        and (
            "LAUNCH_PROXIMITY_DOWN"
            in flags
            or
            "EXPLOSION_READY_DOWN"
            in flags
        )

    )

    if impulse_long:

        flags.add(
            "IMPULSE_CONFIRMED_LONG"
        )

        score += 5

        print(
            f"[IMPULSE_LONG] "
            f"{instId}",
            flush=True
        )

    if impulse_short:

        flags.add(
            "IMPULSE_CONFIRMED_SHORT"
        )

        score += 5

        print(
            f"[IMPULSE_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # CONTINUATION ENGINE
    # =====================

    continuation_long = (

        "IMPULSE_CONFIRMED_LONG"
        in flags

        and "PRESSURE_UP"
        in flags

        and (
            "OI_BUILDUP"
            in flags
            or oi >= 0.15
        )

    )

    continuation_short = (

        "IMPULSE_CONFIRMED_SHORT"
        in flags

        and "PRESSURE_DOWN"
        in flags

        and (
            "OI_BUILDUP"
            in flags
            or oi >= 0.15
        )

    )

    if continuation_long:

        flags.add(
            "CONTINUATION_STRONG_LONG"
        )

        score += 4

        print(
            f"[CONTINUATION_LONG] "
            f"{instId}",
            flush=True
        )

    if continuation_short:

        flags.add(
            "CONTINUATION_STRONG_SHORT"
        )

        score += 4

        print(
            f"[CONTINUATION_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # IMPULSE EXHAUSTION
    # =====================

    exhaustion_long = (

        "IMPULSE_CONFIRMED_LONG"
        in flags

        and "OI_FADE"
        in flags

        and "PRESSURE_DOWN"
        in flags

    )

    exhaustion_short = (

        "IMPULSE_CONFIRMED_SHORT"
        in flags

        and "OI_FADE"
        in flags

        and "PRESSURE_UP"
        in flags

    )

    if exhaustion_long:

        flags.add(
            "IMPULSE_EXHAUSTION_LONG"
        )

        score -= 4

        print(
            f"[EXHAUSTION_LONG] "
            f"{instId}",
            flush=True
        )

    if exhaustion_short:

        flags.add(
            "IMPULSE_EXHAUSTION_SHORT"
        )

        score -= 4

        print(
            f"[EXHAUSTION_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # FAKE BREAKOUT
    # =====================

    fake_breakout_long = (

        "BREAKOUT_CONFIRM_UP"
        in flags

        and oi < 0.05

        and (
            "IMPULSE_CONFIRMED_LONG"
            not in flags
        )

    )

    fake_breakout_short = (

        "BREAKOUT_CONFIRM_DOWN"
        in flags

        and oi < 0.05

        and (
            "IMPULSE_CONFIRMED_SHORT"
            not in flags
        )

    )

    if fake_breakout_long:

        flags.add(
            "FAKE_BREAKOUT_LONG"
        )

        score -= 5

        print(
            f"[FAKE_BREAKOUT_LONG] "
            f"{instId}",
            flush=True
        )

    if fake_breakout_short:

        flags.add(
            "FAKE_BREAKOUT_SHORT"
        )

        score -= 5

        print(
            f"[FAKE_BREAKOUT_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # MARKET REGIME
    # =====================

    market_regime = "NEUTRAL"

    # =====================
    # BULL TREND
    # =====================

    if (

        "IMPULSE_CONFIRMED_LONG"
        in flags

        and "CONTINUATION_STRONG_LONG"
        in flags

        and oi >= 0.15

    ):

        market_regime = "TREND_BULL"

    # =====================
    # BEAR TREND
    # =====================

    elif (

        "IMPULSE_CONFIRMED_SHORT"
        in flags

        and "CONTINUATION_STRONG_SHORT"
        in flags

        and oi >= 0.15

    ):

        market_regime = "TREND_BEAR"

    # =====================
    # PANIC SELL
    # =====================

    elif (

        "LIQUIDATION_CASCADE_ACTIVE"
        in flags

        and "IMPULSE_CONFIRMED_SHORT"
        in flags

    ):

        market_regime = "PANIC_SELL"

    # =====================
    # SHORT SQUEEZE
    # =====================

    elif (

        "LIQUIDATION_CASCADE_ACTIVE"
        in flags

        and "IMPULSE_CONFIRMED_LONG"
        in flags

    ):

        market_regime = "SHORT_SQUEEZE"

    # =====================
    # RANGE
    # =====================

    elif (

        "RANGE_COMPRESSION"
        in flags

        and "TIGHT_RANGE"
        in flags

    ):

        market_regime = "RANGE"

    MARKET_REGIME_MEMORY[instId] = {
        "regime": market_regime,
        "time": time.time()
    }

    market_regime_value = market_regime
    
    print(
        f"[MARKET_REGIME] "
        f"{instId} "
        f"{market_regime}",
        flush=True
    )

    # =====================
    # SMART ENTRY ENGINE
    # =====================

    entry_quality = "NEUTRAL"

    # =====================
    # EARLY ENTRY
    # =====================

    if (

        "PREMOVE" in flags
        or "ENERGY_BUILDUP" in flags

    ) and not (
        "IMPULSE_CONFIRMED_LONG"
        in flags
        or
        "IMPULSE_CONFIRMED_SHORT"
        in flags
    ):

        entry_quality = "EARLY_ENTRY"

    # =====================
    # SAFE ENTRY
    # =====================

    elif (

        "IMPULSE_CONFIRMED_LONG"
        in flags
        or
        "IMPULSE_CONFIRMED_SHORT"
        in flags

    ) and not (
        "IMPULSE_EXHAUSTION_LONG"
        in flags
        or
        "IMPULSE_EXHAUSTION_SHORT"
        in flags
    ):

        entry_quality = "SAFE_ENTRY"

    # =====================
    # LATE ENTRY
    # =====================

    elif (

        "IMPULSE_EXHAUSTION_LONG"
        in flags
        or
        "IMPULSE_EXHAUSTION_SHORT"
        in flags

    ):

        entry_quality = "LATE_ENTRY"

    sig_entry_quality = entry_quality

    print(
        f"[ENTRY_QUALITY] "
        f"{instId} "
        f"{entry_quality}",
        flush=True
    )

    # =====================
    # RETEST ENGINE
    # =====================

    retest_long = (

        "BREAKOUT_CONFIRM_UP"
        in flags

        and "PRESSURE_UP"
        in flags

        and not (
            "IMPULSE_EXHAUSTION_LONG"
            in flags
        )

    )

    retest_short = (

        "BREAKOUT_CONFIRM_DOWN"
        in flags

        and "PRESSURE_DOWN"
        in flags

        and not (
            "IMPULSE_EXHAUSTION_SHORT"
            in flags
        )

    )

    if retest_long:

        flags.add(
            "RETEST_LONG"
        )

        score += 3

        print(
            f"[RETEST_LONG] "
            f"{instId}",
            flush=True
        )

    if retest_short:

        flags.add(
            "RETEST_SHORT"
        )

        score += 3

        print(
            f"[RETEST_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # TAKE PROFIT ENGINE
    # =====================

    take_profit_long = (

        "CONTINUATION_STRONG_LONG"
        in flags

        and (
            "OI_FADE"
            in flags
            or
            "IMPULSE_EXHAUSTION_LONG"
            in flags
        )

    )

    take_profit_short = (

        "CONTINUATION_STRONG_SHORT"
        in flags

        and (
            "OI_FADE"
            in flags
            or
            "IMPULSE_EXHAUSTION_SHORT"
            in flags
        )

    )

    if take_profit_long:

        flags.add(
            "TAKE_PROFIT_LONG"
        )

        print(
            f"[TAKE_PROFIT_LONG] "
            f"{instId}",
            flush=True
        )

    if take_profit_short:

        flags.add(
            "TAKE_PROFIT_SHORT"
        )

        print(
            f"[TAKE_PROFIT_SHORT] "
            f"{instId}",
            flush=True
        )

   
    # =====================
    # BUILDUP BONUS
    # =====================
    buildup_score = 0
    if buildup_score >= 8:

        score += 8

        flags.add("SMART_BUILDUP")

        print(
            f"[SMART_BUILDUP] "
            f"{instId} "
            f"score={buildup_score} "
            f"reasons={buildup_reasons}",
            flush=True
        )
    # =====================
    # VOLATILITY CLIMAX
    # =====================
    
    oi = 0.0

    volatility_climax_long = (

        "ACCELERATION_UP"
        in flags

        and abs(oi) >= 1.5

        and (
            "IMPULSE_EXHAUSTION_LONG"
            in flags
            or
            "OI_FADE"
            in flags
        )

    )

    volatility_climax_short = (

        "ACCELERATION_DOWN"
        in flags

        and abs(oi) >= 1.5

        and (
            "IMPULSE_EXHAUSTION_SHORT"
            in flags
            or
            "OI_FADE"
            in flags
        )

    )

    if volatility_climax_long:

        flags.add(
            "VOLATILITY_CLIMAX_LONG"
        )

        score -= 4

        print(
            f"[VOL_CLIMAX_LONG] "
            f"{instId}",
            flush=True
        )

    if volatility_climax_short:

        flags.add(
            "VOLATILITY_CLIMAX_SHORT"
        )

        score -= 4

        print(
            f"[VOL_CLIMAX_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # SMART REVERSAL
    # =====================

    reversal_long = (

        "IMPULSE_EXHAUSTION_SHORT"
        in flags

        and "PRESSURE_UP"
        in flags

        and (
            "BUYER_ABSORPTION"
            in flags
            or
            "BULLISH_SHIFT"
            in flags
        )

    )

    reversal_short = (

        "IMPULSE_EXHAUSTION_LONG"
        in flags

        and "PRESSURE_DOWN"
        in flags

        and (
            "SELLER_ABSORPTION"
            in flags
            or
            "BEARISH_SHIFT"
            in flags
        )

    )

    if reversal_long:

        flags.add(
            "SMART_REVERSAL_LONG"
        )

        score += 5

        print(
            f"[REVERSAL_LONG] "
            f"{instId}",
            flush=True
        )

    if reversal_short:

        flags.add(
            "SMART_REVERSAL_SHORT"
        )

        score += 5

        print(
            f"[REVERSAL_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # SMART MONEY TRAP ZONES
    # =====================

    liquidity_grab_long = (

        "LONG_FLUSH"
        in flags

        and (
            "BUYER_ABSORPTION"
            in flags
            or
            "SMART_REVERSAL_LONG"
            in flags
        )

    )

    liquidity_grab_short = (

        "SHORT_SQUEEZE"
        in flags

        and (
            "SELLER_ABSORPTION"
            in flags
            or
            "SMART_REVERSAL_SHORT"
            in flags
        )

    )

    if liquidity_grab_long:

        flags.add(
            "LIQUIDITY_GRAB_LONG"
        )

        score += 6

        print(
            f"[LIQUIDITY_GRAB_LONG] "
            f"{instId}",
            flush=True
        )

    if liquidity_grab_short:

        flags.add(
            "LIQUIDITY_GRAB_SHORT"
        )

        score += 6

        print(
            f"[LIQUIDITY_GRAB_SHORT] "
            f"{instId}",
            flush=True
        )

    # =====================
    # MARKET DOMINANCE
    # =====================

    dominance_long = 0
    dominance_short = 0

    # =====================
    # LONG DOMINANCE
    # =====================

    if "PRESSURE_UP" in flags:
        dominance_long += 2

    if (

        "BUYER_ABSORPTION"
        in flags

        and "PRESSURE_DOWN"
        in flags

        and not (
            "CONTINUATION_STRONG_SHORT"
            in flags
        )

    ):

        dominance_long += 2

    if "CONTINUATION_STRONG_LONG" in flags:
        dominance_long += 3

    if "OI_BUILDUP_LONG" in flags:
        dominance_long += 2

    if "SMART_REVERSAL_LONG" in flags:
        dominance_long += 3

    # =====================
    # SHORT DOMINANCE
    # =====================

    if "PRESSURE_DOWN" in flags:
        dominance_short += 2

    if (

        "SELLER_ABSORPTION"
        in flags

        and "PRESSURE_UP"
        in flags

        and not (
            "CONTINUATION_STRONG_LONG"
            in flags
        )

    ):

        dominance_short += 2

    if "CONTINUATION_STRONG_SHORT" in flags:
        dominance_short += 3

    if "OI_BUILDUP_SHORT" in flags:
        dominance_short += 2

    if "SMART_REVERSAL_SHORT" in flags:
        dominance_short += 3

    market_control = "NEUTRAL"

    if dominance_long > dominance_short:
        market_control = "BUYERS_CONTROL"

    elif dominance_short > dominance_long:
        market_control = "SELLERS_CONTROL"

    sig_market_control = market_control

    print(
        f"[MARKET_CONTROL] "
        f"{instId} "
        f"{market_control} "
        f"L={dominance_long} "
        f"S={dominance_short}",
        flush=True
    )

    # =====================
    # STRUCTURE SHIFT
    # =====================

    bullish_structure_shift = (

        "SMART_REVERSAL_LONG"
        in flags

        and "BUYERS_CONTROL"
        in str(sig_market_control)

        and (
            "IMPULSE_EXHAUSTION_SHORT"
            in flags
            or
            "BEAR_TRAP"
            in flags
        )

    )

    bearish_structure_shift = (

        "SMART_REVERSAL_SHORT"
        in flags

        and "SELLERS_CONTROL"
        in str(sig_market_control)

        and (
            "IMPULSE_EXHAUSTION_LONG"
            in flags
            or
            "BULL_TRAP"
            in flags
        )

    )

    if bullish_structure_shift:

        flags.add(
            "BULLISH_STRUCTURE_SHIFT"
        )

        score += 7

        print(
            f"[STRUCTURE_SHIFT_BULL] "
            f"{instId}",
            flush=True
        )

    if bearish_structure_shift:

        flags.add(
            "BEARISH_STRUCTURE_SHIFT"
        )

        score += 7

        print(
            f"[STRUCTURE_SHIFT_BEAR] "
            f"{instId}",
            flush=True
        )

    # =====================
    # SMART MONEY PHASE
    # =====================

    smart_phase = "NEUTRAL"

    # =====================
    # ACCUMULATION
    # =====================

    if (

        "RANGE_COMPRESSION"
        in flags

        and "BUYER_ABSORPTION"
        in flags

        and (
            "PRESSURE_UP"
            in flags
            or
            "OI_BUILDUP_LONG"
            in flags
        )

    ):

        smart_phase = "ACCUMULATION"

    # =====================
    # MANIPULATION
    # =====================

    elif (

        "FAKE_BREAKOUT_LONG"
        in flags

        or "FAKE_BREAKOUT_SHORT"
        in flags

        or "BULL_TRAP"
        in flags

        or "BEAR_TRAP"
        in flags

    ):

        smart_phase = "MANIPULATION"

    # =====================
    # EXPANSION
    # =====================

    elif (

        "IMPULSE_CONFIRMED_LONG"
        in flags

        or "IMPULSE_CONFIRMED_SHORT"
        in flags

    ) and (

        "CONTINUATION_STRONG_LONG"
        in flags

        or "CONTINUATION_STRONG_SHORT"
        in flags

    ):

        smart_phase = "EXPANSION"

    # =====================
    # DISTRIBUTION
    # =====================

    elif (

        "VOLATILITY_CLIMAX_LONG"
        in flags

        or "VOLATILITY_CLIMAX_SHORT"
        in flags

        or "TAKE_PROFIT_LONG"
        in flags

        or "TAKE_PROFIT_SHORT"
        in flags

    ):

        smart_phase = "DISTRIBUTION"

    # =====================
    # COLLAPSE
    # =====================

    elif (

        "PANIC_SELL"
        in str(market_regime)

        and "CONTINUATION_STRONG_SHORT"
        in flags

    ):

        smart_phase = "COLLAPSE"

    sig_smart_phase = smart_phase

    print(
        f"[SMART_PHASE] "
        f"{instId} "
        f"{smart_phase}",
        flush=True
    )

    # =====================
    # SMART FLOW CONFIRMATION
    # =====================

    smart_flow_score = 0

    # =====================
    # OI SUPPORT
    # =====================

    if "OI_BUILDUP" in flags:
        smart_flow_score += 2

    if "OI_BUILDUP_LONG" in flags:
        smart_flow_score += 2

    if "OI_BUILDUP_SHORT" in flags:
        smart_flow_score += 2

    # =====================
    # CONTINUATION
    # =====================

    if "CONTINUATION_STRONG_LONG" in flags:
        smart_flow_score += 3

    if "CONTINUATION_STRONG_SHORT" in flags:
        smart_flow_score += 3

    # =====================
    # PRESSURE PERSISTENCE
    # =====================

    if "PRESSURE_LONG_PERSIST_3" in flags:
        smart_flow_score += 2

    if "PRESSURE_SHORT_PERSIST_3" in flags:
        smart_flow_score += 2

    # =====================
    # LIQUIDATION FLOW
    # =====================

    if "CASCADE_SHORTS" in flags:
        smart_flow_score += 2

    # =====================
    # LIQUIDITY GRABS
    # =====================

    if (
        "LIQUIDITY_GRAB_LONG"
        in flags
        or
        "LIQUIDITY_GRAB_SHORT"
        in flags
    ):

        smart_flow_score += 2

    # =====================
    # REVERSAL FLOW
    # =====================

    if (
        "SMART_REVERSAL_LONG"
        in flags
        or
        "SMART_REVERSAL_SHORT"
        in flags
    ):

        smart_flow_score += 2

    if "CASCADE_LONGS" in flags:
        smart_flow_score += 2

    flow_quality = "WEAK_FLOW"

    if smart_flow_score >= 8:
        flow_quality = "STRONG_FLOW"

    elif smart_flow_score >= 5:
        flow_quality = "MODERATE_FLOW"

    sig_flow_quality = flow_quality

    print(
        f"[SMART_FLOW] "
        f"{instId} "
        f"{flow_quality} "
        f"score={smart_flow_score}",
        flush=True
    )
    
    # =====================
    # LIQUIDITY MAGNET
    # =====================

    liquidity_magnet = "NEUTRAL"

    # =====================
    # UP MAGNET
    # =====================

    if (

        "PRESSURE_UP"
        in flags

        and (
            "EXPLOSION_READY_UP"
            in flags
            or
            "LAUNCH_PROXIMITY_UP"
            in flags
        )

        and (
            "CASCADE_SHORTS"
            in flags
            or
            "LIQUIDITY_GRAB_LONG"
            in flags
        )

    ):

        liquidity_magnet = "MAGNET_UP"

    # =====================
    # DOWN MAGNET
    # =====================

    elif (

        "PRESSURE_DOWN"
        in flags

        and (
            "EXPLOSION_READY_DOWN"
            in flags
            or
            "LAUNCH_PROXIMITY_DOWN"
            in flags
        )

        and (
            "CASCADE_LONGS"
            in flags
            or
            "LIQUIDITY_GRAB_SHORT"
            in flags
        )

    ):

        liquidity_magnet = "MAGNET_DOWN"

    sig_liquidity_magnet = liquidity_magnet

    print(
        f"[LIQUIDITY_MAGNET] "
        f"{instId} "
        f"{liquidity_magnet}",
        flush=True
    )

    # =====================
    # MARKET TRAPS
    # =====================

    market_trap = "NO_TRAP"

    # =====================
    # BULL TRAP
    # =====================

    if (

        "BREAKOUT_UP"
        in flags

        and "ACCELERATION_UP"
        in flags

        and flow_quality == "WEAK_FLOW"

        and not (
            "CONTINUATION_STRONG_LONG"
            in flags
        )

    ):

        market_trap = "BULL_TRAP"

        flags.add(
            "BULL_TRAP"
        )

    # =====================
    # BEAR TRAP
    # =====================

    elif (

        "BREAKOUT_DOWN"
        in flags

        and "ACCELERATION_DOWN"
        in flags

        and flow_quality == "WEAK_FLOW"

        and not (
            "CONTINUATION_STRONG_SHORT"
            in flags
        )

    ):

        market_trap = "BEAR_TRAP"

        flags.add(
            "BEAR_TRAP"
        )

    sig_market_trap = market_trap

    print(
        f"[MARKET_TRAP] "
        f"{instId} "
        f"{market_trap}",
        flush=True
    )

    # =====================
    # MARKET EXHAUSTION
    # =====================

    market_exhaustion = "NO_EXHAUSTION"

    # =====================
    # LONG EXHAUSTION
    # =====================

    if (

        "ACCELERATION_UP"
        in flags

        and (
            "BREAKOUT_UP"
            in flags
            or
            "BREAKOUT_CONFIRM_UP"
            in flags
        )

        and flow_quality == "WEAK_FLOW"

        and not (
            "OI_BUILDUP_LONG"
            in flags
        )

    ):

        market_exhaustion = "LONG_EXHAUSTION"

        flags.add(
            "LONG_EXHAUSTION"
        )

    # =====================
    # SHORT EXHAUSTION
    # =====================

    elif (

        "ACCELERATION_DOWN"
        in flags

        and (
            "BREAKOUT_DOWN"
            in flags
            or
            "BREAKOUT_CONFIRM_DOWN"
            in flags
        )

        and flow_quality == "WEAK_FLOW"

        and not (
            "OI_BUILDUP_SHORT"
            in flags
        )

    ):

        market_exhaustion = "SHORT_EXHAUSTION"

        flags.add(
            "SHORT_EXHAUSTION"
        )

    sig_market_exhaustion = market_exhaustion

    print(
        f"[MARKET_EXHAUSTION] "
        f"{instId} "
        f"{market_exhaustion}",
        flush=True
    )
    # =====================
    # SMART MONEY INTENT
    # =====================

    smart_intent = "NEUTRAL"

    # =====================
    # ACCUMULATION INTENT
    # =====================

    if (

        smart_phase == "ACCUMULATION"

        and (
            "BUYERS_CONTROL"
            in str(sig_market_control)
        )

        and flow_quality == "STRONG_FLOW"

    ):

        smart_intent = "ACCUMULATING_LONG"

    # =====================
    # DISTRIBUTION INTENT
    # =====================

    elif (

        smart_phase == "DISTRIBUTION"

        and (
            "SELLERS_CONTROL"
            in str(sig_market_control)
        )

    ):

        smart_intent = "DISTRIBUTING_LONGS"

    # =====================
    # AGGRESSIVE EXPANSION
    # =====================

    elif (

        smart_phase == "EXPANSION"

        and flow_quality == "STRONG_FLOW"

        and (
            "CONTINUATION_STRONG_LONG"
            in flags
            or
            "CONTINUATION_STRONG_SHORT"
            in flags
        )

    ):

        smart_intent = "AGGRESSIVE_EXPANSION"

    # =====================
    # MANIPULATION
    # =====================

    elif (

        smart_phase == "MANIPULATION"

        or "BULL_TRAP"
        in flags

        or "BEAR_TRAP"
        in flags

    ):

        smart_intent = "MANIPULATING_LIQUIDITY"

    sig_smart_intent = smart_intent

    print(
        f"[SMART_INTENT] "
        f"{instId} "
        f"{smart_intent}",
        flush=True
    )

    # =====================
    # CONTEXT PRIORITY
    # =====================

    context_priority = 0

    # =====================
    # STRUCTURE SHIFT
    # =====================

    if (
        "BULLISH_STRUCTURE_SHIFT"
        in flags
        or
        "BEARISH_STRUCTURE_SHIFT"
        in flags
    ):

        context_priority += 5

    # =====================
    # SMART FLOW
    # =====================

    if flow_quality == "STRONG_FLOW":

        context_priority += 4

    # =====================
    # SMART PHASE
    # =====================

    if smart_phase == "EXPANSION":

        context_priority += 3

    if smart_phase == "ACCUMULATION":

        context_priority += 2

    # =====================
    # LIQUIDITY GRABS
    # =====================

    if (
        "LIQUIDITY_GRAB_LONG"
        in flags
        or
        "LIQUIDITY_GRAB_SHORT"
        in flags
    ):

        context_priority += 4

    # =====================
    # REVERSAL
    # =====================

    if (
        "SMART_REVERSAL_LONG"
        in flags
        or
        "SMART_REVERSAL_SHORT"
        in flags
    ):

        context_priority += 4

    context_grade = "LOW_CONTEXT"

    if context_priority >= 12:

        context_grade = "ELITE_CONTEXT"
    
    elif context_priority >= 7:
    
        context_grade = "HIGH_CONTEXT"
    
    elif context_priority >= 4:
    
        context_grade = "MID_CONTEXT"

    sig_context_grade = context_grade

    print(
        f"[CONTEXT_PRIORITY] "
        f"{instId} "
        f"{context_grade} "
        f"score={context_priority}",
        flush=True
    )

    # =====================
    # SIGNAL CONFIDENCE
    # =====================

    confidence_score = 0

    # =====================
    # FLOW
    # =====================

    if flow_quality == "STRONG_FLOW":
        confidence_score += 3

    elif flow_quality == "MODERATE_FLOW":
        confidence_score += 1

    # =====================
    # CONTEXT
    # =====================

    if context_grade == "ELITE_CONTEXT":
        confidence_score += 4

    elif context_grade == "HIGH_CONTEXT":
        confidence_score += 2

    # =====================
    # STRUCTURE
    # =====================

    if (
        "BULLISH_STRUCTURE_SHIFT"
        in flags
        or
        "BEARISH_STRUCTURE_SHIFT"
        in flags
    ):

        confidence_score += 3

    # =====================
    # REVERSAL
    # =====================

    if (
        "SMART_REVERSAL_LONG"
        in flags
        or
        "SMART_REVERSAL_SHORT"
        in flags
    ):

        confidence_score += 2

    # =====================
    # PENALTIES
    # =====================

    if flow_quality == "WEAK_FLOW":
        confidence_score -= 3

    if smart_phase == "MANIPULATION":
        confidence_score -= 2

    # =====================
    # FINAL CONFIDENCE
    # =====================

    signal_confidence = "LOW_CONFIDENCE"

    if confidence_score >= 9:

        signal_confidence = "ELITE_CONFIDENCE"

    elif confidence_score >= 6:

        signal_confidence = "HIGH_CONFIDENCE"

    elif confidence_score >= 3:

        signal_confidence = "MODERATE_CONFIDENCE"

    sig_signal_confidence = signal_confidence

    print(
        f"[SIGNAL_CONFIDENCE] "
        f"{instId} "
        f"{signal_confidence} "
        f"score={confidence_score}",
        flush=True
    )
    # =========================
    # SQUEEZE MATURITY
    # =========================
    
    squeeze_maturity = detect_squeeze_maturity(flags)
    
    if squeeze_maturity:
    
        flags.add(squeeze_maturity)
    
        score += 3
    
        print(
            f"[SQUEEZE_MATURITY] {instId} {squeeze_maturity}",
            flush=True
        )

    # =========================
    # LAUNCH PROXIMITY
    # =========================
    
    launch_proximity = detect_launch_proximity(flags)
    
    if launch_proximity:
    
        flags.add(launch_proximity)
    
        score += 4
    
        print(
            f"[LAUNCH_PROXIMITY] {instId} {launch_proximity}",
            flush=True
        )

    # =========================
    # EXPLOSION BUILDUP
    # =========================
    
    explosion_buildup = detect_explosion_buildup(flags)
    
    if explosion_buildup:
    
        flags.add(explosion_buildup)
    
        score += 5
    
        print(
            f"[EXPLOSION_BUILDUP] {instId} {explosion_buildup}",
            flush=True
        )

    # =========================
    # FAKE DUMP
    # =========================
    if fake_dump_ok(c5):
        flags.add("FAKE_DUMP")
        score += 1

    # =========================
    # VOLUME SPIKE
    # =========================
    if volume_spike_ok(c5):
        flags.add("VOL_SPIKE")
        score += 1

    # =========================
    # LIQUIDITY SWEEP
    # =========================
    sweep, _meta = liquidity_sweep(c5)
    if sweep:
        flags.add(sweep)
        score += 1

    # =========================
    # ATR EXPANSION
    # =========================
    if atr_expansion_ok(c5):
        flags.add("ATR_EXPANSION")
        score += 1

    # =========================
    # BREAKOUT
    # =========================
    br = breakout_ok(c5)
    if br == "UP":
        flags.add("BREAKOUT_UP")
        score += 1
    elif br == "DOWN":
        flags.add("BREAKOUT_DOWN")
        score += 1

    br_confirm = breakout_confirm_ok(c5)
    if br_confirm == "UP":
        flags.add("BREAKOUT_CONFIRM_UP")
        score += 2
    elif br_confirm == "DOWN":
        flags.add("BREAKOUT_CONFIRM_DOWN")
        score += 2

    # =========================
    # STRUCTURE CONFLICT
    # =========================
    
    bull_conflict = (
        "PRESSURE_UP" in flags
        and (
            "BREAKOUT_DOWN" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
        )
    )
    
    bear_conflict = (
        "PRESSURE_DOWN" in flags
        and (
            "BREAKOUT_UP" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
        )
    )
    
    if bull_conflict or bear_conflict:
    
        flags.add("STRUCTURE_CONFLICT")
    
        score -= 2

    # =========================
    # PRE-BREAKOUT PRESSURE
    # =========================
    pre_breakout = detect_pre_breakout_pressure(c5, flags, pmeta, ema_state)
    if pre_breakout:
        flags.add(pre_breakout)
        score += 1

    # =========================
    # CONFLUENCE BONUS
    # =========================
    long_confluence = (
        "PRESSURE_UP" in flags
        and "EMA_BULL" in flags
        and (
            "OB_WALL_BID" in flags
            or "OB_BIDS" in flags
        )
    )

    short_confluence = (
        "PRESSURE_DOWN" in flags
        and "EMA_BEAR" in flags
        and (
            "OB_WALL_ASK" in flags
            or "OB_ASKS" in flags
        )
    )

    if long_confluence:
        flags.add("CONFLUENCE_LONG")
        score += 1

    elif short_confluence:
        flags.add("CONFLUENCE_SHORT")
        score += 1
   

    
    # =========================
    # ACCUMULATION
    # =========================
    
    acc_score = accumulation_bias(flags)
    
    
    
    # =========================
    # SMART ACCUMULATION BOOST
    # =========================

    has_compression = (
        "COMP_5M" in flags
        or "COMP_15M" in flags
    )

    has_pressure = (
        "PRESSURE_UP" in flags
        or "PRESSURE_DOWN" in flags
    )

    if has_compression and has_pressure:
        score += 1

        print(
            f"[SMART_ACC_BOOST] {instId} "
            f"score={score} acc={acc_score}",
            flush=True
        )

    elif has_compression and acc_score >= 3:
        score += 0.5

        print(
            f"[SMART_ACC_LIGHT] {instId} "
            f"score={score} acc={acc_score}",
            flush=True
        )

    # =========================
    # MARKET ANALYSIS
    # =========================
    strong_setup = score >= PRO_EDGE_MIN_SCORE
    rsi_state = get_rsi_state(c5) or {}
    rsi7 = rsi_state.get("rsi7")
    rsi14 = rsi_state.get("rsi14")

    direction_text, reasons, up_w, down_w = direction_hint(flags)
    direction_code = direction_code_from_text(direction_text)

    # =========================
    # HARD DIRECTION FIX
    # =========================
    
    if not direction_code:
    
        if (
            "BREAKOUT_UP" in flags
            or "BREAKOUT_CONFIRM_UP" in flags
            or "CONTINUATION_UP" in flags
            or "PRESSURE_UP" in flags
        ):
            direction_code = "UP"
    
        elif (
            "BREAKOUT_DOWN" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "CONTINUATION_DOWN" in flags
            or "PRESSURE_DOWN" in flags
        ):
            direction_code = "DOWN"

    # =========================
    # FLOW DEFAULT
    # =========================
    
    flow_ok = False
    
    # =========================
    # EMA DIRECTION BLOCK
    # =========================
    
    ema_bear = (
        "EMA_BEAR" in flags
        or "EMA_BEAR_STRONG" in flags
    )
    
    ema_bull = (
        "EMA_BULL" in flags
        or "EMA_BULL_STRONG" in flags
    )
    
    if direction_code == "UP" and ema_bear:
    
        print(
            f"[EMA_BLOCK_LONG] {instId}",
            flush=True
        )
    
    elif direction_code == "DOWN" and ema_bull:
    
        print(
            f"[EMA_BLOCK_SHORT] {instId}",
            flush=True
        )
    
    else:
        flow_ok = True

    tgt = liquidity_target(pmeta, flags, price)

    entry_zone = calc_entry_zone(price, pmeta, flags, direction_code)

    # =====================
    # TRAP DIRECTION BLOCK
    # =====================

    if (

        direction_code == "DOWN"

        and "SHORT_TRAP_RISK" in flags

    ):

        print(
            f"[SHORT_TRAP_BLOCK] "
            f"{instId}",
            flush=True
        )

        flow_ok = False

        score -= 4

        flags.add("TRAP_BLOCKED_SHORT")

    if (

        direction_code == "UP"

        and "LONG_TRAP_RISK" in flags

    ):

        print(
            f"[LONG_TRAP_BLOCK] "
            f"{instId}",
            flush=True
        )

        flow_ok = False

        score -= 4

        flags.add("TRAP_BLOCKED_LONG")
    
    # =========================
    # MTF DIRECTION BIAS
    # =========================
    
    mtf_long_bias = False
    mtf_short_bias = False
    
    if (
        h4_state in ["EMA_BULL_STRONG", "EMA_BULL"]
        and h1_state in ["EMA_BULL_STRONG", "EMA_BULL", "EMA_BULL_WEAK"]
    ):
        mtf_long_bias = True
    
    if (
        h4_state in ["EMA_BEAR_STRONG", "EMA_BEAR"]
        and h1_state in ["EMA_BEAR_STRONG", "EMA_BEAR", "EMA_BEAR_WEAK"]
    ):
        mtf_short_bias = True
    
    if mtf_long_bias and (
        pressure == "UP"
        or "PRESSURE_UP" in flags
        or "CONTINUATION_UP" in flags
        or "BREAKOUT_UP" in flags
    ):
        score += 1
        flags.add("MTF_LONG_ALIGN")
    
    if mtf_short_bias and (
        pressure == "DOWN"
        or "PRESSURE_DOWN" in flags
        or "CONTINUATION_DOWN" in flags
        or "BREAKOUT_DOWN" in flags
    ):
        score += 1
        flags.add("MTF_SHORT_ALIGN")
    
    # =====================
    # SMART BUILDUP PRIORITY
    # =====================
    
    buildup_score = 0
    buildup_reasons = []
    
    if (
        "RANGE_COMPRESSION" in flags
        or "TIGHT_RANGE" in flags
        or "COMP_PRO_5M" in flags
        or "COMP_PRO_15M" in flags
    ):
        buildup_score += 2
        buildup_reasons.append("compression")
    
    if (
        "BUYER_ABSORPTION" in flags
        or "SELLER_ABSORPTION" in flags
    ):
        buildup_score += 2
        buildup_reasons.append("absorption")
    
    if (
        "EQUAL_HIGHS" in flags
        or "EQUAL_LOWS" in flags
        or "LIQUIDITY_ABOVE" in flags
        or "LIQUIDITY_BELOW" in flags
    ):
        buildup_score += 2
        buildup_reasons.append("liquidity")
    
    if (
        "BULLISH_SHIFT" in flags
        or "BEARISH_SHIFT" in flags
    ):
        buildup_score += 2
        buildup_reasons.append("shift")
    
    if acc_score >= 3:
        buildup_score += 2
        buildup_reasons.append("accumulation")
    
    if (
        "MTF_LONG_ALIGN" in flags
        or "MTF_SHORT_ALIGN" in flags
    ):
        buildup_score += 1
        buildup_reasons.append("mtf_align")
    
    if buildup_score >= 7:
        score += 8
        flags.add("SMART_BUILDUP")
    
        print(
            f"[SMART_BUILDUP] {instId} "
            f"score={buildup_score} "
            f"reasons={buildup_reasons}",
            flush=True
        )
    # =========================
    # PRO EARLY EMA BOOST
    # =========================
    
    score = float(score)
    
    ema_boost = 0
    
    
    # =========================
    # STRONG EMA CONTEXT
    # =========================
    
    if "EMA_BULL" in flags and acc_score >= 1:
        ema_boost += 1
    
    if "EMA_BEAR" in flags and acc_score >= 1:
        ema_boost += 1
    
    
    # =========================
    # MIXED MARKET LOGIC
    # =========================
    
    if "EMA_MIXED" in flags:
    
        # mixed market but higher TF aligned
        if (
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
        ):
            ema_boost += 1
    
        # mixed market but pressure exists
        elif (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        ):
            ema_boost += 0.5
    
        # dead mixed market
        else:
            ema_boost += 0
    
    
    # =========================
    # EMA FLAT PENALTY
    # =========================
    
    if "EMA_FLAT" in flags:
        ema_boost -= 0.5
    
    
    # =========================
    # STRUCTURE CONFLICT PENALTY
    # =========================
    
    if "STRUCTURE_CONFLICT" in flags:
        ema_boost -= 1
    
    # =========================
    # SMART MONEY BOOST
    # =========================

    if "BUYER_ABSORPTION" in flags:
        ema_boost += 2

    if "SELLER_ABSORPTION" in flags:
        ema_boost += 2

    if "BULLISH_SHIFT" in flags:
        ema_boost += 2

    if "BEARISH_SHIFT" in flags:
        ema_boost += 2

    if "ACCELERATION_UP" in flags:
        ema_boost += 1

    if "ACCELERATION_DOWN" in flags:
        ema_boost += 1

    if "LAUNCH_PROXIMITY_UP" in flags:
        ema_boost += 1.5

    if "LAUNCH_PROXIMITY_DOWN" in flags:
        ema_boost += 1.5

    if "EXPLOSION_READY_UP" in flags:
        ema_boost += 1.5

    if "EXPLOSION_READY_DOWN" in flags:
        ema_boost += 1.5

    if "MTF_LONG_ALIGN" in flags:
        ema_boost += 1

    if "MTF_SHORT_ALIGN" in flags:
        ema_boost += 2

    if "BREAKOUT_CONFIRM_UP" in flags:
        ema_boost += 1

    if "BREAKOUT_CONFIRM_DOWN" in flags:
        ema_boost += 1
    # =========================
    # DEBUG
    # =========================
    
    if ema_boost != 0:
    
        print(
            f"[EMA_BOOST] {instId} "
            f"boost={ema_boost} "
            f"score_before={score} "
            f"acc={acc_score} "
            f"flags={list(flags)}",
            flush=True
        )
    
    
    # =========================
    # APPLY EMA BOOST
    # =========================
    
    score += ema_boost
    
    
    # =========================
    # APPLY MTF SCORE
    # =========================
    
    if mtf_score > 0:
    
        print(
            f"[MTF_BOOST] {instId} +{mtf_score}",
            flush=True
        )
    
        score += mtf_score
    
    
    # =========================
    # STAGE
    # =========================
    
    stage, stage_reason = smart_money_stage(
        score,
        flags
    )
    
    
    # =========================
    # EXPECTED MOVE
    # =========================
    
    exp_min, exp_max = expected_move_pct(
        c5,
        pmeta
    )
    
    
    # =========================
    # RESULT FILTER (FIXED)
    # =========================
    swing_only_candidate = False
    can_survive_for_swing = True
    
    if score < MIN_SCORE:
    
        has_breakout = (
            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
        )
    
        has_pressure = (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    
        has_continuation = (
            "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
        )
    
        # --- НОРМАЛЬНЫЙ SWING ПРОХОД ---
        normal_swing_pass = (
            (
                score >= SWING_BUILD_MIN_SCORE and (
                    acc_score >= 2
                    or has_breakout
                    or has_pressure
                    or has_continuation
                )
            )
            or score >= 3
        )
    
        # --- РАННИЙ ПРОХОД (PRO EARLY SURVIVAL) ---
        early_exception_pass = (
        
            # обычный ранний проход
            (
                score >= 1 and (
                    acc_score >= 3
                    or (has_breakout and has_pressure)
                    or (acc_score >= 2 and has_pressure)
                    or (acc_score >= 2 and has_breakout)
                )
            )
        
            # PRESSURE BUILDUP
            or (
                acc_score >= 2
                and has_pressure
            )
        
            # TRANSITION STAGE
            or (
                stage in ["🟠 TRANSITION", "🟣 ACCUMULATION"]
                and acc_score >= 2
            )
        
            # CONTINUATION BUILDUP
            or (
                has_continuation
                and acc_score >= 1
            )
        
            # BREAKOUT BUILDUP
            or (
                has_breakout
                and acc_score >= 1
            )
        )
            
        can_survive_for_swing = (
            normal_swing_pass or early_exception_pass
        )
    
        # ❗ ГЛАВНОЕ ИЗМЕНЕНИЕ — НЕ ДЕЛАЕМ return
        if not can_survive_for_swing:
            signal_type = "WEAK_SKIP"
        else:
            signal_type = "SWING_EARLY"
            swing_only_candidate = True
    
    
    # =========================
    # SWING EARLY
    # =========================
    
    swing_only_candidate = False
    
    if (
        score <= 2
        and acc_score >= 2
    ):
        swing_only_candidate = True
    
    
    # =========================
    # SWING QUALITY FILTER
    # =========================
    
    swing_quality = (
    
        "COMP_5M" in flags
        or "COMP_15M" in flags
        or "VOL_SPIKE" in flags
        or "BREAKOUT_UP" in flags
        or "BREAKOUT_DOWN" in flags
        or "BREAKOUT_CONFIRM_UP" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
        or "CONTINUATION_UP" in flags
        or "CONTINUATION_DOWN" in flags
    )
    
    if swing_only_candidate and not swing_quality:
        swing_only_candidate = False
    
    
    if swing_only_candidate:
        signal_type = "SWING_EARLY"
    
    
    elif score <= 0:
        signal_type = "WEAK_SKIP"

    # =========================
    # STAGE CALCULATION
    # =========================
    
    stage, stage_reason = smart_money_stage(score, flags)

    if "signal_type" not in locals():

        signal_type = "NORMAL"    


    # =========================
    # SIGNAL TYPE DEBUG
    # =========================
    
    print(
        f"[SIGNAL_TYPE] {instId} type={signal_type} "
        f"score={score} acc={acc_score} stage={stage} "
        f"swing_only={swing_only_candidate}",
        flush=True
    )


    # =========================
    # SIGNAL OBJECT
    # =========================
    
    tier = get_signal_tier(score, acc_score)
    
    if "CONTINUATION_UP" in flags:
        print(f"[CONTINUATION_UP] {instId}", flush=True)
    
    if "CONTINUATION_DOWN" in flags:
        print(f"[CONTINUATION_DOWN] {instId}", flush=True)

    entry_price, stop, entry_reason = decide_entry(stage, flags, price, c5)

    # =========================
    # TEMP TARGET
    # =========================
    
    tgt = 0
    
    if direction_code == "UP":
        tgt = round(price * 1.03, 6)
    
    elif direction_code == "DOWN":
        tgt = round(price * 0.97, 6)

    # =========================
    # SETUP RANKING
    # =========================
    
    setup_rank, rank_score, rank_reasons = detect_setup_rank(
        flags,
        score,
        acc_score
    )
    
    print(
        f"[SETUP_RANK] {instId} "
        f"{setup_rank} "
        f"score={rank_score} "
        f"reasons={rank_reasons}",
        flush=True
    )
        
    signal = {
        "instId": instId,
        "symbol": instId,
        "price": price,
        "score": score,

        "candles": c5,

        "setup_rank": setup_rank,
        "rank_score": rank_score,
        "rank_reasons": rank_reasons,
    
        # уже есть — оставляем
        "swing_only_candidate": swing_only_candidate,
    
        "below_main_min_score": score < MIN_SCORE,
        "tier": tier,
        "flags": list(flags),
        "pmeta": pmeta,
        "acc_score": acc_score,
        "strong_setup": strong_setup,
        "direction": direction_text,
        "direction_code": direction_code,
        "dir_reasons": reasons,
        "up_w": up_w,
        "down_w": down_w,
        "entry": entry_reason,
        "stop": stop,
        "entry_reason": entry_reason,
        "entry_type": entry_reason,
        "entry_price": entry_price,
        "entry_zone": entry_zone,
        
        "stage": stage,
        "stage_reason": stage_reason,
    
        "target": tgt,
        "exp_move_min": exp_min,
        "exp_move_max": exp_max,
    
        "ema20": (ema_meta or {}).get("ema20"),
        "ema50": (ema_meta or {}).get("ema50"),
        "ema200": (ema_meta or {}).get("ema200"),
    
        "ema_state": ema_state,
        "h1_state": h1_state,
        "h4_state": h4_state,
        "rsi7": rsi7,
        "rsi14": rsi14,
        "rsi_state": (rsi_state or {}).get("state"),
    
        "ts": now_ts(),
        "created_at": time.time(),
    }

    ep_data = detect_early_pressure(signal)

    if not ep_data or not isinstance(ep_data, dict):
    
        print(
            f"[EP_DATA_INVALID] "
            f"{instId} "
            f"ep_data={ep_data}",
            flush=True
        )
    
        ep_data = {}
    
   
    signal.update(ep_data)

    # =====================
    # MARKET STORY
    # =====================
    
    market_story = generate_market_story(
        signal
    )
    
    signal["market_story"] = market_story

    # =====================
    # CAPITAL FLOW
    # =====================
    
    capital_data = analyze_capital_flow(
        signal
    )
    
    signal.update(capital_data)
    
    print(
        f"[CAPITAL_FLOW] "
        f"{instId} "
        f"state={signal.get('capital_state')} "
        f"score={signal.get('capital_score')}",
        flush=True
    )
    
    print(
        f"[MARKET_STORY] "
        f"{instId} "
        f"story={market_story}",
        flush=True
    )

    # =====================
    # RANGE DETECTOR CHECK
    # =====================
    
    range_data = analyze_range_behavior(
        instId,
        c15
    )
    
    if range_data:
    
        signal["range_pct"] = range_data.get(
            "range_pct"
        )
    
        signal["range_position"] = range_data.get(
            "position"
        )
    
        signal["range_compression"] = range_data.get(
            "compression"
        )
    
        for f in range_data.get("flags", []):
    
            signal["flags"].append(f)
    
        print(
            f"[RANGE_DETECTOR] {instId} "
            f"range={range_data.get('range_pct')}% "
            f"pos={range_data.get('position')} "
            f"compression={range_data.get('compression')}",
            flush=True
        )
    
    print(
        f"[EP_DATA_DEBUG] "
        f"{instId} "
        f"ep_data={ep_data}",
        flush=True
    )
    
    signal.setdefault(
        "early_pressure_score",
        0
    )

    # =====================
    # SIGNAL MODE
    # =====================
    signal_mode = classify_signal_mode(signal)
    
    signal["signal_mode"] = signal_mode

    ep = float(
        signal.get("early_pressure_score") or 0
    )
    # =====================
    # SIGNAL CLASS ENGINE
    # =====================
    
    setup_class = "WATCH"
    
    pressure_persist = any(
        x in signal.get("flags", [])
        for x in [
            "PRESSURE_LONG_PERSIST_3",
            "PRESSURE_SHORT_PERSIST_3"
        ]
    )
    
    range_compression = (
        "RANGE_COMPRESSION"
        in signal.get("flags", [])
    )
    
    explosion_ready = any(
        x in signal.get("flags", [])
        for x in [
            "PRESSURE_LONG_PERSIST_2",
            "PRESSURE_SHORT_PERSIST_2",
            "PRESSURE_LONG_PERSIST_3",
            "PRESSURE_SHORT_PERSIST_3"
        ]
    )
    
    launch_proximity = any(
        x in signal.get("flags", [])
        for x in [
            "LAUNCH_PROXIMITY_UP",
            "LAUNCH_PROXIMITY_DOWN"
        ]
    )
    
    if (
        ep >= 10
        and acc_score >= 2
    ):
    
        setup_class = "EARLY_BUILDUP"
    
    if (
        pressure_persist
        and range_compression
        and ep >= 15
    ):
    
        setup_class = "PRE_LAUNCH"
    
    if (
        explosion_ready
        and launch_proximity
        and score >= 20
    ):
    
        setup_class = "EXPANSION"
    
    if (
        score >= 35
        and not range_compression
    ):
    
        setup_class = "LATE_TREND"
    
    signal["setup_class"] = setup_class
    
    print(
        f"[SETUP_CLASS] "
        f"{instId} "
        f"class={setup_class}",
        flush=True
    )
    
    
    print(
        f"[SIGNAL_MODE] {instId} mode={signal_mode}",
        flush=True
    )

    # =====================
    # PRESSURE MEMORY CHECK
    # =====================
    
    pressure_mem = analyze_pressure_memory(instId, flags)
    
    sig_pressure_power = pressure_mem.get("pressure_power", 0)
    sig_pressure_count = pressure_mem.get("pressure_count", 0)
    sig_pressure_side = pressure_mem.get("pressure_side")
    
    for f in pressure_mem.get("flags", []):
        signal["flags"].append(f)
    
    print(
        f"[PRESSURE_MEMORY] {instId} "
        f"side={sig_pressure_side} "
        f"count={sig_pressure_count} "
        f"power={sig_pressure_power}",
        flush=True
    )
    print(
        f"[EARLY_DEBUG] {instId} "
        f"flags={signal.get('flags')} "
        f"score={signal.get('score')} "
        f"acc={signal.get('acc_score')} "
        f"stage={signal.get('stage')} "
        f"ep={ep}",
        flush=True
    )
   

    
    # =====================
    # EARLY IGNORE
    # =====================
    
    if (
    
        signal_mode in (
            "WATCH",
            "NO_MODE",
        )
    
        and (
            score <= 11
            or ep == 0
        )
    
    ):
    
        print(
            f"[EARLY_IGNORE] "
            f"{instId} "
            f"mode={signal_mode} "
            f"score={score} "
            f"ep={ep}",
            flush=True
        )
    
        return None
    
    signal["sniper"] = sniper_signal(signal)
    
        

    # =====================
    # PRE-SWING PROMOTION
    # =====================
    
    pre_state = PRE_SWING_STATE.get(instId)
    
    if pre_state:
    
        flags = set(signal.get("flags", []))
    
        promote = False
    
        if (
            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
            or "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
        ):
    
            promote = True
    
        if promote:

            signal["signal_group"] = "SWING"
            signal["sendable"] = True
            signal["swing_candidate"] = True
        
            print(
                f"[SWING_PROMOTION] {instId}",
                flush=True
            )

            print(
                f"[FINAL_DECISION] "
                f"{instId} "
                f"group={signal.get('signal_group')} "
                f"mode={signal.get('signal_mode')} "
                f"score={score} "
                f"ep={ep} "
                f"acc={acc} "
                f"sendable={signal.get('sendable')} "
                f"valid={signal.get('valid')} "
                f"entry={signal.get('entry')}",
                flush=True
            )
        
            return signal
  
    # =========================
    # FINAL DECISION
    # =========================
    signal["decision"] = decision_engine(signal)

    # =========================
    # SCALP PIPELINE
    # =========================
    scalp_ok, scalp_reason = is_scalp_candidate(signal)

    signal["scalp_candidate"] = scalp_ok
    signal["scalp_reason"] = scalp_reason

    print(
        f"[SCALP_PIPELINE] {instId} "
        f"ok={scalp_ok} "
        f"reason={scalp_reason}",
        flush=True
    )

    # =====================
    # SIGNAL STRENGTH
    # =====================
    rating, reasons_strength, strength = analyze_signal_strength({
        "flags": signal.get("flags", []),
        "score": signal.get("score", 0),
        "oi_change": signal.get("oi_change")
    })
    
    signal["rating"] = rating
    signal["strength"] = strength
    signal["strength_reasons"] = reasons_strength
    
    
    # =========================
    # FLOW SNAPSHOT
    # =========================
    
    signal_before_flow = signal
    
    try:
    
        flow_result = analyze_flow_snapshot(signal)
    
        if isinstance(flow_result, dict):
    
            signal = flow_result
    
        else:
    
            print(
                f"[FLOW_RETURN_INVALID] "
                f"{instId} "
                f"result={flow_result}",
                flush=True
            )
    
            # Не ломаем весь scan — используем исходный signal
            signal = signal_before_flow
    
            signal["flow_score"] = 0
            signal["capital_flow_score"] = 0
            signal["flow_total_score"] = 0
            signal["flow_state"] = "FLOW_ERROR"
            signal["flow_reasons"] = [
                "FLOW вернул некорректный результат"
            ]
    
    except Exception as e:
    
        print(
            f"[FLOW_PIPELINE_ERROR] "
            f"{instId} "
            f"{e}",
            flush=True
        )
    
        signal = signal_before_flow
    
        signal["flow_score"] = 0
        signal["capital_flow_score"] = 0
        signal["flow_total_score"] = 0
        signal["flow_state"] = "FLOW_ERROR"
        signal["flow_reasons"] = [str(e)]
    
    print(
        f"[FLOW] "
        f"{signal.get('instId')} "
        f"state={signal.get('flow_state')} "
        f"pressure={signal.get('flow_score')} "
        f"capital={signal.get('capital_flow_score')} "
        f"total={signal.get('flow_total_score')} "
        f"reasons={signal.get('flow_reasons')}",
        flush=True
    )
    # =========================
    # STOP HUNT ENGINE
    # =========================
    
    try:
    
        signal = analyze_stop_hunt(signal)
    
    except Exception as e:
    
        print(
            f"[STOP_HUNT_PIPELINE_ERROR] "
            f"{signal.get('instId')} "
            f"{e}",
            flush=True
        )

    # =========================
    # LIQUIDITY SWEEP ENGINE
    # =========================
    
    try:
    
        signal = analyze_liquidity_sweep(signal)
    
    except Exception as e:
    
        print(
            f"[SWEEP_ENGINE_PIPELINE_ERROR] "
            f"{signal.get('instId')} "
            f"{e}",
            flush=True
        )
    # =========================
    # OI CLASSIFIER
    # =========================
    
    signal = analyze_oi_behavior(signal)
    
    print(
        f"[OI_STATE] "
        f"{signal.get('instId')} "
        f"state={signal.get('oi_state')} "
        f"reasons={signal.get('oi_reasons')}",
        flush=True
    )  

    # =========================
    # REAL OI FLOW V2
    # =========================
    
    try:
        real_oi_data = analyze_real_oi_flow(
            signal.get("instId"),
            float(signal.get("price") or 0),
            float(signal.get("oi_change") or 0)
        )
    
        signal["real_oi_state"] = real_oi_data.get("oi_state")
        signal["real_oi_score"] = real_oi_data.get("oi_score")
        signal["real_oi_reason"] = real_oi_data.get("oi_reason")
        signal["real_oi_side"] = real_oi_data.get("oi_side")
    
        print(
            f"[REAL_OI_V2] "
            f"{signal.get('instId')} "
            f"state={signal.get('real_oi_state')} "
            f"score={signal.get('real_oi_score')} "
            f"side={signal.get('real_oi_side')} "
            f"reason={signal.get('real_oi_reason')}",
            flush=True
        )
    
    except Exception as e:
        print(
            f"[REAL_OI_V2_ERROR] {signal.get('instId')} {e}",
            flush=True
        )
    # =========================
    # SMART MONEY MERGE
    # =========================
    
    signal = merge_flow_with_oi(signal)
    
    print(
        f"[SMART_MONEY] "
        f"{signal.get('instId')} "
        f"state={signal.get('smart_money_state')} "
        f"score={signal.get('smart_money_score')} "
        f"reasons={signal.get('smart_money_reasons')}",
        flush=True
    )
    
    # =========================
    # CVD ENGINE
    # =========================
    
    signal = analyze_cvd(signal)
    
    print(
        f"[CVD_STATE] "
        f"{signal.get('instId')} "
        f"state={signal.get('cvd_state')} "
        f"score={signal.get('cvd_score')} "
        f"reasons={signal.get('cvd_reasons')}",
        flush=True
    )

    # =========================
    # LATE MOVE FILTER
    # =========================
    
    signal = analyze_late_move(signal)
    
    print(
        f"[LATE_MOVE_STATE] "
        f"{signal.get('instId')} "
        f"penalty={signal.get('late_move_penalty')} "
        f"reasons={signal.get('late_move_reasons')}",
        flush=True
    )

    # =========================
    # RETEST + RECLAIM ENGINE
    # =========================
    
    signal = analyze_retest_reclaim(signal)
    
    print(
        f"[RETEST_STATE] "
        f"{signal.get('instId')} "
        f"state={signal.get('retest_state')} "
        f"score={signal.get('retest_score')} "
        f"reasons={signal.get('retest_reasons')}",
        flush=True
    )
    # =========================
    # SMART MONEY SCORE IMPACT
    # =========================
    
    signal["smart_money_bonus"] = 4
    
    try:
    
        sm_state = str(
            signal.get("smart_money_state") or ""
        )
    
        sm_score = float(
            signal.get("smart_money_score") or 0
        )
    
        score_before_sm = signal.get("score", 0)
    
        # =====================
        # STRONG SMART MONEY
        # =====================
    
        if sm_state == "STRONG_SMART_MONEY":
    
            signal["smart_money_bonus"] += 4
    
            print(
                f"[SMART_MONEY_BOOST] "
                f"{signal.get('instId')} "
                f"+4 STRONG_SMART_MONEY "
                f"score_before={score_before_sm} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
        # =====================
        # BUILDING SMART MONEY
        # =====================
    
        elif sm_state == "BUILDING_SMART_MONEY":
    
            signal["smart_money_bonus"] += 2
    
            print(
                f"[SMART_MONEY_BOOST] "
                f"{signal.get('instId')} "
                f"+2 BUILDING_SMART_MONEY "
                f"score_before={score_before_sm} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
        # =====================
        # WEAK SMART MONEY
        # =====================
    
        elif sm_state == "WEAK_SMART_MONEY":
    
            signal["smart_money_bonus"] = 0
    
            print(
                f"[SMART_MONEY_PENALTY] "
                f"{signal.get('instId')} "
                f"-4 WEAK_SMART_MONEY "
                f"score_before={score_before_sm} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
        # =====================
        # LONG COVERING WEAKNESS
        # =====================
    
        if "SMART_MONEY_LONG_WEAK_COVERING" in signal.get("flags", []):
    
            signal["smart_money_bonus"] -= 2
    
            print(
                f"[SMART_MONEY_COVERING_PENALTY] "
                f"{signal.get('instId')} "
                f"LONG covering weakness",
                flush=True
            )

        
    
        # =====================
        # SHORT EXIT WEAKNESS
        # =====================
        if "SMART_MONEY_SHORT_WEAK_EXIT" in signal.get("flags", []):
        
            signal["smart_money_bonus"] -= 2
        
            print(
                f"[SMART_MONEY_EXIT_PENALTY] "
                f"{signal.get('instId')} "
                f"SHORT weak exit",
                flush=True
            )
        
        signal["score"] += signal.get("smart_money_bonus", 0)

    except Exception as e:

        print(
            f"[SMART_MONEY_SCORE_ERROR] {e}",
            flush=True
        )
        
    
    # =========================
    # CVD SCORE IMPACT
    # =========================

    signal["cvd_bonus"] = 0
    
    try:
    
        cvd_state = str(signal.get("cvd_state") or "")
    
        direction = str(
            signal.get("direction_code")
            or signal.get("direction")
            or signal.get("side")
            or ""
        ).upper()
    
        score_before_cvd = signal.get("score", 0)
    
        # LONG подтверждается агрессивными покупателями
        if (
            ("LONG" in direction or "UP" in direction or "BUY" in direction)
            and cvd_state in ("BUY_CVD", "STRONG_BUY_CVD")
        ):
    
            bonus = 3 if cvd_state == "STRONG_BUY_CVD" else 2
    
            signal["cvd_bonus"] += bonus
    
            print(
                f"[CVD_BOOST] "
                f"{signal.get('instId')} "
                f"+{bonus} {cvd_state} confirms LONG "
                f"score_before={score_before_cvd} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
        # SHORT подтверждается агрессивными продавцами
        elif (
            ("SHORT" in direction or "DOWN" in direction or "SELL" in direction)
            and cvd_state in ("SELL_CVD", "STRONG_SELL_CVD")
        ):
    
            bonus = 3 if cvd_state == "STRONG_SELL_CVD" else 2
    
            signal["cvd_bonus"] += bonus
    
            print(
                f"[CVD_BOOST] "
                f"{signal.get('instId')} "
                f"+{bonus} {cvd_state} confirms SHORT "
                f"score_before={score_before_cvd} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
        # LONG против агрессивных продавцов
        elif (
            ("LONG" in direction or "UP" in direction or "BUY" in direction)
            and cvd_state in ("SELL_CVD", "STRONG_SELL_CVD")
        ):
    
            penalty = 4 if cvd_state == "STRONG_SELL_CVD" else 2
    
            signal["cvd_bonus"] -= penalty
    
            print(
                f"[CVD_PENALTY] "
                f"{signal.get('instId')} "
                f"-{penalty} {cvd_state} against LONG "
                f"score_before={score_before_cvd} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
        # SHORT против агрессивных покупателей
        elif (
            ("SHORT" in direction or "DOWN" in direction or "SELL" in direction)
            and cvd_state in ("BUY_CVD", "STRONG_BUY_CVD")
        ):
    
            penalty = 4 if cvd_state == "STRONG_BUY_CVD" else 2
    
            signal["cvd_bonus"] -= penalty
    
            print(
                f"[CVD_PENALTY] "
                f"{signal.get('instId')} "
                f"-{penalty} {cvd_state} against SHORT "
                f"score_before={score_before_cvd} "
                f"score_after={signal.get('score')}",
                flush=True
            )
    
    except Exception as e:
    
        print(
            f"[CVD_SCORE_ERROR] {e}",
            flush=True
        )
    
    
    # =========================
    # FINAL QUALITY FILTER
    # =========================
        
    if not signal or not isinstance(signal, dict):
    
        print(
            f"[SIGNAL_NONE_AFTER_BUILD] {instId}",
            flush=True
        )
    
        return None

    symbol = (
        signal.get("symbol")
        or signal.get("instId")
        or instId
    )
    
    filter_result = signal_quality_filter(signal)

    if (
        not filter_result
        or not isinstance(filter_result, tuple)
        or len(filter_result) != 2
    ):
    
        print(
            f"[FILTER_RESULT_INVALID] "
            f"{instId} "
            f"result={filter_result}",
            flush=True
        )
    
        return None
    
    ok, reason = filter_result
    
    signal["filter_pass"] = ok
    signal["filter_reason"] = reason
    
    print(
        f"[FILTER_DEBUG] {instId} "
        f"score={signal.get('score')} "
        f"acc={signal.get('acc_score')} "
        f"ep={signal.get('early_pressure_score')} "
        f"stage={signal.get('stage')} "
        f"entry={signal.get('entry')} "
        f"reason={reason}",
        flush=True
    )

    # =====================
    # LATE ENTRY CHECK
    # =====================
    
    late_entry, late_reason = detect_late_entry(signal)
    
    signal["late_entry"] = late_entry
    signal["late_reason"] = late_reason
    
    print(
        f"[LATE_ENTRY] "
        f"{instId} "
        f"late={late_entry} "
        f"reason={late_reason}",
        flush=True
    )
    
    if late_entry:
    
        signal["filter_pass"] = False
        signal["filter_reason"] = late_reason
    
        print(
            f"[SKIP_LATE_ENTRY] "
            f"{instId} "
            f"{late_reason}",
            flush=True
        )
    
        return None

    # =====================
    # RETEST CHECK
    # =====================
    
    retest_ok, retest_reason = detect_retest_entry(signal)
    
    signal["retest_ok"] = retest_ok
    signal["retest_reason"] = retest_reason
    
    print(
        f"[RETEST_CHECK] "
        f"{instId} "
        f"ok={retest_ok} "
        f"reason={retest_reason}",
        flush=True
    )

    # =====================
    # PREMOVE OVERRIDE
    # =====================
    
    if not signal or not isinstance(signal, dict):

        print(
            f"[OVERRIDE_SIGNAL_NONE] "
            f"{instId}",
            flush=True
        )

        return None

    symbol = (
        signal.get("symbol")
        or signal.get("instId")
        or instId
    )

    stage = str(
        signal.get("stage") or ""
    )

    ep = float(
        signal.get("early_pressure_score") or 0
    )
    
    acc = float(
        signal.get("acc_score") or 0
    )

    ema_distance = float(
        signal.get("ema_distance_pct") or 999
    )
    # =====================
    # EXPANSION RETEST EXCEPTION
    # =====================

    if (

        signal.get("signal_mode") == "EXPANSION"

        and ep >= 10

        and (
            "ACCELERATION_UP" in flags
            or "ACCELERATION_DOWN" in flags

            or "BULLISH_SHIFT" in flags
            or "BEARISH_SHIFT" in flags
        )

    ):

        retest_ok = True

        print(
            f"[EXPANSION_RETEST_OVERRIDE] "
            f"{instId}",
            flush=True
        )

    # =====================
    # SMART TRANSITION BYPASS
    # =====================
    
    if (
    
        signal.get("signal_mode") == "TRANSITION"
    
        and signal.get("smart_money_state") in (
    
            "BUILDING_SMART_MONEY",
            "STRONG_SMART_MONEY"
    
        )
    
        and signal.get("flow_state") in (
    
            "BUILDING_MONEY_FLOW",
            "STRONG_MONEY_FLOW"
    
        )
    
        and (

            signal.get("stop_hunt_state") in (
        
                "PROBABLE_STOP_HUNT",
                "ACTIVE_STOP_HUNT"
        
            )
        
            or signal.get("retest_state") in (
        
                "RETEST_BUILDUP",
                "STRONG_RETEST"
        
            )
        
            or signal.get("real_money_confirm") is True
        
        )
    
        and (
    
            "RANGE_COMPRESSION" in flags
            or "TIGHT_RANGE" in flags
            or "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
    
        )
    
        and ema_distance <= 1.0
    
    ):
    
        signal["signal_group"] = "PRE_SWING"
        signal["sendable"] = True
        signal["valid"] = True
    
        print(
            f"[SMART_TRANSITION_BYPASS] "
            f"{instId}",
            flush=True
        )
    
        return signal

    # =====================
    # HARD BLOCK
    # =====================
    
    if "TRANSITION" in stage:
    
        elite_transition_ok = (
    
            ep >= 7
    
            and (
    
                "PRESSURE_UP" in flags
                or "PRESSURE_DOWN" in flags
    
                or "BULLISH_SHIFT" in flags
                or "BEARISH_SHIFT" in flags
    
                or "EXPLOSION_READY_UP" in flags
                or "EXPLOSION_READY_DOWN" in flags
    
                or "LAUNCH_PROXIMITY_UP" in flags
                or "LAUNCH_PROXIMITY_DOWN" in flags
            )
    
            and (
    
                "EMA_BULL_STRONG" in flags
                or "EMA_BEAR_STRONG" in flags
    
                or "MTF_LONG_ALIGN" in flags
                or "MTF_SHORT_ALIGN" in flags
    
                or "BUYER_ABSORPTION" in flags
                or "SELLER_ABSORPTION" in flags
    
                or "ACCELERATION_UP" in flags
                or "ACCELERATION_DOWN" in flags
            )
        )
    
        if not elite_transition_ok:
    
            print(
                f"[OVERRIDE_BLOCK_TRANSITION] "
                f"{symbol}",
                flush=True
            )
    
            return False
    
        print(
            f"[ELITE_TRANSITION_PASS] "
            f"{symbol}",
            flush=True
        )
    
        signal["valid"] = True
        signal["sendable"] = True

        # =====================
        # EXPANSION PASS
        # =====================
        
        if "EXPANSION" in stage:
        
            expansion_ok = (
        
                ep >= 8
        
                and (
        
                    "ACCELERATION_UP" in flags
                    or "ACCELERATION_DOWN" in flags
        
                    or "BREAKOUT_CONFIRM_UP" in flags
                    or "BREAKOUT_CONFIRM_DOWN" in flags
        
                    or "VOL_SPIKE" in flags
                )
            )
        
            if expansion_ok:
        
                print(
                    f"[EXPANSION_PASS] "
                    f"{symbol}",
                    flush=True
                )
        
                signal["valid"] = True
                signal["sendable"] = True
    

        # =====================
        # EXPANSION QUALITY FILTER
        # =====================

        expansion_quality = False

        if (

            signal.get("smart_money_state") in (

                "BUILDING_SMART_MONEY",
                "STRONG_SMART_MONEY"

            )

            and signal.get("flow_state") in (

                "BUILDING_MONEY_FLOW",
                "STRONG_MONEY_FLOW"

            )

            and (

                signal.get("stop_hunt_state") in (
            
                    "PROBABLE_STOP_HUNT",
                    "ACTIVE_STOP_HUNT"
            
                )
            
                or signal.get("retest_state") in (
            
                    "RETEST_BUILDUP",
                    "STRONG_RETEST"
            
                )
            
                or signal.get("real_money_confirm") is True
            
            )

            and (

                "RANGE_COMPRESSION" in flags
                or "COMP_PRO_5M" in flags
                or "COMP_PRO_15M" in flags
                or "ENERGY_BUILDUP" in flags

            )

        ):

            expansion_quality = True

            print(
                f"[EXPANSION_QUALITY_OK] "
                f"{instId}",
                flush=True
            )

        else:

            print(
                f"[EXPANSION_QUALITY_BAD] "
                f"{instId}",
                flush=True
            )
        # =====================
        # FORCE EXPANSION ROUTE
        # =====================
        
        if (
        
            (
                signal.get("signal_mode") == "EXPANSION"
                or "EXPANSION" in stage
            )
        
            and ep >= 10
        
            and expansion_quality
        
            and not signal.get("late_move")
        
            and signal.get("smart_money_state") in (
        
                "BUILDING_SMART_MONEY",
                "STRONG_SMART_MONEY"
        
            )
        
        ):
        
            signal["signal_group"] = "SWING"
            signal["sendable"] = True
            signal["valid"] = True
        
            print(
                f"[FORCE_EXPANSION_SWING] "
                f"{instId}",
                flush=True
            )
        
        if signal.get("signal_group") is None:
        
            pressure_persist = any(
                x in flags
                for x in [
                    "PRESSURE_LONG_PERSIST_3",
                    "PRESSURE_SHORT_PERSIST_3"
                ]
            )
        
            range_compression = (
                "RANGE_COMPRESSION" in flags
                or "TIGHT_RANGE" in flags
            )
        
            if (
        
                (
        
                    ep >= 8
                    and acc >= 2
        
                )
        
                or (
        
                    signal.get("flow_state") in (
        
                        "BUILDING_MONEY_FLOW",
                        "STRONG_MONEY_FLOW"
        
                    )
        
                    and signal.get("smart_money_state") in (
        
                        "BUILDING_SMART_MONEY",
                        "STRONG_SMART_MONEY"
        
                    )
        
                    and (
        
                        signal.get("stop_hunt_state") in (
        
                            "PROBABLE_STOP_HUNT",
                            "ACTIVE_STOP_HUNT"
        
                        )
        
                        or signal.get("retest_state") in (
        
                            "RETEST_BUILDUP",
                            "STRONG_RETEST"
        
                        )
        
                        or signal.get("real_money_confirm") is True
        
                    )
        
                )
        
            ) and (

                pressure_persist
                or range_compression
            
                or "LAUNCH_PROXIMITY_UP" in flags
                or "LAUNCH_PROXIMITY_DOWN" in flags
            
                or "EXPLOSION_READY_UP" in flags
                or "EXPLOSION_READY_DOWN" in flags
            
            ):
        
                signal["signal_group"] = "PRE_SWING"
        
                signal["sendable"] = True
                signal["valid"] = True
        
                print(
                    f"[FORCE_PRE_SWING] "
                    f"{symbol}",
                    flush=True
                )
             
            # =====================
            # SMART BUILDUP ROUTING
            # =====================
            
            good_context = (
            
                signal.get("stop_hunt_state") in (
                    "ACTIVE_STOP_HUNT",
                    "PROBABLE_STOP_HUNT"
                )
            
                or signal.get("retest_state") in (
                    "RETEST_BUILDUP",
                    "STRONG_RETEST"
                )
            
                or signal.get("smart_money_state") == "STRONG_SMART_MONEY"
            
            )
            
            if (
            
                good_context
            
                and signal.get("flow_state") in (
                    "STRONG_MONEY_FLOW",
                    "BUILDING_MONEY_FLOW"
                )
            
                and (
                    "RANGE_COMPRESSION" in flags
                    or "TIGHT_RANGE" in flags
                    or "COMP_PRO_5M" in flags
                    or "COMP_PRO_15M" in flags
                )
            
            ):
            
                signal["signal_group"] = "PRE_SWING"
            
                signal["sendable"] = True
                signal["valid"] = True
            
                print(
                    f"[SMART_BUILDUP_ROUTE] "
                    f"{instId}",
                    flush=True
                )
            
            else:
            
                print(
                    f"[BLOCK_WEAK_PRE_SWING] "
                    f"{instId} "
                    f"ep={ep} "
                    f"acc={acc}",
                    flush=True
                )
            
                return signal
            
            # =====================
            # EP BLOCK
            # =====================
    
    if (
    
        ep < 5
    
        and signal.get("signal_mode") not in (
            "EXPANSION",
            "TRANSITION",
            "CONFIRMED"
        )
    ):
    
        print(
            f"[OVERRIDE_BLOCK_EP] "
            f"{symbol} "
            f"ep={ep}",
            flush=True
        )
    
        return None
    
    # =====================
    # ACC BLOCK
    # =====================
    
    if acc < 0:
    
        print(
            f"[OVERRIDE_BLOCK_ACC] "
            f"{symbol} "
            f"acc={acc}",
            flush=True
        )
    
        return None
    
    # =====================
    # ALLOW OVERRIDE
    # =====================

    if (
        score >= 28
        and (
            "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
            or "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
        )
    ):

        print(
            f"[PREMOVE_OVERRIDE_OK] "
            f"{symbol} "
            f"score={score} "
            f"ep={ep} "
            f"acc={acc}",
            flush=True
        )

        premove_override = True

    else:

        premove_override = False

    # =========================
    # STRUCTURE PASS
    # =========================

    strong_structure_pass = False

    if (
    
        acc_score >= 2
    
        and (
    
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
    
            or "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
    
            or "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
    
            or "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
    
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
    
            or "BULLISH_SHIFT" in flags
            or "BEARISH_SHIFT" in flags
        )
    
    ):
        strong_structure_pass = True


    # =========================
    # QUALITY FILTER
    # =========================

    quality_pass = False

    if (
        "BREAKOUT_CONFIRM_UP" in flags
        or "BREAKOUT_CONFIRM_DOWN" in flags
    ):
        quality_pass = True

    elif (
        "CONTINUATION_UP" in flags
        or "CONTINUATION_DOWN" in flags
    ):
        quality_pass = True

    elif (
        "VOL_SPIKE" in flags
        and "PRESSURE_UP" in flags
    ):
        quality_pass = True

    elif (
        "VOL_SPIKE" in flags
        and "PRESSURE_DOWN" in flags
    ):
        quality_pass = True

    elif (
        "COMP_5M" in flags
        and (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    ):
        quality_pass = True

    # =========================
    # PRE-MOVE ENERGY PASS
    # =========================

    elif (

        "ENERGY_BUILDUP" in flags
    
        and (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )
    
        and (
            "COMP_PRO_5M" in flags
            or "COMP_PRO_15M" in flags
        )
    
        and (
    
            "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
    
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
        )
    
    ):

        quality_pass = True

    if (
    
        signal.get("signal_mode") == "PREMOVE"
    
        and strong_structure_pass
        and quality_pass
    ):
    
        print(
            f"[PREMOVE_DEBUG] "
            f"mode={signal.get('signal_mode')} "
            f"symbol={instId}",
            flush=True
        )
    
        print(
            f"[PREMOVE_PASS] {instId}",
            flush=True
        )
    
        signal["signal_group"] = "PRE_SWING"
    
    
        # =========================
        # AUTO GROUP ASSIGN
        # =========================
    
        ep = float(
            signal.get("early_pressure_score") or 0
        )
    
        score = float(
            signal.get("score") or 0
        )
    
        flags = set(signal.get("flags", []))
    
        # =====================
        # SWING
        # =====================
    
        if (
    
            signal.get("signal_mode") == "EXPANSION"
    
            and score >= 14
            and ep >= 8
    
            and (
                "BREAKOUT_CONFIRM_UP" in flags
                or "BREAKOUT_CONFIRM_DOWN" in flags
                or "CONTINUATION_UP" in flags
                or "CONTINUATION_DOWN" in flags
                or "ACCELERATION_UP" in flags
                or "ACCELERATION_DOWN" in flags
                or "EXPLOSION_READY_UP" in flags
                or "EXPLOSION_READY_DOWN" in flags
            )
    
            and (
                "MTF_LONG_ALIGN" in flags
                or "MTF_SHORT_ALIGN" in flags
            )
        ):
    
            signal["signal_group"] = "SWING"
        # =====================
        # PRE SWING
        # =====================
    

        signal["signal_group"] = "PRE_SWING"

        signal["sendable"] = True
        signal["valid"] = True
    
        print(
            f"[PRE_SWING_ROUTE] "
            f"{instId} "
            f"mode={signal.get('signal_mode')} "
            f"score={score} "
            f"ep={ep} "
            f"acc={acc}",
            flush=True
        )

        print(
            f"[POST_ROUTE_DEBUG] "
            f"{instId} "
            f"group={signal.get('signal_group')} "
            f"sendable={signal.get('sendable')} "
            f"valid={signal.get('valid')} "
            f"mode={signal.get('signal_mode')} "
            f"entry={signal.get('entry')}",
            flush=True
        )

    # =====================
    # SCALP
    # =====================

    if (
        not signal.get("signal_group")

        and signal.get("signal_mode") in (
            "TRANSITION",
            "CONFIRMED",
        )

        and ep >= 7
        and score >= 12
    ):

        signal["signal_group"] = "SCALP"

    # =====================
    # NO GROUP
    # =====================

    if (

        not signal.get("sendable")
    
        and signal.get("signal_group") != "SWING"
    ):
    
        signal["signal_group"] = None

    # =========================
    # FINAL FILTER
    # =========================

    if (

        not ok

        and not premove_override

        and signal.get("signal_group") not in (
            "PRE_SWING",
            "SWING",
        )

    ):

        print(
            f"[FILTER_BLOCK] "
            f"{instId} reason={reason}",
            flush=True
        )

        return None
    if premove_override:

        print(
            f"[OVERRIDE_FINAL_PASS] "
            f"{instId}",
            flush=True
        )
    if not signal.get("signal_group"):

        # =====================
        # AUTO SCALP FALLBACK
        # =====================

        if (
            signal.get("signal_mode") == "TRANSITION"
            and ep >= 8
            and signal.get("entry") not in (
                "NO_ENTRY",
                "PREMOVE_CONFLICT",
            )
            and (
                "ACCELERATION_UP" in flags
                or "ACCELERATION_DOWN" in flags
                or "BREAKOUT_CONFIRM_UP" in flags
                or "BREAKOUT_CONFIRM_DOWN" in flags
            )
        ):

            signal["signal_group"] = "SCALP"
            signal["sendable"] = True
            signal["valid"] = True

            print(
                f"[AUTO_SCALP_FALLBACK] "
                f"{instId}",
                flush=True
            )

        # =====================
        # KEEP STRONG OVERRIDES
        # =====================

        elif (

            premove_override

            or signal.get("sendable") is True

            or (

                signal.get("signal_mode") == "EXPANSION"
            
                and (
            
                    ep >= 8
            
                    or (
            
                        signal.get("flow_state")
                        in (
                            "BUILDING_MONEY_FLOW",
                            "STRONG_MONEY_FLOW"
                        )
            
                        and signal.get("smart_money_state")
                        in (
                            "BUILDING_SMART_MONEY",
                            "STRONG_SMART_MONEY"
                        )
            
                    )
            
                )
            
            )

            or (

                signal.get("signal_mode") == "PREMOVE"
            
                and (
            
                    ep >= 7
            
                    or (
            
                        signal.get("flow_state")
                        == "BUILDING_MONEY_FLOW"
            
                        and signal.get("stop_hunt_state")
                        == "PROBABLE_STOP_HUNT"
            
                    )
            
                )
            
                and (
                    "PRESSURE_UP" in flags
                    or "PRESSURE_DOWN" in flags
                    or "BULLISH_SHIFT" in flags
                    or "BEARISH_SHIFT" in flags
                    or "ACCELERATION_UP" in flags
                    or "ACCELERATION_DOWN" in flags
                    or "LAUNCH_PROXIMITY_UP" in flags
                    or "LAUNCH_PROXIMITY_DOWN" in flags
                    or "EXPLOSION_READY_UP" in flags
                    or "EXPLOSION_READY_DOWN" in flags
                )

            )

        ):

            signal["sendable"] = True
            signal["valid"] = True

            if not signal.get("signal_group"):

                signal["signal_group"] = "PRE_SWING"

            print(
                f"[KEEP_OVERRIDE_SIGNAL] "
                f"{instId}",
                flush=True
            )

            return signal

        # =====================
        # BUILDUP SETUP PRESERVE
        # =====================

        elif (

            signal.get("entry") == "WATCH_BUILDUP"
        
            and signal.get("flow_state") in (
                "BUILDING_MONEY_FLOW",
                "STRONG_MONEY_FLOW"
            )
        
            and signal.get("smart_money_state") in (
                "BUILDING_SMART_MONEY",
                "STRONG_SMART_MONEY"
            )
        
            and (
                signal.get("stop_hunt_state") in (
                    "PROBABLE_STOP_HUNT",
                    "ACTIVE_STOP_HUNT"
                )
        
                or "RANGE_COMPRESSION" in flags
                or "TIGHT_RANGE" in flags
                or "COMP_PRO_5M" in flags
                or "COMP_PRO_15M" in flags
            )
        
        ):

            signal["sendable"] = True
            signal["valid"] = True

            if not signal.get("signal_group"):

                signal["signal_group"] = "PRE_SWING"

            signal["entry_type"] = "BUILDUP_ENTRY"

            print(
                f"[BUILDUP_SETUP_PRESERVED] "
                f"{instId}",
                flush=True
            )

            return signal

        else:

            print(
                f"[FINAL_INVALID] "
                f"{instId}",
                flush=True
            )
            print(
                f"[INVALID_DEBUG] "
                f"group={signal.get('signal_group')} "
                f"sendable={signal.get('sendable')} "
                f"valid={signal.get('valid')} "
                f"mode={signal.get('signal_mode')} "
                f"entry={signal.get('entry')}",
                flush=True
            )
            return None

    print(
        f"[FINAL_RETURN_SIGNAL] "
        f"{instId} "
        f"group={signal.get('signal_group')} "
        f"mode={signal.get('signal_mode')} "
        f"sendable={signal.get('sendable')} "
        f"valid={signal.get('valid')}",
        flush=True
    )

    return signal
    
# ==============================
# 🎯 SNIPER SIGNAL ENGINE
# ==============================

def sniper_signal(sig):

    if not isinstance(sig, dict):
        return False

    flags = set(sig.get("flags") or [])
    score = int(sig.get("score", 0))

    breakout = (
        "BREAKOUT_CONFIRM_UP" in flags or
        "BREAKOUT_CONFIRM_DOWN" in flags
    )

    impulse_ok = (
        "VOL_SPIKE" in flags and
        "ATR_EXPANSION" in flags
    )

    liquidity = (
        "SWEEP_UP" in flags or
        "SWEEP_DOWN" in flags or
        "STOP_HUNT_UP" in flags or
        "STOP_HUNT_DOWN" in flags
    )

    orderbook = (
        "OB_BIDS" in flags or
        "OB_ASKS" in flags
    )

    whale = (
        "WHALE_FLOW" in flags or
        "WHALE_ACC" in flags
    )
    impulse_ok = (
        "VOL_SPIKE" in flags and
        "ATR_EXPANSION" in flags
    )
    
    if breakout and impulse_ok and liquidity and orderbook and whale and score >= 7:
        return True
    
    return False
        
# =========================
# SCANNER (LEVEL 1 FAST FILTER)
# =========================
def is_bad_symbol(instId: str) -> bool:
    base = str(instId).upper().strip()

    if base.endswith("-USDT"):
        base = base[:-5]
    elif base.endswith("USDT"):
        base = base[:-4]

    for s in EXCLUDE_TOKENS_CONTAINS:
        if s in base:
            return True

    return False

# =====================
# MARKET CAP CHECK
# =====================
def is_market_cap_ok(instId, market_caps):
    try:
        base = get_base_coin(instId)
        cap = market_caps.get(base)

        if cap is None:
            return False

        return cap >= MARKET_CAP_MIN_USD

    except Exception:
        return False



# =====================
# MAIN FUNCTION
# =====================
def get_market_candidates_bybit():
    tickers = get_bybit_tickers_linear()
    print("BYBIT TICKERS COUNT:", len(tickers))

    raw_candidates = []

    # =====================
    # СБОР КАНДИДАТОВ
    # =====================
    for t in tickers:
        sym = t.get("symbol", "")

        if not sym.endswith("USDT"):
            continue

        if is_bad_symbol(sym):
            continue

        try:
            vol_usdt = float(t.get("turnover24h") or 0.0)
        except Exception:
            vol_usdt = 0.0

        try:
            last = float(t.get("lastPrice") or 0.0)
            prev = float(t.get("prevPrice24h") or 0.0)
            pct = ((last - prev) / prev * 100.0) if prev > 0 else 0.0
        except Exception:
            pct = 0.0

        if vol_usdt < SCAN_MIN_VOL_USDT:
            continue

        abs_pct = abs(pct)

        normal_move_ok = abs_pct >= SCAN_MIN_PCT_24H
        prebreak_move_ok = PREBREAK_SCAN_MIN_PCT_24H <= abs_pct <= PREBREAK_SCAN_MAX_PCT_24H

        accumulation_candidate = (
            vol_usdt >= SCAN_MIN_VOL_USDT
            and abs_pct >= 0.20
        )

        if not ACCUMULATION_MODE:

            if not (
                normal_move_ok
                or prebreak_move_ok
                or accumulation_candidate
            ):
                continue

        instId = sym

        if not is_trend_candidate(instId, t):

            print(
                f"[TREND_SKIP] "
                f"{instId} "
                f"pct={round(pct, 2)}",
                flush=True
            )
        
            continue

        print(
            f"[RAW_ADD] "
            f"{instId} "
            f"vol={round(vol_usdt)} "
            f"pct={round(pct,2)}",
            flush=True
        )
        raw_candidates.append((instId, vol_usdt, pct))

    

    # =====================
    # ВНЕ ЦИКЛА
    # =====================
    
    print(f"[DEBUG] raw_candidates before filter: {len(raw_candidates)}", flush=True)

    if not raw_candidates:
        print("[MARKET_CAP] no raw candidates before market cap filter")
        return []

    raw_candidates.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)

    # =====================
    # MARKET CAP
    # =====================
    MARKET_CAP_PREFETCH_MULT = int(os.getenv("MARKET_CAP_PREFETCH_MULT") or "3")
    prefetch_limit = SCAN_BATCH * MARKET_CAP_PREFETCH_MULT

    prefetch_candidates = raw_candidates[:prefetch_limit]

    base_coins = [get_base_coin(instId) for instId, _, _ in prefetch_candidates]
    market_caps = fetch_market_caps_usd(base_coins)

    if not market_caps:
        print("[MARKET_CAP] SKIPPED (DEBUG MODE)")
        return raw_candidates[:SCAN_TOP_N]

    filtered_candidates = []

    for instId, vol_usdt, pct in raw_candidates:
        if is_market_cap_ok(instId, market_caps):
            filtered_candidates.append((instId, vol_usdt, pct))

    print(
        f"[MARKET_CAP] raw={len(raw_candidates)} "
        f"passed={len(filtered_candidates)}"
    )

    filtered_candidates.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)

    return filtered_candidates[:SCAN_TOP_N]
    
        
def get_market_candidates():
    if is_bybit():
        return get_market_candidates_bybit()

    tickers = get_okx_spot_usdt_tickers()
    cands = []

    for t in tickers:
        instId = t.get("instId", "")

        if not instId.endswith(f"-{QUOTE}"):
            continue

        if is_bad_symbol(instId):
            continue

        try:
            vol_usdt = float(t.get("volCcy24h") or 0.0)
        except Exception:
            vol_usdt = 0.0

        try:
            last = float(t.get("last") or 0.0)
            open24 = float(t.get("open24h") or 0.0)
            pct = (last - open24) / open24 * 100.0 if open24 > 0 else 0.0
        except Exception:
            pct = 0.0

        if vol_usdt < SCAN_MIN_VOL_USDT:
            continue

        abs_pct = abs(pct)

        normal_move_ok = abs_pct >= SCAN_MIN_PCT_24H
        prebreak_move_ok = PREBREAK_SCAN_MIN_PCT_24H <= abs_pct <= PREBREAK_SCAN_MAX_PCT_24H

        if not ACCUMULATION_MODE:
            if not (normal_move_ok or prebreak_move_ok):
                continue

        cands.append((instId, vol_usdt, pct))

    cands.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)
    return cands[:SCAN_TOP_N]


# =========================
# BTC MARKET REGIME (SMART)
# =========================

def btc_regime():

    try:

        sig = build_signal(btc_symbol())

    except Exception as e:

        import traceback
    
        print(
            f"[BTC_REGIME_ERROR] {e}",
            flush=True
        )
    
        traceback.print_exc()
    
        return ("NEUTRAL", None)

    # =====================
    # INVALID BTC
    # =====================

    if not isinstance(sig, dict):

        return ("NEUTRAL", None)

    flags = set(sig.get("flags", []))

    ep = float(
        sig.get("early_pressure_score") or 0
    )

    score = float(
        sig.get("score") or 0
    )

    mode = str(
        sig.get("signal_mode") or ""
    )

    # =====================
    # RISK ON
    # =====================

    if (

        ep >= 10

        and score >= 10

        and mode in (
            "TRANSITION",
            "EXPANSION",
            "PREMOVE",
        )

        and (
            "PRESSURE_UP" in flags
            or "BULLISH_SHIFT" in flags
            or "ACCELERATION_UP" in flags
            or "EXPLOSION_READY_UP" in flags
            or "LAUNCH_PROXIMITY_UP" in flags
        )

        and (
            "EMA_BULL" in flags
            or "EMA_BULL_STRONG" in flags
        )

    ):

        print(
            "[BTC_REGIME] RISK_ON",
            flush=True
        )

        return ("RISK_ON", sig)

    # =====================
    # RISK OFF
    # =====================

    if (

        ep >= 10

        and score >= 10

        and mode in (
            "TRANSITION",
            "EXPANSION",
            "PREMOVE",
        )

        and (
            "PRESSURE_DOWN" in flags
            or "BEARISH_SHIFT" in flags
            or "ACCELERATION_DOWN" in flags
            or "EXPLOSION_READY_DOWN" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
        )

        and (

            "EMA_BEAR" in flags
            or "EMA_BEAR_STRONG" in flags
        
            or (
                "BEARISH_SHIFT" in flags
                and "PRESSURE_DOWN" in flags
                and ep >= 12
            )
        )

    ):

        print(
            "[BTC_REGIME] RISK_OFF",
            flush=True
        )

        return ("RISK_OFF", sig)

    # =====================
    # NEUTRAL
    # =====================

    print(
        "[BTC_REGIME] NEUTRAL",
        flush=True
    )

    return ("NEUTRAL", sig)

# =========================
# APPLY MARKET REGIME BIAS
# =========================

def apply_regime_bias(sig, regime):

    try:

        if not sig or not isinstance(sig, dict):
            return sig

        side = str(
            sig.get("direction_code")
            or sig.get("side")
            or ""
        ).upper()

        if regime == "RISK_ON":

            if side in ("LONG", "BUY", "UP"):

                sig["score"] = float(
                    sig.get("score") or 0
                ) + 2

                print(
                    f"[REGIME_BOOST_LONG] "
                    f"{sig.get('symbol')}",
                    flush=True
                )

            elif side in ("SHORT", "SELL", "DOWN"):

                sig["score"] = float(
                    sig.get("score") or 0
                ) - 2

                print(
                    f"[REGIME_PENALTY_SHORT] "
                    f"{sig.get('symbol')}",
                    flush=True
                )

        elif regime == "RISK_OFF":

            if side in ("SHORT", "SELL", "DOWN"):

                sig["score"] = float(
                    sig.get("score") or 0
                ) + 2

                print(
                    f"[REGIME_BOOST_SHORT] "
                    f"{sig.get('symbol')}",
                    flush=True
                )

            elif side in ("LONG", "BUY", "UP"):

                sig["score"] = float(
                    sig.get("score") or 0
                ) - 2

                print(
                    f"[REGIME_PENALTY_LONG] "
                    f"{sig.get('symbol')}",
                    flush=True
                )

        return sig

    except Exception as e:

        print(
            f"[REGIME_BIAS_ERROR] {e}",
            flush=True
        )

        return sig
# =========================
# PRO EDGE FILTER (NEW)
# =========================
def pro_edge_filter(sig, regime):

    if not PRO_EDGE_ENABLED:
        return True

    flags = set(sig.get("flags", []))
    score = int(sig.get("score", 0))
    direction = sig.get("direction", "")
    acc = int(sig.get("acc_score", 0))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")

    # 🔥 фильтр реального импульса
    real_impulse = (
        "ATR_EXPANSION" in flags or
        "VOL_SPIKE" in flags or
        "BREAKOUT_CONFIRM_UP" in flags or
        "BREAKOUT_CONFIRM_DOWN" in flags
    )

    if not real_impulse and score < EDGE_HIGH_SCORE:
        return False

    # Expected move filter
    exp_max = float(sig.get("exp_move_max") or 0.0)
    if exp_max < PRE_MIN_EXPECTED_MOVE_PCT and score < EDGE_MID_SCORE:
        return False

    if score < PRO_EDGE_MIN_SCORE and score < EDGE_HIGH_SCORE:
        return False

    if PRO_EDGE_REJECT_BALANCE and ("БАЛАНС" in direction) and score < EDGE_HIGH_SCORE:
        return False

    pm = sig.get("pmeta") or {}
    range_pct = pm.get("range_pct")

    if range_pct is not None:
        if float(range_pct) < float(PRO_EDGE_MIN_RANGE_PCT) and score < EDGE_MID_SCORE:
            return False

    if PRO_EDGE_REQUIRE_IMPULSE:
        strong_impulse = (
            ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) or
            ("ATR_EXPANSION" in flags and "VOL_SPIKE" in flags) or
            ("VOL_SPIKE" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_DOWN" in flags))
        )
        if not strong_impulse:
            return False

    # EMA trend filter
    if "ВВЕРХ" in direction and ema_state == "EMA_BEAR" and score < EDGE_HIGH_SCORE:
        return False

    if "ВНИЗ" in direction and ema_state == "EMA_BULL" and score < EDGE_HIGH_SCORE:
        return False

    # BTC bias
    if regime == "RISK_OFF" and "ВВЕРХ" in direction:
        return False

    if regime == "RISK_ON" and "ВНИЗ" in direction:
        return False

    return True
      

# =========================
# AI FILTER DEBUG
# =========================
def debug_ai_filter_result(sig, regime, passed, layer="INTRADAY"):

    if not isinstance(sig, dict):
        return

    symbol = sig.get("symbol") or sig.get("instId") or "UNKNOWN"
    score = sig.get("score")
    acc = sig.get("acc_score")
    direction = sig.get("direction")
    tier = sig.get("tier")
    swing_candidate = sig.get("swing_only_candidate")
    below_main = sig.get("below_main_min_score")
    flags = sig.get("flags") or []

    print(
    f"[AI_FILTER][{layer}] "
    f"{'PASSED' if passed else 'BLOCKED'} "
    f"{symbol} | "
    f"regime={regime} | "
    f"score={score} | acc={acc} | tier={tier} | "
    f"direction={direction} | "
    f"oi={oi} | "
    f"swing_candidate={swing_candidate} | "
    f"below_main={below_main} | "
    f"flags={flags}",
    flush=True
)

# =========================
# TRADER INTERPRETATION
# =========================
def interpret_combo(sig):

    flags = set(sig.get("flags", []))
    stage = sig.get("stage", "")
    acc = int(sig.get("acc_score", 0))
    direction = sig.get("direction", "")
    entry = sig.get("entry", "")

    notes = []

    if acc >= 3 and ("PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags):
        if "PRESSURE_DOWN" in flags:
            notes.append("🟣 Накопление + цена у низа диапазона: снизу часто стопы лонгов. Возможен ложный пролив и возврат.")
        if "PRESSURE_UP" in flags:
            notes.append("🟣 Накопление + цена у верха диапазона: сверху часто стопы шортов. Возможен ложный прокол и откат.")

    if "FAKE_DUMP" in flags:
        notes.append("🟡 FAKE_DUMP: прокол вниз и быстрый возврат — похоже на снятие стопов снизу.")

    if "SWEEP_UP" in flags:
        notes.append("💣 SWEEP_UP: прокол верхов + возврат внутрь — сняли стопы шортов сверху, часто потом идут вниз.")

    if "SWEEP_DOWN" in flags:
        notes.append("💣 SWEEP_DOWN: прокол низов + возврат внутрь — сняли стопы лонгов снизу, часто потом идут вверх.")

    if "BULL_TRAP" in flags:
        notes.append("⚠️ BULL TRAP: пробой вверх оказался ложным — часто после этого цена идёт вниз.")

    if "BEAR_TRAP" in flags:
        notes.append("⚠️ BEAR TRAP: пробой вниз оказался ложным — часто после этого цена разворачивается вверх.")

    if "LIQUIDITY_MAGNET_UP" in flags:
        notes.append("🧲 Сверху ликвидность — цена может тянуться к стопам шортов.")

    if "LIQUIDITY_MAGNET_DOWN" in flags:
        notes.append("🧲 Снизу ликвидность — цена может тянуться к стопам лонгов.")

    if ("BREAKOUT_UP" in flags or "BREAKOUT_DOWN" in flags) and ("BREAKOUT_CONFIRM_UP" not in flags and "BREAKOUT_CONFIRM_DOWN" not in flags):
        notes.append("🟠 Пробой без закрепления: возможна ловушка/вытряхивание.")

    if ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and ("ATR_EXPANSION" in flags) and ("VOL_SPIKE" in flags):
        notes.append("🟢 Закрепление + ATR + объём: движение подтверждено, шанс продолжения выше.")

    if "OB_BIDS" in flags:
        notes.append("📘 Стакан: перевес покупателей (BID). Это усиливает лонг-сценарий.")

    if "OB_ASKS" in flags:
        notes.append("📘 Стакан: перевес продавцов (ASK). Это усиливает шорт-сценарий.")

    if "OB_WALL_BID" in flags:
        notes.append("🧱 Стена BID: рядом крупная заявка — часто поддержка.")

    if "OB_WALL_ASK" in flags:
        notes.append("🧱 Стена ASK: рядом крупная заявка — часто сопротивление.")

    if "ACCUMULATION" in stage:
        notes.append("🟣 STAGE=ACCUMULATION: идёт сжатие. Это зона ДО движения — ждём триггер.")

    if "MANIPULATION" in stage:
        notes.append("🟡 STAGE=MANIPULATION: вероятен сбор ликвидности перед импульсом.")

    if "EXPANSION" in stage:
        notes.append("🟢 STAGE=EXPANSION: движение уже пошло. Лучше входить по откату/структуре.")

    if acc >= 3 and "БАЛАНС" in direction:
        notes.append("⚖️ Баланс при сильном накоплении: рынок прячет сторону. Часто потом резкий выстрел.")

    if "SAFE" in entry:
        notes.append("✅ SAFE: самый чистый сценарий по структуре.")
    elif "AGGRESSIVE" in entry:
        notes.append("⚠️ AGGRESSIVE: ранний вход — лучше маленький риск.")
    else:
        notes.append("⏳ WAIT: пока наблюдаем — ждём подтверждение/объём/ATR/свип.")

    return notes
# =========================
# MESSAGE FORMATS
# =========================

def fmt_symbol(instId: str) -> str:
    return instId.replace("-USDT", "")


def msg_short(sig):

    lines = []

    inst = sig.get("instId", "?")
    price = sig.get("price", 0)
    score = sig.get("score", 0)
    direction = sig.get("direction", "⚖️ БАЛАНС")
    acc = sig.get("acc_score", 0)
    entry = sig.get("entry", "WAIT")
    stage = sig.get("stage", "UNKNOWN")
    target = sig.get("target")

    # ✅ БЕРЁМ ГОТОВЫЙ tier
    tier = sig.get("tier", "SIGNAL")

    lines.append(f"{tier} — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"📊 {score}/10 | {direction} | acc={acc}")
    lines.append(f"🎯 ENTRY: {entry}")
    lines.append(f"🧬 STAGE: {stage}")

    if target is not None:
        lines.append(f"🎯 Target: {target:.6g}")

    return "\n".join(lines)


def msg_medium(sig):

    lines = []

    inst = sig.get("instId", "?")
    price = sig.get("price", 0)
    score = sig.get("score", 0)
    tier = sig.get("tier", "SIGNAL")
    direction = sig.get("direction", "⚖️ БАЛАНС")
    up_w = sig.get("up_w", 0)
    down_w = sig.get("down_w", 0)
    acc = sig.get("acc_score", 0)
    entry = sig.get("entry", "WAIT")
    entry_reason = sig.get("entry_reason", "")
    stage = sig.get("stage", "UNKNOWN")
    stage_reason = sig.get("stage_reason", "")
    target = sig.get("target")
    flags = sig.get("flags", [])
    oi_text = oi_status_text(sig)
    oi_trap = oi_trap_detector(sig)

    lines.append(f"{tier}")
    lines.append(f"🧠 RADAR MEDIUM — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"🎯 Expected move: {sig.get('exp_move_min', 0)}–{sig.get('exp_move_max', 0)}%")

    if sig.get("ema_state") and sig.get("ema20") is not None and sig.get("ema50") is not None and sig.get("ema200") is not None:
        lines.append(
            f"📈 EMA: {sig.get('ema_state')} | "
            f"20={sig.get('ema20'):.6g} | "
            f"50={sig.get('ema50'):.6g} | "
            f"200={sig.get('ema200'):.6g}"
        )

    if sig.get("rsi7") is not None and sig.get("rsi14") is not None:
        lines.append(
            f"📍 RSI7={sig['rsi7']:.1f} | RSI14={sig['rsi14']:.1f} | {sig.get('rsi_state', 'UNKNOWN')}"
        )
    
    lines.append(f"📊 {score}/10 | {direction} (up={up_w}, down={down_w}) | acc={acc}")
    lines.append(f"🎯 ENTRY: {entry} — {entry_reason}")
    
    oi_text = oi_status_text(sig)
    if oi_text:
        lines.append(oi_text)
    
    oi_hint = oi_trap_detector(sig)
    if oi_hint:
        lines.append(oi_hint)
    
    lines.append(f"🧬 STAGE: {stage} — {stage_reason}")
    
    pm = sig.get("pmeta") or {}
    if (
        pm.get("range_lo") is not None
        and pm.get("range_hi") is not None
        and pm.get("range_pct") is not None
    ):
        lines.append(
            f"🧲 Range: {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | {pm['range_pct']:.2f}%"
        )

    if target is not None:
        lines.append(f"🎯 Target: {target:.6g}")

    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | {ez.get('low'):.6g} → {ez.get('high'):.6g} | stop {ez.get('stop'):.6g}"
        )

    if flags:
        lines.append("Flags:")
        for f in flags[:14]:
            lines.append(f"• {f}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:12]:
            lines.append(f"• {n}")

    return "\n".join(lines)

def oi_badge(oi):
    try:
        oi = float(oi)
    except:
        return "⚪ n/a"

    if oi >= 3:
        return f"🟢 +{oi:.2f}%"
    elif oi >= 1:
        return f"🟡 +{oi:.2f}%"
    elif oi > -1:
        return f"⚪ {oi:.2f}%"
    elif oi > -3:
        return f"🟠 {oi:.2f}%"
    else:
        return f"🔴 {oi:.2f}%"

def dir_badge(direction):
    d = str(direction).upper()

    if "ВВЕРХ" in d or "LONG" in d or "UP" in d:
        return "🟢⬆️⬆️ ВВЕРХ"

    if "ВНИЗ" in d or "SHORT" in d or "DOWN" in d:
        return "🔴⬇️⬇️ ВНИЗ"

    return "⚪↔️ БАЛАНС"

def msg_watch(sig):

    lines = []

    inst = sig.get("instId", "?")
    price = sig.get("price", 0)
    score = sig.get("score", 0)
    direction = sig.get("direction", "⚖️ БАЛАНС")
    acc = sig.get("acc_score", 0)
    stage = sig.get("stage", "UNKNOWN")
    target = sig.get("target")
    flags = sig.get("flags", [])

    lines.append(f"🟡 WATCH — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"📊 {score}/10 | {direction} | acc={acc}")
    lines.append(f"🧬 STAGE: {stage}")
    lines.append("Смысл: это ранняя зона наблюдения, а не готовый вход.")

    pm = sig.get("pmeta") or {}
    if (
        pm.get("range_lo") is not None
        and pm.get("range_hi") is not None
        and pm.get("range_pct") is not None
    ):
        lines.append(
            f"🧲 Range: {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | {pm['range_pct']:.2f}%"
        )

    if target is not None:
        lines.append(f"🎯 Target: {target:.6g}")

    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | {ez.get('low'):.6g} → {ez.get('high'):.6g} | stop {ez.get('stop'):.6g}"
        )

    if flags:
        lines.append("Flags:")
        for f in flags[:10]:
            lines.append(f"• {f}")

    return "\n".join(lines)


def msg_full(sig):

    lines = []

    tier = sig.get("tier", "SIGNAL")

    lines.append(f"🚨 {tier} — {fmt_symbol(sig['instId'])}")
    lines.append(f"💵 {sig['price']:.6g}")

    if sig.get("sniper"):
        lines.append("🔥 SNIPER ENTRY — сильный импульс, можно искать точку входа")

    lines.append(
        f"🎯 Expected move: {sig.get('exp_move_min',0)}–{sig.get('exp_move_max',0)}%"
    )

    if (
        sig.get("ema_state")
        and sig.get("ema20") is not None
        and sig.get("ema50") is not None
        and sig.get("ema200") is not None
    ):
        lines.append(
            f"📈 EMA: {sig.get('ema_state')} | "
            f"20={sig.get('ema20'):.6g} | "
            f"50={sig.get('ema50'):.6g} | "
            f"200={sig.get('ema200'):.6g}"
        )

    if sig.get("rsi7") is not None and sig.get("rsi14") is not None:
        lines.append(
            f"📍 RSI7={sig['rsi7']:.1f} | "
            f"RSI14={sig['rsi14']:.1f} | "
            f"{sig.get('rsi_state', 'UNKNOWN')}"
        )

    lines.append(f"📊 Score: {sig['score']}/10 | acc={sig.get('acc_score', 0)}")
    lines.append(
        f"🎯 Direction: {sig['direction']} "
        f"(up={sig['up_w']}, down={sig['down_w']})"
    )
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")

    pm = sig.get("pmeta") or {}

    if (
        pm.get("range_lo") is not None
        and pm.get("range_hi") is not None
        and pm.get("range_pct") is not None
    ):
        lines.append(
            f"🧲 Range(lookback): "
            f"{pm['range_lo']:.6g} → {pm['range_hi']:.6g} | "
            f"width≈{pm['range_pct']:.2f}%"
        )

    if sig.get("target") is not None:
        lines.append(f"🎯 Liquidity target: {sig['target']:.6g}")

    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | "
            f"{ez.get('low'):.6g} → {ez.get('high'):.6g} | "
            f"stop {ez.get('stop'):.6g}"
        )

    if sig.get("flags"):
        lines.append("")
        lines.append("Флаги (что увидел бот):")
        for f in sig["flags"]:
            lines.append(f"• {f}")

    if sig.get("dir_reasons"):
        lines.append("")
        lines.append("Причины направления:")
        for r in sig["dir_reasons"][:14]:
            lines.append(f"• {r}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:16]:
            lines.append(f"• {n}")

    return "\n".join(lines)


def swing_grade(sig):
    try:
        score = float(sig.get("score", 0))
        rr = float(sig.get("rr1", 0))
        room = float(sig.get("room_to_target", 0))
        h4 = float(sig.get("h4_bias_score", 0))
        h1 = float(sig.get("h1_setup_score", 0))
        m15 = float(sig.get("m15_trigger_score", 0))

        total = 0.0
        total += score * 0.8
        total += min(rr, 5) * 0.8
        total += h4 * 0.7
        total += h1 * 0.9
        total += m15 * 1.0
        total += min(room, 10) * 0.15

        if sig.get("late"):
            total -= 2

        try:
            entry = float(sig.get("entry_price", 0))
            stop = float(sig.get("stop", 0))

            if entry > 0 and stop > 0:
                stop_pct = abs(entry - stop) / entry * 100

                if stop_pct > 4:
                    total -= 2
                elif stop_pct > 2:
                    total -= 1
        except:
            pass

        total = round(total, 1)

        # ❗ сразу фильтр слабых
        if total < 5:
            return 0, None

        # безопасный риск
        try:
            risk_label, _ = coin_risk_label(sig)
        except:
            risk_label = "unknown"
        # =====================
        # SWING LEVELS
        # =====================
        
        if total >= 9:
        
            title = "🟢 СИЛЬНЫЙ SWING"
        
        elif total >= 7:
        
            title = "🟡 SWING SETUP"
        
        else:
        
            title = "⚪ НАБЛЮДЕНИЕ"
        
        # =====================
        # RISK DOWNGRADE
        # =====================
        
        if (
            isinstance(risk_label, str)
            and "высокий" in risk_label.lower()
        ):
        
            if title == "🟢 СИЛЬНЫЙ SWING":
        
                title = "🟡 SWING SETUP"
        
            elif title == "🟡 SWING SETUP":
        
                title = "⚪ НАБЛЮДЕНИЕ"
        
        return total, title

    except Exception:
        return 0, None
        
def coin_risk_label(sig):
    try:
        symbol = sig.get("instId") or sig.get("symbol") or ""
        price = float(sig.get("price", 0) or 0)
        oi = sig.get("oi_change")
        flags = set(sig.get("flags", []))

        risk = 0
        reasons = []

        # мемы / мелкие токены чаще резче двигаются
        risky_names = ["1000", "PEPE", "DOGE", "SHIB", "FLOKI", "BONK", "TRUMP", "FART"]
        if any(x in symbol.upper() for x in risky_names):
            risk += 1
            reasons.append("мем/агрессивный токен")

        # очень дешёвые монеты чаще шумные
        if price > 0 and price < 0.05:
            risk += 1
            reasons.append("очень низкая цена")

        # OI падает — интерес уходит
        if oi is not None:
            oi = float(oi)
            if oi <= OI_BAD:
                risk += 1
                reasons.append("OI падает")

        # ловушки/манипуляции
        if (
            "SWEEP_UP" in flags or
            "SWEEP_DOWN" in flags or
            "FAKE_DUMP" in flags or
            "BULL_TRAP" in flags or
            "BEAR_TRAP" in flags
        ):
            risk += 1
            reasons.append("есть признаки манипуляции")

        if risk >= 3:
            return "🔴 Риск монеты: высокий", reasons[:3]

        if risk >= 1:
            return "🟡 Риск монеты: средний", reasons[:3]

        return "🟢 Риск монеты: нормальный", reasons[:3]

    except Exception:
        return "⚪ Риск монеты: нет данных", []

def stop_risk_text(sig):
    try:
        entry = sig.get("entry_price")
        stop = sig.get("stop")
        side = str(
            sig.get("direction_code")
            or sig.get("side")
            or ""
        ).upper()

        if entry is None or stop is None:
            return None

        entry = float(entry)
        stop = float(stop)

        if entry <= 0:
            return None

        dist_pct = abs(entry - stop) / entry * 100
        dist_pct = round(dist_pct, 2)

        if dist_pct <= 1.0:
            return f"🟢 Стоп: хороший ({dist_pct}%)"

        if dist_pct <= 2.5:
            return f"🟡 Стоп: широкий ({dist_pct}%)"

        return f"🔴 Стоп: высокий риск ({dist_pct}%)"

    except Exception:
        return None
    
def msg_swing(sig):
    if not sig or not isinstance(sig, dict):

        print(
            "[MSG_SIG_NONE]",
            flush=True
        )
    
        return None
    side = str(
        sig.get("direction_code")
        or sig.get("side")
        or ""
    ).upper()
    if side in ["LONG", "UP", "BUY"]:
        icon = "🟢"
        side_ru = "ЛОНГ / вверх"
    else:
        icon = "🔴"
        side_ru = "ШОРТ / вниз"

    score, grade = swing_grade(sig)
    score_show = min(score, 10)

    # --- переводы внутренних кодов ---
    h1_map = {
        "pullback_hold": "откат удержан",
        "breakout_retest": "пробой + ретест",
        "range_break": "выход из диапазона",
        "trend_hold": "тренд удерживается",
        "none": "нет структуры"
    }

    m15_map = {
        "momentum_ready": "импульс готов",
        "compression_break": "выход из сжатия",
        "breakout_push": "пробой с ускорением",
        "retest_hold": "ретест удержан",
        "none": "нет триггера"
    }

    h1_raw = str(sig.get("h1_setup_type", "none"))
    m15_raw = str(sig.get("m15_trigger_type", "none"))

    h1_text = h1_map.get(h1_raw, h1_raw)
    m15_text = m15_map.get(m15_raw, m15_raw)

    lines = []
    lines.append(f"{icon} <b>{grade} — {sig['symbol']}</b>")
    lines.append("")
    lines.append(f"📊 Сила сигнала: <b>{score_show}/10</b>")
    lines.append(f"🧭 Направление: <b>{side_ru}</b>")
    lines.append("")

    lines.append("📈 Контекст:")
    lines.append(f"• H4: <b>{sig.get('h4_bias','?')}</b>")
    lines.append(f"• H1: <b>{h1_text}</b>")
    lines.append(f"• M15: <b>{m15_text}</b>")

    if sig.get("entry_price") is not None:
        lines.append("")
        lines.append("🎯 План:")
        lines.append(f"• Вход: <b>{sig['entry_price']}</b>")

    if sig.get("stop") is not None:
        lines.append(f"• Стоп: <b>{sig['stop']}</b>")

    if sig.get("tp1") is not None:
        lines.append(f"• TP1: <b>{sig['tp1']}</b>")

    if sig.get("tp2") is not None:
        lines.append(f"• TP2: <b>{sig['tp2']}</b>")

    if sig.get("rr1") is not None:
        lines.append(f"• RR: <b>{sig['rr1']}</b>")
    stop_info = stop_risk_text(sig)
    if stop_info:
        lines.append(f"• {stop_info}")

    room = sig.get("room_to_target")
    if room is not None:
        lines.append(f"• Потенциал: <b>{room}%</b>")

    oi = oi_status_text(sig)
    if oi:
        lines.append("")
        lines.append(oi)

    risk_label, risk_reasons = coin_risk_label(sig)
    lines.append(risk_label)

    if risk_reasons:
        lines.append("Причины риска:")
        for r in risk_reasons:
            lines.append(f"• {r}")

    lines.append("")
    lines.append("🧠 Что делать:")

    # =====================
    # SMART ACTION LOGIC
    # =====================

    mode = str(sig.get("signal_mode") or "")
    ep = float(sig.get("early_pressure_score") or 0)
    score_raw = float(sig.get("score") or 0)
    retest_ok = sig.get("retest_ok") is True

    if (
        mode == "EXPANSION"
        and ep >= 10
        and score_raw >= 18
    ):

        lines.append(
            "🚀 Импульс уже подтверждается. "
            "Искать вход по откату или младшему ТФ."
        )

    elif (
        mode == "TRANSITION"
        and ep >= 8
    ):

        lines.append(
            "🟠 Идёт смена контроля. "
            "Следить за реакцией цены и искать ранний вход."
        )

    elif (
        mode == "PREMOVE"
        and (
            ep >= 5
            or retest_ok
        )
    ):

        lines.append(
            "🟡 Рынок копит энергию. "
            "Готовить уровень входа заранее."
        )

    elif retest_ok:

        lines.append(
            "🟢 Есть удержание уровня / ретест. "
            "Можно смотреть вход с коротким стопом."
        )

    else:

        lines.append(
            "⏳ Пока наблюдать за структурой."
        )

    return "\n".join(lines)


def oi_status_text(sig):
    try:
        oi = sig.get("oi_change", None)

        if oi is None:
            return "⚪ OI: нет данных"

        if oi >= OI_STRONG:
            return f"🟢 OI: +{oi}% — сильный приток денег"

        if oi >= OI_GOOD:
            return f"🟡 OI: +{oi}% — умеренный приток"

        if oi <= OI_BAD:
            return f"🔴 OI: {oi}% — интерес падает"

        return f"⚪ OI: {oi}% — нейтрально"

    except Exception:
        return "⚪ OI: ошибка чтения"


def oi_trap_detector(sig):
    try:
        flags = sig.get("flags", [])
        oi = sig.get("oi_change", None)
        direction = str(sig.get("direction", ""))
        score = float(sig.get("score", 0) or 0)

        if oi is None:
            return None

        # REAL MONEY MOVE
        if "⬆️" in direction and oi >= OI_STRONG:
            return "✅ OI CONFIRM: рост поддержан новыми деньгами"

        if "⬇️" in direction and oi >= OI_STRONG:
            return "✅ OI CONFIRM: падение поддержано новыми шортами"

        # WEAK MOVE
        if "⬆️" in direction and oi <= OI_BAD:
            return "⚠️ OI WARNING: цена растёт, но интерес падает"

        if "⬇️" in direction and oi <= OI_BAD:
            return "⚠️ OI WARNING: цена падает, возможна фиксация"

        # TRAP RISK
        if "BREAKOUT_UP" in flags and oi < OI_GOOD:
            return "🪤 TRAP RISK: пробой вверх без сильного OI"

        if "BREAKOUT_DOWN" in flags and oi < OI_GOOD:
            return "🪤 TRAP RISK: пробой вниз без сильного OI"

        # IMPULSE
        if "VOL_SPIKE" in flags and oi >= OI_STRONG and score >= 6:
            return "💥 IMPULSE: объём + OI подтверждают движение"

        return None

    except Exception:
        return None


def choose_detail_message(sig):
    if MESSAGE_MODE == "SHORT":
        msg = msg_short(sig)
    elif MESSAGE_MODE == "MEDIUM":
        msg = msg_medium(sig)
    elif MESSAGE_MODE == "FULL":
        msg = msg_full(sig)
    elif sig["score"] >= EDGE_HIGH_SCORE:
        msg = msg_full(sig)
    elif sig["score"] >= EDGE_MID_SCORE:
        msg = msg_medium(sig)
    else:
        msg = msg_short(sig)

    oi = sig.get("oi_change")
    if oi is not None:
        msg += f"\n📊 OI: {oi_badge(oi)}"

    return msg


# =========================
# SCALP MESSAGE FORMATTER
# =========================
def msg_scalp(sig):

    if not sig or not isinstance(sig, dict):

        print(
            "[MSG_SCALP_SIG_NONE]",
            flush=True
        )

        return None

    side = sig.get("direction") or "UNKNOWN"

    score = sig.get("score", 0)

    price = sig.get("price")

    stage = sig.get("stage", "UNKNOWN")

    entry = sig.get("entry", "UNKNOWN")

    flags = set(sig.get("flags", []))

    symbol = sig.get("symbol") or sig.get("instId")

    story = []

    story_text = "\n".join(story)

    interpretation = build_market_interpretation(sig)

    msg = f"""
🔥 <b>РАННИЙ SCALP СИГНАЛ — {symbol}</b>

🧭 Направление: {side}

⚡ Тип входа:
{entry}

🧠 Что происходит:
{story_text}

💰 Цена: {price}

📊 Score: {round(score, 1)}
📍 Стадия: {stage}

🧠 Вывод:
{interpretation}

⚠️ Ранний сигнал ДО сильного движения.
""".strip()

    return msg

# =========================
# PRE SWING MESSAGE
# =========================
def msg_pre_swing(sig):
    if not sig or not isinstance(sig, dict):

        print(
            "[MSG_SIG_NONE]",
            flush=True
        )
    
        return None

    print(
        f"[PRE_SWING_DEBUG] "
        f"oi={sig.get('oi_change')} "
        f"flags={sig.get('flags')}",
        flush=True
    )

    symbol = sig.get("symbol") or sig.get("instId") or "UNKNOWN"
    level_icon, level_text = get_signal_level(sig)

    side = (
        sig.get("direction")
        or sig.get("side")
        or "NEUTRAL"
    )

    score = sig.get("score", 0)
    ep = sig.get("early_pressure_score", 0)
    stage = sig.get("stage", "UNKNOWN")
    entry = sig.get("entry", "UNKNOWN")
    acc = sig.get("acc_score", 0)
    oi = sig.get("oi_change")

    flags = sig.get("flags", [])

    story = []

    story = build_smart_story(sig)

    story_text = "\n".join(story)
    if oi is None:

        oi_text = "нет данных"

    else:

        oi_text = f"{oi}%"

    a_plus = sig.get("smart_money_a_plus")
    
    a_plus_text = ""
    
    if a_plus:
    
        a_plus_text = "\n🏆 Smart Money A+"
    
    interpretation = build_market_interpretation(sig)
    
    return f"""
    {level_icon} <b>{level_text} — {symbol}</b>
    
    🧭 Направление: {side}
    
    📊 Score: {round(score, 1)}
    ⚡ EP: {ep}
    🧱 Accumulation: {acc}
    
    🟡 OI: {oi_text}
    {a_plus_text}
    
    🎯 Entry:
    {entry}
    
    🧬 Stage:
    {stage}
    
    🧠 Что происходит:
    {story_text}
    
    🧠 Вывод:
    {interpretation}
    
    ⚠️ Это ранняя swing-фаза ДО полноценного импульса.
    """.strip()

def summary_message(alerts, cycle_info, regime):

    if not alerts:
        return None

    lines = []
    lines.append("🚨 SMART MONEY SCAN")
    lines.append(f"⏱ {cycle_info}")
    lines.append(f"🧭 BTC: {regime}")
    lines.append("")

    clean = []
    used = set()

    for s in alerts:
        sym = s.get("instId")
        if sym in used:
            continue
        used.add(sym)
        clean.append(s)

    top = clean[:3]

    for i, sig in enumerate(top, start=1):
        sym = sig.get("instId", "?")
        score = round(float(sig.get("score", 0)), 2)
        rank = round(float(sig.get("rank", 0)), 1)

        direction = dir_badge(sig.get("direction", ""))
        entry = sig.get("entry", "WAIT")
        stage = sig.get("stage", "NEUTRAL")

        oi = sig.get("oi_change", None)
        oi_text = oi_badge(oi) if oi is not None else "n/a"

        lines.append(
            f"{i}) {sym} | "
            f"score {score}/10 | "
            f"rank {rank} | "
            f"{direction} | "
            f"{entry} | "
            f"{stage} | "
            f"OI {oi_text}"
        )

    return "\n".join(lines)

   
# =========================
# PRE-MOVE MANIPULATION WATCH (V2)
# =========================
def is_pre_move_manip(sig):
    flags = set(sig.get("flags", []))
    stage = sig.get("stage", "")
    acc = int(sig.get("acc_score", 0))
    score = float(sig.get("score", 0))
    if score < 5:
        return False

    already_moving = ("ATR_EXPANSION" in flags) and ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags)
    if already_moving:
        return False

    if acc < MANIP_MIN_ACC_SCORE:
        return False

    if "🟡 MANIPULATION" in stage:
        return True
    if "🟣 ACCUMULATION" in stage and ("PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags or "FAKE_DUMP" in flags or "SWEEP_UP" in flags or "SWEEP_DOWN" in flags):
        return True

    if ("FAKE_DUMP" in flags) and ("COMP_5M" in flags or "COMP_15M" in flags):
        return True

    return False

def manip_summary_message(watch, cycle_info, regime):
    lines = []
    lines.append("🟡 PRE-MOVE WATCH — MANIPULATION / ACCUMULATION")
    lines.append(f"⏱ Cycle: {cycle_info}")
    lines.append(f"🧭 BTC regime: {regime}")

    if not watch:
        return None

    top_n = min(len(watch), 3)
    lines.append(f"Top {top_n}:")

    for sig in watch[:top_n]:
        sym = sig.get("instId") or sig.get("symbol") or sig.get("sym") or "?"
        acc = sig.get("acc_score", 0)
        stage = sig.get("stage", "")
        direction = sig.get("direction", "")
        score = float(sig.get("score") or sig.get("rank") or 0)

        lines.append(f"• {sym}: acc={acc} | {stage} | {direction} | score={score}/10")

    lines.append("")
    lines.append("🎯 Идея: ловим выстрел после манипуляции (не прыгаем в первый памп).")

    return "\n".join(lines)

# =========================
# SPAM CONTROL (cooldowns)
# =========================
def should_alert_symbol(state, sig):
    sym = sig["instId"]
    ss = state["symbols"].get(sym, {})

    prev_score = ss.get("prev_score")
    prev_flags = ss.get("prev_flags", [])
    prev_entry = ss.get("prev_entry")
    prev_direction = ss.get("prev_direction")
    last_alert_ts = ss.get("last_alert_ts", 0)

    now = now_ts()

    if now - int(last_alert_ts or 0) < ALERT_COOLDOWN_SEC:
        return False

    cur_score = sig.get("score", 0)
    cur_flags = sig.get("flags", [])
    cur_entry = sig.get("entry")
    cur_direction = sig.get("direction")

    same_signal = (
        prev_score == cur_score
        and prev_entry == cur_entry
        and prev_direction == cur_direction
        and set(prev_flags) == set(cur_flags)
    )

    if same_signal:
        return False

    changed = (
        prev_score is None
        or cur_score != prev_score
        or set(cur_flags) != set(prev_flags)
        or cur_entry != prev_entry
        or cur_direction != prev_direction
    )

    crossed = (prev_score or 0) < ALERT_MIN_SCORE and cur_score >= ALERT_MIN_SCORE

    return changed or crossed
def should_manip_alert(state, sig):
    sym = sig["instId"]
    ss = state["symbols"].get(sym, {})
    last_ts = ss.get("last_manip_alert_ts", 0)
    prev_m_flags = ss.get("prev_manip_flags", [])
    now = now_ts()

    if now - int(last_ts or 0) < MANIP_COOLDOWN_SEC:
        return False

    cur_flags = sig.get("flags", [])
    changed = (cur_flags != prev_m_flags)
    return changed or (last_ts == 0)

def mark_alert_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_alert_ts"] = sig["ts"]

def mark_manip_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_manip_alert_ts"] = sig["ts"]
    state["symbols"][sym]["prev_manip_flags"] = sig.get("flags", [])

def update_symbol_state(state, sig):
    sym = sig["instId"]

    state["symbols"].setdefault(sym, {})

    state["symbols"][sym]["prev_score"] = sig.get("score")
    state["symbols"][sym]["prev_flags"] = sig.get("flags", [])
    state["symbols"][sym]["prev_entry"] = sig.get("entry")
    state["symbols"][sym]["prev_direction"] = sig.get("direction")
    state["symbols"][sym]["last_ts"] = sig.get("ts", now_ts())
    
def safe_entry_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_safe_entry_ts", 0) or 0)
    return (now_ts() - last) < SAFE_ENTRY_SUPPRESS_SEC


def mark_safe_entry(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_safe_entry_ts"] = now_ts()

def should_send_summary(state, text):
    last = state.get("last_summary_text", "")
    now = now_ts()
    last_ts = int(state.get("last_summary_ts", 0) or 0)

    # одинаковое сообщение меньше 10 минут не шлем
    if text == last and (now - last_ts) < 600:
        return False

    state["last_summary_text"] = text
    state["last_summary_ts"] = now
    return True

# =========================
# START AFTERGLOW
# =========================
def start_afterglow_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_start_trigger_ts", 0) or 0)
    return (now_ts() - last) < START_AFTERGLOW_SEC

# =========================
# EARLY ALERT COOLDOWN
# =========================
def early_alert_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_early_alert_ts", 0) or 0)
    return (now_ts() - last) < EARLY_ALERT_COOLDOWN_SEC


def mark_early_alert(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_early_alert_ts"] = now_ts()

# =========================
# START TRIGGER RECENCY LOCK
# =========================
def start_trigger_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_start_trigger_ts", 0) or 0)
    return (now_ts() - last) < TRIGGER_START_COOLDOWN

# =========================
# PRIORITY ALERT SYSTEM (ADDON — слой сверху)
# =========================
def priority_allowed(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_priority_ts", 0) or 0)
    return (now_ts() - last) >= PRIORITY_COOLDOWN_SEC

def mark_priority(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_priority_ts"] = now_ts()

def is_priority_signal(sig):
    if not PRIORITY_ENABLED:
        return False

    score = int(sig.get("score", 0))
    acc = int(sig.get("acc_score", 0))
    flags = set(sig.get("flags", []))
    exp_max = float(sig.get("exp_move_max") or 0.0)
    if exp_max < PRE_MIN_EXPECTED_MOVE_PCT:
        return False

    # фильтр микро-флета (чтобы не спамило в супер-узком диапазоне)
    pm = sig.get("pmeta") or {}
    range_pct = pm.get("range_pct")
    if range_pct is not None and float(range_pct) < 0.35:
        return False

    if score < PRIORITY_SCORE_MIN:
        return False
    if acc < PRIORITY_ACC_MIN:
        return False

    strong_confirm = (
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and
        ("ATR_EXPANSION" in flags) and
        ("VOL_SPIKE" in flags)
    )

    smart_money_extra = (
        ("SWEEP_UP" in flags or "SWEEP_DOWN" in flags) or
        ("FAKE_DUMP" in flags) or
        ("OB_BIDS" in flags or "OB_ASKS" in flags) or
        ("OB_WALL_BID" in flags or "OB_WALL_ASK" in flags)
    )

    return strong_confirm or smart_money_extra

def msg_priority(sig):

    sym = fmt_symbol(sig["instId"])

    score = sig.get("score", 0)
    strong = sig.get("strong_setup", False)
    pump = sig.get("pump_warning", False)

    # уровни сигнала
    if pump and strong and score >= 9:
        icon = "🚀🚀🚀"
        title = "ELITE PUMP"

    elif pump and strong:
        icon = "🚀🚀"
        title = "PUMP WARNING"

    elif score >= 9 and strong:
        icon = "🟢🟢🟢"
        title = "ELITE SETUP"

    elif strong:
        icon = "🟢🟢"
        title = "STRONG SETUP"

    else:
        icon = "⭐"
        title = "PRIORITY ALERT"

    lines = []
    lines.append(f"{icon} {title} — {sym}")
        # ✅ Continuation highlight (чтобы сразу видно было)
    if "CONTINUATION_UP" in sig.get("flags", []):
        lines.append("📈 M15 CONTINUATION: рост после коррекции")
    elif "CONTINUATION_DOWN" in sig.get("flags", []):
        lines.append("📉 M15 CONTINUATION: падение после коррекции")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | acc={sig.get('acc_score',0)}")
    lines.append(f"🧭 {sig['direction']} | {sig['entry']} | {sig['stage']}")

    if pump:
        lines.append("⚠️ Возможен ранний памп")

    if sig.get("target") is not None:
        lines.append(f"🎯 ликвидность/цель: {sig['target']:.6g}")

    if sig.get("flags"):
        fl = ", ".join(sig["flags"][:10])
        lines.append(f"Flags: {fl}")

    return "\n".join(lines)

# =========================
# V3: 3-LEVEL TRIGGER (NEW)
# =========================
def trigger_allowed(state, instId, key, cooldown_sec):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get(key, 0) or 0)
    return (now_ts() - last) >= cooldown_sec

def trigger_mark(state, instId, key):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId][key] = now_ts()

def is_pre_trigger(sig):

    flags = set(sig.get("flags", []))
    acc = int(sig.get("acc_score", 0))
    score = float(sig.get("score", 0))
    direction = str(sig.get("direction", ""))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")

    if score < 5:
        return False

    if acc < TRIGGER_PRE_ACC:
        return False

    # не даём PRE в полном балансе
    if "БАЛАНС" in direction:
        return False

    # не даём PRE против явного EMA-тренда
    if "ВВЕРХ" in direction and ema_state == "EMA_BEAR":
        return False

    if "ВНИЗ" in direction and ema_state == "EMA_BULL":
        return False

    # если уже есть импульс — это уже не PRE
    if "ATR_EXPANSION" in flags:
        return False

    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        return False

    accumulation = (
        "COMP_5M" in flags or
        "COMP_15M" in flags
    )

    liquidity = (
        "PRESSURE_UP" in flags or
        "PRESSURE_DOWN" in flags or
        "FAKE_DUMP" in flags or
        "SWEEP_UP" in flags or
        "SWEEP_DOWN" in flags
    )

    near = ("NEAR_BREAKOUT_UP" in flags) or ("NEAR_BREAKOUT_DOWN" in flags)

    return (accumulation and liquidity) or (near and liquidity)
def is_start_trigger(sig):
    flags = set(sig.get("flags", []))
    acc = int(sig.get("acc_score", 0))
    score = float(sig.get("score", 0))
    direction = str(sig.get("direction", ""))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")

    if score < 5:
        return False

    if acc < TRIGGER_PRE_ACC:
        return False

    # EMA filter: не даём START против явного EMA-тренда
    if "ВВЕРХ" in direction and ema_state == "EMA_BEAR":
        return False

    if "ВНИЗ" in direction and ema_state == "EMA_BULL":
        return False

    # Контекст накопления
    context_ok = (
        ("COMP_5M" in flags) or
        ("COMP_15M" in flags)
    )

    # Цена у границы диапазона
    near_level = (
        ("NEAR_BREAKOUT_UP" in flags) or
        ("NEAR_BREAKOUT_DOWN" in flags)
    )

    # Давление в сторону
    pressure = (
        ("PRESSURE_UP" in flags) or
        ("PRESSURE_DOWN" in flags)
    )

    # Ранний старт — ДО пробоя
    early_start = context_ok and near_level and pressure

    # Классический старт — уже с импульсом
    impulse_ok = ("ATR_EXPANSION" in flags) or ("VOL_SPIKE" in flags)
    breakout_ok = ("BREAKOUT_UP" in flags) or ("BREAKOUT_DOWN" in flags)

    classic_start = context_ok and impulse_ok and breakout_ok

    return early_start or classic_start
def is_confirm_trigger(sig):
    flags = set(sig.get("flags", []))
    return (("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and ("ATR_EXPANSION" in flags) and ("VOL_SPIKE" in flags))

# =========================
# EARLY PRESSURE ALERT FILTER
# =========================
def is_early_pressure_alert(sig):
    side = sig.get("early_pressure_side")
    ep_score = float(sig.get("early_pressure_score") or 0)
    label = sig.get("early_pressure_label")
    direction = str(sig.get("direction", ""))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")
    stage = str(sig.get("stage", ""))
    flags = set(sig.get("flags", []))
    price = sig.get("price")
    target = sig.get("target")

    if not side or not label:
        return False

    if ep_score < 7:
        return False

    # не шлём, если уже есть SAFE ENTRY
    if "SAFE ENTRY" in str(sig.get("entry", "")):
        return False

    # не шлём в полном балансе
    if "БАЛАНС" in direction:
        return False

    # направление должно совпадать
    if side == "BUY" and "ВВЕРХ" not in direction:
        return False

    if side == "SELL" and "ВНИЗ" not in direction:
        return False

    # против явного EMA не шлём
    if side == "BUY" and ema_state == "EMA_BEAR":
        return False

    if side == "SELL" and ema_state == "EMA_BULL":
        return False

    # слишком близко к цели — поздно
    if too_close_to_target(price, target, min_room_pct=0.35):
        return False

    # если уже confirm / expansion — это не early
    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        return False

    if "ATR_EXPANSION" in flags:
        return False

    if "EXPANSION" in stage:
        return False

    # stage должен быть ранний
    if ("ACCUMULATION" not in stage) and ("TRANSITION" not in stage) and ("NEUTRAL" not in stage):
        return False

    # NEUTRAL пускаем только если есть явный PRE_BREAKOUT
    if "NEUTRAL" in stage:
        if "PRE_BREAKOUT_BUY" not in flags and "PRE_BREAKOUT_SELL" not in flags:
            return False

    # нужен реальный directional stack
    directional_ok = (
        "PRE_BREAKOUT_BUY" in flags or
        "PRE_BREAKOUT_SELL" in flags or
        "PRESSURE_UP" in flags or
        "PRESSURE_DOWN" in flags or
        "CONTINUATION_UP" in flags or
        "CONTINUATION_DOWN" in flags
    )

    if not directional_ok:
        return False

    return True



def msg_pre_trigger(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🟡 PRE-TRIGGER — зона перед выстрелом: {sym}")
    lines.append(f"💵 {sig['price']:.6g} | acc={sig.get('acc_score',0)} | {sig['direction']}")
    lines.append("Смысл: здесь вероятно собирают ликвидность. Готовь уровни диапазона.")
    return "\n".join(lines)

def msg_start_trigger(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🔥 TRIGGER START — старт из накопления: {sym}")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | acc={sig.get('acc_score',0)} | {sig['direction']}")
    if sig.get("target") is not None:
        lines.append(f"🎯 ликвидность/цель: {sig['target']:.6g}")
    lines.append("Действие: открыть график и искать вход по структуре (малый риск).")
    return "\n".join(lines)

def msg_confirm_trigger(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🚀 CONFIRM TRIGGER — самый чистый импульс: {sym}")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | {sig['direction']}")
    lines.append("Условия: CONFIRM + ATR + VOL (шанс продолжения выше).")
    return "\n".join(lines)

# =========================
# EARLY PRESSURE MESSAGE
# =========================
def msg_early_pressure(sig):
    sym = fmt_symbol(sig["instId"])
    side = sig.get("early_pressure_side", "?")
    ep_score = sig.get("early_pressure_score", 0)
    label = sig.get("early_pressure_label", "EARLY_PRESSURE")
    reasons = sig.get("early_pressure_reasons", [])

    arrow = "⬆️ BUY PRESSURE" if side == "BUY" else "⬇️ SELL PRESSURE"

    lines = []
    lines.append(f"🟠 {label} — {sym}")
    lines.append(f"💵 {sig['price']:.6g} | pressure_score={ep_score}")
    lines.append(f"🧭 {arrow} | {sig.get('direction', '')}")
    lines.append(f"🧬 STAGE: {sig.get('stage', '')}")
    lines.append(f"🎯 ENTRY: {sig.get('entry', '')}")

    if sig.get("target") is not None:
        lines.append(f"🎯 ликвидность/цель: {sig['target']:.6g}")

    if reasons:
        lines.append("Причины:")
        for r in reasons[:6]:
            lines.append(f"• {r}")

    lines.append("Действие: открыть график и смотреть вход по малому риску. Это раннее давление, не confirm.")

    return "\n".join(lines)

def msg_market_pressure(buy_symbols, sell_symbols):
    buy_count = len(buy_symbols)
    sell_count = len(sell_symbols)

    if sell_count >= 4 and sell_count >= buy_count + 2:
        lines = []
        lines.append("🚨 MARKET SELL-OFF / RISK-OFF")
        lines.append(f"SELL pressure: {sell_count} | BUY pressure: {buy_count}")
        lines.append(f"Монеты: {', '.join(sell_symbols[:8])}")
        lines.append("Смысл: рынок широко давят вниз. Ищем слабые альты, шорт-сетапы и не лезем в случайные лонги.")
        return "\n".join(lines)

    if buy_count >= 4 and buy_count >= sell_count + 2:
        lines = []
        lines.append("🚀 MARKET BUY PRESSURE / RISK-ON")
        lines.append(f"BUY pressure: {buy_count} | SELL pressure: {sell_count}")
        lines.append(f"Монеты: {', '.join(buy_symbols[:8])}")
        lines.append("Смысл: рынок широко тащат вверх. Ищем сильные альты и продолжение движения.")
        return "\n".join(lines)

    return None

def check_signal_results():

    open_signals = get_open_signals()

    if not open_signals:
        return

    for s in open_signals:

        symbol = s["symbol"]
        entry = s["entry"]
        direction = s["direction"]
        direction_code = direction_code_from_text(direction)
        signal_id = s["id"]
        created_at = s.get("created_at")

        if not created_at:
            continue

        # ⏱ ждём минимум RESULT_CHECK_SEC секунд
        if time.time() - created_at < RESULT_CHECK_SEC:
           continue

        try:
            price = get_last_price(symbol)
        except:
            continue

        move_pct = (price - entry) / entry * 100

        if direction_code == "DOWN":
            move_pct = -move_pct

        if move_pct >= 1.0:
            result = "HIT"
        elif move_pct <= -1.0:
            result = "FAIL"
        else:
            result = "NEUTRAL"

        close_signal(signal_id, move_pct, result)
        update_stats(result, move_pct, s)

        try:
            with open("stats.json", "r") as f:
                stats = json.load(f)

            resolved = stats.get("resolved", stats.get("hit", 0) + stats.get("fail", 0))

            if resolved > 0 and resolved % 5 == 0:
                send_telegram(show_stats())

        except:
            pass

        if s["id"] % 10 == 0:
            send_telegram(show_stats())

        print(f"[ANALYST] {symbol} result={result} move={round(move_pct,2)}%")

        send_telegram(
            f"📊 RESULT {symbol}\n"
            f"{result} | {round(move_pct,2)}%"
        )

def rank_signal(sig):
    try:
        rank = 0.0

        rank += float(sig.get("score", 0)) * 1.2
        rank += min(float(sig.get("rr1", 0)), 5) * 1.5
        rank += float(sig.get("h4_bias_score", 0)) * 0.8
        rank += float(sig.get("h1_setup_score", 0)) * 1.0
        rank += float(sig.get("m15_trigger_score", 0)) * 1.1

        if str(sig.get("status", "")) == "SWING TRIGGER":
            rank += 2.0

        if sig.get("late"):
            rank -= 3.0

        verdict = str(sig.get("verdict", "")).lower()

        if "широкая" in verdict:
            rank -= 2.0

        if "близко" in verdict:
            rank -= 2.0

        # ===== OPEN INTEREST BONUS =====
        oi = sig.get("oi_change")

        if oi is not None:
            if oi >= OI_STRONG:
                rank += 2.0
            elif oi >= OI_GOOD:
                rank += 1.0
            elif oi <= OI_BAD:
                rank -= 1.0

        return round(rank, 2)

    except:
        return 0.0

def confirm_grade(sig):
    try:
        checks = 0

        if sig.get("rr1", 0) >= 1.8:
            checks += 1

        if sig.get("h4_bias_score", 0) >= 3:
            checks += 1

        if sig.get("h1_setup_score", 0) >= 3:
            checks += 1

        if sig.get("m15_trigger_score", 0) >= 3:
            checks += 1

        if not sig.get("late", False):
            checks += 1

        status = str(sig.get("status", ""))

        if status == "SWING TRIGGER" and checks >= 4:
            return "CONFIRMED"

        if checks >= 3:
            return "WATCH"

        return "SKIP"

    except:
        return "SKIP"

def get_open_interest_change(symbol):
    try:
        url = "https://api.bybit.com/v5/market/open-interest"
        params = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min"
        }

        r = requests.get(url, params=params, timeout=8)
        data = r.json()

        rows = data["result"]["list"]
        if len(rows) < 2:
            return None

        now_oi = float(rows[0]["openInterest"])
        prev_oi = float(rows[1]["openInterest"])

        if prev_oi <= 0:
            return None

        change = (now_oi - prev_oi) / prev_oi * 100
        return round(change, 2)

    except:
        return None

def is_best_only_signal(sig):
    try:
        rank = float(sig.get("rank", 0))
        score = float(sig.get("score", 0))
        acc = int(sig.get("acc_score", 0))
        rr1 = float(sig.get("rr1", 0))
        late = bool(sig.get("late", False))
        grade = str(sig.get("grade", "SKIP"))
        oi = sig.get("oi_change")

        if grade != "CONFIRMED":
            return False

        if rank < 22:
            return False

        if score < 7:
            return False

        if acc < 2:
            return False

        if rr1 < 3:
            return False

        if late:
            return False

        if oi is not None and float(oi) < -0.05:
            return False

        return True

    except:
        return False

# =====================
# SAFE MESSAGE BUILDERS
# =====================

def safe_msg_builder(builder, sig, fallback_name="SIGNAL"):

    try:

        if callable(builder):
            return builder(sig)

    except Exception as e:

        print(
            f"[MSG_BUILDER_ERROR] "
            f"{fallback_name} {e}",
            flush=True
        )

    try:

        symbol = (
            sig.get("instId")
            or sig.get("symbol")
            or "UNKNOWN"
        )

        side = (
            sig.get("side")
            or sig.get("direction")
            or "NEUTRAL"
        )

        score = sig.get("score", 0)

        return (
            f"⚠️ <b>{fallback_name}</b>\n\n"
            f"🪙 {symbol}\n"
            f"🧭 {side}\n"
            f"⭐ Score: {score}"
        )

    except:

        return f"⚠️ {fallback_name}"

# =========================
# ELITE PRE-SWING FILTER
# =========================
def is_elite_pre_swing(sig):

    try:

        flags = set(sig.get("flags", []))

        score = float(sig.get("score") or 0)
        ep = float(sig.get("early_pressure_score") or 0)
        acc = float(sig.get("acc_score") or 0)

        has_mtf = (
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
        )

        has_pressure = (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )

        has_launch = (
            "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
            or "EARLY_LAUNCH_UP" in flags
            or "EARLY_LAUNCH_DOWN" in flags
        )

        has_shift = (
            "BULLISH_SHIFT" in flags
            or "BEARISH_SHIFT" in flags
        )

        has_acceleration = (
            "ACCELERATION_UP" in flags
            or "ACCELERATION_DOWN" in flags
        )

        has_absorption = (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        )

        # =====================
        # HARD MINIMUM
        # =====================
        
        if (
        
            score < 12
        
            and ep < 7
        
        ):
        
            return False, "elite_low_score"
        
        # =====================
        # EP FILTER
        # =====================
        
        if ep < 8:
        
            # =====================
            # FAST IMPULSE EXCEPTION
            # =====================

            if (
                has_launch
                and has_acceleration
                and has_shift
            ):

                print(
                    f"[FAST_IMPULSE_EXCEPTION] "
                    f"{sig.get('symbol')}",
                    flush=True
                )

            else:

                return False, "elite_low_ep"

        acc = float(
            sig.get("acc_score") or 0
        )

        # =====================
        # ACC FILTER
        # =====================
        
        if acc < 2:
        
            if (
        
                (
                    has_launch
                    and has_acceleration
                    and has_shift
                )
        
                or (
        
                    ep >= 10
        
                    and (
                        "MTF_LONG_ALIGN" in flags
                        or "MTF_SHORT_ALIGN" in flags
                    )
                )
        
                or (
                    "BUYER_ABSORPTION" in flags
                    or "SELLER_ABSORPTION" in flags
                )
            ):
        
                print(
                    f"[FAST_EXPANSION_ACC_EXCEPTION] "
                    f"{sig.get('symbol')}",
                    flush=True
                )
        
            else:
        
                return False, "elite_low_acc"

        # =====================
        # MTF FILTER
        # =====================
        
        if (
        
            not has_mtf
        
            and ep < 12
        
            and not has_launch
        
            and sig.get("signal_mode") != "PREMOVE"
        
        ):
        
            return False, "elite_no_mtf"

        # =====================
        # PRESSURE FILTER
        # =====================

        if not has_pressure:
            return False, "elite_no_pressure"

        # =====================
        # QUALITY CONFIRMATION
        # =====================

        if not (
            has_launch
            or has_acceleration
            or has_shift
        ):

            # =====================
            # STRONG ACC EXCEPTION
            # =====================
            
            if (
            
                (
                    ep >= 10
                    and acc >= 2
                )
            
                or has_mtf
            
                or (
                    "BUYER_ABSORPTION" in flags
                    or "SELLER_ABSORPTION" in flags
                )
            
            ):
            
                print(
                    f"[ELITE_ACC_EXCEPTION] "
                    f"{sig.get('symbol')}",
                    flush=True
                )
            
            else:
            
                return False, "elite_no_launch_or_shift"

        # =====================
        # ABSORPTION FILTER
        # =====================

        if (

            not has_absorption

            and ep < 12

            and acc < 3

        ):

            return False, "elite_no_absorption"

        return True, "elite_pre_swing"

    except Exception as e:

        print(
            f"[ELITE_PRE_SWING_ERROR] {e}",
            flush=True
        )

        return False, "elite_error"

# =========================
# ELITE SCORE ENGINE
# =========================
def calc_elite_score(sig):

    try:

        flags = set(sig.get("flags", []))

        elite_score = 0
        reasons = []

        ep = float(
            sig.get("early_pressure_score") or 0
        )

        acc = float(
            sig.get("acc_score") or 0
        )

        oi = float(
            sig.get("oi_change") or 0
        )

        score = float(
            sig.get("score") or 0
        )

        # =====================
        # STAGE PRIORITY
        # =====================
        
        stage = str(sig.get("stage") or "")
        
        if "EXPANSION" in stage:
        
            elite_score += 4
            reasons.append("expansion")
        
        elif "TRANSITION" in stage:
        
            elite_score += 1
            reasons.append("transition")

        # =====================
        # EP
        # =====================

        if ep >= 10:

            elite_score += 2
            reasons.append("ep")

        if ep >= 15:

            elite_score += 2
            reasons.append("ep_strong")

        # =====================
        # ACCUMULATION
        # =====================

        if acc >= 2:

            elite_score += 2
            reasons.append("acc")

        if acc >= 4:

            elite_score += 2
            reasons.append("acc_strong")

        # =====================
        # OI
        # =====================

        if abs(oi) >= 0.20:

            elite_score += 2
            reasons.append("oi")

        # =====================
        # MTF
        # =====================

        if (
            "MTF_LONG_ALIGN" in flags
            or "MTF_SHORT_ALIGN" in flags
        ):

            elite_score += 2
            reasons.append("mtf")

        # =====================
        # ACCELERATION
        # =====================

        if (
            "ACCELERATION_UP" in flags
            or "ACCELERATION_DOWN" in flags
        ):

            elite_score += 2
            reasons.append("acceleration")

        # =====================
        # LAUNCH
        # =====================

        if (
            "LAUNCH_PROXIMITY_UP" in flags
            or "LAUNCH_PROXIMITY_DOWN" in flags
            or "EXPLOSION_READY_UP" in flags
            or "EXPLOSION_READY_DOWN" in flags
        ):

            elite_score += 2
            reasons.append("launch")

        # =====================
        # ABSORPTION
        # =====================

        if (
            "BUYER_ABSORPTION" in flags
            or "SELLER_ABSORPTION" in flags
        ):

            elite_score += 2
            reasons.append("absorption")

        # =====================
        # VOLUME
        # =====================

        if "VOL_SPIKE" in flags:

            elite_score += 1
            reasons.append("volume")

        # =====================
        # BIG SCORE BONUS
        # =====================

        if score >= 30:

            elite_score += 2
            reasons.append("big_score")

        elif score >= 20:

            elite_score += 1
            reasons.append("score")

        return elite_score, reasons

    except Exception as e:

        print(
            f"[ELITE_SCORE_ERROR] {e}",
            flush=True
        )

        return 0, []

# =========================
# SIGNAL LEVEL ENGINE
# =========================
def get_signal_level(sig):

    try:

        elite_score, _ = calc_elite_score(sig)
        acc = float(
            sig.get("acc_score") or 0
        )
        
        ep = float(
            sig.get("early_pressure_score") or 0
        )

        # =====================
        # VERY STRONG
        # =====================
        
        stage = str(sig.get("stage") or "")
        
        if (
            elite_score >= 18
            and acc >= 2
            and ep >= 9
            and "EXPANSION" in stage
        ):
        
            return (
                "🟢",
                "ОЧЕНЬ СИЛЬНЫЙ СИГНАЛ"
            )
        # =====================
        # STRONG
        # =====================

        
        if (

            elite_score >= 10
        
            and ep >= 15
        
            and acc >= 2
        
        ):
        
            return (
                "🟡",
                "СИЛЬНЫЙ СИГНАЛ"
            )

        # =====================
        # EARLY / WEAK
        # =====================

        return (
            "🔴",
            "РАННИЙ СИГНАЛ"
        )

    except Exception as e:

        print(
            f"[SIGNAL_LEVEL_ERROR] {e}",
            flush=True
        )

        return (
            "⚪",
            "СИГНАЛ"
        )

# =========================
# ANTI REPEAT CACHE
# =========================

LAST_SIGNAL_CACHE = {}

# =========================
# ANTI REPEAT ENGINE
# =========================

def is_repeat_signal(sig):

    try:

        if not sig or not isinstance(sig, dict):
            return False

        symbol = str(
            sig.get("symbol")
            or sig.get("instId")
            or ""
        )

        direction = str(
            sig.get("direction_code")
            or sig.get("direction")
            or ""
        )

        stage = str(
            sig.get("stage")
            or ""
        )

        entry = str(
            sig.get("entry")
            or sig.get("entry_type")
            or ""
        )

        score = float(
            sig.get("score") or 0
        )

        signature = (
            symbol,
            direction,
            stage,
            entry
        )

        now = time.time()

        old = LAST_SIGNAL_CACHE.get(signature)

        if old:

            old_ts = old.get("ts", 0)
            old_score = old.get("score", 0)

            age = now - old_ts

            score_diff = abs(
                score - old_score
            )

            if (
                age < 1800
                and score_diff <= 2
            ):

                print(
                    f"[REPEAT_SIGNAL_SKIP] "
                    f"{symbol} "
                    f"age={round(age)}s "
                    f"score_diff={score_diff}",
                    flush=True
                )

                return True

        LAST_SIGNAL_CACHE[signature] = {
            "ts": now,
            "score": score
        }

        return False

    except Exception as e:

        print(
            f"[ANTI_REPEAT_ERROR] {e}",
            flush=True
        )

        return False

# =========================
# SMART STORY
# =========================

def build_smart_story(sig):

    try:

        flags = set(sig.get("flags", []))

        story = []

        # =====================
        # ENERGY
        # =====================

        if "ENERGY_BUILDUP" in flags:

            story.append(
                "• рынок накапливает энергию перед движением"
            )

        # =====================
        # PRESSURE
        # =====================

        if "PRESSURE_UP" in flags:

            story.append(
                "• покупатели начинают усиливать давление"
            )

        if "PRESSURE_DOWN" in flags:

            story.append(
                "• продавцы начинают усиливать давление"
            )

        # =====================
        # SHIFT
        # =====================

        if "BULLISH_SHIFT" in flags:

            story.append(
                "• рынок начинает смещаться в LONG"
            )

        if "BEARISH_SHIFT" in flags:

            story.append(
                "• рынок начинает смещаться в SHORT"
            )

        # =====================
        # ACCELERATION
        # =====================

        if "ACCELERATION_UP" in flags:

            story.append(
                "• движение вверх начинает ускоряться"
            )

        if "ACCELERATION_DOWN" in flags:

            story.append(
                "• движение вниз начинает ускоряться"
            )

        # =====================
        # ABSORPTION
        # =====================

        if "BUYER_ABSORPTION" in flags:

            story.append(
                "• крупный покупатель удерживает цену"
            )

        if "SELLER_ABSORPTION" in flags:

            story.append(
                "• крупный продавец удерживает цену"
            )

        # =====================
        # MTF
        # =====================

        if "MTF_LONG_ALIGN" in flags:

            story.append(
                "• таймфреймы поддерживают LONG"
            )

        if "MTF_SHORT_ALIGN" in flags:

            story.append(
                "• таймфреймы поддерживают SHORT"
            )

        # =====================
        # LAUNCH
        # =====================

        if (
            "LAUNCH_PROXIMITY_UP" in flags
            or "EXPLOSION_READY_UP" in flags
        ):

            story.append(
                "• рынок близок к запуску движения вверх"
            )

        if (
            "LAUNCH_PROXIMITY_DOWN" in flags
            or "EXPLOSION_READY_DOWN" in flags
        ):

            story.append(
                "• рынок близок к запуску движения вниз"
            )

        # =====================
        # VOLUME
        # =====================

        if "VOL_SPIKE" in flags:

            story.append(
                "• в рынок начинает заходить объём"
            )

        return story

    except Exception as e:

        print(
            f"[SMART_STORY_ERROR] {e}",
            flush=True
        )

        return []

# =========================
# MARKET INTERPRETATION ENGINE
# =========================
def build_market_interpretation(sig):

    try:

        if not sig or not isinstance(sig, dict):

            print(
                "[INTERPRETATION_SIG_NONE]",
                flush=True
            )

            return "нет данных"

        flags = set(sig.get("flags", []))

        ep = float(
            sig.get("early_pressure_score") or 0
        )

        acc = float(
            sig.get("acc_score") or 0
        )

        oi = float(
            sig.get("oi_change") or 0
        )
        
        thoughts = []

        # =====================
        # LIQUIDITY TARGET
        # =====================
        
        dist_above = float(
            sig.get("dist_above_pct") or 999
        )
        
        dist_below = float(
            sig.get("dist_below_pct") or 999
        )
        
        if dist_above < dist_below:
        
            thoughts.append(
                f"ближайшая ликвидность сверху ({round(dist_above,1)}%)"
            )
        
        elif dist_below < dist_above:
        
            thoughts.append(
                f"ближайшая ликвидность снизу ({round(dist_below,1)}%)"
            )

        # =====================
        # LIQUIDITY MAGNET
        # =====================
        
        if (
        
            dist_above <= 2
        
            and dist_above < dist_below
        
        ):
        
            thoughts.append(
                "рынок может тянуться к ликвидности сверху"
            )
        
        elif (
        
            dist_below <= 2
        
            and dist_below < dist_above
        
        ):
        
            thoughts.append(
                "рынок может тянуться к ликвидности снизу"
            )
        # =====================
        # MARKET CONTROL
        # =====================

        if (
            "PRESSURE_UP" in flags
            or "BULLISH_SHIFT" in flags
        ):

            thoughts.append(
                "👑 покупатели контролируют ситуацию"
            )

        elif (
            "PRESSURE_DOWN" in flags
            or "BEARISH_SHIFT" in flags
        ):

            thoughts.append(
                "👑 продавцы контролируют ситуацию"
            )

        # =====================
        # MTF ALIGNMENT
        # =====================

        if "MTF_LONG_ALIGN" in flags:
        
            thoughts.append(
               "👑 покупатели контролируют ситуацию"
            )
        
        elif "MTF_SHORT_ALIGN" in flags:
        
            thoughts.append(
                "🔻 продавцы контролируют ситуацию"
            )

        # =====================
        # PRICE + OI
        # =====================

        try:

            price_change = float(
                sig.get("price_change_pct") or 0
            )

        except:

            price_change = 0

        # =====================
        # REAL MONEY LONG
        # =====================

        if (
            price_change > 0
            and oi >= 0.25
        ):

            thoughts.append(
                "💰 в рынок заходят новые деньги"
            )

            thoughts.append(
                "🚀 покупатели открывают новые позиции"
            )

            thoughts.append(
                "✅ рост поддерживается реальным капиталом"
            )

        # =====================
        # REAL MONEY SHORT
        # =====================

        elif (
            price_change < 0
            and oi >= 0.25
        ):

            thoughts.append(
                "💰 в рынок заходят новые деньги"
            )

            thoughts.append(
                "🔻 продавцы открывают новые позиции"
            )

            thoughts.append(
                "✅ давление вниз поддерживается капиталом"
            )

        # =====================
        # SHORT SQUEEZE
        # =====================

        elif (
            price_change > 0
            and oi <= -0.25
        ):

            thoughts.append(
                "⚠️ шортисты закрывают позиции"
            )

            thoughts.append(
                "⚠️ рост может быть вызван ликвидацией шортов"
            )

            thoughts.append(
                "⚠️ это не самый сильный тип роста"
            )

        # =====================
        # LONG FLUSH
        # =====================

        elif (
            price_change < 0
            and oi <= -0.25
        ):

            thoughts.append(
                "⚠️ лонгисты выходят из рынка"
            )

            thoughts.append(
                "⚠️ интерес покупателей ослабевает"
            )

            thoughts.append(
                "⚠️ снижение может быть вызвано выходом из позиций"
            )

        # =====================
        # LOW MONEY FLOW
        # =====================

        elif abs(oi) < 0.10:

            thoughts.append(
                "💰 новых денег почти нет"
            )

            thoughts.append(
                "⚠️ движение пока больше техническое"
            )

        # =====================
        # ENERGY
        # =====================
        
        if ep >= 20:
        
            thoughts.append(
                "🔥 энергия перед движением очень высокая"
            )
        # =====================
        # ACCUMULATION
        # =====================

        if acc >= 4:

            thoughts.append(
                "🏦 идёт активное накопление позиции"
            )

        elif acc >= 3:

            thoughts.append(
                "🏦 рынок удерживает накопление"
            )

        # =====================
        # FINAL
        # =====================

        if not thoughts:

            return (
                "рынок пока не показывает "
                "сильной подготовки к движению"
            )

        return ". ".join(thoughts) + "."

    except Exception as e:

        print(
            f"[MARKET_INTERPRETATION_ERROR] {e}",
            flush=True
        )

        return (
            "не удалось построить "
            "интерпретацию рынка"
        )
# =========================
# SIGNAL REPEAT FILTER
# =========================
LAST_SIGNAL_STATE = {}

def is_repeat_signal(sig):

    try:

        symbol = (
            sig.get("symbol")
            or sig.get("instId")
            or "UNKNOWN"
        )

        side = (
            sig.get("direction")
            or sig.get("side")
            or "NEUTRAL"
        )

        stage = sig.get("stage", "")

        score = float(
            sig.get("score") or 0
        )

        old = LAST_SIGNAL_STATE.get(symbol)

        if not old:

            LAST_SIGNAL_STATE[symbol] = {
                "side": side,
                "stage": stage,
                "score": score,
            }

            return False

        same_side = (
            old.get("side") == side
        )

        same_stage = (
            old.get("stage") == stage
        )

        score_diff = abs(
            old.get("score", 0) - score
        )

        # =====================
        # REPEAT DETECTED
        # =====================

        if (
            same_side
            and same_stage
            and score_diff <= 2
        ):

            print(
                f"[REPEAT_SIGNAL_BLOCK] "
                f"{symbol}",
                flush=True
            )

            return True

        # =====================
        # UPDATE STATE
        # =====================

        LAST_SIGNAL_STATE[symbol] = {
            "side": side,
            "stage": stage,
            "score": score,
        }

        return False

    except Exception as e:

        print(
            f"[REPEAT_FILTER_ERROR] {e}",
            flush=True
        )

        return False


# =========================
# MAIN LOOP (STABLE VERSION)
# =========================
if __name__ == "__main__": 

    init_db()

    print("PROGRAM STARTED V2")

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN / CHAT_ID")

    print("TOKENS OK")

    state = load_state()
    print("STATE LOADED")

    if not isinstance(state.get("swing_sent"), dict):
        state["swing_sent"] = {}

    # =====================
    # SCAN SETTINGS
    # =====================

    SCAN_BATCH = int(os.getenv("SCAN_BATCH") or "26")
    TOP_ALERTS_LIMIT = int(os.getenv("TOP_ALERTS_LIMIT") or "3")
    scan_index = 0

    try:
        send_telegram(f"🚀 SMART MONEY SCANNER — PRO EDGE v4 started ({EXCHANGE} market scan)")
    except Exception as e:
        print("START TELEGRAM ERROR:", e)

    # =====================
    # SCALP CACHE
    # =====================
    scalp_sent_cache = {}

    while True:

        check_signal_results()
        t0 = time.time()
    
        try:
    
            regime, _btc = btc_regime()
    
            MARKET_MODE = "NEUTRAL"
    
            if "BULL" in str(regime).upper() or "UP" in str(regime).upper():
                MARKET_MODE = "BULL"
    
            elif "BEAR" in str(regime).upper() or "DOWN" in str(regime).upper():
                MARKET_MODE = "BEAR"
    
            alerts = []
            manip_watch = []
            early_count = 0
            start_count = 0
            pre_count = 0
            early_buy_symbols = []
            early_sell_symbols = []
            swing_candidates = []
    
            # =====================
            # SCAN MONETS
            # =====================
    
            all_candidates = get_market_candidates()
    
            if not all_candidates:
                print("NO CANDIDATES FOUND")
                time.sleep(10)
                continue
    
            total_symbols = len(all_candidates)
    
            if scan_index >= total_symbols:
                scan_index = 0
    
            candidates = all_candidates[
                scan_index:scan_index + SCAN_BATCH
            ]
    
            scan_index += SCAN_BATCH
    
            print(
                f"Scanning {len(candidates)} symbols this cycle | "
                f"index={scan_index}/{total_symbols}"
            )
    
           
    
            # =====================
            # SCAN LOOP
            # =====================
            
            scalp_sent_this_cycle = 0
            
            for instId, vol_usdt, pct in candidates:
            
                print(f"[LOOP] {instId} start")
            
                time.sleep(0.55)
                sig = None

                try:

                    sig = build_signal(instId)

                    # =====================
                    # INVALID CHECK
                    # =====================
                    
                    if not sig:
                    
                        print(
                            f"[INVALID_SIGNAL] {instId}",
                            flush=True
                        )
                    
                        print(
                            f"[INVALID_DEBUG] "
                            f"sig={sig}",
                            flush=True
                        )
                    
                        continue

                    if not isinstance(sig, dict):

                        print(
                            f"[INVALID_TYPE] {instId}",
                            flush=True
                        )

                        continue

                    # =====================
                    # FORCE VALID BY OVERRIDE
                    # =====================

                    if sig.get("sendable") is True:

                        sig["valid"] = True

                        print(
                            f"[FORCE_VALID_BY_OVERRIDE] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # OPEN INTEREST
                    # =====================
                    
                    oi_ttl = int(
                        os.getenv("OI_CACHE_TTL_SEC", "1800")
                    )
                    
                    new_oi = get_open_interest_change(instId)
                    
                    print(
                        f"[OI_DEBUG_RAW] "
                        f"{instId} "
                        f"new_oi={new_oi} "
                        f"type={type(new_oi)}",
                        flush=True
                    )
                    
                    # =====================
                    # OI TREND
                    # =====================
                    
                    if new_oi is not None:
                    
                        oi_trend_data = analyze_oi_trend(
                            instId,
                            new_oi
                        )
                    
                        sig.update(oi_trend_data)
                    
                        for f in oi_trend_data.get(
                            "oi_trend_flags",
                            []
                        ):
                    
                            sig["flags"].append(f)
                    
                    # =====================
                    # OI NORMALIZATION
                    # =====================
                    
                    if new_oi is not None:
                    
                        new_oi = float(new_oi)
                    
                        sig["oi_change"] = new_oi
                        sig["oi_available"] = True

                        # =====================
                        # OI MEMORY
                        # =====================

                        oi_history = OI_MEMORY.get(
                            instId,
                            []
                        )

                        oi_history.append(new_oi)

                        oi_history = oi_history[-5:]

                        OI_MEMORY[instId] = oi_history
                    
                        print(
                            f"[OI_OK] "
                            f"{instId} "
                            f"oi={new_oi}",
                            flush=True
                        )
                    
                    try:
                        # =====================
                        # OI TREND ANALYSIS
                        # =====================

                        oi_trend_up = False
                        oi_trend_down = False

                        if len(oi_history) >= 3:

                            last_3 = oi_history[-3:]

                            # =====================
                            # RISING OI
                            # =====================

                            if (

                                last_3[0] < last_3[1]
                                < last_3[2]

                                and last_3[2] >= 0.08

                            ):

                                oi_trend_up = True

                                sig["oi_trend"] = "UP"

                                flags.append(
                                    "OI_TREND_UP"
                                )

                                sig["score"] += 1

                                print(
                                    f"[OI_TREND_UP] "
                                    f"{instId} "
                                    f"{last_3}",
                                    flush=True
                                )

                            # =====================
                            # FALLING OI
                            # =====================

                            elif (

                                last_3[0] > last_3[1]
                                > last_3[2]

                                and last_3[2] <= -0.08

                            ):

                                oi_trend_down = True

                                sig["oi_trend"] = "DOWN"

                                flags.append(
                                    "OI_TREND_DOWN"
                                )

                                sig["score"] += 1

                                print(
                                    f"[OI_TREND_DOWN] "
                                    f"{instId} "
                                    f"{last_3}",
                                    flush=True
                                )  

                            # =====================
                            # OI SCORE BOOST
                            # =====================

                            oi_trend = sig.get("oi_trend")
                            side = str(
                                sig.get("direction_code")
                                or sig.get("side")
                                or sig.get("direction")
                                or ""
                            ).upper()

                            # LONG + OI растёт = деньги заходят в сторону LONG
                            if (
                                oi_trend == "UP"
                                and side in ("LONG", "BUY", "UP", "ВВЕРХ", "LONG BIAS")
                            ):

                                sig["score"] = float(sig.get("score") or 0) + 2
                                sig["oi_score_boost"] = 2

                                print(
                                    f"[OI_SCORE_BOOST_LONG] "
                                    f"{instId} "
                                    f"trend={oi_trend}",
                                    flush=True
                                )

                            # SHORT + OI падает/растёт: пока даём мягкий boost,
                            # потому что в твоей логике отрицательный OI может показывать выход/давление
                            elif (
                                side in ("SHORT", "SELL", "DOWN", "ВНИЗ", "SHORT BIAS")
                                and oi_trend in ("UP", "DOWN")
                            ):

                                sig["score"] = float(sig.get("score") or 0) + 2
                                sig["oi_score_boost"] = 2

                                print(
                                    f"[OI_SCORE_BOOST_SHORT] "
                                    f"{instId} "
                                    f"trend={oi_trend}",
                                    flush=True
                                )
                        # =====================
                        # OI NOISE FILTER
                        # =====================

                        if abs(new_oi) < 0.02:
                   

                            new_oi = 0

                    except Exception as e:

                        print(
                            f"[OI_PARSE_ERROR] {instId} {e}",
                            flush=True
                        )

                        new_oi = None

                    # =====================
                    # OI STATE CACHE
                    # =====================

                    state["symbols"].setdefault(instId, {})

                    sym_state = state["symbols"][instId]

                    prev = sym_state.get("last_oi_change")

                    prev_ts = int(
                        sym_state.get("last_oi_ts", 0) or 0
                    )

                    age = (
                        now_ts() - prev_ts
                        if prev_ts
                        else None
                    )

                    # =====================
                    # APPLY FRESH OI
                    # =====================

                    if new_oi is not None:

                        sig["oi_change"] = new_oi

                        sym_state["last_oi_change"] = new_oi

                        sym_state["last_oi_ts"] = now_ts()

                        print(
                            f"[OI_NEW] "
                            f"{instId} fresh OI={new_oi}%",
                            flush=True
                        )

                    # =====================
                    # APPLY CACHED OI
                    # =====================

                    elif (
                        prev is not None
                        and age is not None
                        and age <= oi_ttl
                    ):

                        sig["oi_change"] = prev

                        sig["oi_available"] = True

                        print(
                            f"[OI_CACHE] "
                            f"{instId} "
                            f"cached OI={prev}% age={age}s",
                            flush=True
                        )

                    # =====================
                    # NO OI
                    # =====================

                    else:

                        sig["oi_change"] = None

                        print(
                            f"[OI_NONE] "
                            f"{instId} "
                            f"no fresh OI / cache expired",
                            flush=True
                        )

                    flags = set(sig.get("flags", []))

                    # =====================
                    # SKIP LATE EXPANSION
                    # =====================
                    
                    if (
                        sig.get("signal_mode") == "EXPANSION"
                        and "COMP_PRO_5M" not in flags
                        and "COMP_PRO_15M" not in flags
                    ):
                    
                        score = float(sig.get("score") or 0)
                        ep = float(sig.get("early_pressure_score") or 0)
                        oi = abs(float(sig.get("oi_change") or 0))
                    
                        if (
                            score < 25
                            and ep < 10
                            and oi < 0.30
                        ):
                    
                            print(
                                f"[SKIP_LATE_EXPANSION] {instId}",
                                flush=True
                            )

                            continue
                    
                        print(
                            f"[EXPANSION_BYPASS] {instId}",
                            flush=True
                        )

                    # =====================
                    # SKIP LATE PREMOVE
                    # =====================
                    
                    if (
                        sig.get("signal_mode") == "PREMOVE"
                        and ep > 18
                        and score < 35
                        and (
                            "LAUNCH_PROXIMITY_UP" in flags
                            or "LAUNCH_PROXIMITY_DOWN" in flags
                        )
                    ):
                    
                        print(
                            f"[LATE_PREMOVE_SKIP] "
                            f"{instId} "
                            f"score={score} "
                            f"ep={ep}",
                            flush=True
                        )
                    
                        continue
                    # =====================
                    # REVERSAL SETUP ENGINE
                    # =====================
                    
                    flags = set(sig.get("flags", []))
                    
                    has_shift = (
                        "BULLISH_SHIFT" in flags
                        or "BEARISH_SHIFT" in flags
                    )
                    
                    has_pressure = (
                        "PRESSURE_UP" in flags
                        or "PRESSURE_DOWN" in flags
                    )
                    
                    has_absorption = (
                        "BUYER_ABSORPTION" in flags
                        or "SELLER_ABSORPTION" in flags
                    )
                    
                    has_compression = (
                        "COMP_PRO_5M" in flags
                        or "COMP_PRO_15M" in flags
                        or "RANGE_COMPRESSION" in flags
                        or "TIGHT_RANGE" in flags
                    )
                    
                    reversal_setup = (
                        has_shift
                        and has_pressure
                        and has_absorption
                        and has_compression
                        and sig.get("signal_mode") in (
                            "PREMOVE",
                            "TRANSITION"
                        )
                    )
                    
                    sig["reversal_setup"] = reversal_setup
                    
                    if reversal_setup:
                    
                        print(
                            f"[REVERSAL_SETUP] "
                            f"{instId} "
                            f"mode={sig.get('signal_mode')} "
                            f"score={sig.get('score')} "
                            f"ep={sig.get('early_pressure_score')} "
                            f"acc={sig.get('acc_score')}",
                            flush=True
                        )
                    # =====================
                    # LOW ENERGY FILTER
                    # =====================
                    
                    flags = set(sig.get("flags", []))
                    
                    weak_energy = (
                    
                        "ACCELERATION_UP" not in flags
                        and "ACCELERATION_DOWN" not in flags
                    
                        and "EXPLOSION_READY_UP" not in flags
                        and "EXPLOSION_READY_DOWN" not in flags
                    
                        and "LAUNCH_PROXIMITY_UP" not in flags
                        and "LAUNCH_PROXIMITY_DOWN" not in flags
                    
                        and "MTF_LONG_ALIGN" not in flags
                        and "MTF_SHORT_ALIGN" not in flags
                    )
                    
                    oi = float(sig.get("oi_change") or 0)
                    
                    if (
                        weak_energy
                        and abs(oi) < 0.15
                        and sig.get("signal_mode") not in (
                            "PREMOVE",
                            "TRANSITION",
                            "WATCH_REVERSAL"
                        )
                    ):
                    
                        if (
                            sig.get("signal_mode") in (
                                "PREMOVE",
                                "TRANSITION"
                            )
                            and sig.get("flow_state") in (
                                "EARLY_MONEY_FLOW",
                                "BUILDING_MONEY_FLOW",
                                "STRONG_MONEY_FLOW"
                            )
                            and sig.get("smart_money_state") in (
                                "EARLY_SMART_MONEY",
                                "BUILDING_SMART_MONEY",
                                "STRONG_SMART_MONEY"
                            )
                        ):
                    
                            print(
                                f"[LOW_ENERGY_PREMOVE_BYPASS] {instId}",
                                flush=True
                            )
                    
                        else:
                    
                            print(
                                f"[SKIP_LOW_ENERGY] {instId}",
                                flush=True
                            )
                    
                            continue
                    
                    # =====================
                    # FINAL QUALITY SELECTOR
                    # ===================== 

                    flags = set(sig.get("flags", []))

                    score = float(sig.get("score") or 0)
                    ep = float(sig.get("early_pressure_score") or 0)
                    acc = float(sig.get("acc_score") or 0)
                    oi = float(sig.get("oi_change") or 0)

                    group = sig.get("signal_group")
                    mode = sig.get("signal_mode")
                    entry = str(sig.get("entry") or "")

                    has_launch = (
                        "LAUNCH_PROXIMITY_UP" in flags
                        or "LAUNCH_PROXIMITY_DOWN" in flags
                        or "EXPLOSION_READY_UP" in flags
                        or "EXPLOSION_READY_DOWN" in flags
                    )

                    has_acceleration = (
                        "ACCELERATION_UP" in flags
                        or "ACCELERATION_DOWN" in flags
                    )

                    has_mtf = (
                        "MTF_LONG_ALIGN" in flags
                        or "MTF_SHORT_ALIGN" in flags
                    )

                    has_absorption = (
                        "BUYER_ABSORPTION" in flags
                        or "SELLER_ABSORPTION" in flags
                    )

                    has_compression = (
                        "COMP_PRO_5M" in flags
                        or "COMP_PRO_15M" in flags
                        or "COMP_5M" in flags
                        or "COMP_15M" in flags
                    )

                    is_expansion = (
                        mode == "EXPANSION"
                        or "EXPANSION" in str(sig.get("stage", ""))
                    )
                    is_premove = (
                        mode == "PREMOVE"
                        or "PREMOVE" in str(entry)
                    )
                    is_transition = mode == "TRANSITION"

                    quality_points = 0

                    # =====================
                    # EP
                    # =====================

                    if ep >= 20:
                        quality_points += 3

                    elif ep >= 16:
                        quality_points += 2

                    elif ep >= 12:
                        quality_points += 1

                    # =====================
                    # ACCUMULATION
                    # =====================

                    if acc >= 4:
                        quality_points += 4
                    
                    elif acc >= 3:
                        quality_points += 3
                    
                    elif acc >= 2:
                        quality_points += 1
                    
                    else:
                        quality_points -= 1

                    # =====================
                    # OI
                    # =====================

                    if abs(oi) >= 1.5:
                        quality_points += 5

                    elif abs(oi) >= 0.8:
                        quality_points += 4

                    elif abs(oi) >= 0.4:
                        quality_points += 3

                    elif abs(oi) >= 0.25:
                        quality_points += 2

                    elif abs(oi) >= 0.12:
                        quality_points += 1

                    else:

                        quality_points -= 1

                        print(
                            f"[LOW_OI_PENALTY] "
                            f"{instId} "
                            f"oi={oi}",
                            flush=True
                        )

                    # =====================
                    # PRICE + OI CONTEXT
                    # =====================

                    try:

                        price_change = float(
                            sig.get("price_change_pct") or 0
                        )

                    except:

                        price_change = 0

                    # =====================
                    # REAL LONG BUILDUP
                    # цена растет + OI растет
                    # =====================

                    if (
                        price_change > 0
                        and oi >= 0.25
                    ):

                        quality_points += 3

                        flags.add("REAL_LONG_BUILDUP")

                        print(
                            f"[REAL_LONG_BUILDUP] "
                            f"{instId} "
                            f"price={price_change} "
                            f"oi={oi}",
                            flush=True
                        )

                    # =====================
                    # REAL SHORT BUILDUP
                    # цена падает + OI растет
                    # =====================

                    elif (
                        price_change < 0
                        and oi >= 0.25
                    ):

                        quality_points += 3

                        flags.add("REAL_SHORT_BUILDUP")

                        print(
                            f"[REAL_SHORT_BUILDUP] "
                            f"{instId} "
                            f"price={price_change} "
                            f"oi={oi}",
                            flush=True
                        )

                    # =====================
                    # SHORT SQUEEZE
                    # цена растет + OI падает
                    # =====================

                    elif (
                        price_change > 0
                        and oi <= -0.25
                    ):

                        quality_points += 1

                        flags.add("SHORT_SQUEEZE")

                        print(
                            f"[SHORT_SQUEEZE] "
                            f"{instId} "
                            f"price={price_change} "
                            f"oi={oi}",
                            flush=True
                        )

                    # =====================
                    # LONG FLUSH
                    # цена падает + OI падает
                    # =====================

                    elif (
                        price_change < 0
                        and oi <= -0.25
                    ):

                        quality_points += 1

                        flags.add("LONG_FLUSH")

                        print(
                            f"[LONG_FLUSH] "
                            f"{instId} "
                            f"price={price_change} "
                            f"oi={oi}",
                            flush=True
                        )

                    # =====================
                    # LAUNCH
                    # =====================

                    if (
                        has_acceleration
                        and abs(oi) >= 0.15
                    ):

                        energy_stack += 1

                    # =====================
                    # ACCELERATION
                    # =====================

                    if has_acceleration:
                        quality_points += 2

                    # =====================
                    # MTF
                    # =====================

                    if has_mtf:
                        quality_points += 1

                    # =====================
                    # ABSORPTION
                    # =====================

                    if has_absorption:
                        quality_points += 2

                    # =====================
                    # COMPRESSION
                    # =====================

                    if has_compression:
                        quality_points += 1

                    # =====================
                    # ENERGY STACK
                    # =====================

                    energy_stack = 0

                    if has_compression:
                        energy_stack += 1

                    if has_absorption:
                        energy_stack += 1

                    if has_acceleration:
                        energy_stack += 1

                    if has_launch:
                        energy_stack += 1

                    if abs(oi) >= 0.15:
                        energy_stack += 1

                    if ep >= 15:
                        energy_stack += 1

                    sig["energy_stack"] = energy_stack

                    # =====================
                    # ENERGY STACK BOOST
                    # =====================

                    if energy_stack >= 6:

                        quality_points += 4

                        print(
                            f"[ENERGY_STACK_ELITE] "
                            f"{instId} "
                            f"stack={energy_stack}",
                            flush=True
                        )

                    elif energy_stack >= 4:

                        quality_points += 1

                        print(
                            f"[ENERGY_STACK_STRONG] "
                            f"{instId} "
                            f"stack={energy_stack}",
                            flush=True
                        )

                    elif energy_stack <= 2:

                        quality_points -= 5

                        print(
                            f"[ENERGY_STACK_WEAK] "
                            f"{instId} "
                            f"stack={energy_stack}",
                            flush=True
                        )
                    # =====================
                    # LIQUIDATION CASCADE BONUS
                    # =====================

                    if (
                        "LIQUIDATION_CASCADE_ACTIVE"
                        in flags
                    ):

                        quality_points += 4

                        print(
                            f"[CASCADE_BONUS] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # IMPULSE CONFIRMATION BONUS
                    # =====================

                    if (
                        "IMPULSE_CONFIRMED_LONG"
                        in flags
                        or
                        "IMPULSE_CONFIRMED_SHORT"
                        in flags
                    ):

                        quality_points += 5

                        print(
                            f"[IMPULSE_BONUS] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # MARKET REGIME BONUS
                    # =====================

                    regime = sig.get(
                        "market_regime",
                        "NEUTRAL"
                    )

                    # =====================
                    # TREND BULL
                    # =====================

                    if (
                        regime == "TREND_BULL"
                        and side == "LONG"
                    ):

                        quality_points += 3

                        print(
                            f"[REGIME_BULL_BONUS] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # TREND BEAR
                    # =====================

                    if (
                        regime == "TREND_BEAR"
                        and side == "SHORT"
                    ):

                        quality_points += 3

                        print(
                            f"[REGIME_BEAR_BONUS] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # PANIC SELL
                    # =====================

                    if (
                        regime == "PANIC_SELL"
                        and side == "SHORT"
                    ):

                        quality_points += 4

                        print(
                            f"[PANIC_SELL_BONUS] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # SHORT SQUEEZE
                    # =====================

                    if (
                        regime == "SHORT_SQUEEZE"
                        and side == "LONG"
                    ):

                        quality_points += 4

                        print(
                            f"[SHORT_SQUEEZE_BONUS] "
                            f"{instId}",
                            flush=True
                        )
                    # =====================
                    # RANGE COMPRESSION BONUS
                    # =====================
                    
                    range_compression = (
                        "RANGE_COMPRESSION" in flags
                    )
                    
                    tight_range = (
                        "TIGHT_RANGE" in flags
                    )
                    
                    pressure_persist = any(
                        x in flags
                        for x in [
                            "PRESSURE_LONG_PERSIST_3",
                            "PRESSURE_SHORT_PERSIST_3"
                        ]
                    )
                    
                    if range_compression:
                    
                        quality_points += 1
                    
                        print(
                            f"[RANGE_BONUS] "
                            f"{instId} compression",
                            flush=True
                        )
                    
                    if tight_range:
                    
                        quality_points += 1
                        energy_stack += 1
                    
                        print(
                            f"[TIGHT_RANGE_BONUS] "
                            f"{instId}",
                            flush=True
                        )
                    
                    if (
                        range_compression
                        and pressure_persist
                    ):
                    
                        quality_points += 3
                        energy_stack += 2
                    
                        print(
                            f"[ACCUMULATION_RANGE] "
                            f"{instId}",
                            flush=True
                        )

                    sig["quality_points"] = quality_points

                    print(
                        f"[QUALITY_SELECTOR] "
                        f"{instId} "
                        f"qp={quality_points} "
                        f"score={score} "
                        f"ep={ep} "
                        f"acc={acc} "
                        f"oi={oi} "
                        f"mode={mode} "
                        f"entry={entry}",
                        flush=True
                    )
                    
                    # =====================
                    # LIQUIDATION BONUS
                    # =====================
                    
                    short_squeeze = (
                        "SHORT_SQUEEZE" in flags
                    )
                    
                    long_flush = (
                        "LONG_FLUSH" in flags
                    )
                    
                    cascade_shorts = (
                        "CASCADE_SHORTS" in flags
                    )
                    
                    cascade_longs = (
                        "CASCADE_LONGS" in flags
                    )
                    
                    if short_squeeze:
                    
                        quality_points += 2
                        energy_stack += 1
                    
                        print(
                            f"[SHORT_SQUEEZE_BONUS] "
                            f"{instId}",
                            flush=True
                        )
                    
                    if long_flush:
                    
                        quality_points += 2
                        energy_stack += 1
                    
                        print(
                            f"[LONG_FLUSH_BONUS] "
                            f"{instId}",
                            flush=True
                        )
                    
                    if cascade_shorts:
                    
                        quality_points += 3
                        energy_stack += 2
                    
                        print(
                            f"[CASCADE_SHORTS] "
                            f"{instId}",
                            flush=True
                        )
                    
                    if cascade_longs:
                    
                        quality_points += 3
                        energy_stack += 2
                    
                        print(
                            f"[CASCADE_LONGS] "
                            f"{instId}",
                            flush=True
                        )
                    
                    print(
                        f"[BEFORE_CAPITAL_GATE] "
                        f"{instId} "
                        f"group={sig.get('signal_group')} "
                        f"valid={sig.get('valid')} "
                        f"sendable={sig.get('sendable')} "
                        f"score={score}",
                        flush=True
                    )
                    
                    # =====================
                    # CAPITAL GATE FILTER
                    # =====================
                    
                    capital_ok = False

                    if abs(oi) >= 0.50:
                    
                        capital_ok = True
                    
                    elif (
                        abs(oi) >= 0.25
                        and quality_points >= 10
                    ):
                    
                        capital_ok = True
                    
                    elif (
                        acc >= 2
                        and ep >= 15
                        and energy_stack >= 5
                    ):
                    
                        capital_ok = True
                    
                    elif (
                        score >= 35
                        and ep >= 15
                    ):
                    
                        capital_ok = True

                    elif (
                        sig.get("signal_mode") in (
                            "PREMOVE",
                            "TRANSITION"
                        )
                        and sig.get("flow_state") in (
                            "BUILDING_MONEY_FLOW",
                            "STRONG_MONEY_FLOW"
                        )
                        and sig.get("smart_money_state") in (
                            "BUILDING_SMART_MONEY",
                            "STRONG_SMART_MONEY"
                        )
                        and sig.get("retest_state") in (
                            "RETEST_BUILDUP",
                            "STRONG_RETEST"
                        )
                        and ep >= 8
                        and acc >= 2
                    ):
                        capital_ok = True

                    elif (
                        sig.get("signal_mode") == "WATCH_REVERSAL"
                        and sig.get("flow_state") in (
                            "BUILDING_MONEY_FLOW",
                            "STRONG_MONEY_FLOW"
                        )
                        and sig.get("smart_money_state") in (
                            "BUILDING_SMART_MONEY",
                            "STRONG_SMART_MONEY"
                        )
                        and energy_stack >= 3
                    ):
                        capital_ok = True
                    
                        print(
                            f"[WATCH_REVERSAL_CAPITAL_BYPASS] "
                            f"{instId}",
                            flush=True
                        )

                    # =====================
                    # ACCUMULATION BYPASS
                    # =====================

                    print(
                    f"[ENTRY_DEBUG] "
                    f"{instId} "
                    f"entry_type={sig.get('entry_type')} "
                    f"entry={sig.get('entry')}",
                    flush=True
                )
                    if (
                        sig.get("entry_type") in (
                            "ACCUMULATION_LONG",
                            "ACCUMULATION_SHORT"
                        )
                        and sig.get("flow_state") == "STRONG_MONEY_FLOW"
                        and acc >= 3
                        and ep >= 10
                    ):
                        capital_ok = True
                    
                        print(
                            f"[ACCUMULATION_CAPITAL_BYPASS] "
                            f"{instId}",
                            flush=True
                        )
                    if not capital_ok:
                    
                        print(
                            f"[CAPITAL_GATE_SKIP] "
                            f"{instId} "
                            f"oi={oi} "
                            f"qp={quality_points} "
                            f"acc={acc} "
                            f"ep={ep} "
                            f"stack={energy_stack}",
                            flush=True
                        )
                    
                        continue
                    
                    print(
                        f"[AFTER_CAPITAL_GATE] "
                        f"{instId}",
                        flush=True
                    )

                    # =====================
                    # SMART MONEY A+
                    # =====================

                    smart_money_a_plus = (

                        score >= 40
                        and ep >= 20
                        and acc >= 2
                        and energy_stack >= 5
                    
                        and (
                            abs(oi) >= 0.15
                            or quality_points >= 14
                        )
                    
                    )
                    sig["smart_money_a_plus"] = smart_money_a_plus
                    
                    if smart_money_a_plus:
                    
                        print(
                            f"[SMART_MONEY_A_PLUS] "
                            f"{instId} "
                            f"score={score} "
                            f"ep={ep} "
                            f"stack={energy_stack}",
                            flush=True
                        )
                    
                    print(
                        f"[A_PLUS_DEBUG] "
                        f"{instId} "
                        f"score={score} "
                        f"ep={ep} "
                        f"stack={energy_stack} "
                        f"flow={sig.get('flow_state')}",
                        flush=True
                    )

                    # =====================
                    # FINAL QUALITY ENGINE
                    # =====================
                    
                    quality = 0
                    
                    score = float(sig.get("score") or 0)
                    ep = float(sig.get("early_pressure_score") or 0)
                    acc = float(sig.get("acc_score") or 0)
                    oi = abs(float(sig.get("oi_change") or 0))
                    
                    flags = set(sig.get("flags", []))
                    
                    # ---------------------
                    # SCORE
                    # ---------------------
                    
                    if score >= 25:
                        quality += 2
                    
                    elif score >= 18:
                        quality += 1
                    
                    # ---------------------
                    # EARLY PRESSURE
                    # ---------------------
                    
                    if ep >= 16:
                        quality += 2
                    
                    elif ep >= 10:
                        quality += 1
                    
                    # ---------------------
                    # ACC
                    # ---------------------
                    
                    if acc >= 3:
                        quality += 2
                    
                    elif acc >= 2:
                        quality += 1
                    
                    # ---------------------
                    # MONEY FLOW
                    # ---------------------
                    
                    if sig.get("flow_state") == "STRONG_MONEY_FLOW":
                        quality += 2
                    
                    elif sig.get("flow_state") == "BUILDING_MONEY_FLOW":
                        quality += 1
                    
                    # ---------------------
                    # SMART MONEY
                    # ---------------------
                    
                    if (
                        "SMART_MONEY_FLOW_CONFIRMED" in flags
                        or "SMART_MONEY_FLOW_OK" in flags
                    ):
                        quality += 2
                    
                    # ---------------------
                    # MTF
                    # ---------------------
                    
                    if (
                        "MTF_LONG_ALIGN" in flags
                        or "MTF_SHORT_ALIGN" in flags
                    ):
                        quality += 2
                    
                    # ---------------------
                    # ABSORPTION
                    # ---------------------
                    
                    if (
                        "BUYER_ABSORPTION" in flags
                        or "SELLER_ABSORPTION" in flags
                    ):
                        quality += 1
                    
                    # ---------------------
                    # COMPRESSION
                    # ---------------------
                    
                    if (
                        "COMP_PRO_5M" in flags
                        or "COMP_PRO_15M" in flags
                    ):
                        quality += 1
                    
                    # ---------------------
                    # OI
                    # ---------------------
                    
                    if oi >= 0.30:
                        quality += 2
                    
                    elif oi >= 0.15:
                        quality += 1
                    
                    sig["quality"] = quality
                    
                    print(
                        f"[FINAL_QUALITY] "
                        f"{instId} "
                        f"quality={quality}",
                        flush=True
                    )
                    # =====================
                    # DROP LOW VALUE CLONES
                    # =====================
                    
                    is_watch_reversal = (
                        sig.get("signal_mode") == "WATCH_REVERSAL"
                    )
                    
                    if (
                        group == "PRE_SWING"
                        and quality_points < 3
                        and score < 15
                        and not is_watch_reversal
                    ):
                    
                        print(
                            f"[SKIP_LOW_QUALITY] "
                            f"{instId} "
                            f"qp={quality_points}",
                            flush=True
                        )
                    
                        continue

                    # =====================
                    # DROP WEAK TRANSITIONS
                    # =====================

                    if (
                        is_transition
                        and quality_points < 5
                        and abs(oi) < 0.25
                    ):

                        print(
                            f"[SKIP_WEAK_TRANSITION] "
                            f"{instId} "
                            f"qp={quality_points}",
                            flush=True
                        )

                        continue

                    # =====================
                    # DROP WEAK PREMOVES
                    # =====================

                    is_watch_reversal = (
                        sig.get("signal_mode") == "WATCH_REVERSAL"
                    )
                    
                    if (
                        is_premove
                        and quality_points < 6
                        and abs(oi) < 0.25
                        and acc < 3
                        and not is_watch_reversal
                    ):
                        print(
                            f"[SKIP_WEAK_PREMOVE_FINAL] "
                            f"{instId} "
                            f"qp={quality_points}",
                            flush=True
                        )

                        continue
                    # =====================
                    # MESSAGE TYPE
                    # =====================
                    group = sig.get("signal_group")
                    if group == "SWING":

                        print(
                            f"[SWING_ENABLED] {instId}",
                            flush=True
                        )

                        msg = safe_msg_builder(
                            globals().get("msg_swing"),
                            sig,
                            "SWING"
                        )

                    elif group == "PRE_SWING":

                        msg = safe_msg_builder(
                            globals().get("msg_pre_swing"),
                            sig,
                            "PRE_SWING"
                        )

                    else:

                        msg = safe_msg_builder(
                            globals().get("msg_scalp"),
                            sig,
                            "SCALP"
                        )

                    # =====================
                    # INVALID SIGNAL PROTECTION
                    # =====================
                
                    if not sig or not isinstance(sig, dict):
                
                        print(
                            f"[RAW_SKIP] {instId}",
                            flush=True
                        )
                
                        continue
                
                    # =====================
                    # SIGNAL DISPATCH
                    # =====================
                
                    group = sig.get("signal_group")
                    if (
                        sig.get("signal_mode") == "PREMOVE"
                        and not group
                    ):
                    
                        group = "PRE_SWING"
                    
                        sig["signal_group"] = group
                    
                        print(
                            f"[AUTO_PREMOVE_ROUTE] "
                            f"{instId} -> PRE_SWING",
                            flush=True
                        )
                
                    allowed_groups = [
                        "PRE_SWING",
                        "SWING",
                    ]
                    
                    if ENABLE_SCALP_ALERTS:
                    
                        allowed_groups.append("SCALP")
                    
                    if group in allowed_groups:
                
                       
                        # =========================
                        # ELITE PRE-SWING FILTER
                        # =========================
                        
                        if group == "PRE_SWING":
                        
                            elite_ok, elite_reason = (
                                is_elite_pre_swing(sig)
                            )
                        
                            print(
                                f"[ELITE_PRE_SWING] "
                                f"{instId} "
                                f"ok={elite_ok} "
                                f"reason={elite_reason}",
                                flush=True
                            )
                        
                            if not elite_ok:

                                if (
                                    sig.get("sendable") is True
                                    and float(sig.get("score") or 0) >= 20
                                    and float(sig.get("early_pressure_score") or 0) >= 12
                                    and float(sig.get("acc_score") or 0) >= 3
                                ):
                            
                                    print(
                                        f"[PRE_SWING_OVERRIDE_KEEP] "
                                        f"{instId} "
                                        f"score={sig.get('score')} "
                                        f"ep={sig.get('early_pressure_score')} "
                                        f"reason={elite_reason}",
                                        flush=True
                                    )
                            
                                else:
                            
                                    print(
                                        f"[SKIP_WEAK_PRE_SWING] "
                                        f"{instId}",
                                        flush=True
                                    )
                            
                                    continue
                        # =========================
                        # TELEGRAM FINAL FIREWALL
                        # =========================

                        symbol_key = (
                            f"{instId}_"
                            f"{group}_"
                            f"{sig.get('direction_code')}_"
                            f"{sig.get('entry')}"
                        )

                        if not can_send(symbol_key, 600):

                            print(
                                f"[GLOBAL_COOLDOWN_SKIP] "
                                f"{symbol_key}",
                                flush=True
                            )

                            continue

                        if (
                        
                            "NEUTRAL" in str(sig.get("stage"))
                        
                            and sig.get("signal_mode") not in (
                                "PREMOVE",
                                "TRANSITION",
                                "EXPANSION",
                            )
                        
                        ):
                        
                            print(
                                f"[SKIP_NEUTRAL_TG] "
                                f"{instId}",
                                flush=True
                            )
                        
                            continue

                            print(
                                f"[SKIP_NEUTRAL_TG] "
                                f"{instId}",
                                flush=True
                            )

                            continue

                       

                        if (
                            group == "PRE_SWING"
                        
                            and float(sig.get("score") or 0) < 12
                        
                            and float(sig.get("early_pressure_score") or 0) < 8
                        
                            and sig.get("signal_mode") not in (
                                "PREMOVE",
                                "TRANSITION",
                                "WATCH_REVERSAL"
                            )
                        ):
                        
                            print(
                                f"[SKIP_WEAK_PRE_SWING_TG] "
                                f"{instId}",
                                flush=True
                            )
                        
                            continue

                        signal_icon, signal_title = get_signal_level(sig)

                        if signal_title not in (
                        
                            "ОЧЕНЬ СИЛЬНЫЙ СИГНАЛ",
                            "СИЛЬНЫЙ СИГНАЛ"
                        
                        ):
                        
                            print(
                                f"[SKIP_WEAK_TG] "
                                f"{instId} "
                                f"level={signal_title}",
                                flush=True
                            )
                        
                            continue

                        # =========================
                        # A+ ONLY FILTER
                        # =========================
                        
                        # if not sig.get("smart_money_a_plus"):
                        #
                        #     print(
                        #         f"[SKIP_NOT_A_PLUS] "
                        #         f"{instId}",
                        #         flush=True
                        #     )
                        #
                        #     continue
                        send_telegram(msg)
                
                        scalp_sent_this_cycle += 1
                
                        print(
                            f"[SIGNAL_SENT] "
                            f"{instId} "
                            f"group={group}",
                            flush=True
                        )
                
                        alerts.append(sig)
                
                        mark_alert_sent(state, sig)
                
                        continue
                
                except Exception as e:

                    import traceback
                
                    print(
                        traceback.format_exc(),
                        flush=True
                    )
                
                    print(
                        f"[BUILD_SIGNAL_ERROR] "
                        f"{instId} {e}",
                        flush=True
                    )
                
                    continue
                
                # =====================
                # LOCAL SIGNAL DATA
                # =====================
    
                flags = set(sig.get("flags", []))
    
                score = float(sig.get("score", 0))
    
                # =====================
                # MARKET REGIME FILTER
                # =====================
    
                if MARKET_MODE == "BULL":
    
                    if (
                        "PRESSURE_DOWN" in flags
                        and "EMA_BEAR_STRONG" not in flags
                        and "BREAKOUT_CONFIRM_DOWN" not in flags
                    ):
    
                        score -= 1.5
    
                        flags.add("REGIME_BLOCK_SHORT")
    
                if MARKET_MODE == "BEAR":
    
                    if (
                        "PRESSURE_UP" in flags
                        and "EMA_BULL_STRONG" not in flags
                        and "BREAKOUT_CONFIRM_UP" not in flags
                    ):
    
                        score -= 1.5
    
                        flags.add("REGIME_BLOCK_LONG")
    
                # =====================
                # SAVE UPDATED SIGNAL
                # =====================
    
                sig["score"] = round(score, 2)
    
                sig["flags"] = list(flags)
    
               
    
                # =====================
                # LOAD CANDLES FOR TA
                # =====================
    
                candles_m15 = get_tf_candles(
                    instId,
                    "15m",
                    200
                )
    
                candles_h1 = get_tf_candles(
                    instId,
                    "1h",
                    200
                )
    
                candles_day = get_tf_candles(
                    instId,
                    "1d",
                    200
                )
    
                candles_month = get_tf_candles(
                    instId,
                    "1M",
                    120
                )
                # =====================
                # TA SNIPER (SAFE)
                # =====================
                
                try:
                
                    if (
                        is_empty(candles_m15)
                        or is_empty(candles_h1)
                        or is_empty(candles_day)
                        or is_empty(candles_month)
                    ):
                
                        print(f"[TA_SKIP] {instId} empty candles", flush=True)
                
                        ta = None
                
                    else:
                
                        ta = analyze_ta_sniper(
                            symbol=instId,
                            candles_month=candles_month,
                            candles_day=candles_day,
                            candles_h1=candles_h1,
                            candles_m15=candles_m15,
                            max_stop_pct=3.5
                        )
                
                except Exception as e:
                
                    print(
                        f"[TA_ERROR] {instId} {type(e).__name__}: {e}",
                        flush=True
                    )
                
                    print(traceback.format_exc(), flush=True)
                
                    ta = None
            
            # =====================
            # ELITE FILTER PRO
            # =====================
            
            elite_score = 0
            
            reasons = []
            
            # =====================
            # INVALID SIGNAL PROTECTION
            # =====================
            
            if not sig or not isinstance(sig, dict):
            
                print(
                    f"[ELITE_SKIP] {instId}",
                    flush=True
                )
            
                continue
            
            # =====================
            # RR CALC
            # =====================
            
            entry_price = float(
                sig.get("entry_price")
                or sig.get("price")
                or 0
            )
            
            stop_price = float(
                sig.get("stop")
                or 0
            )
            
            target_price = float(
                sig.get("target")
                or 0
            )
            
            rr1 = 0
            
            if (
                entry_price > 0
                and stop_price > 0
                and target_price > 0
            ):
            
                risk = abs(entry_price - stop_price)
                reward = abs(target_price - entry_price)
            
                if risk > 0:
                    rr1 = round(reward / risk, 2)
            
            sig["rr1"] = rr1
            
            print(
                f"[RR_CALC] {instId} "
                f"entry={entry_price} "
                f"stop={stop_price} "
                f"target={target_price} "
                f"rr1={rr1}",
                flush=True
            )
            
            # =====================
            # RR BLOCK
            # =====================
            
            elite = True
            
            sig_side = sig.get("side") or sig.get("direction_code")
            rr1 = float(sig.get("rr1") or 0)
            
            if sig_side not in ["UP", "DOWN"]:
                elite = False
                reasons.append("SIDE_FLAT_BLOCK")
            
            if rr1 <= 0:
                elite = False
                reasons.append("RR_ZERO_BLOCK")

            print(
                f"[ELITE_FINAL] {instId} "
                f"elite={elite} "
                f"score={elite_score} "
                f"rr={rr1} "
                f"side={sig_side}",
                flush=True
            )
            flags = set(sig.get("flags", []))
            # =====================
            # ELITE SIDE CONFIRM
            # =====================
            
            elite_side_ok = False
            
            if sig_side == "UP" and (
                "EMA_BULL" in flags
                or "EMA_BULL_STRONG" in flags
                or "MTF_LONG_ALIGN" in flags
            ):
                elite_side_ok = True
            
            elif sig_side == "DOWN" and (
                "EMA_BEAR" in flags
                or "EMA_BEAR_STRONG" in flags
                or "MTF_SHORT_ALIGN" in flags
            ):
                elite_side_ok = True
            
            
            # conflict block
            flags = set(sig.get("flags", []))
            if "STRUCTURE_CONFLICT" in flags:
            
                # allow strong continuation despite conflict
                if not (
                    (
                        "BREAKOUT_CONFIRM_UP" in flags
                        or "CONTINUATION_UP" in flags
                    )
                    and (
                        "EMA_BULL" in flags
                        or "EMA_BULL_STRONG" in flags
                        or "MTF_LONG_ALIGN" in flags
                    )
                ) and not (
                    (
                        "BREAKOUT_CONFIRM_DOWN" in flags
                        or "CONTINUATION_DOWN" in flags
                    )
                    and (
                        "EMA_BEAR" in flags
                        or "EMA_BEAR_STRONG" in flags
                        or "MTF_SHORT_ALIGN" in flags
                    )
                ):
            
                    elite_side_ok = False
                    reasons.append("STRUCTURE_CONFLICT")
            
            
            if not elite_side_ok:
                elite = False
                reasons.append("ELITE_SIDE_CONTEXT_BLOCK")
                                                
            # =====================
            # 1. SENDABLE
            # =====================
            
            if sig.get("sendable"):
            
                elite_score += 2
                reasons.append("SWING_OK")
            
            # =====================
            # 2. RR
            # =====================
            
            rr1 = float(sig.get("rr1") or 0)
            
            if rr1 >= 2:
            
                elite_score += 2
                reasons.append("RR_OK")
            
            elif rr1 >= 1.5:
            
                elite_score += 1
                reasons.append("RR_MID")
            
            # =====================
            # 3. PRESSURE
            # =====================
            
            flags = sig.get("flags", [])
            
            if "PRESSURE_DOWN" in flags:
            
                elite_score += 2
                reasons.append("PRESSURE_DOWN")
            
            if "PRESSURE_UP" in flags:
            
                elite_score += 2
                reasons.append("PRESSURE_UP")
            
            # =====================
            # 4. BREAKOUT CONFIRM
            # =====================
            
            if (
                "BREAKOUT_CONFIRM_UP" in flags
                or "BREAKOUT_CONFIRM_DOWN" in flags
            ):
            
                elite_score += 2
                reasons.append("BREAKOUT_CONFIRM")
            
            # =====================
            # 5. EARLY PRESSURE
            # =====================
            
            ep_score = float(sig.get("early_pressure_score") or 0)
            
            strong_ep = (
            
                "EMA_BULL_STRONG" in flags
                or "EMA_BEAR_STRONG" in flags
                or "MTF_LONG_ALIGN" in flags
                or "MTF_SHORT_ALIGN" in flags
                or "BREAKOUT_CONFIRM_UP" in flags
                or "BREAKOUT_CONFIRM_DOWN" in flags
            )
            
            if ep_score >= 6 and strong_ep:
            
                elite_score += 2
                reasons.append("EARLY_PRESSURE")
            
            # =====================
            # 6. STRONG ACCUMULATION
            # =====================
            
            acc = int(sig.get("acc_score") or 0)
            
            if acc >= 3:
            
                elite_score += 2
                reasons.append("STRONG_ACC")
            
            elif acc >= 2:
            
                elite_score += 1
                reasons.append("ACC_OK")
            
            # =====================
            # 7. TA CONFIRM
            # =====================
            
            ta = {}
            
            if (
                isinstance(ta, dict)
                and ta.get("entry")
                and ta.get("stop")
                and ta.get("tp1")
            ):
            
                sig_side = sig.get("direction_code")
            
                if (
                    ta.get("side")
                    and sig_side
                    and ta.get("side") == sig_side
                ):
            
                    elite_score += 2
                    reasons.append("TA_CONFIRM")
            
            # =====================
            # 8. OI CONFIRM
            # =====================
            
            oi_change = float(sig.get("oi_change") or 0)
            
            if abs(oi_change) >= 0.15:
            
                elite_score += 1
                reasons.append("OI_ACTIVE")
            
            # =====================
            # FINAL ELITE DECISION
            # =====================
            
            elite = False
            
        
            
            # =====================
            # SEND SIGNAL
            # =====================
            

            if isinstance(ta, dict) and ta.get("entry") and ta.get("stop") and ta.get("tp1"):
                send_telegram(
                    f"🎯 <b>TA SNIPER — {ta.get('symbol')}</b>\n\n"
                    f"🧭 Направление: <b>{ta.get('side')}</b>\n"
                    f"💵 Вход: <b>{ta.get('entry')}</b>\n"
                    f"🛑 Стоп: <b>{ta.get('stop')}</b> ({ta.get('stop_pct')}%)\n"
                    f"🎯 TP1: <b>{ta.get('tp1')}</b>\n"
                    f"🎯 TP2: <b>{ta.get('tp2')}</b>\n\n"
                    f"📍 Уровень: <b>{ta.get('level_price')}</b>\n"
                    f"📏 Дистанция: <b>{ta.get('level_distance_pct')}%</b>\n"
                    f"📦 Проторговка: <b>{ta.get('range_bars')} свечей</b>\n"
                    f"🧲 Диапазон: {ta.get('range_low')} → {ta.get('range_high')}\n"
                    f"💪 Buyer: {ta.get('buyer_power')} | Seller: {ta.get('seller_power')}\n"
                    f"⚡ Breakout: {ta.get('breakout')}"
                )
                                                
                
            
            
            
            # =====================
            # DEFINE SETUP TYPE
            # =====================

            if not sig:
                continue
            
            if "score" not in sig:
                continue
            
            if "acc_score" not in sig:
                continue
            
            sig["setup"] = get_signal_tier(
                sig.get("score", 0),
                sig.get("acc_score", 0)
            )

            # =====================
            # AI SCORE MULTIPLIER
            # =====================

            setup = sig.get("setup", "UNKNOWN")
            mult = get_ai_multiplier(setup)
            sig["score"] = round(sig["score"] * mult, 2)

            print(f"[STEP2] {instId} after_build_signal")



            # =====================
            # MARKET CONTEXT
            # =====================

            print(f"[STEP3] {instId} before_market_context")

            sig = apply_market_context(sig)

            print(
                f"[AFTER_MARKET_CONTEXT] "
                f"{instId} "
                f"sig={bool(sig)}",
                flush=True
            )

            if sig and isinstance(sig, dict):

                print(
                    f"[SCALP_CHECK_AFTER_CONTEXT] "
                    f"{instId} "
                    f"scalp={sig.get('scalp_candidate')} "
                    f"group={sig.get('signal_group')}",
                    flush=True
                )

            print(f"[SIG_RAW] {instId} sig_exists={bool(sig)}")

            
            # =====================
            # REGIME BIAS
            # =====================

            sig = apply_regime_bias(sig, regime)
            # AI market filter
            if MARKET_MODE == "BEAR":
                if "⬆️" in str(sig.get("direction", "")) and float(sig.get("score", 0)) < 7:
                    print(f"[AI_FILTER] skip weak long in bear market {instId}")
                    continue
            
            if MARKET_MODE == "BULL":
                if "⬇️" in str(sig.get("direction", "")) and float(sig.get("score", 0)) < 7:
                    print(f"[AI_FILTER] skip weak short in bull market {instId}")
                    continue

            # =====================
            # SAVE SIGNAL
            # =====================
            
            print(
                f"[SCAN] {instId} "
                f"price={sig.get('price')} "
                f"score={sig.get('score')} "
                f"acc={sig.get('acc_score')} "
                f"oi={sig.get('oi_change')} "
                f"flags={sig.get('flags')}"
            )
            
            # =====================
            # SWING ONLY SKIP
            # =====================
            
            if sig.get("swing_only_candidate"):
            
                print(
                    f"[SWING_ONLY_SKIP_MAIN] {instId} "
                    f"score={sig.get('score')} "
                    f"acc={sig.get('acc_score')} "
                    f"flags={sig.get('flags')}"
                )
            
                continue
            
            # =====================
            # EARLY CANDIDATE DEBUG
            # =====================
            
            if sig.get("early_pressure_label"):
            
                flags_set = set(sig.get("flags", []))
            
                stage_txt = str(sig.get("stage", ""))
            
                is_late_candidate = (
                    ("BREAKOUT_CONFIRM_UP" in flags_set)
                    or ("BREAKOUT_CONFIRM_DOWN" in flags_set)
                    or ("ATR_EXPANSION" in flags_set)
                    or ("EXPANSION" in stage_txt)
                )
            
                if not is_late_candidate:
            
                    print(
                        f"[EARLY_CANDIDATE] {instId} "
                        f"side={sig.get('early_pressure_side')} "
                        f"score={sig.get('early_pressure_score')} "
                        f"label={sig.get('early_pressure_label')} "
                        f"up={sig.get('early_pressure_up_score')} "
                        f"down={sig.get('early_pressure_down_score')} "
                        f"reasons={sig.get('early_pressure_reasons')}"
                    )
            
            # =====================
            # SAVE ENTRY SIGNAL
            # =====================
            
            entry_ok_for_save = is_entry_signal(sig)
            
            same_side_open = has_open_similar_signal(sig)
            
            any_open_same_symbol = has_any_open_signal_for_symbol(instId)
            
            if entry_ok_for_save:
            
                if same_side_open:
            
                    print(
                        f"[SAVE_SKIP] {instId} "
                        f"same-side open signal already exists"
                    )
            
                elif ONE_OPEN_SIGNAL_PER_SYMBOL and any_open_same_symbol:
            
                    print(
                        f"[SAVE_SKIP] {instId} "
                        f"open signal already exists for this symbol"
                    )
            
                else:
            
                    save_signal(sig)
            
                    print(f"[SAVE_OK] {instId} saved")

            print(
                f"[SCALP_GROUP_DEBUG] "
                f"{instId} "
                f"group={sig.get('signal_group')} "
                f"scalp={sig.get('scalp_candidate')}",
                flush=True
            )


            # =====================
            # SCALP TELEGRAM
            # =====================
            if sig.get("signal_group") == "SCALP":   
                symbol = sig.get("instId")

                current_ts = time.time()

                if symbol in scalp_sent_cache:

                    age = current_ts - scalp_sent_cache[symbol]

                    if age < 3600:

                        print(
                            f"[SCALP_CACHE_BLOCK] "
                            f"{symbol} age={round(age)}s",
                            flush=True
                        )

                        continue

            # =====================
            # ELITE SCALP FILTER
            # =====================

            elite_ok, elite_reason = is_elite_scalp(sig)

            print(
                f"[ELITE_CHECK] "
                f"{instId} "
                f"ok={elite_ok} "
                f"reason={elite_reason}",
                flush=True
            )

            if not elite_ok:

                print(
                    f"[ELITE_BLOCK] {instId}",
                    flush=True
                )

                continue

                # =====================
                # SCALP LIMITER
                # =====================

                if scalp_sent_this_cycle >= 3:

                    print(
                        f"[SCALP_LIMIT_BLOCK] {instId}",
                        flush=True
                    )

                    continue
                

                # =====================
                # SCALP QUALITY FILTER
                # =====================

                scalp_score = sig.get("score", 0)

                if scalp_score < 18:

                    print(
                        f"[SCALP_LOW_QUALITY_SKIP] "
                        f"{instId} "
                        f"score={scalp_score}",
                        flush=True
                    )

                    continue

                # cooldown
                if not should_alert_symbol(state, sig):
                    print(
                        f"[SCALP_COOLDOWN_SKIP] {instId}",
                        flush=True
                    )
                    continue

                scalp_msg = msg_scalp(sig)

                send_telegram(scalp_msg)
                
                scalp_sent_cache[symbol] = now_ts

                print(
                    f"[SCALP_SENT] {instId}",
                    flush=True
                )

                alerts.append(sig)

                mark_alert_sent(state, sig)

                continue

            # =====================
            # SWING ELITE TELEGRAM
            # =====================

            elite_swing_ok, elite_swing_reason = is_elite_swing(sig)

            print(
                f"[SWING_ELITE_CHECK] "
                f"{instId} "
                f"ok={elite_swing_ok} "
                f"reason={elite_swing_reason}",
                flush=True
            )

            if elite_swing_ok:

                if not should_alert_symbol(state, sig):

                    print(
                        f"[SWING_COOLDOWN_SKIP] {instId}",
                        flush=True
                    )

                    continue

                swing_msg = (
                    f"📈 <b>ELITE SWING — {instId}</b>\n\n"
                    f"🧭 Direction: {sig.get('direction')}\n"
                    f"💰 Price: {sig.get('price')}\n"
                    f"📊 Score: {sig.get('score')}\n"
                    f"📍 Stage: {sig.get('stage')}\n"
                    f"⚡ Entry: {sig.get('entry')}\n\n"
                    f"🧠 Swing setup detected.\n"
                    f"Reason: {elite_swing_reason}"
                )
                if is_repeat_signal(sig):

                    continue
                send_telegram(swing_msg)

                print(
                    f"[SWING_SENT] {instId}",
                    flush=True
                )

                alerts.append(sig)

                mark_alert_sent(state, sig)

                continue
            # =====================
            # TIER + SEND LOGIC
            # =====================

            score = float(sig.get("score", 0))
            ep_score = float(sig.get("early_pressure_score", 0))
            acc_score = int(sig.get("acc_score", 0))

            if sig.get("sniper"):
                sig["tier"] = "🟢🟢 СИЛЬНЫЙ ВХОД"

            elif score >= 7:
                sig["tier"] = "🟢 СИЛЬНЫЙ СИГНАЛ"

            elif score >= 5:
                sig["tier"] = "🟡 СИГНАЛ"

            elif score >= 40 or ep_score >= 10 or acc_score >= 3:
                sig["tier"] = "🟠 РАННИЙ"

            else:
                sig["tier"] = "🔴 СЛАБЫЙ"

            tier = sig.get("tier")
            entry_ok = is_entry_signal(sig)
            profit_ok = is_profitable(sig)
            can_alert_now = should_alert_symbol(state, sig)

            start_ready = is_start_trigger(sig)
            pre_ready = is_pre_trigger(sig)
            confirm_ready = is_confirm_trigger(sig)
            early_ready = is_early_pressure_alert(sig)

            recent_safe_lock = safe_entry_recent(state, instId)
            recent_start_lock = start_afterglow_recent(state, instId)
            recent_early_lock = early_alert_recent(state, instId)

            sent_main_now = False
            sent_pre_now = False
            sent_start_now = False
            sent_early_now = False

            if early_ready:
                if sig.get("early_pressure_side") == "BUY":
                    early_buy_symbols.append(fmt_symbol(instId))
                elif sig.get("early_pressure_side") == "SELL":
                    early_sell_symbols.append(fmt_symbol(instId))

            if tier in ["🟢🟢 СИЛЬНЫЙ ВХОД", "🟢 СИЛЬНЫЙ СИГНАЛ"]:
                if entry_ok and can_alert_now:
                    send_telegram(msg_full(sig))
                    sent_main_now = True
                    mark_alert_sent(state, sig)
                    alerts.append(sig)

            elif tier == "🟡 СИГНАЛ":
                if entry_ok and can_alert_now:
                    send_telegram(msg_medium(sig))
                    sent_main_now = True
                    mark_alert_sent(state, sig)
                    alerts.append(sig)

            
            elif tier == "🟠 РАННИЙ":

                print(
                    f"[EARLY] {instId} "
                    f"score={score} "
                    f"ep={ep_score} "
                    f"stage={sig.get('stage')}",
                    flush=True
                )

                # =====================
                # LOW SCORE FILTER
                # =====================
                if score < 40:
                    print(
                        f"[EARLY_SKIP_LOW_SCORE] "
                        f"{instId} score={score}",
                        flush=True
                    )
                    continue
                    
                early_ok = (
            
                    ep_score >= 5
            
                    or acc_score >= 2
            
                    or sig.get("stage") in [
                        "🟠 TRANSITION",
                        "🟣 ACCUMULATION"
                    ]
            
                    or "PRESSURE_UP" in sig.get("flags", [])
            
                    or "PRESSURE_DOWN" in sig.get("flags", [])
            
                    or "CONTINUATION_UP" in sig.get("flags", [])
            
                    or "CONTINUATION_DOWN" in sig.get("flags", [])
            
                    or "BREAKOUT_CONFIRM_UP" in sig.get("flags", [])
            
                    or "BREAKOUT_CONFIRM_DOWN" in sig.get("flags", [])
            
                    or "VOL_SPIKE" in sig.get("flags", [])
            
                    or "ATR_EXPANSION" in sig.get("flags", [])
                )
            
                if early_ok and can_alert_now:
            
                    print(f"[TG_SEND_TRY] {instId}", flush=True)
            
                    send_telegram(msg_medium(sig))
            
                    print(f"[TG_SEND_OK] {instId}", flush=True)
            
                    sent_main_now = True
            
                    mark_alert_sent(state, sig)
            
                    alerts.append(sig)
            
                    print(f"[EARLY_SENT] {instId}", flush=True)

            summary_ok = (
                score >= 0
            )
            
            print(
                f"[SUMMARY_DEBUG] {instId} "
                f"score={score} "
                f"acc={acc_score} "
                f"tier={sig.get('tier')} "
                f"flags={sig.get('flags')} "
                f"entry={sig.get('entry')} "
                f"stage={sig.get('stage')}",
                flush=True
            ) 

            if (
                (not sent_main_now)
                and early_ready
                and can_alert_now
                and (not recent_safe_lock)
                and (not recent_start_lock)
                and (not recent_early_lock)
                and (not start_ready)
            ):
                print(
                    f"[EARLY_PRESSURE] {instId} "
                    f"side={sig.get('early_pressure_side')} "
                    f"score={sig.get('early_pressure_score')}",
                    flush=True
                )

                early_count += 1
                sent_early_now = True
                mark_alert_sent(state, sig)
                mark_early_alert(state, instId)

            if (
                (not sent_main_now)
                and is_priority_signal(sig)
                and priority_allowed(state, instId)
            ):
                if pro_edge_filter(sig, regime) and entry_ok:
                    send_telegram(msg_priority(sig))
                    mark_priority(state, instId)

            if (
                MANIP_ALERT_ENABLED
                and (not recent_safe_lock)
                and (not recent_start_lock)
                and (not sent_pre_now)
                and (not sent_start_now)
                and is_pre_move_manip(sig)
            ):
                if should_manip_alert(state, sig):
                    manip_watch.append(sig)
                    mark_manip_sent(state, sig)

            update_symbol_state(state, sig)

            

            # =====================
            # AFTER SCAN
            # =====================
            
            for s in alerts:
                s["rank"] = rank_signal(s)
                s["grade"] = confirm_grade(s)
            
            # добавляем SWING сигналы в общий список
            if "swing_candidates" in locals():
                for sw in swing_candidates:
                    if sw.get("sendable"):
                        sw["rank"] = rank_signal(sw)
                        sw["grade"] = confirm_grade(sw)
                        alerts.append(sw)
            
            alerts.sort(key=lambda s: s.get("rank", 0), reverse=True)
            manip_watch.sort(key=lambda s: s.get("acc_score", 0), reverse=True)
            
            swing_top = [
                s for s in alerts
                if str(s.get("status", "")).startswith("SWING")
                and str(s.get("grade", "")) != "WATCH"
            ][:3]
            
            sent_sw = set()
            ready_swings = []
            
            for sw in swing_top:
                sid = sw.get("instId")
            
                if sid in sent_sw:
                    continue
            
                text = msg_swing(sw)
            
                if text:
                    ready_swings.append((sid, text))
            
            # if ready_swings:
            #     send_telegram("🏆 SWING TOP SETUPS")
            #
            #     for sid, text in ready_swings:
            #         sent_sw.add(sid)
            #         send_telegram(text)
            
            
            cycle_info = time.strftime("%Y-%m-%d %H:%M:%S")

            print("ALERTS FOUND:", len(alerts))
            print(f"EARLY FOUND: {early_count} | START FOUND: {start_count} | PRE FOUND: {pre_count}")
            print(f"EARLY BUY SYMBOLS: {early_buy_symbols}")
            print(f"EARLY SELL SYMBOLS: {early_sell_symbols}")

            market_msg = msg_market_pressure(early_buy_symbols, early_sell_symbols)

            if market_msg:
                send_telegram(market_msg)
            
            # msg = summary_message(alerts, cycle_info, regime)
            # if msg and msg.strip() and should_send_summary(state, msg):
            #     send_telegram(msg)

            # =====================
            # PREMOVE ALERTS
            # =====================
            
            sent_ids = set()

            premove_alerts = [

                s for s in alerts

                if (
                    s.get("setup_rank") == "PRIORITY_1"
                    

                    and (

                        "LAUNCH_PROXIMITY_UP" in s.get("flags", [])
                        or "LAUNCH_PROXIMITY_DOWN" in s.get("flags", [])

                        or "EXPLOSION_READY_UP" in s.get("flags", [])
                        or "EXPLOSION_READY_DOWN" in s.get("flags", [])

                        or (
                            "COMP_PRO_5M" in s.get("flags", [])
                            and s.get("acc_score", 0) >= 3
                            and s.get("early_pressure_score", 0) >= 10
                        )

                        or (
                            "COMP_PRO_15M" in s.get("flags", [])
                            and s.get("acc_score", 0) >= 3
                            and s.get("early_pressure_score", 0) >= 10
                        )
                    )
                )

            ][:3]
            
            for sig in premove_alerts:
            
                sid = sig.get("instId")
            
                if sid in sent_ids:
                    continue
            
                if not can_send(
                    sid + "_PREMOVE",
                    PREMOVE_COOLDOWN_SEC
                ):
                
                    print(
                        f"[PREMOVE_COOLDOWN] {sid}",
                        flush=True
                    )
                
                    continue
                
                sent_ids.add(sid)
                        
                print(f"[PREMOVE_SEND] {sid}", flush=True)

                ok, fw_reason = telegram_firewall(sig, group="PREMOVE")

                if not ok:
                    print(f"[FIREWALL_SKIP] {sid} PREMOVE {fw_reason}", flush=True)
                    continue

                
                print(
                    f"[TG_STAGE] "
                    f"{sid} "
                    f"stage={sig.get('stage')} "
                    f"mode={sig.get('signal_mode')} "
                    f"entry={sig.get('entry')}",
                    flush=True
                )
                send_telegram(
            
                    f"⚠️ <b>PREMOVE — {sid}</b>\n\n"
            
                    f"🧭 Side: {sig.get('direction_code')}\n"
                    f"💰 Price: {sig.get('price')}\n"
                    f"⭐ Rank: {sig.get('setup_rank')}\n"
                    f"📊 Score: {sig.get('score')}\n\n"
            
                    f"📌 Flags:\n"
                    f"{', '.join(sig.get('flags', []))}"
            
                )
            
            # =====================
            # TOP ALERTS
            # =====================

            top_alerts = [
                s for s in alerts
                if is_best_only_signal(s)
            ][:3]

            sent_ids = set()

            for sig in top_alerts:

                sid = sig.get("instId")

                if sid in sent_ids:
                    continue

                if sid in sent_sw:
                    continue

                sent_ids.add(sid)

                if can_send(sid, 3600):

                    print(f"[TG_SEND_TRY] {sid}", flush=True)

                    send_telegram(choose_detail_message(sig))

            save_state(state)

        except Exception as e:
            err = traceback.format_exc()
            send_telegram(f"❌ Scan Error:\n{err}")
