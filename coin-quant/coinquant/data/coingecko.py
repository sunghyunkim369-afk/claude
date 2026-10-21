"""CoinGecko 현재 시가총액 순위 (매일 추천용). 키 없이 되는 공개 API.

과거 순위는 Coin Metrics 를 쓰고, '오늘' 순위는 여기서 받습니다 (Coin Metrics 공개 데이터는 갱신이 늦거나 멈출 수 있음).
"""
from __future__ import annotations

import pandas as pd
import requests

URL = "https://api.coingecko.com/api/v3/coins/markets"


def top_mcap(pages: int = 2) -> pd.Series:
    """심볼(대문자) → 시가총액(달러). 같은 심볼이 여럿이면 큰 것."""
    rows = []
    for page in range(1, pages + 1):
        r = requests.get(URL, params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page},
                         timeout=30, headers={"User-Agent": "coin-quant/1.0"})
        r.raise_for_status()
        rows += r.json()
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.Series(dtype=float)
    df["symbol"] = df["symbol"].str.upper()
    return df.groupby("symbol")["market_cap"].max().sort_values(ascending=False)
