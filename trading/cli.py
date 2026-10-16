"""반자동 매매 프로그램.

  python3 -m trading status            잔고 보기
  python3 -m trading plan [--market kr|us|all]    주문 후보만 보기 (주문 안 함)
  python3 -m trading trade [--market kr|us|all]   후보를 하나씩 확인(y)한 것만 주문
  python3 -m trading order buy AAPL 1  주문 하나 직접 내기 (체결 시험용, 확인 후 주문. -y 면 확인 없이)
  python3 -m trading contest           대회 모드 자동매매 (contest.py 공식, 모의투자 전용)
  python3 -m trading contest --once    대회 모드를 한 번만 돌고 끝내기
  python3 -m trading contest --reset   계좌를 초기화했을 때 진행 상태 지우기
  python3 -m trading contest --mode stable   국내 공식을 안정형으로 (aggressive = 입상형)
  python3 -m trading manual check      오늘 급락 반등 조건 확인 (시총 상위 30 평균 −2% 이하 + −7% 이상 빠진 종목)
  python3 -m trading manual buy 005930 [--pct 20]   수동 매수 (총평가의 20%), 자동매매가 안 파는 종목으로 등록
  python3 -m trading manual sell 005930  수동 보유 종목 전부 매도하고 등록 해제
  python3 -m trading manual add|remove 005930   MTS 로 직접 산 종목 등록 / 해제 (해제하면 자동매매가 관리)
  python3 -m trading manual list       수동 보유 종목 보기
  python3 -m trading schedule          Windows 작업 스케줄러에 예약 (절전에서 깨워 평일 09:10·23:40 실행)
  python3 -m trading auto              자동매매 (장중에 정해진 간격으로 확인하고 바로 주문)
  python3 -m trading update            최신 프로그램 받기 (.env·config.json 은 그대로)
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

from .kis import Kis, KisError

HERE = os.path.dirname(os.path.abspath(__file__))

UPDATE_BASE = "https://raw.githubusercontent.com/sunghyunkim369-afk/claude/claude/awesome-edison-24zdn0/trading/"
# 사용자가 고치는 .env, config.json 은 덮어쓰지 않아요.
# 받을 파일 목록은 저장소의 trading/MANIFEST 를 먼저 보고, 못 받으면 아래 목록을 써요.
UPDATE_FILES = ["__init__.py", "__main__.py", "auto.py", "cli.py", "contest.py", "kis.py", "planner.py", "strategy.py",
                "universe_kr.csv", "start_contest.bat", "show_balance.bat", "schedule.py", "setup_schedule.bat",
                "README.md", ".env.example", "MANIFEST", "tests/__init__.py", "tests/test_trading.py"]


def load_env(path=os.path.join(HERE, ".env")):
    """trading/.env 의 KEY=VALUE 를 읽어요. 이미 설정된 환경변수가 우선이에요."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path=os.path.join(HERE, "config.json")):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_client():
    load_env()
    missing = [k for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT") if not os.environ.get(k)]
    if missing:
        sys.exit(f"환경변수가 없어요: {', '.join(missing)}\ntrading/.env.example 을 trading/.env 로 복사해 채워 주세요.")
    env = os.environ.get("KIS_ENV", "demo")
    return Kis(os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"], os.environ["KIS_ACCOUNT"],
               env=env, cache_dir=os.path.join(HERE, ".cache"))


# ---------- 기록 ----------

def log_path(day=None):
    return os.path.join(HERE, "logs", f"{(day or datetime.now()).strftime('%Y-%m-%d')}.jsonl")


def write_log(entry):
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), **entry},
                           ensure_ascii=False) + "\n")


def orders_sent_today():
    path = log_path()
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if json.loads(line).get("event") == "order_sent")


# ---------- 출력 ----------

def fmt_money(v, currency):
    return f"${v:,.2f}" if currency == "USD" else f"{v:,.0f}원"


def print_orders(orders):
    if not orders:
        print("주문 후보 없음")
        return
    print(f"{'#':>2} {'시장':<4} {'구분':<4} {'종목':<16} {'수량':>6} {'지정가':>14} {'금액':>16}  이유")
    for i, o in enumerate(orders, 1):
        side = "매수" if o["side"] == "buy" else "매도"
        label = f"{o['name']}({o['symbol']})" if o["name"] != o["symbol"] else o["symbol"]
        print(f"{i:>2} {'국내' if o['market'] == 'kr' else '미국':<4} {side:<4} {label:<16} {o['qty']:>6} "
              f"{fmt_money(o['price'], o['currency']):>14} {fmt_money(o['price'] * o['qty'], o['currency']):>16}  {o['reason']}")


def cmd_status(kis, cfg, markets):
    print(f"[{'모의투자' if kis.env == 'demo' else '실전'}] 계좌 {kis.cano}-{kis.acnt_prdt_cd}")
    if "kr" in markets:
        b = kis.kr_balance()
        print(f"\n국내: 예수금 {b['cash']:,.0f}원 / 총평가 {b['total']:,.0f}원")
        for code, h in b["holdings"].items():
            pnl = (h["price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0
            print(f"  {h['name']}({code}) {h['qty']}주  평균 {h['avg_price']:,.0f} → 현재 {h['price']:,.0f}  {pnl:+.1f}%")
    if "us" in markets:
        b = kis.us_balance()
        print("\n미국:")
        try:
            cash = kis.us_cash()
            ccy = ", ".join(f"{c} 예수금 {v['예수금']:,.2f}" for c, v in cash["by_currency"].items()) or "외화 예수금 없음"
            print(f"  {ccy} / 외화사용가능 {cash['usable_usd']:,.2f}")
        except KisError as e:
            print(f"  외화 예수금 조회 실패: {e}")
        try:
            buy = kis.us_buyable("AAPL", "NASD", 200.0)
            print(f"  주문가능금액(달러) {buy['usd']:,.2f}  (원본: {buy['raw']})")
            if buy["usd"] <= 0:
                print("  ※ 달러 주문가능금액이 0이에요. 모의투자 계좌에 달러가 없거나 환전이 필요할 수 있어요.")
        except KisError as e:
            print(f"  주문가능금액 조회 실패: {e}")
        if not b["holdings"]:
            print("  보유 종목 없음")
        for sym, h in b["holdings"].items():
            pnl = (h["price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0
            print(f"  {sym} {h['qty']}주  평균 ${h['avg_price']:,.2f} → 현재 ${h['price']:,.2f}  {pnl:+.1f}%")


def cmd_plan(kis, cfg, markets, show_notes=True):
    from .planner import make_plan
    orders, notes = make_plan(kis, cfg, markets)
    if show_notes and notes:
        print("확인한 종목:")
        for n in notes:
            print("  " + n)
        print()
    print_orders(orders)
    write_log({"event": "plan", "env": kis.env, "orders": orders, "notes": notes})
    return orders


def cmd_trade(kis, cfg, markets, ask=input):
    if kis.env == "real":
        print("⚠ 실전 계좌예요. 실제 돈으로 주문이 나가요.")
        if ask("계속하려면 '실전' 이라고 입력하세요: ").strip() != "실전":
            print("취소했어요.")
            return
    orders = cmd_plan(kis, cfg, markets)
    if not orders:
        return
    limit = cfg["limits"]["max_orders_per_day"]
    print()
    for i, o in enumerate(orders, 1):
        sent = orders_sent_today()
        if sent >= limit:
            print(f"오늘 주문 한도({limit}건)에 도달해서 멈춰요.")
            break
        side = "매수" if o["side"] == "buy" else "매도"
        answer = ask(f"#{i} {o['symbol']} {o['qty']}주 {fmt_money(o['price'], o['currency'])} {side} 주문할까요? [y/N/q] ")
        answer = answer.strip().lower()
        if answer == "q":
            print("중단했어요.")
            break
        if answer != "y":
            write_log({"event": "order_skipped", "env": kis.env, "order": o})
            continue
        try:
            if o["market"] == "kr":
                res = kis.kr_order(o["side"], o["symbol"], o["qty"], o["price"])
            else:
                res = kis.us_order(o["side"], o["symbol"], o["exchange"], o["qty"], o["price"])
            print(f"  → 접수됨 (주문번호 {res.get('ODNO', '?')})")
            write_log({"event": "order_sent", "env": kis.env, "order": o, "result": res})
        except KisError as e:
            print(f"  → 실패: {e}")
            write_log({"event": "order_failed", "env": kis.env, "order": o, "error": str(e)})


def cmd_auto(kis, cfg, markets):
    if kis.env == "real" and os.environ.get("KIS_AUTO_REAL") != "yes":
        sys.exit("실전 계좌 자동매매는 막아 뒀어요. 정말 하려면 .env 에 KIS_AUTO_REAL=yes 를 추가하세요.")
    from . import auto
    auto.run_forever(kis, cfg, HERE, write_log, orders_sent_today)


def cmd_order(kis, side, symbol, qty, exchange=None, ask=input, wait=None):
    """주문 하나를 직접 내고, 잠시 뒤 잔고로 체결됐는지 확인해요 (OpenAPI 체결 시험용)."""
    import time
    wait = wait or time.sleep
    symbol = symbol.upper()
    market = "kr" if symbol.isdigit() and len(symbol) == 6 else "us"
    side_kr = "매수" if side == "buy" else "매도"
    if market == "kr":
        price = kis.kr_price(symbol)["price"]
        text = f"[국내] {symbol} {qty}주 시장가 {side_kr} (현재가 {price:,.0f}원)"
    else:
        exchange = exchange or "NASD"
        price = kis.us_price(symbol, exchange)["price"]
        if price <= 0:
            print(f"{symbol}({exchange}) 현재가를 못 받았어요. 미국 장 시간(한국시간 22:30~05:00)인지, 거래소가 맞는지 확인하세요.")
            return
        limit = round(price * (1.005 if side == "buy" else 0.995), 2)
        text = f"[미국] {symbol}({exchange}) {qty}주 지정가 ${limit:,.2f} {side_kr} (현재가 ${price:,.2f}, 체결되도록 0.5% 불리하게)"
    if ask is None:  # --yes: 확인 없이 주문
        print(text)
    elif ask(f"{text}\n주문할까요? [y/N] ").strip().lower() != "y":
        print("취소했어요.")
        return
    before = (kis.kr_balance() if market == "kr" else kis.us_balance())["holdings"].get(symbol, {}).get("qty", 0)
    res = kis.kr_order(side, symbol, qty, None) if market == "kr" else kis.us_order(side, symbol, exchange, qty, limit)
    print(f"→ 접수됨 (주문번호 {res.get('ODNO', '?')})")
    write_log({"event": "order_sent", "env": kis.env, "manual": True,
               "order": {"market": market, "side": side, "symbol": symbol, "qty": qty}, "result": res})
    print("10초 뒤 잔고로 체결 여부를 확인해요...")
    wait(10)
    after = (kis.kr_balance() if market == "kr" else kis.us_balance())["holdings"].get(symbol, {}).get("qty", 0)
    if after != before:
        print(f"✓ 체결됐어요: 보유 수량 {before} → {after}주. 대회 화면의 OpenAPI 체결횟수도 확인해 보세요.")
    else:
        print("아직 잔고에 반영되지 않았어요. 미체결이거나 잔고 반영이 늦은 거예요. 몇 분 뒤 status 로 다시 확인하세요.")


def cmd_manual(kis, args, pct=20.0, ask=input, wait=None):
    """수동 매매. 등록한 종목은 대회 자동매매가 팔지도 사지도 않아요 (상태: .cache/contest-demo.json 의 manual)."""
    import time
    from datetime import date
    from . import contest
    wait = wait or time.sleep
    if kis.env == "real":
        sys.exit("수동 매매 기능은 대회 모드(모의투자) 전용이에요.")
    usage = "사용법: python -m trading manual check | list | buy 종목코드 [--pct 20] | sell 종목코드 | add 종목코드 | remove 종목코드"
    if not args or args[0] not in ("check", "list", "buy", "sell", "add", "remove"):
        sys.exit(usage)
    action = args[0]
    code = args[1].zfill(6) if len(args) > 1 else None
    if action in ("buy", "sell", "add", "remove") and not (code and code.isdigit() and len(code) == 6):
        sys.exit(usage)
    state = contest.State(os.path.join(HERE, ".cache", f"contest-{kis.env}.json"))
    manual = state.d.setdefault("manual", {})
    today = date.today().isoformat()

    if action == "check":
        universe = contest.load_universe(os.path.join(HERE, "universe_kr.csv"))
        r = contest.CRASH_RULE
        print(f"시총 상위 {r['n']}종목 시세 확인 중 (30초쯤 걸려요)...")
        avg, drops, ok = contest.crash_check(kis, universe)
        print(f"시총 상위 {r['n']}종목 평균 등락률: {avg * 100:+.2f}% (조건: −{r['market'] * 100:.0f}% 이하)")
        if drops:
            print(f"−{r['stock'] * 100:.0f}% 이상 빠진 종목: " +
                  ", ".join(f"{n}({c}) {ch * 100:+.1f}%" for ch, c, n in drops))
        else:
            print(f"−{r['stock'] * 100:.0f}% 이상 빠진 종목: 없음")
        if ok:
            print("→ 조건 충족. 과거 5년: 이런 날 종가에 사면 다음 날 시가까지 비용 빼고 평균 +0.9%(62% 이익), "
                  "5일 보유 평균 +3.1%(64% 이익). 장 마감 직전(15:10~15:20)에 사는 게 가장 좋았어요.")
        else:
            print("→ 오늘은 급락 반등 조건이 아니에요. 그 종목만 혼자 빠진 날은 과거에 반등 효과가 없었어요.")
        return

    if action == "list":
        if not manual:
            print("수동 보유로 등록한 종목이 없어요.")
            return
        held = kis.kr_balance()["holdings"]
        for c, v in manual.items():
            h = held.get(c)
            if h:
                gain = h["price"] / h["avg_price"] - 1 if h["avg_price"] else 0.0
                print(f"  {h['name']}({c}) {h['qty']}주, 평가 {h['qty'] * h['price']:,.0f}원 ({gain * 100:+.2f}%), "
                      f"{v.get('since', '?')} 부터")
            else:
                print(f"  {v.get('name', c)}({c}) 등록돼 있지만 잔고에 없어요 (remove 로 지우세요)")
        return

    if action == "add":
        name = kis.kr_price(code).get("name") or code
        manual[code] = {"name": name, "since": today}
        state.save()
        print(f"{name}({code}) 을(를) 수동 보유로 등록했어요. 자동매매가 이 종목을 팔지 않아요.")
        return

    if action == "remove":
        v = manual.pop(code, None)
        state.save()
        print(f"{(v or {}).get('name', code)}({code}) 등록을 풀었어요. 다음 리밸런스 때 목표 종목이 아니면 자동매매가 팔아요."
              if v else f"{code} 은(는) 등록돼 있지 않아요.")
        return

    if action == "buy":
        bal = kis.kr_balance()
        q = kis.kr_price(code)
        name = q.get("name") or code
        budget = bal["total"] * pct / 100
        o = contest.kr_buy_order(kis, code, name, budget, "수동 매수")
        if not o:
            return
        text = (f"[수동 매수] {name}({code}) {o['qty']}주, 지정가 {o['limit_price']:,}원 "
                f"(현재가 {q['price']:,.0f}원, 오늘 {q.get('change', 0) * 100:+.1f}%, 총평가의 {pct:g}% ≈ {budget:,.0f}원)")
        if ask is None:
            print(text)
        elif ask(f"{text}\n주문할까요? [y/N] ").strip().lower() != "y":
            print("취소했어요.")
            return
        res = kis.kr_order("buy", code, o["qty"], o["limit_price"])
    else:  # sell
        held = kis.kr_balance()["holdings"]
        h = held.get(code)
        if not h:
            manual.pop(code, None)
            state.save()
            sys.exit(f"{code} 을(를) 가지고 있지 않아요. (수동 보유 등록도 지웠어요)")
        name = h["name"]
        text = f"[수동 매도] {name}({code}) {h['qty']}주 시장가 (현재가 {h['price']:,.0f}원)"
        if ask is None:
            print(text)
        elif ask(f"{text}\n주문할까요? [y/N] ").strip().lower() != "y":
            print("취소했어요.")
            return
        o = {"qty": h["qty"], "price": h["price"]}
        res = kis.kr_order("sell", code, h["qty"], None)
    print(f"→ 접수됨 (주문번호 {res.get('ODNO', '?')})")
    state.record("kr", code, o["qty"] * o["price"], today)
    if action == "buy":
        manual[code] = {"name": name, "since": today}
        print("자동매매가 이 종목을 팔지 않게 등록했어요. 팔 때: python -m trading manual sell " + code)
    else:
        manual.pop(code, None)
        print("수동 보유 등록도 풀었어요.")
    state.save()
    write_log({"event": "order_sent", "env": kis.env, "manual": True, "contest": True,
               "order": {"market": "kr", "side": action, "symbol": code, "qty": o["qty"]}, "result": res})


def cmd_contest(kis, cfg, markets, once=False):
    if kis.env == "real":
        sys.exit("대회 모드는 모의투자(KIS_ENV=demo) 전용이에요.")
    from . import auto
    if once:
        # 작업 스케줄러로 창 없이 돌 때: 화면 대신 logs/scheduled.txt 에 남겨요
        path = os.path.join(HERE, "logs", "scheduled.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        def out(msg=""):
            print(msg)
            with open(path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        auto.run_contest_once(kis, cfg, HERE, write_log, out=out)
        return
    auto.run_forever(kis, cfg, HERE, write_log, orders_sent_today, contest_mode=True)


def cmd_update(fetch=None):
    """GitHub 에서 프로그램 파일을 받아 바꿔요. 하나라도 실패하면 아무것도 바꾸지 않아요."""
    fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=20).read())
    files = UPDATE_FILES
    try:
        listed = [l.strip() for l in fetch(UPDATE_BASE + "MANIFEST").decode().splitlines()]
        listed = [l for l in listed if l and not l.startswith("#")]
        # 비밀값·설정 파일이나 폴더 밖 경로는 목록에 있어도 받지 않아요.
        listed = [l for l in listed if l not in (".env", "config.json") and ".." not in l and not l.startswith("/")]
        if listed:
            files = listed
    except Exception:
        pass
    new = {}
    for name in files:
        try:
            new[name] = fetch(UPDATE_BASE + urllib.parse.quote(name))
        except Exception as e:
            sys.exit(f"받기 실패 ({name}): {e}\n아무 파일도 바꾸지 않았어요.")
    changed = []
    for name, data in new.items():
        path = os.path.join(HERE, *name.split("/"))
        old = open(path, "rb").read() if os.path.exists(path) else None
        if old != data:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            changed.append(name)
    print("바뀐 파일: " + ", ".join(changed) if changed else "이미 최신이에요.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m trading", description="한국투자증권 매매 프로그램 (국내+미국)")
    ap.add_argument("command", choices=["status", "plan", "trade", "auto", "contest", "order", "manual", "schedule",
                                        "unschedule", "update"])
    ap.add_argument("args", nargs="*", help="order 일 때: buy|sell 종목 수량 (예: buy AAPL 1)")
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--exchange", choices=["NASD", "NYSE", "AMEX"], help="미국 종목 거래소 (기본 NASD)")
    ap.add_argument("-y", "--yes", action="store_true", help="order·manual 을 확인(y) 없이 바로 주문")
    ap.add_argument("--pct", type=float, default=20.0, help="manual buy 금액 (총평가의 %%, 기본 20)")
    ap.add_argument("--once", action="store_true", help="contest 를 한 번만 돌고 끝내기 (작업 스케줄러용)")
    ap.add_argument("--reset", action="store_true", help="contest 진행 상태 지우기 (계좌를 초기화했을 때)")
    ap.add_argument("--mode", choices=["aggressive", "stable"], help="contest 국내 공식 바꾸기 (입상형/안정형)")
    ap.add_argument("--us-mode", choices=["aggressive", "basic", "off"], help="contest 미국 공식 (aggressive: 고변동 입상형, off: 미국 쉬기)")
    args = ap.parse_args(argv)
    markets = ("kr", "us") if args.market == "all" else (args.market,)
    if args.command == "update":
        cmd_update()
        return
    if args.command in ("schedule", "unschedule"):
        from . import schedule
        if args.command == "schedule":
            make_client()  # .env 가 제대로 있는지 먼저 확인
            schedule.register(HERE)
        else:
            schedule.unregister()
        return

    cfg = load_config()
    kis = make_client()
    if args.command == "contest" and (args.reset or args.mode or args.us_mode):
        from . import contest
        path = os.path.join(HERE, ".cache", f"contest-{kis.env}.json")
        if args.reset and os.path.exists(path):
            os.remove(path)
            print("대회 진행 상태를 지웠어요. 다음 실행 때 처음부터 시작해요.")
        if args.mode:
            st = contest.State(path)
            st.d["kr_mode"] = args.mode
            st.save()
            print(f"국내 공식: {'입상형' if args.mode == 'aggressive' else '안정형'}으로 바꿨어요.")
        if args.us_mode:
            st = contest.State(path)
            st.d["us_mode"] = args.us_mode
            st.save()
            name = {"aggressive": "입상형(고변동 1종목)", "basic": "기본(5종목 보유)", "off": "끔 (국내만)"}[args.us_mode]
            print(f"미국 공식: {name}으로 바꿨어요.")
        return
    if args.command == "contest":
        try:
            cmd_contest(kis, cfg, markets, once=args.once)
        except KisError as e:
            sys.exit(f"API 오류: {e}")
        except KeyboardInterrupt:
            sys.exit("\n중단했어요.")
        return
    if args.command == "manual":
        if not 0 < args.pct <= 100:
            sys.exit("--pct 는 0 보다 크고 100 이하로 정해 주세요.")
        try:
            cmd_manual(kis, args.args, args.pct, ask=None if args.yes else input)
        except KisError as e:
            sys.exit(f"API 오류: {e}")
        return
    if args.command == "order":
        if len(args.args) != 3 or args.args[0] not in ("buy", "sell") or not args.args[2].isdigit():
            sys.exit("사용법: python -m trading order buy AAPL 1   (또는 sell, 국내는 종목코드 6자리)")
        if args.yes and kis.env == "real" and os.environ.get("KIS_AUTO_REAL") != "yes":
            sys.exit("실전 계좌에서 확인 없이 주문하는 건 막아 뒀어요 (.env 에 KIS_AUTO_REAL=yes 가 있어야 해요).")
        try:
            cmd_order(kis, args.args[0], args.args[1], int(args.args[2]), args.exchange,
                      ask=None if args.yes else input)
        except KisError as e:
            sys.exit(f"API 오류: {e}")
        return
    try:
        {"status": cmd_status, "plan": cmd_plan, "trade": cmd_trade, "auto": cmd_auto, "contest": cmd_contest}[args.command](kis, cfg, markets)
    except KisError as e:
        sys.exit(f"API 오류: {e}")
    except KeyboardInterrupt:
        sys.exit("\n중단했어요.")
