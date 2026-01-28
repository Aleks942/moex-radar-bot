# open_interest.py
import requests
import pandas as pd
from bs4 import BeautifulSoup

def get_open_interest_signal():
    """
    Возвращает словарь с анализом открытых позиций по фьючерсу IMOEX.
    Формат: {'signal': 'bullish'/'bearish'/'neutral', 'text': 'строка для вставки в сообщение'}
    """

    url = "https://www.moex.com/ru/derivatives/open-positions?assetw=IMOEX"
    html = requests.get(url, timeout=15).text
    dfs = pd.read_html(html)
    df = dfs[0]  # первая таблица с данными

    phys = df.iloc[1]
    jur = df.iloc[2]

    long_phys = int(str(phys[1]).replace(" ", ""))
    short_phys = int(str(phys[2]).replace(" ", ""))
    long_jur = int(str(jur[1]).replace(" ", ""))
    short_jur = int(str(jur[2]).replace(" ", ""))

    # Соотношение и сигнал
    signal = "neutral"
    comment = "🟡 НЕЙТРАЛЬНЫЙ"
    if short_jur > long_jur * 2:
        signal = "bearish"
        comment = "🔴 МЕДВЕЖИЙ (юрлица усиливают шорты)"
    elif long_jur > short_jur * 2:
        signal = "🟢 БЫЧИЙ (юрлица усиливают лонги)"

    text = (
        f"📊 Open Interest (IMOEX Futures)\n"
        f"Физики: {long_phys:,} / {short_phys:,}\n"
        f"Юрлица: {long_jur:,} / {short_jur:,}\n\n"
        f"Сигнал: {comment}"
    )

    return {"signal": signal, "text": text}
