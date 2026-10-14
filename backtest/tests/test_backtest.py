"""엔진 검증 (네트워크 없이). 실행: python -m pytest tests  또는  python -m unittest discover -s tests"""

import os
import sys
import unittest

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
    return df


def cfg_with(**risk):
    c = yaml.safe_load(yaml.safe_dump(CFG))
    c["risk"].update(risk)
    return c


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

    @unittest.skipIf(ta is None, "pandas-ta 없음")
    def test_builtin_matches_pandas_ta(self):
        df = random_ohlcv(500, seed=3)
        a = add_indicators(df, CFG["indicators"], True).iloc[150:]
        b = add_indicators(df, CFG["indicators"], False).iloc[150:]
        for c in ["ema_fast", "ema_slow", "macd", "macd_signal", "rsi", "atr", "vol_ma", "adx"]:
            self.assertLess(float((a[c] - b[c]).abs().max()), 0.05, c)

    def test_signal_rules(self):
        df = random_ohlcv(600, seed=5)
        d = add_signals(add_indicators(df, CFG["indicators"]), CFG["entry"], CFG["exit"])
        b = d[d["buy_signal"]]
        self.assertGreater(len(b), 0)
        self.assertTrue((b["Close"] > b["ema_slow"]).all())
        self.assertTrue((b["adx"] > 25).all())
        self.assertTrue((b["rsi"] < 70).all())
        self.assertTrue((b["Volume"] > b["vol_ma"]).all())
        prev = d.shift(1).loc[b.index]
        self.assertTrue(((prev["macd"] <= prev["macd_signal"]) & (b["macd"] > b["macd_signal"])).all())


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
