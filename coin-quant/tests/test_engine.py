import numpy as np
import pandas as pd
import pytest

from coinquant.backtest.engine import Costs, Rules, run
from coinquant.backtest import metrics

IDX = pd.date_range("2020-01-01", periods=10, freq="D")


def frame(vals, cols=("A",)):
    return pd.DataFrame(np.array(vals, dtype=float).reshape(len(IDX), -1), index=IDX, columns=list(cols))


def test_buy_and_hold_cost_once():
    close = frame(np.linspace(100, 190, 10))
    open_ = close.shift(1).fillna(100)
    tgt = frame(np.ones(10))
    c = Costs(fee=0.001, slip_core=0.0, slip_other=0.002, core=())
    res = run(tgt, open_, close, c, Rules())
    # 1일째 종가 신호 → 2일째 시가(=1일 종가 100)에 매수, 비용 0.3%
    expected = (190 / 100) / (1 + 0.003)
    assert res.equity.iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert res.costs_paid == pytest.approx(1 / 1.003 * 0.003, rel=1e-9)


def test_no_lookahead_fill_is_next_open():
    # 5일째 시가에 가격이 두 배로 뜀. 4일째 종가에 낸 신호는 뛴 가격에 체결되어야 함
    c = [100] * 4 + [200] * 6
    close = frame(c)
    open_ = frame([100] * 4 + [200] * 6)
    tgt = frame([0, 0, 0, 1, 1, 1, 1, 1, 1, 1])
    res = run(tgt, open_, close, Costs(0, 0, 0, ()), Rules())
    assert res.equity.iloc[-1] == pytest.approx(1.0)  # 점프 이득 없음
    # 3일째 종가 신호면 4일째 시가(100)에 사서 이득
    tgt2 = frame([0, 0, 1, 1, 1, 1, 1, 1, 1, 1])
    res2 = run(tgt2, open_, close, Costs(0, 0, 0, ()), Rules())
    assert res2.equity.iloc[-1] == pytest.approx(2.0)


def test_exec_lag_uses_next_close():
    close = frame([100, 100, 100, 150, 150, 150, 150, 150, 150, 150])
    open_ = frame([100, 100, 100, 100, 150, 150, 150, 150, 150, 150])
    tgt = frame([0, 0, 1, 1, 1, 1, 1, 1, 1, 1])
    r0 = run(tgt, open_, close, Costs(0, 0, 0, ()), Rules(exec_lag=0))
    r1 = run(tgt, open_, close, Costs(0, 0, 0, ()), Rules(exec_lag=1))
    assert r0.equity.iloc[-1] == pytest.approx(1.5)
    assert r1.equity.iloc[-1] == pytest.approx(1.0)


@pytest.mark.parametrize("mode,expected", [("last", 0.5 * (1 - 0.003)), ("zero", 0.0)])
def test_delisting_modes(mode, expected):
    vals = [100, 100, 80, 60, 50] + [np.nan] * 5
    close = frame(vals)
    open_ = close.shift(1).fillna(100)
    tgt = frame(np.ones(10))
    res = run(tgt, open_, close, Costs(0.0, 0.0, 0.003, ()), Rules(delist_mode=mode))
    buy_cost = 1 / 1.003
    assert res.equity.iloc[-1] == pytest.approx(buy_cost * expected, rel=1e-9)
    assert res.trades["reason"].iloc[0] == "delist"


def test_max_orders_and_caps():
    close = frame(np.full((10, 3), 100.0), cols=("A", "B", "C"))
    tgt = frame(np.tile([0.6, 0.3, 0.1], (10, 1)), cols=("A", "B", "C"))
    res = run(tgt, close, close, Costs(0, 0, 0, ()), Rules(max_orders=2, max_weight=0.5))
    w = res.weights.iloc[1]
    assert w["A"] == pytest.approx(0.5)   # 최대 비중 50%
    assert w["B"] == pytest.approx(0.3)
    assert w["C"] == 0.0                  # 주문 수 제한으로 가장 작은 주문은 안 냄
    assert res.orders.iloc[1] == 2


def test_round_trip_cost():
    close = frame(np.full(10, 100.0))
    tgt = frame([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    res = run(tgt, close, close, Costs(0.0005, 0.001, 0.001, ()), Rules())
    assert res.equity.iloc[-1] == pytest.approx((1 / 1.0015) * (1 - 0.0015), rel=1e-9)
    assert len(res.trades) == 1 and res.trades["ret"].iloc[0] < 0


def test_metrics_basic():
    eq = pd.Series(np.r_[np.linspace(1, 2, 200), np.linspace(2, 1, 165)],
                   index=pd.date_range("2021-01-01", periods=365))
    s = metrics.summarize(eq)
    assert s["MDD"] == pytest.approx(-0.5, abs=1e-9)
    assert s["총수익"] == pytest.approx(0.0, abs=1e-9)
    assert 0 <= s["월승률"] <= 1


def test_monthly_no_stub_when_start_is_month_end():
    eq = pd.Series(np.linspace(1, 2, 62), index=pd.date_range("2024-12-31", periods=62))
    m = metrics.monthly(eq)
    assert m.index[0].month == 1 and (m != 0).all()
