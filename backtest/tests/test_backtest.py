"""엔진 검증 (네트워크 없이). 실행: python -m pytest tests  또는  python -m unittest discover -s tests"""

import os
import sys
import unittest
import unittest.mock

import numpy as np
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import data  # noqa: E402
from backtest import Backtest, buy_and_hold, metrics  # noqa: E402
from broker import Order, SimulatedBroker  # noqa: E402
from indicators import add_indicators, ta  # noqa: E402
from main import prepare, run  # noqa: E402
from strategy import add_signals  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))


def random_ohlcv(n=700, seed=0, drift=0.0005, vol=0.02, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[100, c[:-1]] * (1 + rng.normal(0, vol / 4, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    v = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v},
                        index=pd.bdate_range(start, periods=n))


def signal_frame(dates, buy_on=(), sell_on=(), price=100.0, atr=2.0, rank=30.0):
    """신호를 직접 지정한 프레임 (엔진만 검증)."""
    df = pd.DataFrame({"Open": price, "High": price + 1, "Low": price - 1, "Close": price, "Volume": 1.0,
                       "atr": atr, "rank": rank, "buy_signal": False, "sell_signal": False}, index=dates)
    df.loc[list(buy_on), "buy_signal"] = True
    df.loc[list(sell_on), "sell_signal"] = True
    df["sell_reason"] = ""
    df.loc[list(sell_on), "sell_reason"] = "MACD 데드크로스"
    return df


def cfg_with(**risk):
    c = yaml.safe_load(yaml.safe_dump(CFG))
    c["risk"].update(risk)
    return c


class UniverseTest(unittest.TestCase):
    def test_marcap_text_is_sorted_numerically(self):
        import universe
        n = 400
        listing = pd.DataFrame({"Code": [f"{(i + 10) * 10:06d}" for i in range(n)],
                                "Name": [f"종목{i}" for i in range(n)],
                                "Marcap": [f"{(n - i) * 1000:,}" for i in range(n)]})  # 쉼표 있는 문자열
        big = pd.DataFrame({"Code": ["005930", "000660", "005380"], "Name": ["삼성전자", "SK하이닉스", "현대차"],
                            "Marcap": ["999,999,999", "888,888,888", "777,777,777"]})
        fake = type("F", (), {"StockListing": staticmethod(lambda m: pd.concat([big, listing]) if m == "KOSPI" else listing)})
        with unittest.mock.patch.dict(sys.modules, {"FinanceDataReader": fake}):
            df, _ = universe.from_marcap()
        kospi = df[df["index"] == "KOSPI200"]
        self.assertEqual(list(kospi["code"][:3]), ["005930", "000660", "005380"])
        self.assertEqual(len(kospi), 200)

    def test_rejects_list_without_large_caps(self):
        import universe
        with self.assertRaises(ValueError):
            universe.check(pd.DataFrame({"code": ["095570", "006840"]}))

    def test_saved_universe_has_large_caps(self):
        import universe
        universe.check(universe.load())


class DataTest(unittest.TestCase):
    def test_market_detection(self):
        self.assertEqual(data.market_of("005930"), "KR")
        self.assertEqual(data.market_of("AAPL"), "US")
        self.assertEqual(data.market_of("BRK-B"), "US")

    def test_us_falls_back_to_yfinance(self):
        df = random_ohlcv(100)
        calls = []

        def bad(*a):
            calls.append("fdr")
            raise ConnectionError("blocked")

        def good(*a):
            calls.append("yf")
            out = df.copy()
            out.columns = pd.MultiIndex.from_product([out.columns, ["AAPL"]])  # yfinance 2단 열
            return out

        got = data.fetch("AAPL", df.index[0], df.index[-1], sources={"fdr": bad, "yfinance": good}, log=lambda *_: None)
        self.assertEqual(calls, ["fdr", "yf"])
        self.assertEqual(list(got.columns), data.COLUMNS)

    def test_kr_does_not_use_yfinance_and_drops_halts(self):
        df = random_ohlcv(100)
        df.iloc[10, df.columns.get_loc("Open")] = 0  # 거래정지일
        got = data.fetch("005930", df.index[0], df.index[-1],
                         sources={"fdr": lambda *a: df, "yfinance": None}, log=lambda *_: None)
        self.assertEqual(len(got), 99)


class IndicatorTest(unittest.TestCase):
    def test_no_lookahead(self):
        """앞부분만 넣고 계산한 값 == 전체로 계산한 값 (미래 데이터를 쓰면 달라져요)."""
        df = random_ohlcv(400)
        full = add_signals(add_indicators(df, CFG["indicators"]), CFG["entry"], CFG["exit"])
        cut = add_signals(add_indicators(df.iloc[:300], CFG["indicators"]), CFG["entry"], CFG["exit"])
        cols = ["ema_slow", "adx", "macd", "macd_signal", "rsi", "atr", "vol_ma", "buy_signal", "sell_signal"]
        pd.testing.assert_frame_equal(full[cols].iloc[:300], cut[cols], check_dtype=False)

    def test_short_history_gives_nan_not_error(self):
        for use_ta in (True, False):
            d = add_indicators(random_ohlcv(30), CFG["indicators"], use_ta)
            self.assertTrue(d["ema_slow"].isna().all())
            self.assertEqual(d["ema_slow"].dtype, float)
            add_signals(d, CFG["entry"], CFG["exit"])

    @unittest.skipIf(ta is None, "pandas-ta 없음")
    def test_builtin_matches_pandas_ta(self):
        df = random_ohlcv(500, seed=3)
        a = add_indicators(df, CFG["indicators"], True).iloc[150:]
        b = add_indicators(df, CFG["indicators"], False).iloc[150:]
        for c in ["ema_fast", "ema_slow", "macd", "macd_signal", "rsi", "atr", "vol_ma", "adx"]:
            self.assertLess(float((a[c] - b[c]).abs().max()), 0.05, c)

    def test_signal_rules(self):
        df = random_ohlcv(600, seed=5)
        spec = {**CFG["entry"], "cross_lookback": 1, "adx_min": 25}  # 처음 요청 규칙: 골든크로스 당일
        d = add_signals(add_indicators(df, CFG["indicators"]), spec, CFG["exit"])
        b = d[d["buy_signal"]]
        self.assertGreater(len(b), 0)
        self.assertTrue((b["Close"] > b["ema_slow"]).all())
        self.assertTrue((b["adx"] > 25).all())
        self.assertTrue((b["rsi"] < 70).all())
        self.assertTrue((b["Volume"] > b["vol_ma"]).all())
        prev = d.shift(1).loc[b.index]
        self.assertTrue(((prev["macd"] <= prev["macd_signal"]) & (b["macd"] > b["macd_signal"])).all())


class SignalOptionTest(unittest.TestCase):
    def test_lookback_and_ema_exit(self):
        df = add_indicators(random_ohlcv(800, seed=7), CFG["indicators"])
        base = add_signals(df, CFG["entry"], CFG["exit"])
        wide = add_signals(df, {**CFG["entry"], "cross_lookback": 5}, CFG["exit"])
        # 당일 조건을 만족하면 5일 조건도 만족해요 (신호가 더 많아지기만 해요)
        self.assertTrue((wide["buy_signal"] | ~base["buy_signal"]).all())
        self.assertGreater(wide["buy_signal"].sum(), base["buy_signal"].sum())
        ema = add_signals(df, CFG["entry"], {**CFG["exit"], "macd_dead_cross": False, "ema_exit": True})
        self.assertTrue((ema["sell_signal"] == (ema["Close"] < ema["ema_slow"])).all())
        self.assertTrue((ema.loc[ema["sell_signal"], "sell_reason"] == "종가 < EMA60").all())


class BrokerTest(unittest.TestCase):
    def test_costs(self):
        br = SimulatedBroker(CFG["costs"], 0.001)
        bar = {"Open": 10000, "High": 10500, "Low": 9500, "Close": 10000}
        f = br.submit(Order("005930", "KR", "sell", 10), bar, None)
        self.assertAlmostEqual(f.price, 9990)
        self.assertAlmostEqual(f.fee, 9990 * 10 * (0.00015 + 0.0018))
        f = br.submit(Order("AAPL", "US", "buy", 10), {"Open": 100.0, "Low": 99}, None)
        self.assertAlmostEqual(f.price, 100.1)
        self.assertAlmostEqual(f.fee, 100.1 * 10 * 0.0025)

    def test_stop_fill_price(self):
        br = SimulatedBroker(CFG["costs"], 0.0)
        o = Order("A", "US", "sell", 1, kind="stop", stop_price=95)
        self.assertIsNone(br.submit(o, {"Open": 100, "Low": 96}, None))
        self.assertEqual(br.submit(o, {"Open": 100, "Low": 90}, None).price, 95)   # 장중에 닿음
        self.assertEqual(br.submit(o, {"Open": 90, "Low": 85}, None).price, 90)    # 갭하락: 시가

    def test_buy_limited_by_cash(self):
        br = SimulatedBroker(CFG["costs"], 0.0)
        f = br.submit(Order("A", "US", "buy", 1000), {"Open": 100.0}, None, cash=1000)
        self.assertEqual(f.qty, 9)  # 10주는 수수료 때문에 1000달러를 넘어요


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range("2024-01-01", periods=30)

    def bt(self, frames, cfg=CFG, market="US"):
        return Backtest(frames, market, cfg, self.dates[0], self.dates[-1])

    def test_signal_today_fills_next_open(self):
        f = signal_frame(self.dates, buy_on=[self.dates[5]])
        f.loc[self.dates[6], "Open"] = 101.0  # 다음 날 시가
        eq, tr = self.bt({"A": f}).run(100_000)
        self.assertEqual(len(tr), 1)
        t = tr.iloc[0]
        self.assertEqual(t["entry_date"], self.dates[6])
        self.assertAlmostEqual(t["entry_price"], 101.0 * 1.001)
        self.assertEqual(t["exit_reason"], "기간 종료")

    def test_position_size_one_percent_risk(self):
        f = signal_frame(self.dates, buy_on=[self.dates[5]], atr=2.0)
        _, tr = self.bt({"A": f}, cfg_with(max_position_pct=1.0)).run(100_000)
        # 1% = 1000달러, 손절폭 2*ATR = 4 → 250주
        self.assertEqual(tr.iloc[0]["qty"], 250)

    def test_max_position_pct_caps_size(self):
        f = signal_frame(self.dates, buy_on=[self.dates[5]], atr=0.1)
        _, tr = self.bt({"A": f}).run(100_000)
        self.assertEqual(tr.iloc[0]["qty"], 250)  # 25% / 100달러

    def test_stop_loss(self):
        f = signal_frame(self.dates, buy_on=[self.dates[5]], atr=2.0)
        f.loc[self.dates[10], "Low"] = 90.0  # 진입 100.1 - 4 = 96.1 아래
        _, tr = self.bt({"A": f}).run(100_000)
        t = tr.iloc[0]
        self.assertEqual(t["exit_reason"], "손절")
        self.assertEqual(t["exit_date"], self.dates[10])
        self.assertAlmostEqual(t["exit_price"], (100.1 - 4.0) * 0.999)

    def test_trailing_stop_uses_yesterdays_high(self):
        f = signal_frame(self.dates, buy_on=[self.dates[5]], atr=2.0)
        f.loc[self.dates[8]:, ["Open", "High", "Low", "Close"]] = [120.0, 121.0, 119.0, 120.0]
        # 최고가 121 - 3*2 = 115. 10일째 저가 114 → 115에 체결
        f.loc[self.dates[12], "Low"] = 114.0
        _, tr = self.bt({"A": f}).run(100_000)
        t = tr.iloc[0]
        self.assertEqual(t["exit_reason"], "트레일링 스탑")
        self.assertEqual(t["exit_date"], self.dates[12])
        self.assertAlmostEqual(t["exit_price"], 115.0 * 0.999)

    def test_dead_cross_sells_next_open(self):
        f = signal_frame(self.dates, buy_on=[self.dates[5]], sell_on=[self.dates[9]])
        f.loc[self.dates[10], "Open"] = 105.0
        _, tr = self.bt({"A": f}).run(100_000)
        t = tr.iloc[0]
        self.assertEqual((t["exit_date"], t["exit_reason"]), (self.dates[10], "MACD 데드크로스"))
        self.assertAlmostEqual(t["exit_price"], 105.0 * 0.999)

    def test_max_positions_and_ranking(self):
        frames = {f"S{i}": signal_frame(self.dates, buy_on=[self.dates[5]], atr=2.0, rank=i) for i in range(8)}
        _, tr = self.bt(frames, cfg_with(max_position_pct=0.1)).run(100_000)
        self.assertEqual(sorted(tr["symbol"]), ["S3", "S4", "S5", "S6", "S7"])  # ADX 높은 5개

    def test_pnl_accounting(self):
        """현금 흐름으로 계산한 최종 자산 == 시작 자본 + 거래 손익 합."""
        df = random_ohlcv(900, seed=11, drift=0.001)
        frames = prepare({"AAA": df, "BBB": random_ohlcv(900, seed=12, drift=0.0008)}, CFG)
        eq, tr = Backtest(frames, "US", CFG, df.index[100], df.index[-1]).run(100_000)
        self.assertGreater(len(tr), 3)
        self.assertAlmostEqual(eq["equity"].iloc[-1], 100_000 + tr["pnl"].sum(), places=4)
        self.assertTrue((eq["cash"] >= -1e-6).all())
        self.assertTrue((eq["positions"] <= 5).all())
        self.assertTrue((pd.to_datetime(tr["exit_date"]) >= pd.to_datetime(tr["entry_date"])).all())

    def test_no_lookahead_in_engine(self):
        """미래 가격을 크게 바꿔도 그 이전 거래는 그대로여야 해요."""
        df = random_ohlcv(900, seed=21, drift=0.001)
        cut = df.index[700]
        changed = df.copy()
        changed.loc[cut:, ["Open", "High", "Low", "Close"]] *= 3
        r = []
        for d in (df, changed):
            fr = prepare({"AAA": d}, CFG)
            _, tr = Backtest(fr, "US", CFG, df.index[100], df.index[-1]).run(100_000)
            r.append(tr[pd.to_datetime(tr["exit_date"]) < cut].reset_index(drop=True))
        self.assertGreater(len(r[0]), 0)
        pd.testing.assert_frame_equal(r[0], r[1])


class ContestTest(unittest.TestCase):
    def setUp(self):
        import contest
        self.contest = contest
        self.raw = {f"S{i}": random_ohlcv(600, seed=i, drift=0.0006) for i in range(1, 7)}
        self.params = dict(lookback=20, top_k=3, stop_atr=2.0, rsi_max=80, rebalance_days=5, full_rebalance=False,
                           regime="none", regime_off="cash")

    def test_holds_at_most_top_k_and_trades_next_open(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        rot = self.contest.Rotation(fr, "US", CFG, self.params)
        d = rot.dates
        curve, st = rot.run(d[300], d[340], 100_000)
        self.assertEqual(curve.iloc[0], 100_000)  # 첫날은 종가에 판단만, 체결은 다음 날
        self.assertGreater(st["orders"], 0)
        self.assertLessEqual(st["symbols"], st["orders"])
        self.assertLessEqual(len(rot.targets(d[320])), 3)

    def test_daily_full_rebalance_trades_more(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        d = sorted(self.raw["S1"].index)
        weekly = self.contest.Rotation(fr, "US", CFG, self.params).run(d[300], d[325], 100_000)[1]
        daily = self.contest.Rotation(fr, "US", CFG, {**self.params, "rebalance_days": 1, "full_rebalance": True}
                                      ).run(d[300], d[325], 100_000)[1]
        self.assertGreater(daily["amount"], weekly["amount"])
        self.assertGreaterEqual(daily["days"], weekly["days"])

    def test_regime_cash_sells_everything_on_bad_days(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        bars = self.contest.build_bars(fr)
        d = sorted(bars)
        bad = {x: {"breadth": 0.1, "index": 1.0, "index_up": False} for x in d}
        good = {x: {"breadth": 0.9, "index": 1.0, "index_up": True} for x in d}
        p = {**self.params, "regime": "breadth50", "regime_off": "cash"}
        rot = self.contest.Rotation(fr, "US", CFG, p, bars, bad)
        curve, st = rot.run(d[300], d[340], 100_000)
        self.assertEqual(st["orders"], 0)
        self.assertEqual(curve.iloc[-1], 100_000)
        on = self.contest.Rotation(fr, "US", CFG, p, bars, good)
        self.assertEqual(on.targets(d[320]), on._ranked(d[320]))
        top2 = self.contest.Rotation(fr, "US", CFG, {**p, "regime_off": "top2"}, bars, bad)
        self.assertEqual(top2.targets(d[320]), top2._ranked(d[320])[:2])

    def test_regime_hold_top1_and_drop(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        bars = self.contest.build_bars(fr)
        d = sorted(bars)
        bad = {x: {"breadth": 0.1, "index": 1.0, "index_up": False, "ret": -0.03} for x in d}
        p = {**self.params, "stop_atr": 0.0, "regime": "drop2"}
        self.assertFalse(self.contest.Rotation(fr, "US", CFG, p, bars, bad).risk_on(d[320]))
        calm = {x: {**bad[x], "ret": -0.01} for x in d}
        self.assertTrue(self.contest.Rotation(fr, "US", CFG, p, bars, calm).risk_on(d[320]))
        top1 = self.contest.Rotation(fr, "US", CFG, {**p, "regime_off": "top1"}, bars, bad)
        self.assertEqual(top1.targets(d[320]), top1._ranked(d[320])[:1])
        hold = self.contest.Rotation(fr, "US", CFG, {**p, "regime_off": "hold"}, bars, bad)
        self.assertIsNone(hold.targets(d[320]))
        curve, st = hold.run(d[300], d[340], 100_000)  # 매일 나쁜 날이면 한 번도 거래 안 함
        self.assertEqual(st["orders"], 0)
        r = self.contest.build_regime(bars)
        x0, x1 = d[100], d[101]
        self.assertAlmostEqual(r[x1]["ret"], r[x1]["index"] / r[x0]["index"] - 1)

    def test_regime_values_use_only_past(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        r1 = self.contest.build_regime(self.contest.build_bars(fr))
        cut = {s: f.iloc[:350] for s, f in fr.items()}
        r2 = self.contest.build_regime(self.contest.build_bars(cut))
        for d in list(r2)[-20:]:
            self.assertEqual(r1[d], r2[d])

    def test_adaptive_uses_only_finished_windows(self):
        rule = self.contest.RULES["US"]
        ok = {"amount": 1, "days": 9, "symbols": 9, "orders": 9}
        wins = [(i, i + 10) for i in range(0, 60, 5)]       # 겹치는 구간들
        good_early = ([0.1] * 6 + [-0.1] * 6, [ok] * 12)    # 앞쪽에서만 좋은 조합
        good_late = ([-0.1] * 6 + [0.1] * 6, [ok] * 12)
        lines = self.contest.adaptive_report(wins, {"A": good_early, "B": good_late}, [0.0] * 12, rule, 1.0, 6,
                                             recents=(2,))
        text = "\n".join(lines)
        self.assertIn("최근 2구간", text)

    def test_market_state_measures(self):
        import pandas as pd
        dates = pd.bdate_range("2024-01-01", periods=80)
        trend = {d: {"index": 1.01 ** i, "breadth": 0.8} for i, d in enumerate(dates)}
        chop = {d: {"index": 1.03 if i % 2 else 1.0, "breadth": 0.5} for i, d in enumerate(dates)}
        st_t = self.contest.build_states(trend)[dates[-1]]
        st_c = self.contest.build_states(chop)[dates[-1]]
        self.assertAlmostEqual(st_t["er"], 1.0)
        self.assertLess(st_c["er"], 0.1)
        self.assertGreater(st_c["flips"], 0.9)
        self.assertGreater(st_c["vol"], st_t["vol"])

    def test_skip_month_momentum_column(self):
        fr = self.contest.prepare(self.raw, CFG, [60], [20])
        f = fr["S1"]
        i = 300
        expect = f["Close"].iloc[i - 20] / f["Close"].iloc[i - 80] - 1
        self.assertAlmostEqual(f["mom_60_20"].iloc[i], expect)
        self.assertEqual(self.contest.score_key({"lookback": 60, "skip": 20}), "mom_60_20")
        self.assertEqual(self.contest.score_key({"lookback": 20, "skip": 0}), "ret_20")
        p = {**self.params, "lookback": 60, "skip": 20, "trend_filter": False, "rsi_max": 101}
        rot = self.contest.Rotation(fr, "US", CFG, p)
        d = rot.dates[300]
        ranked = sorted(fr, key=lambda s: -fr[s].loc[d, "mom_60_20"])[:3]
        self.assertEqual(rot.targets(d), ranked)

    def test_take_profit_and_stop_loss(self):
        dates = pd.bdate_range("2024-01-01", periods=10)
        base = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1.0,
                             "ema_fast": 90.0, "ema_slow": 80.0, "rsi": 50.0, "atr": 1.0, "ret_20": 0.5}, index=dates)
        cfg = {**CFG, "costs": {**CFG["costs"], "slippage": 0.0, "US": {"buy_fee": 0, "sell_fee": 0, "sell_tax": 0}}}
        p = {**self.params, "top_k": 1, "rebalance_days": 100, "stop_atr": 0.0, "rsi_max": 101,
             "tp_pct": 0.2, "sl_pct": 0.1}
        up = base.copy()
        up.loc[dates[3], "High"] = 125.0            # +20% 닿음 → 120 에 익절
        rot = self.contest.Rotation({"A": up}, "US", cfg, p)
        curve, st = rot.run(dates[0], dates[-1], 1000.0)
        self.assertAlmostEqual(curve.iloc[-1], 1000.0 + 9 * 20)  # 9주(1% 여유 두고 매수) × +20
        self.assertEqual(st["orders"], 2)
        gap = base.copy()
        gap.loc[dates[3], ["Open", "High", "Low"]] = [85.0, 86.0, 84.0]  # 시가부터 −15% → 시가 85 에 손절
        curve, _ = self.contest.Rotation({"A": gap}, "US", cfg, p).run(dates[0], dates[-1], 1000.0)
        self.assertAlmostEqual(curve.iloc[-1], 1000.0 - 9 * 15)

    def test_volume_factor_columns(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        f, i = fr["S1"], 300
        v, c = f["Volume"], f["Close"]
        self.assertAlmostEqual(f["vr_20_60"].iloc[i], v.iloc[i - 19:i + 1].mean() / v.iloc[i - 59:i + 1].mean())
        self.assertAlmostEqual(f["vr_5_60"].iloc[i], v.iloc[i - 4:i + 1].mean() / v.iloc[i - 59:i + 1].mean())
        up = (c.diff() > 0).iloc[i - 19:i + 1]
        self.assertAlmostEqual(f["upvol_20"].iloc[i], v.iloc[i - 19:i + 1][up].sum() / v.iloc[i - 19:i + 1].sum())
        self.assertAlmostEqual(f["turnover_20"].iloc[i], (c * v).iloc[i - 19:i + 1].mean())
        self.assertAlmostEqual(f["vol_spike"].iloc[i], v.iloc[i] / v.iloc[i - 59:i + 1].mean())
        # 미래 거래량을 바꿔도 그 전 값은 그대로
        raw2 = {s: x.copy() for s, x in self.raw.items()}
        raw2["S1"].loc[raw2["S1"].index[i + 1]:, "Volume"] *= 10
        f2 = self.contest.prepare(raw2, CFG, [20])["S1"]
        cols = ["vr_20_60", "vr_5_60", "upvol_20", "turnover_20", "vol_spike"]
        pd.testing.assert_frame_equal(f[cols].iloc[:i + 1], f2[cols].iloc[:i + 1])
        # 거래량 0 (거래 정지) 이면 NaN → 조건 통과 못 함
        zero = {"S1": self.raw["S1"].assign(Volume=0.0)}
        z = self.contest.prepare(zero, CFG, [20])["S1"].iloc[i]
        self.assertFalse(self.contest.volume_ok(z.to_dict(), {"vr20_min": 1.0}))

    def test_volume_filters_and_rank(self):
        fr = self.contest.prepare(self.raw, CFG, [20])
        p = {**self.contest.VOL_BASE, "rsi_max": 101, "trend_filter": False}
        base = self.contest.Rotation(fr, "US", CFG, p)
        d = base.dates[320]
        self.assertEqual(base.targets(d), sorted(fr, key=lambda s: -fr[s].loc[d, "ret_20"])[:3])
        none = self.contest.Rotation(fr, "US", CFG, {**p, "vr20_min": 100.0})
        self.assertEqual(none.targets(d), [])
        quiet = self.contest.Rotation(fr, "US", CFG, {**p, "vr20_max": 1.0})
        self.assertTrue(all(fr[s].loc[d, "vr_20_60"] <= 1.0 for s in quiet.targets(d)))
        rot = self.contest.Rotation(fr, "US", CFG, {**p, "rank": "ret_x_vr"})
        def sc(s):
            r, vr = fr[s].loc[d, "ret_20"], fr[s].loc[d, "vr_20_60"]
            return r * vr if r >= 0 else r / vr
        self.assertEqual(rot.targets(d), sorted(fr, key=lambda s: -sc(s))[:3])
        self.assertEqual(self.contest.vol_name(p), "기준(거래량 조건 없음)")
        self.assertEqual(self.contest.vol_name({**p, "turnover_min": 5e9}), "turnover_min=5e+09")
        _, st = base.run(base.dates[300], base.dates[330], 100_000)
        self.assertGreater(st["held"], 0)

    def test_crash_slot_buys_close_and_sells_next_open(self):
        dates = pd.bdate_range("2024-01-01", periods=10)

        def frame(ret20, closes=None, opens=None):
            f = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 89.0, "Close": 100.0, "Volume": 1.0,
                              "ema_fast": 90.0, "ema_slow": 80.0, "rsi": 50.0, "atr": 1.0, "ret_20": ret20,
                              "ret_1": 0.0}, index=dates)
            for i, v in (closes or {}).items():
                f.iloc[i, f.columns.get_loc("Close")] = v
            for i, v in (opens or {}).items():
                f.iloc[i, f.columns.get_loc("Open")] = v
            f["ret_1"] = f["Close"].pct_change().fillna(0.0)
            return f
        # A: 3일째 종가 90 (−10%), 4일째부터 95 / B: 모멘텀 1위, 가격 그대로 / C: 그대로
        a = frame(0.1, {3: 90.0, **{i: 95.0 for i in range(4, 10)}}, {i: 95.0 for i in range(4, 10)})
        frames = {"A": a, "B": frame(0.5), "C": frame(0.2)}
        cfg = {**CFG, "costs": {**CFG["costs"], "slippage": 0.0, "US": {"buy_fee": 0, "sell_fee": 0, "sell_tax": 0}}}
        p = {**self.contest.CRASH_BASE, "top_k": 1, "crash_n": 3, "crash_hold": 1, "crash_exit": "open"}
        rot = self.contest.Rotation(frames, "US", cfg, p)
        self.assertEqual(rot.crash_pick(dates[3]), "A")   # 3종목 평균 −3.3% ≤ −2%, A −10%
        self.assertIsNone(rot.crash_pick(dates[2]))
        curve, st = rot.run(dates[0], dates[-1], 1000.0)
        # B 9주 → 3일째 종가에 B 팔고 A 11주(90) → 4일째 시가 95 매도 → 5일째 B 10주 다시
        self.assertAlmostEqual(curve.iloc[-1], 1055.0)
        self.assertEqual(st["orders"], 5)
        off = self.contest.Rotation(frames, "US", cfg, {**p, "crash_hold": 0}).run(dates[0], dates[-1], 1000.0)
        self.assertAlmostEqual(off[0].iloc[-1], 1000.0)

    def test_rules(self):
        rule = self.contest.RULES["KR"]
        good = {"amount": 5e8, "days": 5, "symbols": 5, "orders": 5}
        self.assertTrue(self.contest.passes(good, rule))
        self.assertFalse(self.contest.passes({**good, "amount": 4.9e8}, rule))
        self.assertFalse(self.contest.passes({**good, "symbols": 4}, rule))

    def test_no_lookahead(self):
        changed = {s: f.copy() for s, f in self.raw.items()}
        cut = changed["S1"].index[350]
        for f in changed.values():
            f.loc[cut:, ["Open", "High", "Low", "Close"]] *= 2
        res = []
        for raw in (self.raw, changed):
            rot = self.contest.Rotation(self.contest.prepare(raw, CFG, [20]), "US", CFG, self.params)
            d = rot.dates
            res.append(rot.run(d[300], d[349], 100_000))
        pd.testing.assert_series_equal(res[0][0], res[1][0])


class MetricsTest(unittest.TestCase):
    def test_known_values(self):
        idx = pd.date_range("2020-01-01", periods=3, freq="365D")
        eq = pd.Series([100.0, 50.0, 200.0], index=idx)
        m = metrics(eq, pd.DataFrame({"pnl": [1, -1, 2, 3]}))
        self.assertAlmostEqual(m["총수익률"], 1.0)
        self.assertAlmostEqual(m["MDD"], -0.5)
        self.assertAlmostEqual(m["승률"], 0.75)
        self.assertAlmostEqual(m["CAGR"], 2 ** (365.25 / 730) - 1, places=6)

    def test_buy_and_hold(self):
        dates = pd.bdate_range("2024-01-01", periods=5)
        f = pd.DataFrame({"Open": [100.0] * 5, "Close": [100, 110, 120, 130, 200.0]}, index=dates)
        bh = buy_and_hold({"A": f}, "US", CFG, dates[0], dates[-1], 10_000)
        self.assertAlmostEqual(bh.iloc[0], 10_000 - 99 * 100.1 * 0.0025 + (100 - 100.1) * 99, places=6)
        self.assertGreater(bh.iloc[-1], 19_000)


class EndToEndTest(unittest.TestCase):
    def test_run_both_markets(self):
        raw = {"005930": random_ohlcv(1500, seed=1) * [1000, 1000, 1000, 1000, 1],
               "AAPL": random_ohlcv(1500, seed=2, drift=0.0008)}
        frames = prepare(raw, CFG)
        start, end = raw["AAPL"].index[150], raw["AAPL"].index[-1]
        res = run(CFG, frames, start, end)
        names = [r["구분"].strip() for r in res["rows"]]
        self.assertEqual(names, ["KR 포트폴리오", "005930", "US 포트폴리오", "AAPL"])
        self.assertEqual(set(res["equities"]), {"KR", "US"})
        self.assertAlmostEqual(res["equities"]["KR"]["equity"].iloc[0], CFG["capital"]["KR"], delta=CFG["capital"]["KR"] * 0.01)


if __name__ == "__main__":
    unittest.main()
