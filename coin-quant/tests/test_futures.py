import numpy as np
import pandas as pd
import pytest

from coinquant.backtest.futures import FutRules, run_futures

IDX = pd.date_range("2022-01-01", periods=8, freq="D")


def f(vals):
    return pd.DataFrame({"A": np.array(vals, dtype=float)}, index=IDX)


def test_short_profit_fee_and_funding():
    close = f([100, 100, 90, 80, 80, 80, 80, 80])
    tgt = f([-1, -1, -1, 0, 0, 0, 0, 0])
    fund = f([0, 0.001, 0.001, 0, 0, 0, 0, 0])            # 양수 펀딩 → 숏이 받음
    rate = pd.Series({"A": 0.001})
    r = run_futures(tgt, close, close, close, close, fund, rate, FutRules(min_trade=0.5))   # 수량 고정(비중 재조정 끔)
    # 2일째 시가 100에 평가금 1만큼 숏 → 3일째 시가 90에 펀딩 → 4일째 시가 80에 환매
    q = 1 / 100 * (1 / 1)                                  # 평가금 1 → 명목 1 → 수량 0.01
    expect = 1 - 100 * q * 0.001 + 100 * q * 0.001 + 90 * q * 0.001 + (100 - 80) * q - 80 * q * 0.001
    assert r.equity.iloc[-1] == pytest.approx(expect, rel=1e-6)
    assert r.trades["side"].iloc[0] == "숏" and r.trades["ret"].iloc[0] > 0.18


def test_short_liquidation_1x():
    close = f([100, 100, 150, 150, 150, 150, 150, 150])
    high = f([100, 100, 210, 150, 150, 150, 150, 150])     # 고가가 진입가의 2배를 넘음
    tgt = f([-1] * 8)
    r = run_futures(tgt, close, high, close, close, f([0] * 8), pd.Series({"A": 0.0}),
                    FutRules(leverage=1, maint_margin=0.005, min_trade=10))
    assert r.liquidations >= 1
    assert r.equity.iloc[2] == pytest.approx(1 - (199.5 - 100) / 100, abs=1e-6)   # 청산가 199.5에서 정리


def test_flip_long_to_short():
    close = f([100, 100, 110, 110, 100, 100, 100, 100])
    tgt = f([1, 1, -1, -1, 0, 0, 0, 0])
    r = run_futures(tgt, close, close, close, close, f([0] * 8), pd.Series({"A": 0.0}), FutRules())
    assert list(r.trades["side"]) == ["롱", "숏"]
    assert r.equity.iloc[-1] == pytest.approx(1.1 * (1 + 10 / 110), rel=1e-9)
