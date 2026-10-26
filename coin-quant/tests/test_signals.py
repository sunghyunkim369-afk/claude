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


def test_swing_sim_rules():
    from coinquant.research.short_swing import swing_sim
    idx = pd.date_range("2021-01-01", periods=10)
    c = pd.DataFrame({"A": [100, 100, 105, 111, 111, 100, 94, 94, 94, 94]}, index=idx, dtype=float)
    e = pd.DataFrame({"A": [False, True] + [False] * 8}, index=idx)
    x = pd.DataFrame(False, index=idx, columns=["A"])
    p = swing_sim(e, x, c, target=0.10)               # 100에 진입 → 111(+11%)에서 목표가 청산
    assert list(p["A"]) == [0, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    p = swing_sim(e, x, c, stop=0.05, max_days=20)    # 손절만: 94(−6%)에서 청산
    assert p["A"].iloc[5] == 1 and p["A"].iloc[6] == 0
    p = swing_sim(e, x, c, max_days=3)                # 최대 3일
    assert p["A"].sum() == 3


def test_swing_sim_no_lookahead():
    from coinquant.research.short_swing import swing_sim
    rng = np.random.default_rng(5)
    idx = pd.date_range("2021-01-01", periods=200)
    c = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, 0.03, (200, 2)), axis=0)), index=idx, columns=["A", "B"])
    e = c > c.rolling(5).mean()
    x = c < c.rolling(3).mean()
    full = swing_sim(e, x, c, target=0.1, stop=0.05, max_days=10)
    cut = swing_sim(e.iloc[:120], x.iloc[:120], c.iloc[:120], target=0.1, stop=0.05, max_days=10)
    pd.testing.assert_frame_equal(full.iloc[:120], cut)


def test_limit_entry_sleeve():
    from coinquant.research.limit_entry import sleeve
    pos = np.array([0, 1, 1, 1, 0, 0], float)
    o = np.array([100, 100, 99, 104, 110, 110], float)
    h = np.array([100, 101, 102, 112, 112, 110], float)
    l_ = np.array([100, 99, 97, 103, 108, 110], float)
    c = np.array([100, 100, 101, 110, 110, 110], float)
    # 1일째 종가 100 신호 → −2% 지정가 98: 2일째 저가 97 이 닿음 → 98 에 체결 (시가 99 보다 낮은 지정가)
    r, at = sleeve(pos, o, h, l_, c, method="limit", disc=0.02, fee=0.0, slip=0.0)
    assert at[0][1] is True or at[0][1] == True  # noqa: E712
    assert r[2] == pytest.approx(101 / 98 - 1)
    # −5% 지정가 95: 저가 97 이라 안 닿음 → 포기 (한 번만 시도면 이후에도 안 삼)
    r, at = sleeve(pos, o, h, l_, c, method="limit", disc=0.05, fee=0.0, slip=0.0)
    assert len(at) == 1 and not at[0][1] and np.allclose(r, 0)
    # 놓친 거래를 시장가로 샀다면: 2일째 시가 99 매수 → 4일째 신호 꺼짐 → 5일째 시가 110 매도
    assert at[0][2] == pytest.approx(110 / 99 - 1)


def test_upbit_tick_rounding():
    from coinquant.data.upbit import round_down_tick
    assert round_down_tick(123_456_789) == 123_456_000
    assert round_down_tick(3_456.7) == 3_455
    assert round_down_tick(512.34) == 512
    assert round_down_tick(0.123456) == 0.123


def test_levels_no_lookahead_and_basic():
    from coinquant.levels import pivot_lines, volume_zones
    rng = np.random.default_rng(7)
    n = 500
    idx = pd.date_range("2020-01-01", periods=n)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, n))), index=idx)
    h, l_ = c * 1.02, c * 0.98
    v = pd.Series(rng.lognormal(10, 0.5, n), index=idx)
    atr = (h - l_).rolling(14).mean()
    T = 400
    for f, args in ((volume_zones, (h, l_, c, v)), (pivot_lines, (h, l_, c, atr))):
        full = f(*args)
        cut = f(*[s.iloc[:T] for s in args])
        pd.testing.assert_frame_equal(full.iloc[:T], cut, check_freq=False)
    z = volume_zones(h, l_, c, v)
    ok = z.dropna(subset=["res_lo"])
    assert (ok["res_lo"] > c.reindex(ok.index)).all()        # 위쪽 매물대는 현재가보다 위
    p = pivot_lines(h, l_, c, atr).dropna(subset=["sup"])
    assert (p["sup"] < c.reindex(p.index)).all()              # 아래쪽 지지선은 현재가보다 아래


def test_exit_after_failed_recovery_no_lookahead():
    from coinquant.research.levels_regime import exit_after_failed_recovery
    idx = pd.date_range("2024-01-01", periods=12)
    c = pd.DataFrame({"A": [100, 98, 97, 96, 95, 101, 100, 98, 101, 100, 100, 100.0],
                      "B": [100, 98, 101, 100, 100, 100, 100, 100, 100, 100, 100, 100.0]}, index=idx)
    lv = pd.DataFrame(100.0, index=idx, columns=c.columns)
    x = exit_after_failed_recovery(c, lv.shift(1), 3)
    # A: 2일째 이탈 → 3일 복귀 못 함 → 5일째부터 금지, 101 복귀(6일째)에 해제
    assert x["A"].tolist()[:6] == [False, False, False, False, True, False]
    assert not x["B"].any()  # 다음 날 바로 복귀 = 가짜 이탈
    for t in range(3, 12):
        part = exit_after_failed_recovery(c.iloc[:t], lv.shift(1).iloc[:t], 3)
        assert part.equals(x.iloc[:t])


def test_alert_status_and_change(tmp_path, monkeypatch):
    from coinquant.app import alert
    from coinquant.trend import MultiTrend
    idx = pd.date_range("2023-01-01", periods=400)
    up = pd.Series(np.exp(np.linspace(0, 1.0, 400)), index=idx)
    c = pd.DataFrame({"UP": up, "DN": up.iloc[::-1].to_numpy()}, index=idx)
    mt = MultiTrend(c, c, c)
    mt.close = c
    d = idx[-1]
    assert alert.coin_status(mt, d, "UP")["status"] == "매수 가능"
    assert alert.coin_status(mt, d, "DN")["status"] == "관망"
    # 미래 데이터 없음: 앞부분만으로 계산한 상태 = 전체로 계산한 그날 상태
    t = idx[300]
    part = MultiTrend(c.loc[:t], c.loc[:t], c.loc[:t])
    part.close = c.loc[:t]
    assert alert.coin_status(part, t, "UP") == alert.coin_status(mt, t, "UP")
