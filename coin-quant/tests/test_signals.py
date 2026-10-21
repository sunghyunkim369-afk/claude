import numpy as np
import pandas as pd
import pytest

from coinquant.backtest import signals as sg
from coinquant.overrides import Judgments
from coinquant.universe import Panel, _split_gaps, classify


def synthetic_panel(n=500, assets=("BTC", "ETH", "AAA", "BBB"), seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-01", periods=n, freq="D")
    r = rng.normal(0.001, 0.04, (n, len(assets)))
    close = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=list(assets))
    open_ = close.shift(1).bfill()
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, close.shape))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, close.shape))
    vol = pd.DataFrame(rng.lognormal(15, 0.5, close.shape), index=idx, columns=list(assets))
    mcap = close * 1e6
    meta = pd.DataFrame({"base": list(assets), "kind": "normal", "first": idx[0], "last": idx[-1],
                         "delisted": False, "migrated_to": None}, index=list(assets))
    return Panel(open_, high, low, close, vol, mcap, meta, close.notna())


def test_no_lookahead_in_any_signal():
    """T일까지만 잘라서 계산한 신호 == 전체로 계산한 신호의 T일 값 (미래 데이터를 쓰면 달라짐)."""
    p = synthetic_panel()
    full = sg.Features(p)
    T = p.close.index[350]
    cut = sg.Features(p.slice(None, T))
    for name, fn in sg.timing_signals(full).items():
        a = fn().loc[T]
        b = sg.timing_signals(cut)[name]().loc[T]
        assert (a.fillna(False) == b.fillna(False)).all(), name
    for name, fn in sg.selection_scores(full).items():
        a = fn().loc[T]
        b = sg.selection_scores(cut)[name]().loc[T]
        pd.testing.assert_series_equal(a, b, check_names=False, rtol=1e-6, obj=name)


def test_weights_topk_and_slots():
    idx = pd.date_range("2020-01-01", periods=3)
    score = pd.DataFrame([[3, 2, 1, np.nan]] * 3, index=idx, columns=list("abcd"))
    mem = pd.DataFrame([[True, True, True, True]] * 3, index=idx, columns=list("abcd"))
    w = sg.weights_topk(score, mem, 2)
    assert list(w.iloc[0]) == [0.5, 0.5, 0.0, 0.0]
    on = pd.DataFrame([[True, False, True, True]] * 3, index=idx, columns=list("abcd"))
    w2 = sg.weights_slots(on, mem)
    assert w2.iloc[0].sum() == pytest.approx(0.75)   # 꺼진 몫은 현금


def test_random_timing_matches_exposure():
    s = pd.Series(([True] * 30 + [False] * 10) * 50)
    rt = sg.random_timing(s, seed=3)
    assert abs(rt.mean() - s.mean()) < 0.1


def test_gap_split_and_classify():
    idx = pd.DatetimeIndex(list(pd.date_range("2022-01-01", periods=5)) + list(pd.date_range("2022-02-01", periods=5)))
    df = pd.DataFrame({"close": range(10)}, index=idx)
    parts = _split_gaps(df, 10)
    assert len(parts) == 2 and len(parts[1]) == 5
    assert classify("USDC") == "stable"
    assert classify("BTCUP") == "leveraged"
    assert classify("JUP") == "normal"
    assert classify("1000SATS") == "meme"
    assert classify("BNB") == "exchange"


def test_judgments_layer(tmp_path):
    j = Judgments(tmp_path)
    j.add("lock", "BTC", "장기 보유")
    j.add("exclude", "XRP", "소송")
    j.add("weight", "SOL", "생태계", value=0.2)
    j.add("release", "XRP", "해소")
    act = j.active()
    assert set(act) == {"BTC", "SOL"}
    formula = pd.Series({"BTC": 0.1, "ETH": 0.5, "XRP": 0.3})
    current = pd.Series({"BTC": 0.4})
    final = Judgments.apply(formula, current, act, max_total=1.0)
    assert final["BTC"] == pytest.approx(0.4)      # 공식은 0.1로 줄이려 했지만 고정
    assert final["SOL"] == pytest.approx(0.2)
    assert final.sum() <= 1.0 + 1e-9               # 넘치면 판단 없는 코인(ETH, XRP)을 줄임
    assert final["ETH"] / final["XRP"] == pytest.approx(0.5 / 0.3)
    with pytest.raises(ValueError):
        j.add("lock", "ETH", "  ")                 # 이유 필수
