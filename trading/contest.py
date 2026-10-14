"""대회 모드 (BanKIS 대학생 모의투자대회, ~11/20): python -m trading contest

backtest/contest.py 로 과거 5년의 '5주 구간' 수백 개를 시험해서 정한 공식이에요.

국내 (코스피200·코스닥150 후보 universe_kr.csv 안에서만):
  - 후보: 어제 종가 > EMA20 > EMA60, RSI14 < 80
  - 점수: 최근 20거래일 수익률. 상위 5종목이 목표
  - 시장이 나쁜 날(대상 종목 동일비중 지수 < 그 지수의 EMA20)엔 상위 2종목만 (나머지 현금)
    → 과거 검증에서 중앙값 수익률은 같고 최악 5주 손실이 −47% → −24% 로 줄었어요
  - 2거래일마다(장 시작 직후) 목표에서 빠진 종목은 시장가 매도, 새 종목은 총평가금액의 1/5 만큼 시장가 매수
  - 과거 검증: 수상 조건(매매금액 5억·매매일수 5일·5종목·체결 5회)을 모든 구간에서 채웠고, 매매금액은 원금의 약 12배

미국:
  - 순환매가 과거에 매수 후 보유보다 나은 점이 없어서, 조건만 채우고 보유해요
  - 미국 장마다 1종목씩, 5종목이 될 때까지: 60거래일 수익률 1위(상승 추세 종목 중, 아직 안 가진 것)를
    주문가능금액 / 남은 자리 수 만큼 매수 → 5일에 걸쳐 5종목·5회 체결
"""

import csv
import json
import math
import os
from datetime import date

from .kis import KisError

KR = {"lookback": 20, "top_k": 5, "rebalance_days": 2, "rsi_max": 80,
      # 시장 국면 필터: 대상 종목 동일비중 지수가 그 지수의 EMA20 아래면(시장이 나쁜 날) 상위 2종목만, 나머지 현금
      "regime_ema": 20, "risk_off_top": 2}
US = {"lookback": 60, "top_k": 5, "rsi_max": 80}


# ---------- 지표 (backtest/indicators.py 와 같은 정의) ----------

def ema(values, n):
    if len(values) < n:
        return None
    e = sum(values[:n]) / n
    k = 2 / (n + 1)
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values, n=14):
    """Wilder RSI (backtest 의 ewm(alpha=1/n, adjust=False) 와 같은 계산)."""
    if len(values) <= n:
        return None
    a = 1 / n
    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
    up, down = max(diffs[0], 0.0), max(-diffs[0], 0.0)
    for d in diffs[1:]:
        up = up * (1 - a) + max(d, 0.0) * a
        down = down * (1 - a) + max(-d, 0.0) * a
    return 50.0 if up + down == 0 else 100 * up / (up + down)


def score(closes, lookback, rsi_max):
    """상승 추세·과열 아님이면 lookback 일 수익률, 아니면 None. closes 는 어제 종가까지."""
    if len(closes) < max(61, lookback + 1):
        return None
    c = closes[-1]
    e20, e60, r = ema(closes, 20), ema(closes, 60), rsi(closes)
    if e20 is None or e60 is None or r is None:
        return None
    if not (c > e20 > e60) or r >= rsi_max:
        return None
    return c / closes[-1 - lookback] - 1


def closes_until_yesterday(rows, today):
    """[(YYYYMMDD, 종가)] 에서 오늘 막대(장중 값)는 빼요. 신호는 어제 종가로만 판단해요."""
    t = today.strftime("%Y%m%d")
    return [c for d, c in rows if d < t]


# ---------- 상태 (재시작해도 이어서) ----------

class State:
    def __init__(self, path):
        self.path = path
        self.d = {"kr_rebalances": [], "us_buys": [], "orders": 0, "amount": 0.0, "days": [], "symbols": []}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.d.update(json.load(f))

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=1)

    def record(self, market, symbol, amount, day):
        """주문 접수 기준 누적 (실제 수상 조건은 체결 기준이에요)."""
        if market != "kr":
            return
        self.d["orders"] += 1
        self.d["amount"] += amount
        if day not in self.d["days"]:
            self.d["days"].append(day)
        if symbol not in self.d["symbols"]:
            self.d["symbols"].append(symbol)


def business_days_between(a, b):
    """a 다음 날부터 b 까지의 평일 수 (공휴일은 몰라요)."""
    a, b = date.fromisoformat(a), date.fromisoformat(b)
    n, d = 0, a
    while d < b:
        d = date.fromordinal(d.toordinal() + 1)
        if d.weekday() < 5:
            n += 1
    return n


def load_universe(path):
    with open(path, encoding="utf-8") as f:
        rows = [l for l in f if not l.startswith("#")]
    return [(r["code"].zfill(6), r["name"]) for r in csv.DictReader(rows)]


# ---------- 국내 ----------

def kr_due(state, session):
    last = state.d["kr_rebalances"][-1] if state.d["kr_rebalances"] else None
    return last is None or (last != session and business_days_between(last, session) >= KR["rebalance_days"])


def equal_weight_index(series_by_code):
    """{코드: [(YYYYMMDD, 종가)]} → 날짜순 동일비중 지수 값 (매일 종목 평균 수익률로 누적)."""
    by_date = {}
    for rows in series_by_code.values():
        for (d0, c0), (d1, c1) in zip(rows, rows[1:]):
            if c0 > 0:
                by_date.setdefault(d1, []).append(c1 / c0 - 1)
    level, out = 1.0, []
    for d in sorted(by_date):
        level *= 1 + sum(by_date[d]) / len(by_date[d])
        out.append(level)
    return out


def market_risk_on(index_values, n):
    """지수 > 지수 EMA(n) 이면 True (좋은 날). 데이터가 부족하면 True."""
    e = ema(index_values, n)
    return True if e is None else index_values[-1] > e


def kr_targets(kis, universe, today, out=print):
    """반환: (목표 [(점수, 코드, 이름)], 시장이 좋은 날인지)."""
    scored, series = [], {}
    t = today.strftime("%Y%m%d")
    for i, (code, name) in enumerate(universe, 1):
        try:
            rows = kis.kr_daily(code, with_dates=True)
        except KisError as e:
            out(f"    {name}({code}) 일봉 실패: {e}")
            continue
        past = [(d, c) for d, c in rows if d < t]
        series[code] = past
        s = score([c for _, c in past], KR["lookback"], KR["rsi_max"])
        if s is not None:
            scored.append((s, code, name))
        if i % 50 == 0:
            out(f"    종목 확인 {i}/{len(universe)}")
    scored.sort(reverse=True)
    risk_on = market_risk_on(equal_weight_index(series), KR["regime_ema"])
    keep = KR["top_k"] if risk_on else KR["risk_off_top"]
    return scored[:keep], risk_on


def kr_plan(kis, targets):
    """(매도 주문들, 매수 주문들). 목표에 없는 보유 종목은 전부 팔고, 새 목표는 총평가의 1/5 씩
    (시장이 나쁜 날 목표가 2종목이어도 1/5 씩이라 나머지는 현금으로 남아요)."""
    bal = kis.kr_balance()
    held = bal["holdings"]
    want = {code for _, code, _ in targets}
    sells = [{"market": "kr", "side": "sell", "symbol": c, "name": h["name"], "qty": h["qty"], "price": h["price"],
              "currency": "KRW", "reason": "목표 5종목에서 빠짐"} for c, h in held.items() if c not in want]
    per = bal["total"] / KR["top_k"] if bal["total"] > 0 else 0
    buys = []
    for sc, code, name in targets:
        if code in held:
            continue
        price = kis.kr_price(code)["price"]
        qty = math.floor(per * 0.99 / price) if price > 0 else 0
        if qty > 0:
            buys.append({"market": "kr", "side": "buy", "symbol": code, "name": name, "qty": qty, "price": price,
                         "currency": "KRW", "reason": f"20일 수익률 {sc * 100:+.1f}%"})
    return sells, buys


# ---------- 미국 ----------

def us_due(state, session, holdings):
    return len(holdings) < US["top_k"] and session not in state.d["us_buys"]


def us_pick(kis, watchlist, held, today, out=print):
    best = None
    for w in watchlist:
        sym, excg = w["symbol"], w["exchange"]
        if sym in held:
            continue
        try:
            rows = kis.us_daily(sym, excg, with_dates=True)
        except KisError as e:
            out(f"    {sym} 일봉 실패: {e}")
            continue
        s = score(closes_until_yesterday(rows, today), US["lookback"], US["rsi_max"])
        if s is not None and (best is None or s > best[0]):
            best = (s, sym, excg)
    return best
