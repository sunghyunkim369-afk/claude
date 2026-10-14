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
        # 국내는 시장가로 내서 바로 체결되게 해요.
        return kis.kr_order(o["side"], o["symbol"], o["qty"], None), o["qty"], None
    k = slippage_pct / 100
    price = round(o["price"] * (1 + k if o["side"] == "buy" else 1 - k), 2)
    qty = o["qty"]
    if o["side"] == "buy":
        # 비싸진 가격만큼 수량을 줄여서 계획한 금액을 넘지 않게 해요.
        qty = math.floor(o["qty"] * o["price"] / price)
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
                return
            amount = qty * (price or o["price"])
            state.record(o["market"], o["symbol"], amount, session)
            log({"event": "order_sent", "env": kis.env, "contest": True,
                 "order": {**o, "qty": qty, "sent_price": price}, "result": res})
            out(f"  {side} {o['name']}({o['symbol']}) {qty}주 접수 (주문번호 {res.get('ODNO', '?')}) - {o['reason']}")
            sent += 1
        except KisError as e:
            log({"event": "order_failed", "env": kis.env, "contest": True, "order": o, "error": str(e)})
            out(f"  {side} {o['name']}({o['symbol']}) 실패: {e}")

    kr_session = market_session("kr", now_utc)
    if kr_session and contest.kr_due(state, kr_session):
        today = now_utc.astimezone(KST).date()
        out(f"[{stamp}] 국내 리밸런스: {len(universe)}종목 순위 계산 중 (몇 분 걸려요)")
        targets, risk_on = contest.kr_targets(kis, universe, today, out)
        out("  시장: " + ("좋은 날 (지수 > 20일 평균) → 5종목" if risk_on
                         else "나쁜 날 (지수 < 20일 평균) → 상위 2종목만, 나머지 현금"))
        out(f"  목표 {len(targets)}종목: " + ", ".join(f"{n}({c}) {s * 100:+.1f}%" for s, c, n in targets) if targets
            else "  상승 추세 종목이 없어 현금 보유")
        sells, buys = contest.kr_plan(kis, targets)
        for o in sells:
            place(o, kr_session)
        for o in buys:
            place(o, kr_session)
        state.d["kr_rebalances"].append(kr_session)
        d = state.d
        out(f"  대회 조건 진행(주문 접수 기준): 매매금액 {d['amount'] / 1e8:.2f}억/5억, 매매일 {len(d['days'])}/5일, "
            f"종목 {len(d['symbols'])}/5, 주문 {d['orders']}/5회")
        state.save()

    us_session = market_session("us", now_utc)
    if us_session:
        held = kis.us_balance()["holdings"]
        if contest.us_due(state, us_session, held):
            today = us_eastern(now_utc).date()
            pick = contest.us_pick(kis, cfg["us"]["watchlist"], held, today, out)
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
    if not any(market_session(m, t) for m in ("kr", "us")):
        out(f"[{t.astimezone(KST):%m-%d %H:%M}] 장이 닫혀 있어서 할 일이 없어요.")
        return 0
    keep_awake(True)
    try:
        for attempt in range(retries):
            try:
                n = run_contest_cycle(kis, cfg, state, now(), log, out, universe)
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
        out(f"대회 모드: 국내 {len(universe)}종목 중 상위 5종목 2거래일마다 교체, 미국은 하루 1종목씩 5종목까지 매수 후 보유")
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
