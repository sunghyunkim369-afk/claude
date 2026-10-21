"""자동매매: 장이 열려 있는 동안 정해진 간격으로 계획을 세우고 바로 주문해요.

  python -m trading auto

멈추기: Ctrl+C, 또는 trading 폴더에 STOP 이라는 이름의 파일을 만들면 다음 확인 때 멈춰요.
"""

import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone

from .kis import KisError
from .planner import make_plan

KST = timezone(timedelta(hours=9))

DEFAULTS = {
    "interval_min": 15,
    # 미국 모의투자는 지정가만 돼서, 체결되도록 현재가보다 조금 불리한 가격으로 내요.
    "us_limit_slippage_pct": 0.5,
}


# ---------- 장 시간 ----------

def _nth_sunday(year, month, n):
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def us_eastern(now_utc):
    """미국 동부 시각. 서머타임: 3월 둘째 일요일 ~ 11월 첫째 일요일 (tzdata 없이 계산)."""
    y = now_utc.year
    dst_start = datetime.combine(_nth_sunday(y, 3, 2), datetime.min.time(), timezone.utc) + timedelta(hours=7)
    dst_end = datetime.combine(_nth_sunday(y, 11, 1), datetime.min.time(), timezone.utc) + timedelta(hours=6)
    offset = -4 if dst_start <= now_utc < dst_end else -5
    return now_utc.astimezone(timezone(timedelta(hours=offset)))


def _between(t, start, end):
    m = t.hour * 60 + t.minute
    return start <= m < end


def market_session(market, now_utc):
    """장이 열려 있으면 그 시장의 현지 날짜(중복 주문 방지 기준)를, 아니면 None.
    장 시작 직후 5분과 마감 직전(국내 동시호가 15:20~, 미국 마감 5분 전)은 피해요.
    공휴일은 모르지만, 그날은 서버가 주문을 거절하고 기록만 남아요."""
    if market == "kr":
        t = now_utc.astimezone(KST)
        ok = t.weekday() < 5 and _between(t, 9 * 60 + 5, 15 * 60 + 20)
    else:
        t = us_eastern(now_utc)
        ok = t.weekday() < 5 and _between(t, 9 * 60 + 35, 15 * 60 + 55)
    return t.date().isoformat() if ok else None


# ---------- 오늘 주문한 종목 (중복 방지) ----------

class OrderBook:
    """시장별 '현지 날짜'마다 주문한 종목을 파일에 저장해요. 프로그램을 껐다 켜도 같은 날 같은 종목을 또 사지 않아요."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)

    def _key(self, market, session):
        return f"{market}:{session}"

    def symbols(self, market, session):
        return {e["symbol"] for e in self.data.get(self._key(market, session), [])}

    def pending_buys(self, market, session):
        return sum(1 for e in self.data.get(self._key(market, session), []) if e["side"] == "buy")

    def add(self, market, session, side, symbol):
        self.data.setdefault(self._key(market, session), []).append({"side": side, "symbol": symbol})
        # 오래된 날짜는 정리해요.
        keep = sorted(self.data)[-10:]
        self.data = {k: self.data[k] for k in keep}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)


# ---------- 한 번 돌기 ----------

def send(kis, o, slippage_pct):
    if o["market"] == "kr":
        # 국내는 시장가로 내서 바로 체결되게 해요. limit_price 가 있으면 그 지정가로.
        limit = o.get("limit_price")
        return kis.kr_order(o["side"], o["symbol"], o["qty"], limit), o["qty"], limit
    k = slippage_pct / 100
    price = round(o["price"] * (1 + k if o["side"] == "buy" else 1 - k), 2)
    qty = o["qty"]
    if o["side"] == "buy":
        # 비싸진 가격만큼 수량을 줄여서 계획한 금액을 넘지 않게 해요.
        qty = math.floor(o["qty"] * o["price"] / price)
        if qty == 0 and o["qty"] == 1:
            qty = 1  # 1주 주문(조건 채우기)은 그대로 내요
    if qty <= 0:
        return None, 0, price
    return kis.us_order(o["side"], o["symbol"], o["exchange"], qty, price), qty, price


def run_cycle(kis, cfg, book, now_utc, log, orders_sent_today, out=print):
    auto = {**DEFAULTS, **cfg.get("auto", {})}
    limit = cfg["limits"]["max_orders_per_day"]
    sessions = {m: market_session(m, now_utc) for m in ("kr", "us")}
    markets = tuple(m for m, s in sessions.items() if s)
    if not markets:
        return 0
    skip = {m: book.symbols(m, sessions[m]) for m in markets}
    pending = {m: book.pending_buys(m, sessions[m]) for m in markets}
    orders, notes = make_plan(kis, cfg, markets, skip=skip, pending=pending)
    log({"event": "auto_plan", "env": kis.env, "markets": list(markets), "orders": orders, "notes": notes})
    stamp = now_utc.astimezone(KST).strftime("%H:%M")
    out(f"[{stamp}] {'·'.join('국내' if m == 'kr' else '미국' for m in markets)} 확인: 주문 후보 {len(orders)}건")
    sent = 0
    for o in orders:
        if orders_sent_today() >= limit:
            out(f"  오늘 주문 한도({limit}건) 도달, 오늘은 더 주문하지 않아요")
            log({"event": "auto_limit_reached", "limit": limit})
            break
        side = "매수" if o["side"] == "buy" else "매도"
        try:
            res, qty, price = send(kis, o, auto["us_limit_slippage_pct"])
            if res is None:
                continue
            book.add(o["market"], sessions[o["market"]], o["side"], o["symbol"])
            log({"event": "order_sent", "env": kis.env, "auto": True, "order": {**o, "qty": qty, "sent_price": price},
                 "result": res})
            out(f"  {side} {o['name']} {qty}주 접수 (주문번호 {res.get('ODNO', '?')}) - {o['reason']}")
            sent += 1
        except KisError as e:
            log({"event": "order_failed", "env": kis.env, "auto": True, "order": o, "error": str(e)})
            out(f"  {side} {o['name']} 실패: {e}")
    return sent


def run_us_aggressive(kis, state, session, today, stamp, place, out=print):
    """미국 입상형: 2거래일마다 20일 수익률 1위 1종목에 거의 전부, 조건 못 채웠으면 다른 상위 종목 1주씩."""
    from . import contest
    d = state.d
    due = contest.us_agg_due(state, session)
    need_filler = contest.us_conditions_left(state) and session not in d.get("us_days", [])
    target = d.get("us_target")
    if not (due or need_filler or (target and not d.get("us_target_filled"))):
        return 0
    before = d.get("us_orders", 0)
    held = kis.us_balance()["holdings"]
    ranked = contest.us_rank(kis, today, out, d.get("blocked", [])) if (due or need_filler or not target) else []
    if due:
        if not ranked:
            out(f"[{stamp}] 미국 입상형: 상승 추세 종목이 없어 현금으로")
        target = [ranked[0][1], ranked[0][2]] if ranked else None
        d["us_target"] = target
        d["us_target_filled"] = False
        d.setdefault("us_rebalances", []).append(session)
        fillers = set(d.get("us_fillers", []))
        for sym, h in held.items():
            if (not target or sym != target[0]) and sym not in fillers:
                place({"market": "us", "side": "sell", "symbol": sym, "exchange": h.get("exchange") or "NASD",
                       "name": sym, "qty": h["qty"], "price": h["price"], "currency": "USD",
                       "reason": "1위에서 빠짐"}, session)
    if target and not d.get("us_target_filled"):
        sym, excg = target
        price = kis.us_price(sym, excg)["price"]
        cash = kis.us_buyable(sym, excg, price)["usd"] if price > 0 else 0
        qty = math.floor(cash * 0.97 / price) if price > 0 else 0
        if qty > 0:
            out(f"[{stamp}] 미국 입상형: {sym} 매수")
            if place({"market": "us", "side": "buy", "symbol": sym, "exchange": excg, "name": sym, "qty": qty,
                      "price": price, "currency": "USD", "reason": "20일 수익률 1위"}, session):
                d["us_target_filled"] = True
        else:
            out(f"[{stamp}] 미국 {sym}: 주문가능금액(${cash:,.2f})이 부족해서 다음 확인 때 다시 시도해요")
            if cash <= 0:
                out("  ※ 달러 주문가능금액이 0이에요. show_balance.bat 결과를 확인해 주세요 (모의투자 달러 예수금·환전 필요 여부)")
    if need_filler:
        used = set(d.get("us_symbols", [])) | ({target[0]} if target else set())
        pick = next(((s, sym, ex) for s, sym, ex in ranked if sym not in used), None)
        if pick:
            _, sym, excg = pick
            price = kis.us_price(sym, excg)["price"]
            if price > 0:
                out(f"[{stamp}] 미국 수상 조건 채우기: {sym} 1주")
                if place({"market": "us", "side": "buy", "symbol": sym, "exchange": excg, "name": sym, "qty": 1,
                          "price": price, "currency": "USD", "reason": "조건 채우기(5종목·5일)"}, session):
                    d.setdefault("us_fillers", []).append(sym)
    out(f"  미국 조건 진행(주문 접수 기준): 종목 {len(d.get('us_symbols', []))}/5, 매매일 {len(d.get('us_days', []))}/5, "
        f"주문 {d.get('us_orders', 0)}/5회")
    return d.get("us_orders", 0) - before


def kr_fill_missing(kis, state, session, place, out=print, universe=None):
    """오늘 목표 종목 중 아직 안 산 것을 사요 (총평가 / 목표 수 만큼). 다 사면 남은 현금을 한 번 채우고 kr_fill 을 지워요.
    모의투자 매매불가 종목이면 다음 순위 종목으로 바꿔서 사요."""
    from . import contest
    fill = state.d["kr_fill"]
    bal = kis.kr_balance()
    held = bal["holdings"]
    manual = state.d.get("manual", {})
    per = contest.auto_budget(bal, manual, fill["top_k"]) * fill.get("scale", 1.0)
    for _ in range(3):  # 매매불가로 바꾼 종목도 같은 번에 사 보려고 몇 번 돌아요
        kr_substitute_blocked(kis, state, fill, held, manual, session, out, universe)
        todo = [t for t in fill["targets"] if t[1] not in held and t[1] not in fill["placed"] and t[1] not in manual
                and t[1] not in state.d.get("blocked", [])]
        if not todo:
            break
        for sc, code, name in todo:
            o = contest.kr_buy_order(kis, code, name, per, f"20일 수익률 {sc * 100:+.1f}%", out)
            if o and place(o, session):
                fill["placed"].append(code)
                fill.setdefault("spent", {})[code] = o["qty"] * o["price"]
        if not any(t[1] in state.d.get("blocked", []) for t in fill["targets"]):
            break
    if all(t[1] in fill["placed"] or t[1] in held or t[1] in manual for t in fill["targets"]):
        if not fill.get("topped"):
            fill["topped"] = True
            kr_top_up(kis, fill, bal, manual, per, session, place, out)
        state.d["kr_fill"] = None
    else:
        out("  못 산 종목은 다음 확인 때(15분 뒤) 다시 시도해요")


def kr_substitute_blocked(kis, state, fill, held, manual, session, out=print, universe=None):
    """목표 중 모의투자 매매불가 종목을 다음 순위(오늘 순위 계산 때 남겨 둔 reserve)로 바꿔요.
    reserve 가 없으면(예전 버전에서 계산한 순위) 순위를 다시 계산해요."""
    from datetime import date as _date
    from . import contest
    blocked = set(state.d.get("blocked", []))
    if not any(t[1] in blocked and t[1] not in held for t in fill["targets"]):
        return
    cached = state.d.get("kr_ranked") or {}
    reserve = [tuple(t) for t in cached.get("reserve", [])] if cached.get("session") == session else []
    taken = {t[1] for t in fill["targets"]}
    if not [r for r in reserve if r[1] not in blocked and r[1] not in taken and r[1] not in manual] and universe:
        out("  매매불가 종목 대신 살 다음 순위를 찾으려고 순위를 다시 계산해요 (몇 분 걸려요)")
        rest = []
        p = contest.kr_params(state)
        top, _ = contest.kr_targets(kis, universe, _date.fromisoformat(session), out, p,
                                    exclude=set(manual) | blocked | taken, reserve=rest)
        reserve = list(top) + rest
        if cached.get("session") == session:
            cached["reserve"] = [list(r) for r in reserve]
    new = []
    for t in [tuple(x) for x in fill["targets"]]:
        if t[1] in blocked and t[1] not in held:
            rep = next((r for r in reserve if r[1] not in blocked and r[1] not in taken and r[1] not in manual), None)
            if rep:
                out(f"  {t[2]}({t[1]}) 는 모의투자 매매불가라 다음 순위 {rep[2]}({rep[1]}) {rep[0] * 100:+.1f}% 를 대신 사요")
                taken.add(rep[1])
                new.append(rep)
                continue
        new.append(t)
    fill["targets"] = [list(t) for t in new]


def kr_top_up(kis, fill, bal, manual, per, session, place, out=print):
    """마지막 종목은 주문가능금액(지정가 +1% 기준) 때문에 1/top_k 보다 조금 덜 사져요.
    그 모자란 만큼(1/top_k 까지만, 남은 현금 안에서) 한 번 더 사서 놀고 있는 현금을 줄여요."""
    from . import contest
    spent = fill.get("spent", {})
    held = bal["holdings"]
    value = {c: spent.get(c, held[c]["qty"] * held[c]["price"] if c in held else 0.0) for _, c, _ in fill["targets"]}
    used = sum(h["qty"] * h["price"] for c, h in held.items() if c not in spent) + sum(spent.values())
    left = bal["total"] - used
    # 이번에 새로 산 종목만 (이미 들고 있던 종목이 떨어져서 모자란 건 채우지 않아요 — 백테스트와 같게)
    cands = [(per - value[c], c, n) for _, c, n in fill["targets"] if c in spent and c not in manual]
    if not cands or left <= 0:
        return
    short, code, name = max(cands)
    price = kis.kr_price(code)["price"]
    if price <= 0 or min(short, left) < price:
        return
    limit = contest.kr_limit_up(price)
    try:
        cash = kis.kr_buyable(code, limit)
    except KisError as e:
        out(f"    {name}({code}) 주문가능금액 조회 실패: {e}")
        return
    qty = math.floor(min(short, left, cash * 0.998) / price)
    qty = min(qty, math.floor(cash * 0.998 / limit))
    if qty > 0:
        place({"market": "kr", "side": "buy", "symbol": code, "name": name, "qty": qty, "price": price,
               "limit_price": limit, "currency": "KRW", "reason": "남은 현금 채우기 (1/top_k 까지)"}, session)


def kr_fill_short(kis, state, bal, manual, p, session, place, out=print):
    """리밸런스한 날, 오늘 목표 종목 중 산 금액(수량 × 매입가)이 몫의 90% 도 안 되는 것을 마저 사요.
    지정가(+1%)보다 값이 뛰어 일부만 체결됐거나 매도 대금이 늦게 잡혀 덜 산 경우예요.
    매입가 기준이라 산 뒤에 값이 떨어진 종목을 더 사지는 않아요 (백테스트와 같게)."""
    from . import contest
    cached = state.d.get("kr_ranked")
    if not cached or cached.get("session") != session:
        return
    per = contest.auto_budget(bal, manual, p["top_k"]) * cached.get("scale", 1.0)
    for sc, code, name in [tuple(t) for t in cached["targets"]]:
        h = bal["holdings"].get(code)
        if not h or code in manual:
            continue
        cost = h["qty"] * h["avg_price"]
        if cost < per * 0.9:
            out(f"  {name}({code}) 산 금액 {cost:,.0f}원이 몫 {per:,.0f}원보다 적어서 마저 사요")
            o = contest.kr_buy_order(kis, code, name, per - cost, "모자란 비중 채우기", out)
            if o:
                place(o, session)


def kr_regime_top_up(kis, state, targets, manual, p, session, place, out=print):
    """약세장 → 보통으로 바뀐 날: 약세장 때 절반 금액으로 들고 있던 목표 종목을 다시 1/top_k 까지 채워요."""
    from . import contest
    bal = kis.kr_balance()
    per = contest.auto_budget(bal, manual, p["top_k"])
    for sc, code, name in targets:
        h = bal["holdings"].get(code)
        if not h or code in manual:
            continue
        value = h["qty"] * h["price"]
        if value < per * 0.9:
            out(f"  {name}({code}) 약세장이 끝나서 1/{p['top_k']} 까지 다시 채워요")
            o = contest.kr_buy_order(kis, code, name, per - value, "약세장 끝, 비중 다시 채우기", out)
            if o:
                place(o, session)


def kr_manual_adjust(kis, state, session, place, today, out=print):
    """수동 보유 종목: 'manual keep' 으로 정한 비중(target_pct)보다 많으면 한 번만 그만큼 팔아 맞추고,
    어제 종가가 20일선 아래면 알려 줘요 (파는 건 직접: manual sell)."""
    from . import contest
    manual = state.d.get("manual", {})
    bal = kis.kr_balance()
    for code, v in manual.items():
        h = bal["holdings"].get(code)
        if not h or h["price"] <= 0:
            continue
        name = v.get("name") or h["name"]
        pct = v.get("target_pct")
        if pct:
            extra = math.floor((h["qty"] * h["price"] - bal["total"] * pct / 100) / h["price"])
            if extra <= 0 or extra * h["price"] < bal["total"] * 0.01:
                v.pop("target_pct")
            elif place({"market": "kr", "side": "sell", "symbol": code, "name": name, "qty": extra, "price": h["price"],
                        "currency": "KRW", "reason": f"수동 보유 비중 {pct:.0f}% 로 맞춤"}, session):
                v.pop("target_pct")
                time.sleep(1)  # 매도 체결이 잔고에 반영되게 잠깐 쉬어요
        try:
            closes = contest.closes_until_yesterday(kis.kr_daily(code, with_dates=True), today)
        except KisError:
            continue
        e = contest.ema(closes, 20)
        if e and closes and closes[-1] < e:
            out(f"  ※ {name}: 어제 종가 {closes[-1]:,.0f}원 < 20일선 {e:,.0f}원, 단기 추세가 꺾였어요. "
                f"팔려면: python -m trading manual sell {code}")


def run_contest_cycle(kis, cfg, state, now_utc, log, out=print, universe=None):
    """대회 모드 한 번 돌기 (trading/contest.py 공식). 반환: 낸 주문 수."""
    from . import contest
    sent = 0
    slip = {**DEFAULTS, **cfg.get("auto", {})}["us_limit_slippage_pct"]
    stamp = now_utc.astimezone(KST).strftime("%H:%M")

    def place(o, session):
        nonlocal sent
        side = "매수" if o["side"] == "buy" else "매도"
        try:
            res, qty, price = send(kis, o, slip)
            if res is None:
                return False
            amount = qty * (price or o["price"])
            state.record(o["market"], o["symbol"], amount, session)
            if o["market"] == "us":
                contest.us_record(state, o["symbol"], session)
            log({"event": "order_sent", "env": kis.env, "contest": True,
                 "order": {**o, "qty": qty, "sent_price": price}, "result": res})
            out(f"  {side} {o['name']}({o['symbol']}) {qty}주 접수 (주문번호 {res.get('ODNO', '?')}) - {o['reason']}")
            sent += 1
            return True
        except KisError as e:
            log({"event": "order_failed", "env": kis.env, "contest": True, "order": o, "error": str(e)})
            out(f"  {side} {o['name']}({o['symbol']}) 실패: {e}")
            if "매매불가" in str(e) and o["symbol"] not in state.d.setdefault("blocked", []):
                state.d["blocked"].append(o["symbol"])  # 모의투자에서 못 사는 종목(레버리지 ETF 등)은 다음부터 빼요
                out(f"  → {o['symbol']} 은(는) 모의투자 매매불가라 앞으로 후보에서 빼요")
            return False

    kr_session = market_session("kr", now_utc)
    if kr_session:
        state.reload_manual()
    if kr_session and contest.kr_due(state, kr_session):
        today = now_utc.astimezone(KST).date()
        threshold = cfg.get("contest", {}).get("switch_to_stable_at", contest.SWITCH_TO_STABLE_AT)
        gain = contest.maybe_switch_to_stable(state, kis.kr_balance()["total"], threshold, out)
        mode = state.d.get("kr_mode", contest.DEFAULT_KR_MODE)
        p = contest.kr_params(state)
        out(f"[{stamp}] 국내 리밸런스 ({'입상형: 상위 3종목' if mode == 'aggressive' else '안정형: 상위 5종목'}, "
            f"시작 대비 {gain * 100:+.1f}%): {len(universe)}종목 순위 계산 중 (몇 분 걸려요)")
        manual = state.d.get("manual", {})
        if manual:
            out("  수동 보유(자동매매가 안 건드림): " + ", ".join(f"{v.get('name', c)}({c})" for c, v in manual.items()))
            kr_manual_adjust(kis, state, kr_session, place, today, out)
        cached = state.d.get("kr_ranked")
        if cached and cached.get("session") == kr_session and cached.get("mode") == mode:
            # 같은 날 앞선 시도에서 순위를 이미 계산했으면 (주문 단계에서 실패한 경우) 다시 계산하지 않아요
            targets, risk_on = [tuple(t) for t in cached["targets"] if t[1] not in manual], cached["risk_on"]
            out("  (아까 계산한 순위를 그대로 써요)")
        else:
            reserve = []
            targets, risk_on = contest.kr_targets(kis, universe, today, out, p,
                                                  exclude=set(manual) | set(state.d.get("blocked", [])),
                                                  reserve=reserve)
            state.d["kr_ranked"] = {"session": kr_session, "mode": mode, "targets": targets, "risk_on": risk_on,
                                    "reserve": reserve}
            state.save()
            time.sleep(2)  # 250종목 조회 직후라 계좌 조회 전에 잠깐 쉬어요
        if p.get("regime_ema"):
            out("  시장: " + ("좋은 날 (지수 > 20일 평균) → 5종목" if risk_on
                             else "나쁜 날 (지수 < 20일 평균) → 상위 2종목만, 나머지 현금"))
        elif p.get("regime_breadth"):
            out("  시장: " + (f"보통 → 상위 {p['top_k']}종목, 1/{p['top_k']} 씩" if risk_on
                             else f"약세장 (60일선 위 종목 {p['regime_breadth'] * 100:.0f}% 미만) → "
                                  f"상위 {p['risk_off_top']}종목, 금액은 {p.get('risk_off_scale', 1):.0%} 만 (나머지 현금)"))
        out(f"  목표 {len(targets)}종목: " + ", ".join(f"{n}({c}) {s * 100:+.1f}%" for s, c, n in targets) if targets
            else "  상승 추세 종목이 없어 현금 보유")
        # 약세장이면 금액만 줄여요 (risk_off_scale). 판단이 바뀐 날은 들고 있던 종목도 그 비중으로 맞춰요.
        scale = 1.0 if risk_on else p.get("risk_off_scale", 1.0)
        last_on = state.d.get("kr_last_risk_on", True)
        state.d["kr_ranked"]["scale"] = scale
        # 2종목 시절에 산 종목이 1/2 씩이라, 3종목 공식의 첫 리밸런스 때 한 번만 1/3 로 맞춰요
        trim = (mode == "aggressive" and state.d.get("kr_equalized_top_k") != p["top_k"]) \
            or (scale < 1 and last_on)
        sells, buys = contest.kr_plan(kis, targets, p, manual, trim=trim, scale=scale)
        state.d["kr_equalized_top_k"] = p["top_k"]
        regained = risk_on and not last_on and p.get("risk_off_scale", 1.0) < 1
        state.d["kr_last_risk_on"] = risk_on
        for o in sells:
            place(o, kr_session)
        state.d["kr_rebalances"].append(kr_session)
        # 매수는 매도가 끝난 뒤 주문가능금액을 보고 1% 높은 지정가로. 못 산 건 같은 날 다음 확인 때 다시 시도해요.
        state.d["kr_fill"] = {"session": kr_session, "targets": [list(t) for t in targets], "placed": [],
                              "top_k": p["top_k"], "scale": scale}
        kr_fill_missing(kis, state, kr_session, place, out, universe)
        if regained:
            kr_regime_top_up(kis, state, targets, manual, p, kr_session, place, out)
        d = state.d
        out(f"  대회 조건 진행(주문 접수 기준): 매매금액 {d['amount'] / 1e8:.2f}억/5억, 매매일 {len(d['days'])}/5일, "
            f"종목 {len(d['symbols'])}/5, 주문 {d['orders']}/5회")
        state.save()

    elif kr_session:
        if not state.d.get("kr_fill") and state.d.get("kr_rebalances", [None])[-1] == kr_session \
                and state.d.get("kr_fill_checked") != kr_session:
            # 오늘 리밸런스했는데 (예전 버전에서) 목표 종목을 다 못 샀을 수 있어요 → 한 번 확인해서 채워요
            state.d["kr_fill_checked"] = kr_session
            mode = state.d.get("kr_mode", contest.DEFAULT_KR_MODE)
            p = contest.kr_params(state)
            manual = state.d.get("manual", {})
            bal = kis.kr_balance()
            if len([c for c in bal["holdings"] if c not in manual]) >= p["top_k"]:
                kr_fill_short(kis, state, bal, manual, p, kr_session, place, out)
            else:
                cached = state.d.get("kr_ranked")
                if cached and cached.get("session") == kr_session:
                    targets = [tuple(t) for t in cached["targets"]]
                else:
                    out(f"[{stamp}] 국내: 오늘 못 산 종목 확인용 순위 계산 (몇 분 걸려요)")
                    targets, _ = contest.kr_targets(kis, universe, now_utc.astimezone(KST).date(), out, p,
                                                    exclude=manual)
                state.d["kr_fill"] = {"session": kr_session, "targets": [list(t) for t in targets], "placed": [],
                                      "top_k": p["top_k"]}
        fill = state.d.get("kr_fill")
        if fill and fill.get("session") == kr_session:
            kr_fill_missing(kis, state, kr_session, place, out, universe)
        state.save()

    us_session = market_session("us", now_utc)
    if state.d.get("us_mode") == "off":
        us_session = None  # 미국은 쉬어요 (python -m trading contest --us-mode basic 으로 다시 켜기)
    if us_session and state.d.get("us_mode", "basic") == "aggressive":
        sent += run_us_aggressive(kis, state, us_session, us_eastern(now_utc).date(), stamp, place, out)
        state.save()
    elif us_session:
        held = kis.us_balance()["holdings"]
        if contest.us_due(state, us_session, held):
            today = us_eastern(now_utc).date()
            pick = contest.us_pick(kis, cfg["us"]["watchlist"], held, today, out, state.d.get("blocked", []))
            if pick is None:
                out(f"[{stamp}] 미국: 살 만한 상승 추세 종목이 없어요")
            else:
                sc, sym, excg = pick
                price = kis.us_price(sym, excg)["price"]
                cash = kis.us_buyable(sym, excg, price)["usd"]
                per = cash / (contest.US["top_k"] - len(held))
                # 0.5% 불리한 지정가만큼은 send() 가 수량을 줄여요
                qty = math.floor(per * 0.98 / price) if price > 0 else 0
                if qty > 0:
                    out(f"[{stamp}] 미국 {len(held) + 1}번째 종목 매수")
                    place({"market": "us", "side": "buy", "symbol": sym, "exchange": excg, "name": sym, "qty": qty,
                           "price": price, "currency": "USD", "reason": f"60일 수익률 {sc * 100:+.1f}%"}, us_session)
                else:
                    out(f"[{stamp}] 미국 {sym}: 주문가능금액(${cash:,.2f})이 부족해요")
            state.d["us_buys"].append(us_session)
            state.save()
    return sent


def keep_awake(on=True):
    """Windows 에서 프로그램이 도는 동안 PC 가 절전에 들어가지 않게 해요 (다른 OS 는 아무것도 안 해요)."""
    try:
        import ctypes
        flags = 0x80000000 | (0x00000001 if on else 0)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def run_contest_once(kis, cfg, here, log, now=lambda: datetime.now(timezone.utc), sleep=time.sleep,
                     out=print, retries=3):
    """작업 스케줄러용: 장이 열려 있으면 대회 모드를 한 번만 돌고 끝내요.
    절전에서 막 깨어나면 인터넷 연결이 늦을 수 있어서, 실패하면 1분 쉬고 다시 시도해요."""
    from . import contest
    state = contest.State(os.path.join(here, ".cache", f"contest-{kis.env}.json"))
    universe = contest.load_universe(os.path.join(here, "universe_kr.csv"))
    t = now()
    markets = ("kr",) if state.d.get("us_mode") == "off" else ("kr", "us")
    if not any(market_session(m, t) for m in markets):
        out(f"[{t.astimezone(KST):%m-%d %H:%M}] 장이 닫혀 있어서 할 일이 없어요.")
        return 0
    keep_awake(True)
    try:
        for attempt in range(retries):
            try:
                n = run_contest_cycle(kis, cfg, state, now(), log, out, universe)
                kr_session = market_session("kr", now())
                if kr_session and state.d.get("kr_rebalances", [None])[-1] == kr_session \
                        and state.d.get("kr_fill_checked") != kr_session:
                    # 리밸런스한 날은 2분 뒤 한 번 더: 못 산 종목·덜 산 종목을 채워요 (예약 실행은 한 번만 돌아서)
                    out("  2분 뒤 체결 확인하고 덜 산 종목을 채워요")
                    sleep(120)
                    n += run_contest_cycle(kis, cfg, state, now(), log, out, universe)
                out(f"[{now().astimezone(KST):%m-%d %H:%M}] 끝 (주문 {n}건)")
                return n
            except Exception as e:
                log({"event": "auto_error", "error": str(e), "once": True})
                out(f"  오류: {e}" + (" (1분 뒤 다시 시도)" if attempt < retries - 1 else ""))
                if attempt < retries - 1:
                    sleep(60)
        return 0
    finally:
        keep_awake(False)


def run_forever(kis, cfg, here, log, orders_sent_today, now=lambda: datetime.now(timezone.utc),
                sleep=time.sleep, out=print, contest_mode=False):
    auto = {**DEFAULTS, **cfg.get("auto", {})}
    interval = timedelta(minutes=auto["interval_min"])
    stop_file = os.path.join(here, "STOP")
    book = OrderBook(os.path.join(here, ".cache", f"auto-orders-{kis.env}.json"))
    if contest_mode:
        from . import contest
        state = contest.State(os.path.join(here, ".cache", f"contest-{kis.env}.json"))
        universe = contest.load_universe(os.path.join(here, "universe_kr.csv"))
        mode = state.d.get("kr_mode", contest.DEFAULT_KR_MODE)
        out(f"대회 모드: 국내 {len(universe)}종목 중 {'입상형(상위 3종목)' if mode == 'aggressive' else '안정형(상위 5종목)'} "
            f"2거래일마다 교체, 미국은 하루 1종목씩 5종목까지 매수 후 보유")
    out(f"자동매매 시작 ({'모의투자' if kis.env == 'demo' else '실전'}), {auto['interval_min']}분마다 확인해요.")
    out("멈추려면 Ctrl+C 를 누르거나 trading 폴더에 STOP 파일을 만드세요.")
    last_run = None
    waiting_shown = False
    keep_awake(True)  # 켜 둔 동안은 절전에 들어가지 않게
    while True:
        if os.path.exists(stop_file):
            out("STOP 파일이 있어서 멈춰요. 다시 시작하려면 STOP 파일을 지우세요.")
            return
        t = now()
        open_now = any(market_session(m, t) for m in ("kr", "us"))
        if open_now and (last_run is None or t - last_run >= interval):
            waiting_shown = False
            try:
                if contest_mode:
                    run_contest_cycle(kis, cfg, state, t, log, out, universe)
                else:
                    run_cycle(kis, cfg, book, t, log, orders_sent_today, out)
            except Exception as e:  # 네트워크 오류 등으로 프로그램이 꺼지지 않게
                log({"event": "auto_error", "error": str(e)})
                out(f"  오류: {e} (다음 확인 때 다시 시도해요)")
            last_run = t
        elif not open_now and not waiting_shown:
            out(f"[{t.astimezone(KST).strftime('%H:%M')}] 장이 닫혀 있어요. 열리면 자동으로 시작해요.")
            waiting_shown = True
        sleep(30)
