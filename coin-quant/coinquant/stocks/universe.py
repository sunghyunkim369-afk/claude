"""주식 대상 목록.

- 미국: S&P 500 현재 구성 종목 (github.com/datasets/s-and-p-500-companies) — 과거 편입·퇴출 기록이 없어 생존 편향 있음
- 한국: KRX 상장 종목 중 시가총액 상위 (FinanceDataReader), 우선주·스팩·리츠·ETF 제외
- 지수·시장 지표: 아래 MARKET 표
"""
from __future__ import annotations

import io
import re

import pandas as pd
import requests

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

# 시장 요약 표에 쓰는 지표 (yfinance 기호, 이름, 묶음)
MARKET = [
    ("^KS11", "코스피", "국내"), ("^KQ11", "코스닥", "국내"), ("KRW=X", "원/달러 환율", "국내"),
    ("^GSPC", "S&P 500", "미국"), ("^IXIC", "나스닥 종합", "미국"), ("^DJI", "다우", "미국"), ("^RUT", "러셀 2000", "미국"),
    ("^SOX", "필라델피아 반도체", "미국"), ("^VIX", "VIX(변동성)", "미국"),
    ("^TNX", "미 10년 국채금리", "금리·원자재"), ("DX-Y.NYB", "달러 인덱스", "금리·원자재"),
    ("CL=F", "WTI 유가", "금리·원자재"), ("GC=F", "금", "금리·원자재"), ("BTC-USD", "비트코인", "금리·원자재"),
]
# 공식 검증·신호에 쓰는 지수 (긴 역사)
INDEX_SIGNAL = [("^GSPC", "S&P 500"), ("^IXIC", "나스닥 종합"), ("^KS11", "코스피"), ("^KQ11", "코스닥")]

KR_EXCLUDE = re.compile(r"(우$|우B$|우C$|\d우|스팩|리츠|ETN|인프라|선박)")


def us_universe() -> pd.DataFrame:
    r = requests.get(SP500_CSV, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df = df.rename(columns={"Symbol": "code", "Security": "name", "GICS Sector": "sector"})
    df["ticker"] = df["code"].str.replace(".", "-", regex=False)      # BRK.B → BRK-B (yfinance)
    df["market"] = "US"
    return df[["ticker", "code", "name", "sector", "market"]]


def kr_universe(top: int = 200) -> pd.DataFrame:
    import FinanceDataReader as fdr
    ls = fdr.StockListing("KRX")
    cols = {c.lower(): c for c in ls.columns}
    code, name = cols.get("code", "Code"), cols.get("name", "Name")
    mkt, cap = cols.get("market", "Market"), cols.get("marcap", "Marcap")
    df = ls[[code, name, mkt, cap]].copy()
    df.columns = ["code", "name", "board", "marcap"]
    df = df[df["board"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    df = df[~df["name"].astype(str).str.contains(KR_EXCLUDE)]
    df = df.sort_values("marcap", ascending=False).head(top)
    df["ticker"] = df["code"].astype(str).str.zfill(6) + df["board"].map(lambda b: ".KS" if b == "KOSPI" else ".KQ")
    df["sector"] = ""
    df["market"] = "KR"
    return df[["ticker", "code", "name", "sector", "market", "marcap"]].reset_index(drop=True)
