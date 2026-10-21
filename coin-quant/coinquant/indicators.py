"""지표 계산. 모든 함수는 '그날 종가까지'의 데이터만 씁니다 (미래 데이터 없음).

입력은 날짜 × 코인 표(DataFrame). 결과도 같은 모양입니다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.rolling(n, min_periods=n).mean()


def ret(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close / close.shift(n) - 1


def macd(close: pd.DataFrame, fast=12, slow=26, sig=9):
    line = ema(close, fast) - ema(close, slow)
    signal = line.ewm(span=sig, adjust=False, min_periods=sig).mean()
    return line, signal, line - signal


def true_range(high, low, close):
    pc = close.shift(1)
    return np.maximum(high - low, np.maximum((high - pc).abs(), (low - pc).abs()))


def atr(high, low, close, n=14):
    return true_range(high, low, close).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def realized_vol(close: pd.DataFrame, n=30, annual=365) -> pd.DataFrame:
    return np.log(close).diff().rolling(n, min_periods=int(n * 0.8)).std() * np.sqrt(annual)


def donchian_high(high: pd.DataFrame, n: int) -> pd.DataFrame:
    """직전 n일 고가 (오늘 제외)."""
    return high.shift(1).rolling(n, min_periods=n).max()


def donchian_low(low: pd.DataFrame, n: int) -> pd.DataFrame:
    return low.shift(1).rolling(n, min_periods=n).min()


def regression_slope(close: pd.DataFrame, n: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """로그 가격 회귀 추세선: 하루 기울기, 오늘 추세선 값, 잔차 표준편차(채널 폭)."""
    y = np.log(close)
    k = pd.Series(np.arange(len(y), dtype=float), index=y.index)
    roll = lambda d: d.rolling(n, min_periods=n).sum()  # noqa: E731
    sy = roll(y)
    syy = roll(y * y)
    sky = roll(y.mul(k, axis=0))
    sk = k.rolling(n).sum()
    skk = (k * k).rolling(n).sum()
    var_k = skk / n - (sk / n) ** 2
    cov = sky.div(n).sub(sy.mul(sk / n, axis=0) / n, axis=0)
    slope = cov.div(var_k, axis=0)
    mean_y = sy / n
    fit_last = mean_y + slope.mul(k - sk / n, axis=0)
    var_y = syy / n - mean_y ** 2
    resid = np.sqrt((var_y - slope ** 2 * var_k.to_numpy()[:, None]).clip(lower=0))
    return slope, np.exp(fit_last), resid


def obv(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume.fillna(0)).cumsum()


def volume_ratio(volume: pd.DataFrame, n=20) -> pd.DataFrame:
    """오늘 거래량 / 직전 n일 평균."""
    return volume / volume.shift(1).rolling(n, min_periods=n).mean()


def up_volume_share(close: pd.DataFrame, volume: pd.DataFrame, n=20) -> pd.DataFrame:
    up = (close.diff() > 0).astype(float) * volume
    tot = volume.rolling(n, min_periods=n).sum()
    return up.rolling(n, min_periods=n).sum() / tot.replace(0, np.nan)


def relative_strength(close: pd.DataFrame, btc: pd.Series, n=30) -> pd.DataFrame:
    """BTC 대비 n일 상대 수익률."""
    ratio = close.div(btc, axis=0)
    return ratio / ratio.shift(n) - 1


def btc_dominance(mcap: pd.DataFrame, btc_col="BTC") -> pd.Series:
    total = mcap.sum(axis=1, min_count=1)
    return mcap[btc_col] / total
