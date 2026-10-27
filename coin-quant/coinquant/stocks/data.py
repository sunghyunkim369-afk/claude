"""주식 일봉 받기 (yfinance) + 캐시.

- 수정주가(auto_adjust=True: 배당·분할 반영)로 받음 → 수익률 계산이 맞음
- 캐시: data_cache/stocks/{시장}.parquet 대신 CSV (의존성 줄이기): 종목별 열, 날짜 행 (open/high/low/close/volume 5개 파일)
- 판단은 그날 종가, 체결은 다음 거래일 시가 (코인과 같은 원칙)
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

FIELDS = ("Open", "High", "Low", "Close", "Volume")


def download(tickers: list[str], start: str = "1990-01-01", chunk: int = 100, log=print) -> dict[str, pd.DataFrame]:
    """{필드: DataFrame(날짜 × 종목)}. 실패한 종목은 빠짐."""
    import yfinance as yf
    parts = {f: [] for f in FIELDS}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        for attempt in range(3):
            try:
                d = yf.download(batch, start=start, auto_adjust=True, progress=False, threads=True, group_by="column")
                break
            except Exception as e:  # 일시 차단·네트워크
                log(f"  yfinance 실패 {type(e).__name__} → {2 ** attempt * 5}초 후 다시")
                time.sleep(2 ** attempt * 5)
        else:
            continue
        if d is None or d.empty:
            continue
        for f in FIELDS:
            x = d[f] if isinstance(d.columns, pd.MultiIndex) else d[[f]].rename(columns={f: batch[0]})
            parts[f].append(x)
        log(f"  주가 {min(i + chunk, len(tickers))}/{len(tickers)}")
    out = {}
    for f, xs in parts.items():
        if xs:
            df = pd.concat(xs, axis=1)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            out[f.lower()] = df.loc[:, ~df.columns.duplicated()].sort_index()
    if "close" in out:
        good = out["close"].columns[out["close"].notna().sum() > 0]
        out = {k: v.reindex(columns=good) for k, v in out.items()}
    return out


def save(frames: dict[str, pd.DataFrame], d: Path, name: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for f, df in frames.items():
        df.to_csv(d / f"{name}_{f}.csv.gz", date_format="%Y-%m-%d")


def load(d: Path, name: str) -> dict[str, pd.DataFrame] | None:
    out = {}
    for f in ("open", "high", "low", "close", "volume"):
        p = d / f"{name}_{f}.csv.gz"
        if not p.exists():
            return None
        out[f] = pd.read_csv(p, index_col=0, parse_dates=True)
    return out


def update(d: Path, name: str, tickers: list[str], start: str = "1990-01-01", log=print) -> dict[str, pd.DataFrame]:
    """캐시가 있으면 최근 30일만 다시 받아 붙임 (수정주가가 바뀌면 전체를 다시 받음)."""
    old = load(d, name)
    if old is None or set(tickers) - set(old["close"].columns):
        new = download(tickers, start=start, log=log)
        save(new, d, name)
        return new
    since = (old["close"].index[-1] - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    rec = download(tickers, start=since, log=log)
    if not rec:
        return old
    # 겹치는 날 종가가 0.5% 넘게 다르면 배당·분할로 과거 수정주가가 바뀐 것 → 그 종목은 전체 다시
    ov = old["close"].index.intersection(rec["close"].index)
    diff = (rec["close"].loc[ov] / old["close"].loc[ov].reindex(columns=rec["close"].columns) - 1).abs().max()
    redo = list(diff[diff > 0.005].index)
    if redo:
        log(f"  수정주가 바뀐 종목 {len(redo)}개 전체 다시 받기")
        full = download(redo, start=start, log=log)
    out = {}
    for f in old:
        x = pd.concat([old[f][~old[f].index.isin(rec[f].index)], rec[f]]).sort_index()
        if redo and f in full:
            x.loc[:, [c for c in redo if c in full[f].columns]] = full[f].reindex(x.index)[[c for c in redo if c in full[f].columns]]
        out[f] = x[~x.index.duplicated(keep="last")]
    save(out, d, name)
    return out
