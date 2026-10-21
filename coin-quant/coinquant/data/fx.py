"""원/달러 환율 (김치 프리미엄 계산용).

1순위: 미국 연준(FRED) 일별 환율을 옮겨 둔 공개 데이터 (github.com/datasets/exchange-rates)
2순위: frankfurter.app (유럽중앙은행 기준환율)
주말·휴일은 직전 영업일 값으로 채웁니다.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

GITHUB_FX = "https://raw.githubusercontent.com/datasets/exchange-rates/main/data/daily.csv"
FRANKFURTER = "https://api.frankfurter.app/{start}..?from=USD&to=KRW"


def download_usdkrw(out: Path) -> pd.Series:
    s = None
    try:
        r = requests.get(GITHUB_FX, timeout=120)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df = df[df["Country"] == "South Korea"]
        s = pd.Series(df["Exchange rate"].astype(float).values, index=pd.to_datetime(df["Date"]))
    except Exception:
        pass
    try:  # 최근 값 보강
        start = (s.index.max() if s is not None else pd.Timestamp("2015-01-01")).strftime("%Y-%m-%d")
        r = requests.get(FRANKFURTER.format(start=start), timeout=60)
        r.raise_for_status()
        rates = r.json()["rates"]
        s2 = pd.Series({pd.Timestamp(k): v["KRW"] for k, v in rates.items()})
        s = s2 if s is None else pd.concat([s, s2[~s2.index.isin(s.index)]])
    except Exception:
        pass
    if s is None:
        raise ConnectionError("환율 데이터를 받을 수 없습니다")
    s = s.sort_index()
    s.name = "usdkrw"
    out.parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(out, header=True, index_label="date")
    return s


def load_usdkrw(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    s = pd.read_csv(path, parse_dates=["date"]).set_index("date")["usdkrw"]
    return s.asfreq("D").ffill()
