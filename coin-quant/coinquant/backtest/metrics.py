"""성과 지표. 평균만 보지 않고 중앙값·하위 10%·MDD·승률·BTC 대비를 같이 계산합니다."""
from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

DAYS = 365


def max_drawdown(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min())


def cagr(eq: pd.Series) -> float:
    yrs = (eq.index[-1] - eq.index[0]).days / DAYS
    if yrs <= 0 or eq.iloc[0] <= 0:
        return np.nan
    if eq.iloc[-1] <= 0:
        return -1.0
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1)


def sharpe(r: pd.Series) -> float:
    sd = r.std()
    return float(r.mean() / sd * np.sqrt(DAYS)) if sd > 0 else np.nan


def monthly(eq: pd.Series) -> pd.Series:
    m = eq.resample("ME").last()
    first = eq.iloc[:1]
    m = pd.concat([first, m])
    m = m[~m.index.duplicated(keep="first")]      # 시작일이 월말이면 '하루짜리 첫 달(0%)'이 생기지 않게
    return m.pct_change().dropna()


def summarize(eq: pd.Series, bench: pd.Series | None = None, trades: pd.DataFrame | None = None,
              weights: pd.DataFrame | None = None, turnover: pd.Series | None = None,
              costs_paid: float | None = None) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    mo = monthly(eq)
    out = {
        "총수익": eq.iloc[-1] / eq.iloc[0] - 1,
        "연복리(CAGR)": cagr(eq),
        "MDD": max_drawdown(eq),
        "샤프": sharpe(r),
        "월평균": mo.mean(),
        "월중앙값": mo.median(),
        "월하위10%": mo.quantile(0.10),
        "월승률": (mo > 0).mean(),
        "최악월": mo.min(),
    }
    if bench is not None:
        b = bench.reindex(eq.index).dropna()
        out["BTC보유CAGR"] = cagr(b)
        out["초과CAGR(vs BTC)"] = out["연복리(CAGR)"] - out["BTC보유CAGR"]
        bm = monthly(b)
        ex = (mo - bm.reindex(mo.index)).dropna()
        out["BTC대비 월승률"] = (ex > 0).mean()
    if trades is not None and len(trades):
        t = trades["ret"].dropna()
        out.update({"거래수": len(t), "거래승률": (t > 0).mean(), "거래평균": t.mean(),
                    "거래중앙값": t.median(), "거래하위10%": t.quantile(0.10), "최악거래": t.min()})
    if weights is not None:
        out["평균투자비중"] = weights.sum(axis=1).mean()
    if turnover is not None:
        yrs = max((eq.index[-1] - eq.index[0]).days / DAYS, 1e-9)
        out["연회전율"] = turnover.sum() / yrs
    if costs_paid is not None:
        out["누적비용(초기자본대비)"] = costs_paid / eq.iloc[0]
    return out


def deflated_sharpe(sr_best: float, sr_all: list[float], n_obs: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Bailey & López de Prado (2014). 여러 개 중 최고를 골랐을 때 그 샤프가 우연이 아닐 확률.

    sr 는 '일간' 샤프 기준으로 넣습니다. 0.95 이상이어야 '우연 아님'에 가깝습니다.
    """
    nd = NormalDist()
    N = max(len(sr_all), 1)
    var = float(np.var(sr_all, ddof=1)) if N > 1 else 0.0
    gamma = 0.5772156649
    if N > 1 and var > 0:
        sr0 = np.sqrt(var) * ((1 - gamma) * nd.inv_cdf(1 - 1 / N) + gamma * nd.inv_cdf(1 - 1 / (N * np.e)))
    else:
        sr0 = 0.0
    denom = np.sqrt(max(1 - skew * sr_best + (kurt - 1) / 4 * sr_best ** 2, 1e-12))
    z = (sr_best - sr0) * np.sqrt(n_obs - 1) / denom
    return float(nd.cdf(z))


def fmt(v, kind="pct") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "num":
        return f"{v:.2f}"
    if kind == "int":
        return f"{int(v)}"
    return str(v)
