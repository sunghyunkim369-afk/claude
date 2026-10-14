"""관심 종목과 보유 종목을 보고 주문 후보를 만들어요. 주문은 내지 않아요."""

import math

from . import strategy


def _size(budget_per_position, max_order, buyable, price):
    amount = min(budget_per_position, max_order, buyable)
    return max(0, math.floor(amount / price)) if price > 0 else 0


def plan_domestic(kis, cfg, notes, skip=(), pending=0):
    dom = cfg["domestic"]
    limits = cfg["limits"]
    bal = kis.kr_balance()
    held = bal["holdings"]
    names = {w["code"]: w.get("name", "") for w in dom["watchlist"]}
    orders = []

    # 보유 종목은 관심 목록에 없어도 매도 규칙을 확인해요.
    codes = [c for c in dict.fromkeys(list(held) + [w["code"] for w in dom["watchlist"]]) if c not in skip]
    # 아직 체결 안 된 매수 주문도 보유 종목 수에 넣어요.
    open_positions = len(held) + pending
    per_position = dom["budget_krw"] / dom["max_positions"]

    for code in codes:
        try:
            q = kis.kr_price(code)
            closes = kis.kr_daily(code)
        except Exception as e:  # 한 종목 실패가 전체를 막지 않게
            notes.append(f"[국내] {code} 조회 실패: {e}")
            continue
        name = names.get(code) or q["name"] or (held.get(code) or {}).get("name", "")
        price = q["price"]
        # 일봉 마지막 값이 오늘이면 현재가로 대체되도록 제외해요.
        action, reason = strategy.decide(closes[:-1] if closes else closes, price, held.get(code), cfg.get("strategy"))
        if action == "sell":
            orders.append({"market": "kr", "side": "sell", "symbol": code, "name": name,
                           "qty": held[code]["qty"], "price": price, "currency": "KRW", "reason": reason})
        elif action == "buy":
            if open_positions >= dom["max_positions"]:
                notes.append(f"[국내] {name} 매수 신호지만 최대 보유 종목 수({dom['max_positions']}) 도달")
                continue
            buyable = kis.kr_buyable(code, price)
            qty = _size(per_position, limits["max_order_krw"], buyable, price)
            if qty == 0:
                notes.append(f"[국내] {name} 매수 신호지만 살 수 있는 수량 0 (주문가능 {buyable:,.0f}원)")
                continue
            orders.append({"market": "kr", "side": "buy", "symbol": code, "name": name,
                           "qty": qty, "price": price, "currency": "KRW", "reason": reason})
            open_positions += 1
        else:
            notes.append(f"[국내] {name}: {reason}")
    return orders


def plan_us(kis, cfg, notes, skip=(), pending=0):
    us = cfg["us"]
    limits = cfg["limits"]
    exchanges = {w["symbol"]: w["exchange"] for w in us["watchlist"]}
    bal = kis.us_balance()
    held = bal["holdings"]
    orders = []

    symbols = [s for s in dict.fromkeys(list(held) + list(exchanges)) if s not in skip]
    open_positions = len(held) + pending
    per_position = us["budget_usd"] / us["max_positions"]

    for sym in symbols:
        excg = exchanges.get(sym) or held[sym].get("exchange") or "NASD"
        try:
            q = kis.us_price(sym, excg)
            closes = kis.us_daily(sym, excg)
        except Exception as e:
            notes.append(f"[미국] {sym} 조회 실패: {e}")
            continue
        price = q["price"]
        if price <= 0:
            notes.append(f"[미국] {sym}: 현재가 없음 (장 시작 전이거나 거래 정지)")
            continue
        action, reason = strategy.decide(closes[:-1] if closes else closes, price, held.get(sym), cfg.get("strategy"))
        if action == "sell":
            orders.append({"market": "us", "side": "sell", "symbol": sym, "exchange": excg, "name": sym,
                           "qty": held[sym]["qty"], "price": price, "currency": "USD", "reason": reason})
        elif action == "buy":
            if open_positions >= us["max_positions"]:
                notes.append(f"[미국] {sym} 매수 신호지만 최대 보유 종목 수({us['max_positions']}) 도달")
                continue
            buyable = kis.us_buyable(sym, excg, price)["usd"]
            qty = _size(per_position, limits["max_order_usd"], buyable, price)
            if qty == 0:
                notes.append(f"[미국] {sym} 매수 신호지만 살 수 있는 수량 0 (주문가능 ${buyable:,.2f})")
                continue
            orders.append({"market": "us", "side": "buy", "symbol": sym, "exchange": excg, "name": sym,
                           "qty": qty, "price": price, "currency": "USD", "reason": reason})
            open_positions += 1
        else:
            notes.append(f"[미국] {sym}: {reason}")
    return orders


def make_plan(kis, cfg, markets=("kr", "us"), skip=None, pending=None):
    """skip: {"kr": {종목코드}, "us": {심볼}} 오늘 이미 주문한 종목은 건너뛰어요 (자동매매 중복 주문 방지).
    pending: {"kr": n, "us": n} 아직 체결 안 됐을 수 있는 오늘 매수 주문 수."""
    skip = skip or {}
    pending = pending or {}
    notes = []
    orders = []
    if "kr" in markets:
        orders += plan_domestic(kis, cfg, notes, skip.get("kr", ()), pending.get("kr", 0))
    if "us" in markets:
        orders += plan_us(kis, cfg, notes, skip.get("us", ()), pending.get("us", 0))
    # 매도를 먼저 내서 현금을 확보해요.
    orders.sort(key=lambda o: o["side"] != "sell")
    return orders, notes
