"""차트 + 가치·온체인 판단을 합치면 나아지나? + 주봉·1시간봉 + 큰 사건 때 공식은 어땠나: `python -m coinquant combo-study`

데이터: Coin Metrics 공개 CSV (BTC 2013~, ETH 2016~, 2026-05 까지). 가격은 종가만 있어 고가·저가 = 종가로 계산(근사).
비용: 업비트 0.15% (매매마다). 판단 그날 종가 → 다음 날 체결. 개발 2014-07 ~ 2024-12, 참고 2025-01 ~.

A. 주봉: 같은 공식을 주봉으로 (봉 개수 그대로 = 7배 느린 공식). 1시간봉·4시간봉 쪽은 docs/timeframe-check-2026-11-03.md.
B. 큰 사건(코로나·중국 채굴 금지·루나·FTX·ETF 승인·엔 캐리 청산·바이낸스 해킹)에서 공식이 실제로 한 일.
C. '가치평가·온체인 판단'을 규칙으로: (숫자는 흔히 쓰는 값을 미리 정함, 고르지 않음)
   - MVRV(시가총액 ÷ 실현 시가총액: 모든 코인을 마지막으로 옮긴 가격 기준 평가) ≥ 3.5 = 과열 → 공식 비중 절반 / 0
   - MVRV ≤ 1.0 = 저평가 → 공식이 꺼져 있어도 50% 보유
   - 거래소 순유입(30일 합) > 0 = 거래소로 코인이 몰림(팔 준비) → 공식 비중 절반
   - 활성 주소 30일 평균이 1년 평균보다 낮음 = 사용 감소 → 공식 비중 절반
채택 기준(미리 정함): BTC·ETH 균등에서 샤프가 공식보다 높을 확률 ≥ 90%(부트스트랩), MDD 가 더 깊지 않음, BTC 단독에서도 샤프 낮지 않음.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..trend import MultiTrend
from .risk_overlay import boot_prob

COST = 0.0015
DEV = ("2014-07-01", "2024-12-31")
HOLD = ("2025-01-01", "2026-12-31")
EVENTS = [("2017-09-04", "중국 ICO 금지"), ("2020-03-12", "코로나 폭락"), ("2021-05-19", "중국 채굴 금지 폭락"),
          ("2022-05-09", "루나 붕괴"), ("2022-11-08", "FTX 파산"), ("2024-01-10", "미국 현물 ETF 승인"),
          ("2024-08-05", "엔 캐리 청산"), ("2025-02-21", "Bybit 해킹")]
COLS = ["time", "PriceUSD", "CapMVRVCur", "FlowInExNtv", "FlowOutExNtv", "AdrActCnt"]


def load(cache: Path) -> dict[str, pd.DataFrame]:
    out = {}
    d = cache / "coinmetrics_full"
    d.mkdir(parents=True, exist_ok=True)
    for a in ("btc", "eth"):
        p = d / f"{a}.csv"
        if not p.exists():
            import requests
            r = requests.get(f"https://raw.githubusercontent.com/coinmetrics/data/master/csv/{a}.csv", timeout=120)
            r.raise_for_status()
            p.write_bytes(r.content)
        x = pd.read_csv(p, usecols=lambda c: c in COLS, parse_dates=["time"]).set_index("time").sort_index()
        out[a.upper()] = x[x["PriceUSD"].notna()].loc["2013-01-01":]
    return out


def formula(c: pd.Series) -> pd.Series:
    mt = MultiTrend(c.to_frame("x"), c.to_frame("x"), c.to_frame("x"))
    M, L = mt.h["중기"], mt.h["장기"]
    return (M.state & ~(L.score <= -2)).astype(float)["x"]


def rets(c: pd.Series, pos: pd.Series) -> pd.Series:
    pos = pos.reindex(c.index).ffill().fillna(0.0)
    return pos.shift(1).fillna(0) * c.pct_change().fillna(0) - pos.diff().abs().fillna(pos.abs()) * COST


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 365
    return {"cagr": eq.iloc[-1] ** (1 / yrs) - 1, "mdd": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0, "tot": float(eq.iloc[-1] - 1)}


def port(rs: dict[str, pd.Series]) -> pd.Series:
    return pd.DataFrame(rs).mean(axis=1)


def main(cfg: dict | None = None, log=print) -> Path:
    t0 = time.time()
    cfg = cfg or config_mod.load()
    D = load(config_mod.cache_dir(cfg))
    C = {a: d["PriceUSD"] for a, d in D.items()}
    base = {a: formula(c) for a, c in C.items()}
    hold = {a: pd.Series(1.0, index=c.index) for a, c in C.items()}

    # A. 주봉 (같은 봉 개수 = 7배 느린 공식)
    weekly = {}
    for a, c in C.items():
        w = c.resample("W-SUN").last()
        pw = formula(w)
        pw.index = pw.index + pd.Timedelta(days=1)          # 일요일 종가 판단 → 월요일부터
        weekly[a] = pw.reindex(c.index, method="ffill").fillna(0.0)
    variants_a = {"계속 보유": hold, "일봉 (지금 공식)": base, "주봉 (같은 봉 개수 = 7배 느림)": weekly}

    # C. 가치·온체인
    mv = {a: d["CapMVRVCur"] for a, d in D.items()}
    nf = {a: (d["FlowInExNtv"] - d["FlowOutExNtv"]).rolling(30, min_periods=20).sum() for a, d in D.items()}
    ad = {a: d["AdrActCnt"].rolling(30, min_periods=20).mean() / d["AdrActCnt"].rolling(365, min_periods=200).mean() for a, d in D.items()}
    v = {}
    v["일봉 (지금 공식)"] = base
    v["C1. MVRV ≥ 3.5 과열이면 절반"] = {a: base[a] * np.where(mv[a] >= 3.5, 0.5, 1.0) for a in C}
    v["C2. MVRV ≥ 3.5 과열이면 0"] = {a: base[a] * np.where(mv[a] >= 3.5, 0.0, 1.0) for a in C}
    v["C3. MVRV ≤ 1 저평가면 공식 꺼져도 50%"] = {a: np.maximum(base[a], np.where(mv[a] <= 1.0, 0.5, 0.0)) for a in C}
    v["C4. C1 + C3"] = {a: np.maximum(base[a] * np.where(mv[a] >= 3.5, 0.5, 1.0), np.where(mv[a] <= 1.0, 0.5, 0.0)) for a in C}
    v["C5. 거래소 순유입(30일) > 0 이면 절반"] = {a: base[a] * np.where(nf[a] > 0, 0.5, 1.0) for a in C}
    v["C6. 활성 주소 30일 < 1년 평균이면 절반"] = {a: base[a] * np.where(ad[a] < 1, 0.5, 1.0) for a in C}
    v = {k: {a: pd.Series(np.asarray(x[a], float), index=C[a].index) for a in C} for k, x in v.items()}

    def evaluate(variants):
        rows = {}
        for nm, P in variants.items():
            R = {a: rets(C[a], P[a]) for a in C}
            pr = port(R)
            row = {"port_dev": stats(pr.loc[DEV[0]:DEV[1]]), "port_hold": stats(pr.loc[HOLD[0]:]),
                   "btc_dev": stats(R["BTC"].loc[DEV[0]:DEV[1]]), "eth_dev": stats(R["ETH"].loc["2016-09-01":DEV[1]]),
                   "expo": float(pd.DataFrame(P).loc[DEV[0]:DEV[1]].mean().mean()),
                   "trades": int(sum((P[a].diff().abs() > 0).loc[DEV[0]:DEV[1]].sum() for a in C)),
                   "curve": (1 + pr.loc[DEV[0]:DEV[1]]).cumprod(), "btc_curve": (1 + R["BTC"].loc[DEV[0]:DEV[1]]).cumprod()}
            rows[nm] = row
        return rows
    A = evaluate(variants_a)
    Cres = evaluate(v)
    for nm in Cres:
        if nm != "일봉 (지금 공식)":
            Cres[nm]["boot"] = boot_prob(Cres[nm]["curve"], Cres["일봉 (지금 공식)"]["curve"])
            Cres[nm]["boot_btc"] = boot_prob(Cres[nm]["btc_curve"], Cres["일봉 (지금 공식)"]["btc_curve"])
    log(f"  A·C 완료 ({time.time() - t0:.0f}초)")

    # B. 큰 사건
    c = C["BTC"]
    p = base["BTC"]
    ev = []
    for dt, nm in EVENTS:
        d = pd.Timestamp(dt)
        if d not in c.index:
            continue
        pre = c.index[c.index < d][-1]
        w = slice(d - pd.Timedelta(days=1), d + pd.Timedelta(days=30))
        bh = c.loc[w]
        fr = rets(c, p).loc[d:d + pd.Timedelta(days=30)]
        was = p.at[pre]
        after = p.loc[d:d + pd.Timedelta(days=60)]
        exit_d = next((x for x, val in after.items() if was > 0 and val == 0), None)
        entry_d = next((x for x, val in p.loc[d:].items() if val > 0 and (was == 0 or exit_d is not None and x > exit_d)), None)
        ev.append({"date": d.date(), "name": nm, "was": "보유 중" if was > 0 else "현금", "bh30": float(bh.iloc[-1] / bh.iloc[0] - 1),
                   "f30": float((1 + fr).prod() - 1), "day": float(c.at[d] / c.at[pre] - 1),
                   "exit": (exit_d - d).days if exit_d is not None else None, "reentry": (entry_d - d).days if entry_d is not None else None,
                   "low30": float(c.loc[d:d + pd.Timedelta(days=30)].min() / c.at[pre] - 1)})
    path = render(A, Cres, ev, time.time() - t0, D["BTC"].index[-1])
    log(f"  → {path}")
    return path


def render(A, Cres, ev, secs, last) -> Path:
    P = lambda x, d=1: "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{d}f}%"  # noqa: E731
    F2 = lambda x: f"{x:.2f}"  # noqa: E731
    today = pd.Timestamp.now(tz="Asia/Seoul").date()
    L = [f"# 차트 + 가치·온체인 판단 합치기, 주봉, 큰 사건 때 공식 ({today})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         f"- 데이터: Coin Metrics 공개 CSV (BTC 2013~, ETH 2016~, ~{last:%Y-%m-%d}). 종가만 있어 고가·저가 = 종가 근사 → 기존 검증 숫자와 조금 다름.",
         f"- 비용 0.15%/매매, 판단 종가 → 다음 날. 개발 {DEV[0]} ~ {DEV[1]}, 참고 2025-01 ~. 'BTC·ETH' = 둘 균등(ETH 는 2016-09부터 성적에 들어감).",
         f"- 계산 {secs:.0f}초.", "",
         "## A. 주봉으로 하면", "",
         "| 방식 | BTC·ETH 연복리 | MDD | 샤프 | BTC 단독 샤프 | 평균 투자 비중 | 매매 횟수 | 2025~ 총수익 | 2025~ MDD |", "|---|---|---|---|---|---|---|---|---|"]
    for nm, r in A.items():
        d, h = r["port_dev"], r["port_hold"]
        L.append(f"| {nm} | {P(d['cagr'])} | {P(d['mdd'], 0)} | {F2(d['sharpe'])} | {F2(r['btc_dev']['sharpe'])} | {r['expo']:.0%} | {r['trades']} | {P(h['tot'])} | {P(h['mdd'], 0)} |")
    L += ["", "## B. 큰 사건 때 공식이 실제로 한 일 (BTC, 일봉 공식)", "",
          "| 날짜 | 사건 | 그 전날 공식 | 그날 BTC | 30일 안 최저 (전날 대비) | 30일 뒤 BTC | 30일 뒤 공식 | 공식이 판 날 | 다시 산 날 |", "|---|---|---|---|---|---|---|---|---|"]
    for e in ev:
        ex = "-" if e["exit"] is None else f"{e['exit']}일 뒤"
        re_ = "-" if e["reentry"] is None else f"{e['reentry']}일 뒤"
        L.append(f"| {e['date']} | {e['name']} | {e['was']} | {P(e['day'])} | {P(e['low30'])} | {P(e['bh30'])} | {P(e['f30'])} | {ex} | {re_} |")
    L += ["", "## C. 가치·온체인 판단을 규칙으로 더하면", "",
          "| 방식 | BTC·ETH 연복리 | MDD | 샤프 | 공식보다 나을 확률 (90% 구간) | BTC 단독 샤프 (나을 확률) | 평균 투자 비중 | 2025~ 총수익 | 2025~ MDD | 판정 |", "|---|---|---|---|---|---|---|---|---|---|"]
    b0 = Cres["일봉 (지금 공식)"]
    passed = []
    for nm, r in Cres.items():
        d, h = r["port_dev"], r["port_hold"]
        if "boot" in r:
            p, lo, hi = r["boot"]
            pb = r["boot_btc"][0]
            ok = p >= 0.9 and d["mdd"] >= b0["port_dev"]["mdd"] and r["btc_dev"]["sharpe"] >= b0["btc_dev"]["sharpe"]
            if ok:
                passed.append(nm)
            bt = f"{p:.0%} ({lo:+.2f}~{hi:+.2f})"
            bb = f"{F2(r['btc_dev']['sharpe'])} ({pb:.0%})"
        else:
            ok, bt, bb = None, "-", F2(r["btc_dev"]["sharpe"])
        L.append(f"| {nm} | {P(d['cagr'])} | {P(d['mdd'], 0)} | {F2(d['sharpe'])} | {bt} | {bb} | {r['expo']:.0%} | {P(h['tot'])} | {P(h['mdd'], 0)} | "
                 f"{'' if ok is None else ('통과' if ok else '탈락')} |")
    L += ["", f"**통과: {', '.join(passed) if passed else '없음'}**", "", "## 결론", "", "(실행 결과를 보고 사람이 씀)", ""]
    p = Path(config_mod.ROOT) / "docs" / f"combo-study-{today}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p
