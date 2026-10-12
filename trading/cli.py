"""반자동 매매 프로그램.

  python3 -m trading status            잔고 보기
  python3 -m trading plan [--market kr|us|all]    주문 후보만 보기 (주문 안 함)
  python3 -m trading trade [--market kr|us|all]   후보를 하나씩 확인(y)한 것만 주문
"""

import argparse
import json
import os
import sys
from datetime import datetime

from .kis import Kis, KisError
from .planner import make_plan

HERE = os.path.dirname(os.path.abspath(__file__))


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
        if not b["holdings"]:
            print("  보유 종목 없음")
        for sym, h in b["holdings"].items():
            pnl = (h["price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0
            print(f"  {sym} {h['qty']}주  평균 ${h['avg_price']:,.2f} → 현재 ${h['price']:,.2f}  {pnl:+.1f}%")


def cmd_plan(kis, cfg, markets, show_notes=True):
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m trading", description="한국투자증권 반자동 매매 (국내+미국)")
    ap.add_argument("command", choices=["status", "plan", "trade"])
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    args = ap.parse_args(argv)
    markets = ("kr", "us") if args.market == "all" else (args.market,)

    cfg = load_config()
    kis = make_client()
    try:
        {"status": cmd_status, "plan": cmd_plan, "trade": cmd_trade}[args.command](kis, cfg, markets)
    except KisError as e:
        sys.exit(f"API 오류: {e}")
    except KeyboardInterrupt:
        sys.exit("\n중단했어요.")
