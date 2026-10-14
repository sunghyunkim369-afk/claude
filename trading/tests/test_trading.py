"""가짜 API 응답으로 매매 프로그램을 검증해요 (네트워크 안 씀).

  python3 -m unittest discover -s trading/tests -t .
"""

import json
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

from trading import cli, strategy
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

    def test_rate_limit_gives_up(self):
        kis, t = client({"/uapi/domestic-stock/v1/quotations/inquire-price":
                         lambda q, b: {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초과"}})
        kis._sleep = lambda s: None
        with self.assertRaises(KisError):
            kis.kr_price("005930")
        self.assertEqual(len([c for c in t.calls if c["path"].endswith("inquire-price")]), 4)

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


class UpdateCommandTest(unittest.TestCase):
    def test_update_replaces_program_but_keeps_settings(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "HERE", d), mock.patch("builtins.print"):
            for name, text in ((".env", "KIS_APP_KEY=mine"), ("config.json", "{}"), ("kis.py", "old")):
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
            cli.cmd_update(fetch=lambda url: b"new " + url.rsplit("/", 1)[1].encode())
            self.assertEqual(open(os.path.join(d, "kis.py")).read(), "new kis.py")
            self.assertEqual(open(os.path.join(d, ".env")).read(), "KIS_APP_KEY=mine")
            self.assertEqual(open(os.path.join(d, "config.json")).read(), "{}")
            self.assertTrue(os.path.exists(os.path.join(d, "tests", "test_trading.py")))

    def test_update_changes_nothing_on_failure(self):
        def fetch(url):
            if url.endswith("planner.py"):
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
        for name in cli.UPDATE_FILES:
            self.assertTrue(os.path.exists(os.path.join(here, *name.split("/"))), name)


if __name__ == "__main__":
    unittest.main()
