"""Дневные открытые позиции IMOEX из публичного JSON MOEX ISS.

Сохраняет вызов get_open_interest_signal() и ключи signal/text.
Порог 2x из исходного модуля сохранён: это классификация БАЛАНСА
позиций юрлиц, а не прогноз цены и не оценка прироста позиций.
При недоступности данных signal='unavailable', available=False.
"""

from datetime import date
from html import escape

import requests


_BASE = (
    "https://iss.moex.com/iss/statistics/engines/futures/markets/forts"
    "/openpositions"
)
_ASSET = "IMOEX"


def _get_json(url, params):
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def _records(payload, block_name):
    block = payload.get(block_name)
    if not isinstance(block, dict):
        raise ValueError(f"В ISS отсутствует блок {block_name}")
    columns = block.get("columns", [])
    data = block.get("data", [])
    if not isinstance(columns, list) or not isinstance(data, list):
        raise ValueError(f"Некорректный блок {block_name}")
    result = []
    for row in data:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError(f"Некорректная строка {block_name}")
        result.append(dict(zip(columns, row)))
    return result


def _integer(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Отсутствует числовое значение OI")
    result = int(value)
    if isinstance(value, float) and value != result:
        raise ValueError("Количество контрактов должно быть целым")
    return result


def _number(value, signed=False):
    return (f"{value:+,}" if signed else f"{value:,}").replace(",", " ")


def get_open_interest_signal():
    """Возвращает signal, text, available, date; вызов без аргументов.

    IMOEX в этом источнике — однодневный фьючерс с автопролонгацией.
    Дата берётся из последнего доступного отчёта, не из календаря.
    """
    try:
        catalog = _get_json(
            f"{_BASE}.json",
            {"iss.meta": "off", "iss.only": "assets"},
        )
        assets = [
            row for row in _records(catalog, "assets")
            if row.get("asset_code") == _ASSET and row.get("asset_type") == "F"
        ]
        if len(assets) != 1 or not assets[0].get("date_till"):
            raise ValueError("Нет однозначного последнего отчёта IMOEX")
        asof = assets[0]["date_till"]
        report_date = date.fromisoformat(asof)

        payload = _get_json(
            f"{_BASE}/{_ASSET}.json",
            {"date": asof, "iss.meta": "off", "iss.only": "open_positions"},
        )
        groups = {}
        for row in _records(payload, "open_positions"):
            if row.get("asset") != _ASSET or row.get("tradedate") != asof:
                raise ValueError("Актив или дата ответа не совпадает с запросом")
            group = _integer(row.get("is_fiz"))
            if group not in (0, 1) or group in groups:
                raise ValueError("Некорректные категории участников OI")
            groups[group] = row
        if set(groups) != {0, 1}:
            raise ValueError("Не получены обе категории: физлица и юрлица")

        jur, phys = groups[0], groups[1]
        long_phys = _integer(phys.get("open_position_long"))
        short_phys = _integer(phys.get("open_position_short"))
        long_jur = _integer(jur.get("open_position_long"))
        short_jur = _integer(jur.get("open_position_short"))
        positions = (long_phys, short_phys, long_jur, short_jur)
        if min(positions) < 0 or sum(positions) <= 0:
            raise ValueError("Нет корректных открытых позиций")
        if long_phys + long_jur != short_phys + short_jur:
            raise ValueError("Общие длинные и короткие позиции не совпадают")

        signal = "neutral"
        comment = "🟡 БЕЗ ПЕРЕВЕСА БОЛЕЕ 2×"
        if short_jur > long_jur * 2:
            signal = "bearish"
            comment = "🔴 ПЕРЕВЕС ШОРТОВ (>2×)"
        elif long_jur > short_jur * 2:
            signal = "bullish"
            comment = "🟢 ПЕРЕВЕС ЛОНГОВ (>2×)"

        lines = [
            "📊 OI — IMOEX (однодневный фьючерс)",
            f"Срез: {report_date:%d.%m.%Y} • дневные данные",
            "Контракты: лонг / шорт",
            f"Физлица: {_number(long_phys)} / {_number(short_phys)}",
            f"Юрлица: {_number(long_jur)} / {_number(short_jur)}",
            "",
        ]
        changes = [row.get(field) for row in (phys, jur)
                   for field in ("oichange_long", "oichange_short")]
        if all(value is not None for value in changes):
            p_long, p_short, j_long, j_short = map(_integer, changes)
            lines.extend([
                "Изменение к предыдущему отчёту: лонг / шорт",
                f"Физлица: {_number(p_long, True)} / {_number(p_short, True)}",
                f"Юрлица: {_number(j_long, True)} / {_number(j_short, True)}",
                "",
            ])
        lines.append(f"Баланс юрлиц: {comment}")
        print(f"[OI_OK] asset={_ASSET} date={asof} signal={signal}", flush=True)
        return {
            "signal": signal,
            "text": escape("\n".join(lines)),
            "available": True,
            "date": asof,
        }

    except Exception as exc:
        print(f"[OI_ERROR] {type(exc).__name__}: {exc}", flush=True)
        return {
            "signal": "unavailable",
            "text": "⚠️ OI IMOEX недоступен — баланс позиций не определён.",
            "available": False,
            "date": None,
        }
