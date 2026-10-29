"""4시간봉 거래량 돌파 (롱만, 하루 한 번 판단) — 근거: docs/breakout4h-2026-10-29.md

매일 09:00 KST(UTC 00:00)에 끝나는 4시간봉에서만 판단 (메시지 시각에 맞춤):
- 매수: 그 봉 종가가 직전 20봉 최고가 위 + 거래량이 직전 20봉 평균의 2배
- 매도: 그 봉 종가가 10봉 최저가 아래 / 손절: 진입가 − 2×ATR(20) 을 거래소 예약 주문으로
신호가 없으면 아무것도 안 함 (거래는 코어 3개 합쳐 한 달에 한 번 정도).
가격은 바이낸스 현물 4시간봉 (시세 API), 모의 성적은 start 부터 비용 0.15% 로 매번 다시 계산.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import requests

from ..trend import num

HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")


def fetch_4h(symbols: dict[str, str], bars: int = 2000) -> dict[str, pd.DataFrame]:
    """끝난 4시간봉만 (지금 진행 중인 봉은 버림). 막히면 빈 dict."""
    out = {}
    now_ms = int(time.time() * 1000)
    for a, s in symbols.items():
        rows, end = [], None
        for host in HOSTS:
            try:
                rows, end = [], None
                while len(rows) < bars:
                    p = {"symbol": s, "interval": "4h", "limit": 1000}
                    if end:
                        p["endTime"] = end
                    r = requests.get(host + "/api/v3/klines", params=p, timeout=20)
                    r.raise_for_status()
                    js = r.json()
                    if not js:
                        break
                    rows = js + rows
                    end = js[0][0] - 1
                    if len(js) < 1000:
                        break
                break
            except requests.RequestException:
                rows = []
                continue
        if not rows:
            continue
        df = pd.DataFrame(rows).iloc[:, :7]
        df.columns = ["t", "open", "high", "low", "close", "volume", "ct"]
        df = df[df["ct"].astype("int64") < now_ms]
        df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms")
        out[a] = df[["open", "high", "low", "close", "volume"]].astype(float)
        out[a] = out[a][~out[a].index.duplicated()].sort_index()
    return out


def wide(per: dict[str, pd.DataFrame]) -> dict:
    from ..research.futures_lab import clean_wicks
    per = {a: clean_wicks(d) for a, d in per.items()}
    return {k: pd.DataFrame({a: d[k] for a, d in per.items()}).sort_index() for k in ("open", "high", "low", "close", "volume")}


def evaluate(B: dict, start: pd.Timestamp) -> dict:
    """규칙대로 돌린 결과 + 오늘(가장 최근 09:00 KST 봉) 할 일."""
    from .. import indicators as ind
    from ..research import breakout4h as b4
    from ..research.futures_lab import port
    S = b4.signals(B)
    opts = dict(b4.VARIANTS)["B. 하루 한 번(09:00 KST 봉) 판단 · 손절 예약 주문"]
    C = B["close"]
    R, P, T = b4.run_variant(B, S, opts, {a: b4.SPOT_COST for a in C.columns}, funding=False)
    idx = C.index
    dec = idx[idx.hour == b4.KST9]
    d0 = dec[-1]                       # 가장 최근 판단 봉 (UTC 20:00 시작 → KST 09:00 끝)
    i0 = idx.get_loc(d0)
    prev = idx.get_loc(dec[-2]) if len(dec) > 1 else 0
    low10 = ind.donchian_low(B["low"], 10)
    res = {"d0": d0 + pd.Timedelta(hours=4), "coins": {}, "buy": [], "sell": [], "stopped": []}
    for j, a in enumerate(C.columns):
        tr = list(T[a])
        is_open = P[a].iloc[-1] != 0 and bool(tr)
        open_tr = tr.pop() if is_open else None
        info = {"close": float(C[a].iloc[-1]), "sig_close": float(C.at[d0, a])}
        for (ei, xi, side, ret, _) in tr:            # 지난 판단 뒤에 끝난 거래
            if xi <= prev:
                continue
            if idx[xi].hour == b4.KST9 and C.iat[xi, j] < low10.iat[xi, j]:
                res["sell"].append(a)
            else:
                res["stopped"].append((a, ret))
        for (ei, *_ ) in tr + ([open_tr] if open_tr else []):
            if ei == i0:
                res["buy"].append(a)
        if open_tr:
            ei = open_tr[0]
            ent = float(C.iat[ei, j])
            info["hold"] = {"date": idx[ei] + pd.Timedelta(hours=4), "price": ent, "pnl": info["close"] / ent - 1 - 2 * b4.SPOT_COST,
                            "stop": ent - float(S["stop"].iat[ei, j]), "exit_lvl": float(low10[a].iloc[-1])}
        if a in res["buy"]:
            info["stop"] = info["sig_close"] - float(S["stop"].at[d0, a])
        res["coins"][a] = info
    pr = port(R, C).loc[start:]
    n = sum(1 for a in T for t in T[a] if idx[t[0]] >= start)
    res["paper"] = {"ret": float((1 + pr.fillna(0)).prod() - 1) if len(pr) else 0.0, "n": n, "since": max(start, idx[0])}
    return res


def lines(cfg: dict, log=print) -> tuple[list[str], list[str]]:
    bc = cfg.get("breakout_mode", {})
    if not bc.get("enabled", False):
        return [], []
    core = list(cfg["universe"]["core"])
    per = fetch_4h({a: f"{a}USDT" for a in core})
    if len(per) < len(core):
        return [], ["⚡ 4시간 돌파: 시세를 못 받아 건너뜀"]
    res = evaluate(wide(per), pd.Timestamp(bc.get("start", "2026-10-29")))
    each = float(bc.get("sleeve", 0.3)) / len(core)
    mode = "모의" if bc.get("paper", False) else "소액"
    todo, L = [], []
    late = pd.Timestamp.now(tz="UTC").tz_localize(None) - res["d0"]
    for a in res["buy"]:
        v = res["coins"][a]
        gap = v["close"] / v["sig_close"] - 1
        todo.append(f"⚡ <b>4시간 돌파 매수: {a}</b> — 자금의 {each:.0%}, 손절 예약 <b>${num(v['stop'])}</b> "
                    f"({v['stop'] / v['sig_close'] - 1:+.1%})" + (f" · 신호 뒤 {gap:+.1%} 움직임" if late > pd.Timedelta(hours=4) else ""))
    for a in res["sell"]:
        todo.append(f"⚡ <b>4시간 돌파 매도: {a}</b> (10봉 최저 아래 마감) — 손절 예약 취소 후 매도")
    for a, r in res["stopped"]:
        todo.append(f"⚡ 4시간 돌파 {a}: 손절 예약가에 나감 ({r:+.1%}) — 확인만")
    holds = {a: v["hold"] for a, v in res["coins"].items() if "hold" in v and a not in res["buy"]}
    if not (res["buy"] or res["sell"] or res["stopped"] or holds):
        L.append(f"⚡ <b>4시간 돌파</b>: 오늘 신호 없음 <i>(기회 올 때만 알림 · 코어 3개 합쳐 한 달 1번꼴)</i>")
    else:
        L.append(f"<b>⚡ 4시간 돌파 ({mode})</b>  <i>09시 봉 기준 · 1회 자금의 {each:.0%} · 롱만</i>")
        for a, hd in holds.items():
            L.append(f"🟢 <b>{a} 보유</b> {hd['date']:%m/%d} ${num(hd['price'])} → {hd['pnl']:+.1%} · 손절 예약 ${num(hd['stop'])}"
                     f" · 09시 봉이 ${num(hd['exit_lvl'])} 아래 마감하면 매도")
    pp = res["paper"]
    if pp["n"]:
        L.append(f"📒 {pp['since']:%y/%m/%d}~ 기록: {pp['ret']:+.1%} (거래 {pp['n']}번, 비용 포함)")
    if late > pd.Timedelta(hours=4) and res["buy"]:
        L.append(f"⚠ 메시지가 신호({res['d0'] + pd.Timedelta(hours=9):%H시} KST)보다 {late / pd.Timedelta(hours=1):.0f}시간 늦음 — 4시간 늦은 체결도 검증에선 성적이 비슷했음")
    return todo, L
