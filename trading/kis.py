"""한국투자증권 Open API 클라이언트 (국내주식 + 미국주식).

표준 라이브러리만 써요. 요청 규격은 한국투자증권 공식 예제
(github.com/koreainvestment/open-trading-api, examples_llm/) 를 따랐어요.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

DOMAINS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "demo": "https://openapivts.koreainvestment.com:29443",
}

# 주문은 거래소 코드(NASD), 시세는 시세용 코드(NAS)를 써요.
US_PRICE_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}

# 미국 주문 TR. 매도는 실전/모의 번호가 달라요 (실전 TTTT1006U, 모의 VTTT1001U).
US_ORDER_TR = {
    ("real", "buy"): "TTTT1002U",
    ("real", "sell"): "TTTT1006U",
    ("demo", "buy"): "VTTT1002U",
    ("demo", "sell"): "VTTT1001U",
}


class KisError(Exception):
    pass


def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


class Kis:
    def __init__(self, app_key, app_secret, account, env="demo", cache_dir=None,
                 min_interval=None, transport=None):
        if env not in DOMAINS:
            raise ValueError("env 는 'demo' 또는 'real' 이어야 해요")
        if "-" not in account:
            raise ValueError("계좌번호는 '12345678-01' 형식으로 넣어 주세요")
        self.app_key = app_key
        self.app_secret = app_secret
        self.cano, self.acnt_prdt_cd = account.split("-", 1)
        self.env = env
        self.base = DOMAINS[env]
        self.cache_dir = cache_dir
        # 모의투자 서버는 초당 호출 한도가 낮아서 간격을 넉넉히 둬요 (0.6초로는 초과가 났어요).
        self.min_interval = min_interval if min_interval is not None else (1.1 if env == "demo" else 0.1)
        self._last_call = 0.0
        self._token = None
        self._transport = transport or self._http
        self._sleep = time.sleep

    # ---------- HTTP ----------

    def _http(self, method, url, headers, body):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                return dict(res.headers), json.loads(res.read().decode())
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                payload = json.loads(text)
            except ValueError:
                raise KisError(f"HTTP {e.code}: {text[:200]}") from None
            return dict(e.headers), payload

    def _throttle(self):
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    # ---------- 토큰 ----------
    # 토큰은 하루 동안 유효하고 발급 횟수 제한이 있어서 파일에 저장해 다시 써요.

    def _token_path(self):
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, f"token-{self.env}.json")

    def token(self):
        now = datetime.now()
        if self._token and self._token["expires"] > now + timedelta(minutes=10):
            return self._token["value"]
        path = self._token_path()
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            expires = datetime.fromisoformat(saved["expires"])
            if saved.get("app_key") == self.app_key and expires > now + timedelta(minutes=10):
                self._token = {"value": saved["value"], "expires": expires}
                return saved["value"]

        self._throttle()
        _, body = self._transport("POST", self.base + "/oauth2/tokenP",
                                  {"content-type": "application/json"},
                                  {"grant_type": "client_credentials",
                                   "appkey": self.app_key, "appsecret": self.app_secret})
        if "access_token" not in body:
            raise KisError(f"토큰 발급 실패: {body.get('error_description') or body}")
        expires = datetime.strptime(body["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
        self._token = {"value": body["access_token"], "expires": expires}
        if path:
            os.makedirs(self.cache_dir, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"value": body["access_token"], "expires": expires.isoformat(),
                           "app_key": self.app_key}, f)
        return body["access_token"]

    def _call(self, method, path, tr_id, params):
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "tr_cont": "",
        }
        url = self.base + path
        body = None
        if method == "GET":
            url += "?" + urllib.parse.urlencode(params)
        else:
            body = params
        # 초당 호출 한도 초과(EGW00201)는 서버가 요청을 처리하기 전에 거절한 것이라
        # 주문이어도 다시 보내도 중복되지 않아요. 잠깐 쉬었다가 최대 3번 다시 시도해요.
        for attempt in range(4):
            self._throttle()
            _, res = self._transport(method, url, headers, body)
            if res.get("msg_cd") != "EGW00201" or attempt == 3:
                break
            self._sleep(1.0 + attempt)
        if str(res.get("rt_cd")) != "0":
            raise KisError(f"[{tr_id}] {res.get('msg_cd', '')} {res.get('msg1', res)}".strip())
        return res

    def _tr(self, real_id):
        """조회·주문 TR 은 모의투자에서 앞 글자가 V 로 바뀌어요 (TTTC → VTTC)."""
        return ("V" + real_id[1:]) if self.env == "demo" else real_id

    # ---------- 국내주식 ----------

    def kr_price(self, code):
        res = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        out = res["output"]
        return {"price": _num(out["stck_prpr"]), "name": out.get("hts_kor_isnm", "")}

    def kr_daily(self, code, days=150, with_dates=False):
        """일봉 종가 (오래된 날짜 → 최근 순). 수정주가 기준, 한 번에 최대 100개.
        with_dates 면 [(YYYYMMDD, 종가)]."""
        end = date.today()
        start = end - timedelta(days=days)
        res = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                         "FHKST03010100",
                         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                          "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                          "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                          "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        rows = [r for r in res.get("output2") or [] if r.get("stck_bsop_date")]
        rows.sort(key=lambda r: r["stck_bsop_date"])
        if with_dates:
            return [(r["stck_bsop_date"], _num(r["stck_clpr"])) for r in rows]
        return [_num(r["stck_clpr"]) for r in rows]

    def kr_balance(self):
        res = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", self._tr("TTTC8434R"),
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                          "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                          "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                          "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
        holdings = {}
        for r in res.get("output1") or []:
            qty = int(_num(r.get("hldg_qty")))
            if qty > 0:
                holdings[r["pdno"]] = {"name": r.get("prdt_name", ""), "qty": qty,
                                       "avg_price": _num(r.get("pchs_avg_pric")),
                                       "price": _num(r.get("prpr"))}
        summary = (res.get("output2") or [{}])[0]
        return {"holdings": holdings,
                "cash": _num(summary.get("dnca_tot_amt")),
                "total": _num(summary.get("tot_evlu_amt"))}

    def kr_buyable(self, code, price):
        """미수 없이 살 수 있는 금액(원)."""
        res = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-psbl-order", self._tr("TTTC8908R"),
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "PDNO": code,
                          "ORD_UNPR": str(int(price)), "ORD_DVSN": "00",
                          "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N"})
        return _num(res["output"].get("nrcvb_buy_amt"))

    def kr_order(self, side, code, qty, price=None):
        """side 는 'buy' 또는 'sell'. price 를 주면 지정가, None 이면 시장가."""
        tr = {"sell": "TTTC0011U", "buy": "TTTC0012U"}[side]
        res = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash", self._tr(tr),
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "PDNO": code,
                          "ORD_DVSN": "00" if price else "01", "ORD_QTY": str(int(qty)),
                          "ORD_UNPR": str(int(price)) if price else "0",
                          "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "",
                          "CNDT_PRIC": ""})
        return res.get("output", {})

    # ---------- 미국주식 ----------

    def us_price(self, symbol, exchange):
        res = self._call("GET", "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
                         {"AUTH": "", "EXCD": US_PRICE_EXCD[exchange], "SYMB": symbol})
        return {"price": _num(res["output"].get("last")), "name": symbol}

    def us_daily(self, symbol, exchange, with_dates=False):
        """일봉 종가 (오래된 날짜 → 최근 순). 수정주가 기준, 최대 100개. with_dates 면 [(YYYYMMDD, 종가)]."""
        res = self._call("GET", "/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000",
                         {"AUTH": "", "EXCD": US_PRICE_EXCD[exchange], "SYMB": symbol,
                          "GUBN": "0", "BYMD": "", "MODP": "1"})
        rows = [r for r in res.get("output2") or [] if r.get("xymd")]
        rows.sort(key=lambda r: r["xymd"])
        if with_dates:
            return [(r["xymd"], _num(r["clos"])) for r in rows]
        return [_num(r["clos"]) for r in rows]

    def us_balance(self, exchanges=("NASD", "NYSE", "AMEX")):
        """미국 보유 종목. 모의투자는 거래소별로 따로 조회해야 해서 모두 돌아요."""
        holdings = {}
        for excg in (exchanges if self.env == "demo" else ("NASD",)):
            res = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-balance", self._tr("TTTS3012R"),
                             {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                              "OVRS_EXCG_CD": excg, "TR_CRCY_CD": "USD",
                              "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
            for r in res.get("output1") or []:
                qty = int(_num(r.get("ovrs_cblc_qty")))
                if qty > 0:
                    holdings[r["ovrs_pdno"]] = {"name": r.get("ovrs_item_name", ""), "qty": qty,
                                                "avg_price": _num(r.get("pchs_avg_pric")),
                                                "price": _num(r.get("now_pric2")),
                                                "exchange": r.get("ovrs_excg_cd") or excg}
        return {"holdings": holdings}

    def us_buyable(self, symbol, exchange, price):
        """해외 주문가능금액(USD)과 환율."""
        res = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-psamount", self._tr("TTTS3007R"),
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "OVRS_EXCG_CD": exchange,
                          "OVRS_ORD_UNPR": f"{price:.2f}", "ITEM_CD": symbol})
        out = res["output"]
        # 달러 예수금이 없을 때 '환전 후 주문가능금액' 칸에만 값이 있는 경우가 있어서 가장 큰 값을 써요
        keys = ("ovrs_ord_psbl_amt", "frcr_ord_psbl_amt1", "ord_psbl_frcr_amt", "echm_af_ord_psbl_amt")
        return {"usd": max(_num(out.get(k)) for k in keys), "fx": _num(out.get("exrt")),
                "raw": {k: out.get(k) for k in keys + ("max_ord_psbl_qty", "echm_af_ord_psbl_qty")}}

    def us_cash(self):
        """해외 체결기준현재잔고: 통화별 외화예수금·사용가능금액 (주문가능금액이 0 일 때 원인 확인용)."""
        res = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-present-balance", self._tr("CTRP6504R"),
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "WCRC_FRCR_DVSN_CD": "02",
                          "NATN_CD": "000", "TR_MKET_CD": "00", "INQR_DVSN_CD": "00"})
        rows = res.get("output2") or []
        if isinstance(rows, dict):
            rows = [rows]
        by_ccy = {r.get("crcy_cd", "?"): {"예수금": _num(r.get("frcr_dncl_amt_2")),
                                          "출금가능": _num(r.get("frcr_drwg_psbl_amt_1"))} for r in rows}
        total = res.get("output3") or {}
        if isinstance(total, list):
            total = total[0] if total else {}
        return {"by_currency": by_ccy, "usable_usd": _num(total.get("frcr_use_psbl_amt")),
                "total_krw": _num(total.get("tot_asst_amt"))}

    def us_order(self, side, symbol, exchange, qty, price):
        """지정가 주문 (모의투자 미국 주문은 지정가만 돼요)."""
        res = self._call("POST", "/uapi/overseas-stock/v1/trading/order", US_ORDER_TR[(self.env, side)],
                         {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd, "OVRS_EXCG_CD": exchange,
                          "PDNO": symbol, "ORD_QTY": str(int(qty)), "OVRS_ORD_UNPR": f"{price:.2f}",
                          "CTAC_TLNO": "", "MGCO_APTM_ODNO": "",
                          "SLL_TYPE": "00" if side == "sell" else "",
                          "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"})
        return res.get("output", {})

