"""가짜 API 응답으로 매매 프로그램을 검증해요 (네트워크 안 씀).

  python3 -m unittest discover -s trading/tests -t .
"""

import json
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

from datetime import datetime, timezone

from trading import auto, cli, strategy
from trading.kis import Kis, KisError
from trading.planner import make_plan


def rising(n=80, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


class FakeTransport:
    """KIS 서버 흉내. 경로별 응답을 돌려주고 요청을 기록해요."""

    def __init__(self, routes=None):
        self.calls = []
        self.routes = routes or {}

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append({"method": method, "path": parsed.path, "headers": headers,
                           "query": query, "body": body, "host": parsed.netloc})
        if parsed.path == "/oauth2/tokenP":
            return {}, {"access_token": "tok", "access_token_token_expired": "2099-01-01 00:00:00"}
        handler = self.routes.get(parsed.path)
        if handler is None:
            return {}, {"rt_cd": "0", "output": {}}
        return {}, handler(query, body)


def client(routes=None, env="demo"):
    t = FakeTransport(routes)
    return Kis("key", "secret", "50123456-01", env=env, min_interval=0, transport=t), t


class StrategyTest(unittest.TestCase):
    def test_buys_in_uptrend_near_short_ma(self):
        closes = rising()
        action, _ = strategy.decide(closes, closes[-1] + 0.5)
        self.assertEqual(action, "buy")

    def test_waits_when_too_far_above_short_ma(self):
        closes = rising()
        action, reason = strategy.decide(closes, closes[-1] * 1.3)
        self.assertIsNone(action)
        self.assertIn("대기", reason)

    def test_no_buy_in_downtrend(self):
        closes = rising(step=-1.0, start=200)
        self.assertIsNone(strategy.decide(closes, closes[-1])[0])

    def test_stop_loss_and_take_profit(self):
        closes = rising()
        self.assertEqual(strategy.decide(closes, 90, {"qty": 1, "avg_price": 100})[0], "sell")
        self.assertEqual(strategy.decide(closes, 179, {"qty": 1, "avg_price": 140})[0], "sell")
        self.assertIsNone(strategy.decide(closes, 179, {"qty": 1, "avg_price": 170})[0])

    def test_trend_break_sells(self):
        closes = rising()
        action, reason = strategy.decide(closes, 120, {"qty": 1, "avg_price": 125})
        self.assertEqual(action, "sell")
        self.assertIn("추세 이탈", reason)

    def test_not_enough_data(self):
        self.assertIsNone(strategy.decide([1, 2, 3], 4)[0])


class KisClientTest(unittest.TestCase):
    def test_demo_uses_mock_domain_and_v_tr_ids(self):
        kis, t = client({"/uapi/domestic-stock/v1/trading/order-cash": lambda q, b: {"rt_cd": "0", "output": {"ODNO": "1"}}})
        kis.kr_order("buy", "005930", 3, 70000)
        call = t.calls[-1]
        self.assertEqual(call["host"], "openapivts.koreainvestment.com:29443")
        self.assertEqual(call["headers"]["tr_id"], "VTTC0012U")
        self.assertEqual(call["body"]["ORD_QTY"], "3")
        self.assertEqual(call["body"]["ORD_UNPR"], "70000")
        self.assertEqual(call["body"]["ORD_DVSN"], "00")

    def test_real_tr_ids(self):
        kis, t = client(env="real")
        kis.kr_order("sell", "005930", 1, 70000)
        self.assertEqual(t.calls[-1]["headers"]["tr_id"], "TTTC0011U")
        self.assertEqual(t.calls[-1]["host"], "openapi.koreainvestment.com:9443")
        kis.us_order("sell", "AAPL", "NASD", 1, 200)
        self.assertEqual(t.calls[-1]["headers"]["tr_id"], "TTTT1006U")

    def test_us_order_demo_tr_ids_and_limit_price(self):
        kis, t = client()
        kis.us_order("buy", "AAPL", "NASD", 2, 201.456)
        call = t.calls[-1]
        self.assertEqual(call["headers"]["tr_id"], "VTTT1002U")
        self.assertEqual(call["body"]["OVRS_ORD_UNPR"], "201.46")
        self.assertEqual(call["body"]["ORD_DVSN"], "00")
        kis.us_order("sell", "AAPL", "NASD", 2, 201)
        self.assertEqual(t.calls[-1]["headers"]["tr_id"], "VTTT1001U")

    def test_us_price_uses_quote_exchange_code(self):
        kis, t = client({"/uapi/overseas-price/v1/quotations/price": lambda q, b: {"rt_cd": "0", "output": {"last": "123.4"}}})
        self.assertEqual(kis.us_price("SPY", "AMEX")["price"], 123.4)
        self.assertEqual(t.calls[-1]["query"]["EXCD"], "AMS")

    def test_error_raises(self):
        kis, _ = client({"/uapi/domestic-stock/v1/quotations/inquire-price": lambda q, b: {"rt_cd": "1", "msg_cd": "E1", "msg1": "잘못된 종목"}})
        with self.assertRaises(KisError):
            kis.kr_price("999999")

    def test_rate_limit_retried(self):
        replies = iter([{"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
                        {"rt_cd": "0", "output": {"stck_prpr": "70000"}}])
        kis, t = client({"/uapi/domestic-stock/v1/quotations/inquire-price": lambda q, b: next(replies)})
        waits = []
        kis._sleep = waits.append
        self.assertEqual(kis.kr_price("005930")["price"], 70000)
        self.assertEqual(len([c for c in t.calls if c["path"].endswith("inquire-price")]), 2)
        self.assertIn(1.0, waits)

    def test_ledger_rate_limit_retried(self):
        replies = iter([{"rt_cd": "1", "msg_cd": "EGW00215", "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다."},
                        {"rt_cd": "0", "output1": [], "output2": [{"dnca_tot_amt": "1", "tot_evlu_amt": "1"}]}])
        kis, t = client({"/uapi/domestic-stock/v1/trading/inquire-balance": lambda q, b: next(replies)})
        kis._sleep = lambda s: None
        self.assertEqual(kis.kr_balance()["total"], 1)

    def test_rate_limit_gives_up(self):
        kis, t = client({"/uapi/domestic-stock/v1/quotations/inquire-price":
                         lambda q, b: {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초과"}})
        kis._sleep = lambda s: None
        with self.assertRaises(KisError):
            kis.kr_price("005930")
        self.assertEqual(len([c for c in t.calls if c["path"].endswith("inquire-price")]), 6)

    def test_token_cached_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            t = FakeTransport()
            Kis("key", "secret", "50123456-01", cache_dir=d, min_interval=0, transport=t).token()
            path = os.path.join(d, "token-demo.json")
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            t2 = FakeTransport()
            Kis("key", "secret", "50123456-01", cache_dir=d, min_interval=0, transport=t2).token()
            self.assertEqual(t2.calls, [])  # 저장된 토큰을 다시 써요

    def test_daily_sorted_oldest_first(self):
        rows = [{"stck_bsop_date": "20261002", "stck_clpr": "3"}, {"stck_bsop_date": "20261001", "stck_clpr": "2"}]
        kis, _ = client({"/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": lambda q, b: {"rt_cd": "0", "output2": rows}})
        self.assertEqual(kis.kr_daily("005930"), [2.0, 3.0])


CFG = {
    "domestic": {"budget_krw": 5_000_000, "max_positions": 2,
                 "watchlist": [{"code": "000001", "name": "상승"}, {"code": "000002", "name": "하락"},
                               {"code": "000003", "name": "상승2"}]},
    "us": {"budget_usd": 3000, "max_positions": 3, "watchlist": [{"symbol": "UP", "exchange": "NASD"}]},
    "strategy": {},
    "limits": {"max_order_krw": 1_500_000, "max_order_usd": 500, "max_orders_per_day": 10},
}


def fake_market(held_kr=None, held_us=None):
    daily = {"000001": rising(), "000002": rising(step=-1, start=200), "000003": rising(), "000009": rising(step=-1, start=200)}

    def kr_daily(q, b):
        return {"rt_cd": "0", "output2": [{"stck_bsop_date": f"2026{i:04d}", "stck_clpr": str(c)}
                                          for i, c in enumerate(daily[q["FID_INPUT_ISCD"]], 1)]}

    def kr_price(q, b):
        return {"rt_cd": "0", "output": {"stck_prpr": str(daily[q["FID_INPUT_ISCD"]][-1]), "hts_kor_isnm": ""}}

    def kr_balance(q, b):
        return {"rt_cd": "0", "output1": held_kr or [], "output2": [{"dnca_tot_amt": "5000000", "tot_evlu_amt": "5000000"}]}

    def us_daily(q, b):
        return {"rt_cd": "0", "output2": [{"xymd": f"2026{i:04d}", "clos": str(c)} for i, c in enumerate(rising(), 1)]}

    return {
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": kr_daily,
        "/uapi/domestic-stock/v1/quotations/inquire-price": kr_price,
        "/uapi/domestic-stock/v1/trading/inquire-balance": kr_balance,
        "/uapi/domestic-stock/v1/trading/inquire-psbl-order": lambda q, b: {"rt_cd": "0", "output": {"nrcvb_buy_amt": "5000000"}},
        "/uapi/overseas-price/v1/quotations/price": lambda q, b: {"rt_cd": "0", "output": {"last": str(rising()[-1])}},
        "/uapi/overseas-price/v1/quotations/dailyprice": us_daily,
        "/uapi/overseas-stock/v1/trading/inquire-balance": lambda q, b: {"rt_cd": "0", "output1": held_us or []},
        "/uapi/overseas-stock/v1/trading/inquire-psamount": lambda q, b: {"rt_cd": "0", "output": {"ovrs_ord_psbl_amt": "10000", "exrt": "1400"}},
    }


class PlannerTest(unittest.TestCase):
    def test_plan_sizes_and_limits(self):
        kis, _ = client(fake_market())
        orders, notes = make_plan(kis, CFG)
        kr = [o for o in orders if o["market"] == "kr"]
        # 상승 두 종목 매수, 하락 종목은 건너뜀. 종목당 예산 250만 원이지만 1회 한도 150만 원.
        self.assertEqual([o["symbol"] for o in kr], ["000001", "000003"])
        for o in kr:
            self.assertLessEqual(o["qty"] * o["price"], 1_500_000)
        us = [o for o in orders if o["market"] == "us"]
        self.assertEqual(len(us), 1)
        self.assertLessEqual(us[0]["qty"] * us[0]["price"], 500)
        self.assertTrue(any("하락" in n for n in notes))

    def test_max_positions_counts_existing_holdings(self):
        held = [{"pdno": "000009", "prdt_name": "보유", "hldg_qty": "10", "pchs_avg_pric": "100", "prpr": "121"}]
        kis, _ = client(fake_market(held_kr=held))
        orders, _ = make_plan(kis, CFG, markets=("kr",))
        buys = [o for o in orders if o["side"] == "buy"]
        sells = [o for o in orders if o["side"] == "sell"]
        self.assertEqual(len(buys), 1)  # 보유 1 + 매수 1 = 최대 2
        # 관심 목록 밖 보유 종목도 매도 규칙을 봐요 (하락 추세 → 추세 이탈 매도), 매도가 먼저
        self.assertEqual(sells[0]["symbol"], "000009")
        self.assertEqual(orders[0]["side"], "sell")


class TradeCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(cli, "HERE", self.tmp.name)
        self.patch.start()
        # log_path 의 기본값이 HERE 를 쓰므로 함수 자체를 감싸요
        self.log_patch = mock.patch.object(cli, "log_path",
                                           lambda day=None: os.path.join(self.tmp.name, "logs", "today.jsonl"))
        self.log_patch.start()

    def tearDown(self):
        self.patch.stop()
        self.log_patch.stop()
        self.tmp.cleanup()

    def sent_orders(self, t):
        return [c for c in t.calls if c["method"] == "POST" and c["path"] != "/oauth2/tokenP"]

    def test_only_confirmed_orders_are_sent(self):
        kis, t = client(fake_market())
        answers = iter(["y", "n", "y"])
        with mock.patch("builtins.print"):
            cli.cmd_trade(kis, CFG, ("kr", "us"), ask=lambda _: next(answers))
        self.assertEqual(len(self.sent_orders(t)), 2)
        with open(cli.log_path()) as f:
            events = [json.loads(line)["event"] for line in f]
        self.assertEqual(events.count("order_sent"), 2)
        self.assertEqual(events.count("order_skipped"), 1)

    def test_daily_order_limit(self):
        kis, t = client(fake_market())
        cfg = {**CFG, "limits": {**CFG["limits"], "max_orders_per_day": 1}}
        with mock.patch("builtins.print"):
            cli.cmd_trade(kis, cfg, ("kr", "us"), ask=lambda _: "y")
        self.assertEqual(len(self.sent_orders(t)), 1)

    def test_real_env_requires_typed_confirmation(self):
        kis, t = client(fake_market(), env="real")
        with mock.patch("builtins.print"):
            cli.cmd_trade(kis, CFG, ("kr",), ask=lambda _: "y")
        self.assertEqual(self.sent_orders(t), [])
        self.assertEqual(t.calls, [])  # 확인 전에는 아무 요청도 안 해요


class ContestModeTest(unittest.TestCase):
    """대회 모드: 국내 상위 5종목 2거래일마다 교체, 미국 하루 1종목씩 5종목까지."""

    def setUp(self):
        from trading import contest
        self.contest = contest
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []
        self.no_sleep = mock.patch.object(auto.time, "sleep", lambda s: None)
        self.no_sleep.start()

    def tearDown(self):
        self.no_sleep.stop()
        self.tmp.cleanup()

    def market(self, strengths, held_kr=None, held_us=None):
        """strengths: {코드: 하루 상승률}. 클수록 20일 수익률이 높아요."""
        def series(step):
            return [100 * (1 + step) ** i for i in range(90)]

        def kr_daily(q, b):
            closes = series(strengths[q["FID_INPUT_ISCD"]])
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": f"2026{i:04d}", "stck_clpr": str(c)}
                                              for i, c in enumerate(closes, 1)]}
        held_kr = held_kr or []
        return {
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice": kr_daily,
            "/uapi/domestic-stock/v1/quotations/inquire-price": lambda q, b: {"rt_cd": "0", "output": {"stck_prpr": "10000"}},
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order": lambda q, b: {"rt_cd": "0", "output": {"nrcvb_buy_amt": "100000000"}},
            "/uapi/domestic-stock/v1/trading/inquire-balance": lambda q, b: {
                "rt_cd": "0", "output1": held_kr, "output2": [{"dnca_tot_amt": "100000000", "tot_evlu_amt": "100000000"}]},
            "/uapi/overseas-stock/v1/trading/inquire-balance": lambda q, b: {"rt_cd": "0", "output1": held_us or []},
            "/uapi/overseas-price/v1/quotations/dailyprice": lambda q, b: {"rt_cd": "0", "output2": [
                {"xymd": f"2026{i:04d}", "clos": str(c)} for i, c in enumerate(series(0.004 if q["SYMB"] == "UP" else 0.001), 1)]},
            "/uapi/overseas-price/v1/quotations/price": lambda q, b: {"rt_cd": "0", "output": {"last": "100"}},
            "/uapi/overseas-stock/v1/trading/inquire-psamount": lambda q, b: {"rt_cd": "0", "output": {"ovrs_ord_psbl_amt": "50000"}},
        }

    def cycle(self, kis, when, universe, cfg=None, mode=None):
        state = self.contest.State(os.path.join(self.tmp.name, "state.json"))
        if mode:
            state.d["kr_mode"] = mode
        cfg = cfg or {"us": {"watchlist": [{"symbol": "UP", "exchange": "NASD"}, {"symbol": "SLOW", "exchange": "NYSE"}]},
                      "auto": {}}
        return auto.run_contest_cycle(kis, cfg, state, when, self.logs.append, out=lambda *_: None, universe=universe), state

    def test_indicators_and_score(self):
        rising = [100 * 1.01 ** i for i in range(90)]
        self.assertAlmostEqual(self.contest.score(rising, 20, 101), 1.01 ** 20 - 1)
        self.assertIsNone(self.contest.score(rising, 20, 80))          # RSI 100: 과열이라 제외
        self.assertIsNone(self.contest.score(rising[::-1], 20, 101))   # 하락 추세
        self.assertIsNone(self.contest.score(rising[:50], 20, 101))    # 데이터 부족
        rows = [("20261014", 1.0), ("20261015", 2.0)]
        from datetime import date as _d
        self.assertEqual(self.contest.closes_until_yesterday(rows, _d(2026, 10, 15)), [1.0])

    def test_kr_rebalance_buys_top5_then_waits_two_business_days(self):
        # 상승률이 다른 7종목 (RSI 제한 80 을 넘지 않게 등락을 섞어요)
        strengths = {f"00000{i}": 0.002 * i for i in range(1, 8)}
        universe = [(c, f"종목{c[-1]}") for c in strengths]
        kis, t = client(self.market(strengths))
        with mock.patch.object(self.contest, "score", lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1):
            n, state = self.cycle(kis, auto_utc(2026, 10, 15, 0, 30), universe, mode="stable")   # 목 09:30 KST
        buys = [c["body"] for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual(n, 5)
        self.assertEqual(sorted(b["PDNO"] for b in buys), ["000003", "000004", "000005", "000006", "000007"])
        self.assertTrue(all(b["ORD_DVSN"] == "00" for b in buys))             # 지정가
        self.assertTrue(all(b["ORD_UNPR"] == "10100" for b in buys))          # 현재가 10000 + 1%
        self.assertEqual(int(buys[0]["ORD_QTY"]), 1960)                        # 1억/5*0.99/10100
        self.assertEqual(state.d["kr_rebalances"], ["2026-10-15"])
        self.assertEqual(len(state.d["symbols"]), 5)
        self.assertTrue(self.contest.kr_due(state, "2026-10-19") and not self.contest.kr_due(state, "2026-10-16"))

    def test_market_filter(self):
        up = [("2026%04d" % i, 100 * 1.01 ** i) for i in range(1, 40)]
        down = [("2026%04d" % i, 100 * 0.99 ** i) for i in range(1, 40)]
        self.assertTrue(self.contest.market_risk_on(self.contest.equal_weight_index({"A": up, "B": up}), 20))
        self.assertFalse(self.contest.market_risk_on(self.contest.equal_weight_index({"A": down, "B": down}), 20))
        idx = self.contest.equal_weight_index({"A": up[:3], "B": down[:3]})
        self.assertAlmostEqual(idx[0], 1.0)  # +1% 와 −1% 평균

    def test_kr_bad_market_keeps_top2(self):
        strengths = {f"00000{i}": 0.002 * i for i in range(1, 8)}
        universe = [(c, c) for c in strengths]
        kis, t = client(self.market(strengths))
        with mock.patch.object(self.contest, "score", lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1), \
                mock.patch.object(self.contest, "market_risk_on", lambda idx, n: False):
            n, _ = self.cycle(kis, auto_utc(2026, 10, 15, 0, 30), universe, mode="stable")
        buys = [c["body"]["PDNO"] for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual(sorted(buys), ["000006", "000007"])
        self.assertEqual(n, 2)

    def test_aggressive_default_buys_top2_with_half_each(self):
        strengths = {f"00000{i}": 0.002 * i for i in range(1, 8)}
        universe = [(c, c) for c in strengths]
        kis, t = client(self.market(strengths))
        with mock.patch.object(self.contest, "score", lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1):
            n, state = self.cycle(kis, auto_utc(2026, 10, 15, 0, 30), universe)
        buys = [c["body"] for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual(sorted(b["PDNO"] for b in buys), ["000006", "000007"])
        self.assertEqual(int(buys[0]["ORD_QTY"]), 4900)   # 1억/2*0.99/10100
        self.assertEqual(state.d["start_total"], 100_000_000)

    def test_switch_to_stable_after_gain(self):
        state = self.contest.State(os.path.join(self.tmp.name, "s.json"))
        say = []
        self.contest.maybe_switch_to_stable(state, 100_000_000, 0.2, say.append)
        self.assertEqual(state.d.get("kr_mode", "aggressive"), "aggressive")
        self.contest.maybe_switch_to_stable(state, 119_000_000, 0.2, say.append)
        self.assertEqual(state.d.get("kr_mode", "aggressive"), "aggressive")
        gain = self.contest.maybe_switch_to_stable(state, 121_000_000, 0.2, say.append)
        self.assertAlmostEqual(gain, 0.21)
        self.assertEqual(state.d["kr_mode"], "stable")
        self.contest.maybe_switch_to_stable(state, 90_000_000, 0.2, say.append)
        self.assertEqual(state.d["kr_mode"], "stable")   # 한 번 바꾸면 그대로
        self.assertEqual(self.contest.kr_params(state)["top_k"], 5)

    def test_ranking_reused_after_order_step_failure(self):
        strengths = {f"00000{i}": 0.002 * i for i in range(1, 4)}
        universe = [(c, c) for c in strengths]
        routes = self.market(strengths)
        bal = routes["/uapi/domestic-stock/v1/trading/inquire-balance"]
        fail = {"n": 0}

        def flaky(q, b):
            fail["n"] += 1
            if fail["n"] == 2:  # 순위 계산 뒤 주문 단계의 잔고 조회에서 실패
                raise OSError("network")
            return bal(q, b)
        routes["/uapi/domestic-stock/v1/trading/inquire-balance"] = flaky
        kis, t = client(routes)
        state = self.contest.State(os.path.join(self.tmp.name, "r.json"))
        sc = lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1
        cfg = {"us": {"watchlist": []}, "auto": {}}
        with mock.patch.object(self.contest, "score", sc), mock.patch.object(auto.time, "sleep", lambda s: None):
            with self.assertRaises(OSError):
                auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 0, 5), self.logs.append, lambda *_: None, universe)
            daily_before = len([c for c in t.calls if "itemchartprice" in c["path"]])
            n = auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 0, 20), self.logs.append, lambda *_: None, universe)
        self.assertEqual(len([c for c in t.calls if "itemchartprice" in c["path"]]), daily_before)  # 순위 다시 안 함
        self.assertEqual(n, 2)

    def test_tick_and_limit(self):
        c = self.contest
        self.assertEqual([c.kr_tick(x) for x in (1999, 4999, 19990, 49950, 199900, 499500, 500000)],
                         [1, 5, 10, 50, 100, 500, 1000])
        self.assertEqual(c.kr_limit_up(10000), 10100)
        self.assertEqual(c.kr_limit_up(27350), 27650)   # 27623.5 → 50원 단위 올림
        self.assertEqual(c.kr_limit_up(198000), 200000)  # 199980 → 100원 단위 올림 → 200000

    def test_buy_capped_by_buyable_and_retried_later(self):
        strengths = {"000001": 0.01, "000002": 0.009}
        universe = [(c, c) for c in strengths]
        routes = self.market(strengths)
        cash = {"v": "60000000"}
        routes["/uapi/domestic-stock/v1/trading/inquire-psbl-order"] = lambda q, b: {"rt_cd": "0", "output": {"nrcvb_buy_amt": cash["v"]}}
        kis, t = client(routes)
        state = self.contest.State(os.path.join(self.tmp.name, "f.json"))
        sc = lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1
        cfg = {"us": {"watchlist": []}, "auto": {}}
        with mock.patch.object(self.contest, "score", sc):
            auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 0, 5), self.logs.append, lambda *_: None, universe)
            cash["v"] = "0"  # 첫 매수 뒤 남은 돈이 없다고 가정 → 두 번째는 못 사요
        buys = [c["body"]["PDNO"] for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual(len(buys), 2)  # 첫 사이클에서는 둘 다 시도 (cash 바꾸기 전에 둘 다 계획)
        self.assertIsNone(state.d.get("kr_fill"))

    def test_fill_missing_after_old_version_rebalance(self):
        strengths = {"000001": 0.01, "000002": 0.009}
        universe = [(c, c) for c in strengths]
        held = [{"pdno": "000001", "prdt_name": "A", "hldg_qty": "10", "pchs_avg_pric": "1", "prpr": "1"}]
        kis, t = client(self.market(strengths, held_kr=held))
        state = self.contest.State(os.path.join(self.tmp.name, "o.json"))
        state.d["kr_rebalances"] = ["2026-10-15"]  # 예전 버전이 오늘 리밸런스 기록만 하고 한 종목을 못 산 상태
        sc = lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1
        cfg = {"us": {"watchlist": []}, "auto": {}}
        with mock.patch.object(self.contest, "score", sc):
            auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 1, 0), self.logs.append, lambda *_: None, universe)
            n_after_first = len([c for c in t.calls if c["path"].endswith("order-cash")])
            auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 1, 15), self.logs.append, lambda *_: None, universe)
        buys = [c["body"]["PDNO"] for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual(buys, ["000002"])           # 빠진 종목만, 한 번만
        self.assertEqual(n_after_first, 1)

    def test_kr_sells_dropped_holdings(self):
        strengths = {"000001": 0.01}
        held = [{"pdno": "999999", "prdt_name": "빠진종목", "hldg_qty": "7", "pchs_avg_pric": "1", "prpr": "1"}]
        kis, t = client(self.market(strengths, held_kr=held))
        with mock.patch.object(self.contest, "score", lambda closes, lb, rm: 0.1):
            self.cycle(kis, auto_utc(2026, 10, 15, 0, 30), [("000001", "A")], mode="stable")
        orders = [(c["headers"]["tr_id"], c["body"]["PDNO"]) for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual(orders[0], ("VTTC0011U", "999999"))   # 매도 먼저
        self.assertEqual(orders[1], ("VTTC0012U", "000001"))

    def test_us_buys_one_per_session_until_five(self):
        kis, t = client(self.market({}))
        with mock.patch.object(self.contest, "score", lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1):
            n, state = self.cycle(kis, auto_utc(2026, 10, 15, 14, 0), [])    # 미국 10:00 EDT
            n2, _ = self.cycle(kis, auto_utc(2026, 10, 15, 15, 0), [])        # 같은 날 다시: 안 사요
        us = [c["body"] for c in t.calls if c["path"].endswith("/trading/order")]
        self.assertEqual((n, n2, len(us)), (1, 0, 1))
        self.assertEqual(us[0]["PDNO"], "UP")
        # 주문가능 5만 달러 / 5자리 * 0.98 / (100 * 1.005)
        self.assertEqual(int(us[0]["ORD_QTY"]), 97)
        held = [{"ovrs_pdno": s, "ovrs_cblc_qty": "1", "pchs_avg_pric": "1", "now_pric2": "1"} for s in "ABCDE"]
        kis2, t2 = client(self.market({}, held_us=held))
        self.cycle(kis2, auto_utc(2026, 10, 16, 14, 0), [])
        self.assertFalse(any(c["path"].endswith("/trading/order") for c in t2.calls))

    def test_us_aggressive_target_plus_filler_then_waits(self):
        held = [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "3", "pchs_avg_pric": "1", "now_pric2": "100", "ovrs_excg_cd": "NASD"}]
        routes = self.market({}, held_us=held)
        ranking = {"SOXL": 0.5, "TQQQ": 0.3, "NVDA": 0.2}
        routes["/uapi/overseas-price/v1/quotations/dailyprice"] = lambda q, b: {"rt_cd": "0", "output2": [
            {"xymd": f"2026{i:04d}", "clos": str(100 + i * ranking.get(q["SYMB"], -1))} for i in range(1, 90)]}
        kis, t = client(routes)
        state = self.contest.State(os.path.join(self.tmp.name, "us.json"))
        state.d["us_mode"] = "aggressive"
        cfg = {"us": {"watchlist": []}, "auto": {}}
        sc = lambda closes, lb, rm: (closes[-1] / closes[0] - 1) if closes[-1] > closes[0] else None
        with mock.patch.object(self.contest, "score", sc):
            auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 14, 0), self.logs.append, lambda *_: None, [])
        orders = [(c["headers"]["tr_id"], c["body"]["PDNO"], c["body"]["ORD_QTY"], c["body"]["OVRS_EXCG_CD"])
                  for c in t.calls if c["path"].endswith("/trading/order")]
        self.assertEqual(orders[0][:2], ("VTTT1001U", "AAPL"))                  # 1위 아닌 보유 종목 매도
        self.assertEqual((orders[1][1], orders[1][3]), ("SOXL", "AMEX"))        # 1위 (NYSE Arca → AMEX)
        self.assertGreater(int(orders[1][2]), 100)
        self.assertEqual((orders[2][1], orders[2][2]), ("TQQQ", "1"))           # 조건 채우기 1주
        self.assertEqual(state.d["us_target"], ["SOXL", "AMEX"])
        n_before = len(t.calls)
        with mock.patch.object(self.contest, "score", sc):   # 같은 날 다시: 할 일 없음
            auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 15, 0), self.logs.append, lambda *_: None, [])
        self.assertFalse(any(c["path"].endswith("/trading/order") for c in t.calls[n_before:]))
        self.assertTrue(self.contest.us_conditions_left(state))
        self.assertFalse(self.contest.us_agg_due(state, "2026-10-16"))
        self.assertTrue(self.contest.us_agg_due(state, "2026-10-19"))

    def test_untradeable_symbol_is_skipped_next_time(self):
        routes = self.market({})
        routes["/uapi/overseas-stock/v1/trading/order"] = lambda q, b: {
            "rt_cd": "1", "msg_cd": "40070000", "msg1": "모의투자 주문처리가 안되었습니다(매매불가 종목)"}
        kis, t = client(routes)
        state = self.contest.State(os.path.join(self.tmp.name, "b.json"))
        cfg = {"us": {"watchlist": [{"symbol": "UP", "exchange": "NASD"}, {"symbol": "SLOW", "exchange": "NYSE"}]}, "auto": {}}
        sc = lambda closes, lb, rm: closes[-1] / closes[-1 - lb] - 1
        with mock.patch.object(self.contest, "score", sc):
            auto.run_contest_cycle(kis, cfg, state, auto_utc(2026, 10, 15, 14, 0), self.logs.append, lambda *_: None, [])
        self.assertEqual(state.d["blocked"], ["UP"])
        with mock.patch.object(self.contest, "score", sc):
            pick = self.contest.us_pick(kis, cfg["us"]["watchlist"], {}, datetime(2026, 10, 16).date(),
                                        blocked=state.d["blocked"])
        self.assertEqual(pick[1], "SLOW")

    def test_buyable_uses_after_exchange_amount(self):
        routes = {"/uapi/overseas-stock/v1/trading/inquire-psamount": lambda q, b: {"rt_cd": "0", "output": {
            "ovrs_ord_psbl_amt": "0", "echm_af_ord_psbl_amt": "70000.5", "exrt": "1400"}}}
        kis, _ = client(routes)
        self.assertEqual(kis.us_buyable("AAPL", "NASD", 200)["usd"], 70000.5)

    def test_us_off_does_nothing_at_night(self):
        kis, t = client(self.market({}))
        state = self.contest.State(os.path.join(self.tmp.name, "off.json"))
        state.d["us_mode"] = "off"
        auto.run_contest_cycle(kis, {"us": {"watchlist": []}, "auto": {}}, state, auto_utc(2026, 10, 15, 14, 0),
                               self.logs.append, lambda *_: None, [])
        self.assertEqual([c for c in t.calls if "overseas" in c["path"]], [])

    def test_universe_file(self):
        here = os.path.dirname(cli.__file__)
        u = self.contest.load_universe(os.path.join(here, "universe_kr.csv"))
        self.assertEqual(len(u), 250)
        self.assertTrue(all(len(c) == 6 and c.isdigit() for c, _ in u))
        self.assertIn(("005930", "삼성전자"), u)


def auto_utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class ScheduleTest(unittest.TestCase):
    def test_task_xml(self):
        from datetime import date as _d
        from trading import schedule
        import xml.dom.minidom
        x = schedule.task_xml(r"C:\Py\pythonw.exe", r"C:\a&b\repo", "09:10:00", _d(2026, 10, 15))
        doc = xml.dom.minidom.parseString(x.replace('encoding="UTF-16"', ""))
        self.assertEqual(doc.getElementsByTagName("WakeToRun")[0].firstChild.data, "true")
        self.assertEqual(doc.getElementsByTagName("StartBoundary")[0].firstChild.data, "2026-10-15T09:10:00")
        self.assertEqual(doc.getElementsByTagName("WorkingDirectory")[0].firstChild.data, r"C:\a&b\repo")
        self.assertEqual(doc.getElementsByTagName("Arguments")[0].firstChild.data, "-m trading contest --once")
        self.assertEqual(len(doc.getElementsByTagName("DaysOfWeek")[0].childNodes), 5)

    def test_us_task_time_inside_session_all_year(self):
        from trading import schedule
        h, m = map(int, schedule.TASKS["KIS 대회 미국"][:5].split(":"))
        for month in (10, 12):  # 서머타임, 아닌 때
            when = datetime(2026, month, 15, h - 9, m, tzinfo=timezone.utc)  # KST → UTC
            self.assertIsNotNone(auto.market_session("us", when), month)
        h, m = map(int, schedule.TASKS["KIS 대회 국내"][:5].split(":"))
        self.assertIsNotNone(auto.market_session("kr", datetime(2026, 10, 15, h - 9, m, tzinfo=timezone.utc)))


class ContestOnceTest(unittest.TestCase):
    def test_closed_market_does_nothing(self):
        kis, t = client(fake_market())
        with tempfile.TemporaryDirectory() as d:
            import shutil
            shutil.copy(os.path.join(os.path.dirname(cli.__file__), "universe_kr.csv"), d)
            n = auto.run_contest_once(kis, {"us": {"watchlist": []}}, d, lambda e: None,
                                      now=lambda: datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc), out=lambda *_: None)
        self.assertEqual((n, t.calls), (0, []))

    def test_retries_after_error(self):
        kis, _ = client(fake_market())
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise OSError("network down")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(auto, "run_contest_cycle", boom):
            import shutil
            shutil.copy(os.path.join(os.path.dirname(cli.__file__), "universe_kr.csv"), d)
            auto.run_contest_once(kis, {}, d, lambda e: None, now=lambda: datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc),
                                  sleep=lambda s: None, out=lambda *_: None)
        self.assertEqual(len(calls), 3)


class OrderCommandTest(unittest.TestCase):
    def test_us_order_marketable_limit_and_fill_check(self):
        held = []
        routes = fake_market(held_us=held)
        routes["/uapi/overseas-price/v1/quotations/price"] = lambda q, b: {"rt_cd": "0", "output": {"last": "200"}}

        def order(q, b):
            held.append({"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "1", "pchs_avg_pric": "201", "now_pric2": "200"})
            return {"rt_cd": "0", "output": {"ODNO": "7"}}
        routes["/uapi/overseas-stock/v1/trading/order"] = order
        kis, t = client(routes)
        out = []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "HERE", d), \
                mock.patch("builtins.print", side_effect=lambda *a: out.append(" ".join(map(str, a)))):
            cli.cmd_order(kis, "buy", "aapl", 1, None, ask=lambda _: "y", wait=lambda s: None)
        body = [c for c in t.calls if c["path"].endswith("/trading/order")][0]["body"]
        self.assertEqual((body["PDNO"], body["OVRS_ORD_UNPR"], body["OVRS_EXCG_CD"]), ("AAPL", "201.00", "NASD"))
        self.assertTrue(any("체결됐어요" in line for line in out))

    def test_yes_skips_confirmation(self):
        kis, t = client(fake_market())
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "HERE", d), mock.patch("builtins.print"):
            cli.cmd_order(kis, "buy", "000001", 1, None, ask=None, wait=lambda s: None)
        self.assertTrue(any(c["path"].endswith("order-cash") for c in t.calls))

    def test_cancel_sends_nothing(self):
        kis, t = client(fake_market())
        with mock.patch("builtins.print"):
            cli.cmd_order(kis, "buy", "000001", 1, None, ask=lambda _: "n", wait=lambda s: None)
        self.assertFalse(any(c["method"] == "POST" and "order" in c["path"] for c in t.calls))


class UpdateCommandTest(unittest.TestCase):
    def test_update_replaces_program_but_keeps_settings(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "HERE", d), mock.patch("builtins.print"):
            for name, text in ((".env", "KIS_APP_KEY=mine"), ("config.json", "{}"), ("kis.py", "old")):
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
            manifest = b"# list\nkis.py\ntests/test_trading.py\n.env\nconfig.json\n../evil.py\n"
            cli.cmd_update(fetch=lambda url: manifest if url.endswith("MANIFEST") else b"new " + url.rsplit("/", 1)[1].encode())
            self.assertEqual(open(os.path.join(d, "kis.py")).read(), "new kis.py")
            self.assertEqual(open(os.path.join(d, ".env")).read(), "KIS_APP_KEY=mine")
            self.assertEqual(open(os.path.join(d, "config.json")).read(), "{}")
            self.assertTrue(os.path.exists(os.path.join(d, "tests", "test_trading.py")))
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(d), "evil.py")))

    def test_update_changes_nothing_on_failure(self):
        def fetch(url):
            if url.endswith("planner.py") or url.endswith("MANIFEST"):
                raise OSError("network")
            return b"new"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "HERE", d):
            with open(os.path.join(d, "kis.py"), "w") as f:
                f.write("old")
            with self.assertRaises(SystemExit):
                cli.cmd_update(fetch=fetch)
            self.assertEqual(open(os.path.join(d, "kis.py")).read(), "old")

    def test_update_file_list_matches_repo(self):
        here = os.path.dirname(cli.__file__)
        with open(os.path.join(here, "MANIFEST")) as f:
            manifest = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.assertEqual(sorted(manifest), sorted(cli.UPDATE_FILES))
        for name in manifest:
            self.assertTrue(os.path.exists(os.path.join(here, *name.split("/"))), name)
        py = {f for f in os.listdir(here) if f.endswith(".py")}
        self.assertLessEqual(py, set(manifest))  # 새 .py 파일을 MANIFEST 에 빠뜨리지 않게


def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class MarketHoursTest(unittest.TestCase):
    def test_korea(self):
        self.assertEqual(auto.market_session("kr", utc(2026, 10, 15, 1, 0)), "2026-10-15")   # 목 10:00 KST
        self.assertIsNone(auto.market_session("kr", utc(2026, 10, 15, 6, 25)))               # 15:25 동시호가
        self.assertIsNone(auto.market_session("kr", utc(2026, 10, 17, 1, 0)))                # 토요일

    def test_us_dst(self):
        # 10월(서머타임): 미국 10:00 EDT = 14:00 UTC = 23:00 KST, 날짜는 미국 기준
        self.assertEqual(auto.market_session("us", utc(2026, 10, 15, 14, 0)), "2026-10-15")
        self.assertIsNone(auto.market_session("us", utc(2026, 10, 15, 13, 0)))               # 09:00 EDT 장 전
        # 12월(서머타임 아님): 14:00 UTC = 09:00 EST 장 전, 15:00 UTC = 10:00 EST 장중
        self.assertIsNone(auto.market_session("us", utc(2026, 12, 15, 14, 0)))
        self.assertEqual(auto.market_session("us", utc(2026, 12, 15, 15, 0)), "2026-12-15")
        # 한국 날짜가 바뀌어도(01:00 KST) 미국 세션 날짜는 그대로
        self.assertEqual(auto.market_session("us", utc(2026, 10, 15, 16, 0)), "2026-10-15")

    def test_dst_boundaries(self):
        self.assertEqual(auto.us_eastern(utc(2026, 3, 8, 6, 59)).utcoffset().total_seconds(), -5 * 3600)
        self.assertEqual(auto.us_eastern(utc(2026, 3, 8, 7, 0)).utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(auto.us_eastern(utc(2026, 11, 1, 6, 0)).utcoffset().total_seconds(), -5 * 3600)


class AutoTradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def sent(self):
        return [l for l in self.logs if l["event"] == "order_sent"]

    def run_cycle(self, kis, when, cfg=CFG):
        book = auto.OrderBook(os.path.join(self.tmp.name, "book.json"))
        return auto.run_cycle(kis, cfg, book, when, self.logs.append, lambda: len(self.sent()), out=lambda *_: None)

    def test_korea_cycle_market_orders_and_no_repeat(self):
        kis, t = client(fake_market())
        when = utc(2026, 10, 15, 1, 0)
        self.assertEqual(self.run_cycle(kis, when), 2)
        posts = [c for c in t.calls if c["path"].endswith("order-cash")]
        self.assertEqual([c["body"]["ORD_DVSN"] for c in posts], ["01", "01"])
        self.assertEqual(posts[0]["body"]["ORD_UNPR"], "0")
        # 15분 뒤 다시 돌아도 같은 종목을 또 사지 않아요 (체결 전이라 잔고에 없어도)
        self.assertEqual(self.run_cycle(kis, utc(2026, 10, 15, 1, 15)), 0)
        # 다음 날에는 다시 판단해요 (가짜 잔고는 계속 비어 있어서 다시 2건).
        self.assertEqual(self.run_cycle(kis, utc(2026, 10, 16, 1, 0)), 2)

    def test_us_cycle_limit_price_within_budget(self):
        kis, t = client(fake_market())
        self.assertEqual(self.run_cycle(kis, utc(2026, 10, 15, 14, 0)), 1)
        body = [c for c in t.calls if c["path"].endswith("/trading/order")][0]["body"]
        price = float(body["OVRS_ORD_UNPR"])
        self.assertAlmostEqual(price, round(rising()[-1] * 1.005, 2))
        self.assertLessEqual(int(body["ORD_QTY"]) * price, CFG["limits"]["max_order_usd"])

    def test_closed_market_does_nothing(self):
        kis, t = client(fake_market())
        self.assertEqual(self.run_cycle(kis, utc(2026, 10, 17, 1, 0)), 0)
        self.assertEqual(t.calls, [])

    def test_daily_limit(self):
        kis, _ = client(fake_market())
        cfg = {**CFG, "limits": {**CFG["limits"], "max_orders_per_day": 1}}
        self.assertEqual(self.run_cycle(kis, utc(2026, 10, 15, 1, 0), cfg), 1)

    def test_stop_file_and_errors_do_not_crash(self):
        kis, _ = client({"/uapi/domestic-stock/v1/trading/inquire-balance": lambda q, b: (_ for _ in ()).throw(OSError("net"))})
        times = iter([utc(2026, 10, 15, 1, 0), utc(2026, 10, 15, 1, 1)])
        stop = os.path.join(self.tmp.name, "STOP")

        def sleep(_):
            open(stop, "w").close()

        auto.run_forever(kis, {**CFG, "us": {**CFG["us"], "watchlist": []}}, self.tmp.name, self.logs.append,
                         lambda: 0, now=lambda: next(times), sleep=sleep, out=lambda *_: None)
        self.assertTrue(any(l["event"] == "auto_error" for l in self.logs))

    def test_real_env_auto_blocked_without_flag(self):
        kis, t = client(env="real")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KIS_AUTO_REAL", None)
            with self.assertRaises(SystemExit):
                cli.cmd_auto(kis, CFG, ("kr", "us"))
        self.assertEqual(t.calls, [])


if __name__ == "__main__":
    unittest.main()
