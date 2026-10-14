"""일봉 데이터 수집. 6자리 숫자 종목코드는 국내(KR), 나머지는 해외(US)로 구분해요.

국내·해외 모두 FinanceDataReader 를 먼저 쓰고, 해외는 실패하면 yfinance 로 받아요.
받은 데이터는 cache_dir 에 CSV 로 저장해서 같은 날 다시 실행할 때는 다시 받지 않아요.
"""

import os
import re
from datetime import date, datetime, timedelta

import pandas as pd

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def market_of(symbol):
    return "KR" if re.fullmatch(r"\d{6}", str(symbol)) else "US"


def date_range(period):
    end = pd.Timestamp(period.get("end") or date.today())
    if period.get("start"):
        start = pd.Timestamp(period["start"])
    else:
        start = end - pd.DateOffset(years=int(period.get("years", 5)))
    warmup_start = start - timedelta(days=int(period.get("warmup_days", 200)))
    return warmup_start, start, end


def clean(df):
    """열 이름을 맞추고, 거래정지일(시가 0 등)과 결측을 빼요."""
    if isinstance(df.columns, pd.MultiIndex):  # yfinance 는 (항목, 티커) 2단 열을 줄 때가 있어요
        df = df.droplevel(-1, axis=1)
    df = df.rename(columns=str.title)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"열이 없어요: {missing}")
    df = df[COLUMNS].apply(pd.to_numeric, errors="coerce").dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]


def _from_fdr(symbol, start, end):
    import FinanceDataReader as fdr
    return fdr.DataReader(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


def _from_yfinance(symbol, start, end):
    import yfinance as yf
    # 액면분할·배당을 반영한 가격으로 받아요.
    return yf.download(symbol, start=start.strftime("%Y-%m-%d"), end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                       auto_adjust=True, progress=False)


def fetch(symbol, start, end, cache_dir=None, sources=None, log=print):
    """sources 는 테스트용: {"fdr": fn, "yfinance": fn}."""
    sources = sources or {"fdr": _from_fdr, "yfinance": _from_yfinance}
    cache = None
    if cache_dir:
        cache = os.path.join(cache_dir, f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}.csv")
        if os.path.exists(cache) and datetime.fromtimestamp(os.path.getmtime(cache)).date() == date.today():
            return pd.read_csv(cache, index_col=0, parse_dates=True)

    order = ["fdr"] if market_of(symbol) == "KR" else ["fdr", "yfinance"]
    errors = []
    for name in order:
        try:
            df = clean(sources[name](symbol, start, end))
            df = df[(df.index >= start) & (df.index <= end)]
            if len(df) < 30:
                raise ValueError(f"데이터가 {len(df)}일뿐이에요")
            if name != order[0]:
                log(f"  {symbol}: {order[0]} 실패, {name} 로 받았어요")
            if cache:
                os.makedirs(cache_dir, exist_ok=True)
                df.to_csv(cache)
            return df
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError(f"{symbol} 데이터를 받지 못했어요 ({'; '.join(errors)})")


def load_all(symbols, period, cache_dir=None, log=print):
    """{symbol: DataFrame}. 받지 못한 종목은 알리고 빼요."""
    warmup_start, start, end = date_range(period)
    out = {}
    for s in symbols:
        s = str(s)
        try:
            out[s] = fetch(s, warmup_start, end, cache_dir, log=log)
            log(f"  {s} ({market_of(s)}): {len(out[s])}일 ({out[s].index[0]:%Y-%m-%d} ~ {out[s].index[-1]:%Y-%m-%d})")
        except Exception as e:
            log(f"  {s}: 건너뜀 - {e}")
    return out, start, end
