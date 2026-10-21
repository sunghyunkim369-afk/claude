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
    done = 0
    # 매도 먼저 (현금 확보)
    for o in sorted(orders, key=lambda x: x["side"] != "ask"):
        krw = min(o["krw"], cap)
        if krw < MIN_ORDER:
            log(f"  건너뜀 {o['asset']}: {krw:,.0f}원 < 최소 주문 {MIN_ORDER:,}원")
            continue
        side = "매수" if o["side"] == "bid" else "매도"
        if not auto and not _ask(f"  {side} {o['asset']} {krw:,.0f}원 실행할까요?"):
            log("  취소")
            _log(udir, {"order": o, "status": "user_cancel", "paper": paper})
            continue
        market = f"KRW-{o['asset']}"
        try:
            if paper:
                res = _paper_fill(cfg, udir, o, krw)
            else:
                from ..data import upbit
                if o["side"] == "bid":
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
