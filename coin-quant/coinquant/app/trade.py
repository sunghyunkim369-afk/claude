"""주문: `python -m coinquant trade`

단계 (config.toml [trading])
- stage = 1: 추천만. 이 명령은 주문 목록만 보여주고 끝납니다.
- stage = 2: 주문마다 y/N 확인 후 실행. paper = true 면 모의 체결(user_data/paper_portfolio.json), false 면 업비트 실제 주문.
- 자동 주문: stage = 2 + auto_order = true + `--auto` 를 셋 다 직접 켰을 때만 확인 없이 실행.
안전장치: 주문 1건 최대 금액(max_order_krw), 하루 최대 주문 수(max_orders_per_day), 업비트 최소 주문 5,000원.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .. import config as config_mod
from . import portfolio
from ..trend import num
from .report import user_dir

MIN_ORDER = 5000


def _ask(msg: str) -> bool:
    try:
        return input(msg + " [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def _log(udir: Path, rec: dict) -> None:
    with open(udir / "orders_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")


def run(cfg: dict | None = None, auto: bool = False, log=print) -> int:
    cfg = cfg or config_mod.load()
    tcfg = cfg["trading"]
    udir = user_dir()
    p = udir / "orders-latest.json"
    if not p.exists():
        log("추천 주문이 없습니다. 먼저 `python -m coinquant report` 를 실행하세요.")
        return 0
    plan = json.loads(p.read_text(encoding="utf-8"))
    orders = plan["orders"][: int(cfg["portfolio"]["max_orders_per_day"])]
    log(f"{plan['date']} 기준 추천 주문 {len(orders)}건 (평가금 {plan['total_krw']:,.0f}원)")
    for o in orders:
        log(f"  {'매수' if o['side'] == 'bid' else '매도'} {o['asset']:<6} {o['krw']:>14,.0f}원  (목표 비중 {o['target_weight']:.1%})")
    if int(tcfg["stage"]) < 2:
        log("stage = 1 (추천만) 이라 주문하지 않습니다. 주문하려면 config.toml 의 [trading] stage = 2.")
        return 0
    if auto and not tcfg.get("auto_order"):
        log("--auto 는 config.toml 에 auto_order = true 가 있을 때만 됩니다. 확인 모드로 진행합니다.")
        auto = False
    paper = bool(tcfg.get("paper", True))
    cap = float(tcfg["max_order_krw"])
    log(f"모드: {'모의 주문' if paper else '★ 업비트 실제 주문 ★'} · 주문 1건 최대 {cap:,.0f}원")
    client = None
    if not paper:
        from ..data.upbit import UpbitPrivate
        client = UpbitPrivate()
    # 어제 건 지정가 매수: 모의는 체결 여부 판정, 실제는 안 체결된 것 취소 (신호가 유지되면 아래에서 새 가격으로 다시 검)
    settle_limits(cfg, udir, client, log)
    done = 0
    # 매도 먼저 (현금 확보)
    for o in sorted(orders, key=lambda x: x["side"] != "ask"):
        krw = min(o["krw"], cap)
        if krw < MIN_ORDER:
            log(f"  건너뜀 {o['asset']}: {krw:,.0f}원 < 최소 주문 {MIN_ORDER:,}원")
            continue
        side = "매수" if o["side"] == "bid" else "매도"
        how = f"지정가 {num(o['limit_price'])}원" if o["side"] == "bid" and o.get("limit_price") else "시장가"
        if not auto and not _ask(f"  {side}({how}) {o['asset']} {krw:,.0f}원 실행할까요?"):
            log("  취소")
            _log(udir, {"order": o, "status": "user_cancel", "paper": paper})
            continue
        market = f"KRW-{o['asset']}"
        try:
            limit = o["side"] == "bid" and o.get("limit_price")
            if paper and limit:
                res = _paper_limit(udir, o, krw, plan["date"])
            elif paper:
                res = _paper_fill(cfg, udir, o, krw)
            else:
                from ..data import upbit
                if limit:
                    res = client.limit_order(market, "bid", o["limit_price"], krw / o["limit_price"])
                    _open_orders_add(udir, {"uuid": res.get("uuid"), "asset": o["asset"], "price": o["limit_price"],
                                            "date": plan["date"]})
                elif o["side"] == "bid":
                    res = client.order(market, "bid", krw=krw)
                else:
                    px = float(upbit.ticker([market])["trade_price"].iloc[0])
                    vol = krw / px
                    if o.get("full_exit") and krw >= o["krw"]:   # 전량 매도 (금액 한도에 안 걸렸을 때만)
                        acc = {a["currency"]: float(a["balance"]) for a in client.accounts()}
                        vol = acc.get(o["asset"], vol)
                    res = client.order(market, "ask", volume=vol)
            _log(udir, {"order": o, "krw": krw, "status": "sent", "paper": paper, "result": res})
            log(f"  완료: {res if paper else res.get('uuid')}")
            done += 1
        except Exception as e:
            _log(udir, {"order": o, "krw": krw, "status": "error", "paper": paper, "error": str(e)})
            log(f"  실패: {e}")
    return done


def _paper_fill(cfg: dict, udir: Path, o: dict, krw: float) -> dict:
    """모의 체결: 리포트 기준 가격 × (1 ± 슬리피지) + 수수료."""
    from ..universe import Panel
    from .report import _krw_prices
    cache = config_mod.cache_dir(cfg)
    panel = Panel.load(cache / "panel")
    d = panel.close["BTC"].dropna().index[-1]
    px_krw, _, _ = _krw_prices(cache, panel, d)
    px = float(px_krw[o["asset"]])
    core = set(cfg["universe"]["core"])
    slip = cfg["costs"]["slippage_core"] if o["asset"] in core else cfg["costs"]["slippage_other"]
    fee = cfg["costs"]["fee"]
    st = portfolio.load_paper(udir)
    q = st["qty"].get(o["asset"], 0.0)
    if o["side"] == "bid":
        krw = min(krw, st["cash_krw"] / (1 + fee))
        fill = px * (1 + slip)
        q += krw / fill
        st["cash_krw"] -= krw * (1 + fee)
    else:
        fill = px * (1 - slip)
        sell_q = q if o.get("full_exit") else min(q, krw / fill)
        q -= sell_q
        st["cash_krw"] += sell_q * fill * (1 - fee)
    st["qty"][o["asset"]] = q
    portfolio.save_paper(udir, st)
    return {"fill_price": fill, "qty_after": q, "cash_after": st["cash_krw"]}


# ---------------------------------------------------------------- 지정가 매수 처리

def _open_orders_path(udir: Path) -> Path:
    return udir / "open_limit_orders.json"


def _open_orders_add(udir: Path, rec: dict) -> None:
    p = _open_orders_path(udir)
    cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    cur.append(rec)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")


def _paper_limit(udir: Path, o: dict, krw: float, signal_date: str) -> dict:
    """모의 지정가: 오늘은 걸어만 두고, 다음 실행 때 다음 날 저가로 체결 여부를 판정."""
    rec = {"asset": o["asset"], "price": o["limit_price"], "krw": krw, "signal_date": signal_date, "paper": True}
    _open_orders_add(udir, rec)
    return {"status": "지정가 대기", **rec}


def settle_limits(cfg: dict, udir: Path, client, log=print) -> None:
    p = _open_orders_path(udir)
    if not p.exists():
        return
    pending = json.loads(p.read_text(encoding="utf-8"))
    keep = []
    for r in pending:
        try:
            if r.get("paper"):
                res = _paper_limit_resolve(cfg, udir, r)
                if res == "대기":
                    keep.append(r)
                    continue
                log(f"  어제 지정가 {r['asset']} {num(r['price'])}원: {res}")
            elif client is not None and r.get("uuid"):
                st = client.get_order(r["uuid"])
                if st.get("state") == "wait":
                    client.cancel(r["uuid"])
                    log(f"  어제 지정가 {r['asset']} {num(r['price'])}원: 안 닿아서 취소 (체결 {st.get('executed_volume', '0')})")
                else:
                    log(f"  어제 지정가 {r['asset']}: {st.get('state')}")
            _log(udir, {"limit": r, "status": "settled"})
        except Exception as e:
            log(f"  지정가 정리 실패 {r.get('asset')}: {e}")
            keep.append(r)
    p.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")


def _paper_limit_resolve(cfg: dict, udir: Path, r: dict) -> str:
    """신호 다음 날 원화 저가가 지정가 이하면 min(시가, 지정가)에 체결, 아니면 미체결로 종료."""
    import pandas as pd
    from ..universe import Panel
    from ..data import fx as fx_mod
    cache = config_mod.cache_dir(cfg)
    day = pd.Timestamp(r["signal_date"]) + pd.Timedelta(days=1)
    up = cache / "upbit" / "krw_1d" / f"KRW-{r['asset']}.csv"
    o = lo = None
    if up.exists():
        df = pd.read_csv(up, parse_dates=["date"]).set_index("date")
        if day in df.index:
            o, lo = float(df.loc[day, "open"]), float(df.loc[day, "low"])
    if lo is None:
        panel = Panel.load(cache / "panel")
        if day not in panel.close.index or r["asset"] not in panel.close:
            return "대기"
        rate = fx_mod.load_usdkrw(cache / "fx_usdkrw.csv")
        k = float(rate.loc[:day].iloc[-1]) if rate is not None else float("nan")
        o, lo = float(panel.open.at[day, r["asset"]]) * k, float(panel.low.at[day, r["asset"]]) * k
    if lo > r["price"]:
        return "안 닿아서 미체결 (신호가 유지되면 오늘 새 가격으로 다시)"
    fill = min(o, r["price"])
    fee = cfg["costs"]["fee"]
    st = portfolio.load_paper(udir)
    krw = min(r["krw"], st["cash_krw"] / (1 + fee))
    st["qty"][r["asset"]] = st["qty"].get(r["asset"], 0.0) + krw / fill
    st["cash_krw"] -= krw * (1 + fee)
    portfolio.save_paper(udir, st)
    return f"체결 {num(fill)}원"
