"""검증할 신호 모음.

- 타이밍 신호: 코인별로 '보유(True) / 현금(False)'. 그날 종가까지만 봅니다.
- 선택 점수: 기타 그룹에서 상위 K개를 고를 때 쓰는 숫자 (클수록 우선).
- 국면 필터: 날짜별 True(투자) / False(현금).
지표 값이 아직 없는 날(상장 직후 등)은 False 로 둡니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..universe import Panel


def _state(entry: pd.DataFrame, exit_: pd.DataFrame) -> pd.DataFrame:
    """진입 조건이 켜지면 보유, 청산 조건이 켜지면 현금 (돌파 전략용)."""
    e = entry.to_numpy(bool)
    x = exit_.to_numpy(bool)
    out = np.zeros_like(e)
    cur = np.zeros(e.shape[1], dtype=bool)
    for i in range(len(e)):
        cur = np.where(e[i], True, np.where(x[i], False, cur))
        out[i] = cur
    return pd.DataFrame(out, index=entry.index, columns=entry.columns)


class Features:
    """한 번 계산해 여러 신호가 같이 씁니다."""

    def __init__(self, p: Panel, cols: list[str] | None = None):
        cols = cols or p.assets
        self.c = p.close[cols]
        self.h = p.high[cols]
        self.l = p.low[cols]
        self.v = p.volume_usd[cols]
        self.btc = p.close["BTC"]
        self._cache: dict = {}

    def get(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def ema(self, n):
        return self.get(("ema", n), lambda: ind.ema(self.c, n))

    def sma(self, n):
        return self.get(("sma", n), lambda: ind.sma(self.c, n))

    def macd(self):
        return self.get("macd", lambda: ind.macd(self.c))

    def reg(self, n):
        return self.get(("reg", n), lambda: ind.regression_slope(self.c, n))

    def atr(self, n=14):
        return self.get(("atr", n), lambda: ind.atr(self.h, self.l, self.c, n))

    def vol(self, n=30):
        return self.get(("rv", n), lambda: ind.realized_vol(self.c, n))


def timing_signals(f: Features) -> dict[str, callable]:
    c = f.c
    def dc(n, x):  # noqa: E306
        return _state(c > ind.donchian_high(f.h, n), c < ind.donchian_low(f.l, x))
    return {
        # 추세
        "EMA 20>50": lambda: f.ema(20) > f.ema(50),
        "EMA 50>200": lambda: f.ema(50) > f.ema(200),
        "EMA 정배열(가격>20>50>200)": lambda: (c > f.ema(20)) & (f.ema(20) > f.ema(50)) & (f.ema(50) > f.ema(200)),
        "200일선 위": lambda: c > f.sma(200),
        "회귀기울기60>0": lambda: f.reg(60)[0] > 0,
        "회귀기울기90>0 & 추세선 위": lambda: (f.reg(90)[0] > 0) & (c > f.reg(90)[1]),
        "돈치안20 돌파(10일 저가 청산)": lambda: dc(20, 10),
        "돈치안55 돌파(20일 저가 청산)": lambda: dc(55, 20),
        # 모멘텀
        "MACD>시그널": lambda: f.macd()[0] > f.macd()[1],
        "MACD>0 & 히스토그램>0": lambda: (f.macd()[0] > 0) & (f.macd()[2] > 0),
        "7일수익>0": lambda: ind.ret(c, 7) > 0,
        "30일수익>0": lambda: ind.ret(c, 30) > 0,
        "90일수익>0": lambda: ind.ret(c, 90) > 0,
        "BTC대비 30일 상대강도>0": lambda: ind.relative_strength(c, f.btc, 30) > 0,
        # 거래량
        "돈치안20 돌파+거래량1.5배": lambda: _state((c > ind.donchian_high(f.h, 20)) & (ind.volume_ratio(f.v, 20) > 1.5),
                                              c < ind.donchian_low(f.l, 10)),
        "OBV>OBV 20일 EMA": lambda: (lambda o: o > ind.ema(o, 20))(ind.obv(c, f.v)),
        "상승일 거래량 비중>50%": lambda: ind.up_volume_share(c, f.v, 20) > 0.5,
        # 변동성 (손절)
        "샹들리에(22일 최고가-3ATR) 위": lambda: c > c.rolling(22, min_periods=22).max() - 3 * f.atr(22),
    }


def selection_scores(f: Features) -> dict[str, callable]:
    c = f.c
    return {
        "7일 수익률 상위": lambda: ind.ret(c, 7),
        "30일 수익률 상위": lambda: ind.ret(c, 30),
        "90일 수익률 상위": lambda: ind.ret(c, 90),
        "BTC대비 30일 상대강도 상위": lambda: ind.relative_strength(c, f.btc, 30),
        "거래량 배수(20일) 상위": lambda: ind.volume_ratio(f.v.rolling(3).mean(), 20),
        "상승일 거래량 비중 상위": lambda: ind.up_volume_share(c, f.v, 20),
        "회귀기울기60 상위": lambda: f.reg(60)[0],
        "저변동성 상위(30일 변동성 낮은 순)": lambda: -f.vol(30),
        "[위약] 고변동성 상위": lambda: f.vol(30),
    }


def regime_filters(p: Panel, funding: pd.Series | None = None) -> dict[str, pd.Series]:
    btc = p.close["BTC"]
    out = {
        "BTC 200일선 위": btc > ind.sma(btc.to_frame(), 200)["BTC"],
        "BTC EMA50>200": ind.ema(btc.to_frame(), 50)["BTC"] > ind.ema(btc.to_frame(), 200)["BTC"],
        "BTC 90일수익>0": ind.ret(btc.to_frame(), 90)["BTC"] > 0,
    }
    if p.mcap.notna().any().any() and "BTC" in p.mcap:
        dom = ind.btc_dominance(p.mcap)
        out["BTC 도미넌스 30일 하락(알트 우호)"] = (dom.diff(30) < 0) & dom.notna()
    if funding is not None and len(funding):
        f7 = funding.reindex(p.close.index).rolling(7, min_periods=5).mean()
        out["펀딩비 과열 아님(7일평균<0.03%/8h)"] = (f7 < 0.0003) | f7.isna()
        out["BTC 200일선 위 & 펀딩 과열 아님"] = out["BTC 200일선 위"] & out["펀딩비 과열 아님(7일평균<0.03%/8h)"]
    return {k: v.fillna(False) for k, v in out.items()}


# ---------- 비중 만들기 ----------

def weights_single(on: pd.DataFrame, asset: str, alive: pd.DataFrame) -> pd.DataFrame:
    w = pd.DataFrame(0.0, index=on.index, columns=[asset])
    w[asset] = on[asset].astype(float).fillna(0.0).where(alive[asset], 0.0)
    return w


def weights_slots(on: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    """대상 코인마다 같은 몫(1/N). 신호가 꺼진 몫은 현금."""
    members = members.reindex(columns=on.columns, fill_value=False)
    n = members.sum(axis=1).replace(0, np.nan)
    # on 은 True/False 또는 0~1 보유 비율(추세 3단 분할 등)
    return on.astype(float).fillna(0.0).where(members, 0.0).div(n, axis=0).fillna(0.0)


def weights_topk(score: pd.DataFrame, members: pd.DataFrame, k: int) -> pd.DataFrame:
    s = score.where(members.reindex(columns=score.columns, fill_value=False))
    rank = s.rank(axis=1, ascending=False, method="first")
    pick = rank <= k
    n = pick.sum(axis=1).replace(0, np.nan)
    return pick.astype(float).div(n, axis=0).fillna(0.0)


def weights_random(members: pd.DataFrame, k: int, rebalance_days: int, seed: int) -> pd.DataFrame:
    """[위약] 리밸런스마다 대상 중 무작위 K개."""
    rng = np.random.default_rng(seed)
    M = members.to_numpy(bool)
    W = np.zeros(M.shape)
    cur = np.zeros(M.shape[1])
    for i in range(len(M)):
        if i % rebalance_days == 0 or (cur > 0).sum() == 0:
            idx = np.flatnonzero(M[i])
            cur = np.zeros(M.shape[1])
            if len(idx):
                pick = rng.choice(idx, size=min(k, len(idx)), replace=False)
                cur[pick] = 1.0 / len(pick)
        cur = np.where(M[i], cur, 0.0)
        W[i] = cur
    return pd.DataFrame(W, index=members.index, columns=members.columns)


def random_timing(like: pd.Series, seed: int) -> pd.Series:
    """[위약] 실제 신호와 보유 비율·평균 보유기간이 같은 무작위 켜기/끄기."""
    x = like.fillna(False).astype(bool).to_numpy()
    p_on = x.mean()
    switches = max((x[1:] != x[:-1]).sum(), 1)
    avg_run = len(x) / switches
    p_flip = 1 / max(avg_run, 1)
    # 켜짐 상태 유지 확률을 보유 비율에 맞춤
    a = p_flip * 2 * (1 - p_on) if 0 < p_on < 1 else 0.0   # on→off
    b = p_flip * 2 * p_on if 0 < p_on < 1 else 0.0        # off→on
    rng = np.random.default_rng(seed)
    out = np.zeros(len(x), dtype=bool)
    cur = rng.random() < p_on
    u = rng.random(len(x))
    for i in range(len(x)):
        out[i] = cur
        cur = (u[i] >= a) if cur else (u[i] < b)
    return pd.Series(out, index=like.index)
