"""지수가 고점 대비 얼마나 빠졌을 때 사면 어땠나 — `python -m coinquant stocks-dip` (GitHub Actions 에서)

질문: 코인은 '떨어질 때 사기'가 효과 없었는데, 이익이라는 기준점이 있는 주식 지수는 다를까?
대상: 세계 대표 지수 25개(stocks-bias 와 같은 목록, 성과로 고르지 않음). 지수 가격(배당 빠짐 → 보유 쪽이 실제보다 조금 불리).
1. 고점 대비 위치 구간별(0~−10%, −10~−20%, −20~−30%, −30~−40%, −40% 이하)로 그날 샀다면 1년·3년 뒤 수익.
2. 사건: 신고가 뒤 처음 −20%·−30%에 닿은 날 사서 1년·3년 보유 (같은 하락 안에서는 한 번만 셈) + 그 뒤 추가 하락·회복까지 걸린 시간.
비교: 아무 날이나 샀을 때(같은 지수, 같은 기간).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from . import data as sd
from .bias_check import WORLD

# 생존 편향 보강: 크게 망가졌거나 외국인에게 닫혔던 시장, 고인플레이션 시장 (yfinance 에 있는 것만 실제로 들어감)
TROUBLED = [("IMOEX.ME", "러시아 MOEX", "RUB=X"), ("GD.AT", "그리스 아테네 종합", "EUR=X"), ("^MERV", "아르헨티나 메르발", "ARS=X"),
            ("XU100.IS", "튀르키예 BIST 100", "TRY=X"), ("^CASE30", "이집트 EGX 30", "EGP=X"), ("^SET.BK", "태국 SET", "THB=X"),
            ("^KLSE", "말레이시아 KLCI", "MYR=X"), ("PSEI.PS", "필리핀 PSEi", "PHP=X"), ("^TA125.TA", "이스라엘 TA-125", "ILS=X"),
            ("^JKSE", "인도네시아", "IDR=X"), ("^BVSP", "브라질 보베스파", "BRL=X"), ("^MXX", "멕시코 IPC", "MXN=X"),
            ("^KS11", "한국 코스피", "KRW=X"), ("^N225", "일본 닛케이", "JPY=X"), ("000001.SS", "중국 상하이", "CNY=X")]
BANDS = [(0.0, -0.10), (-0.10, -0.20), (-0.20, -0.30), (-0.30, -0.40), (-0.40, -1.0)]
FOCUS = {"^IXIC": "미국 나스닥", "^GSPC": "미국 S&P 500", "^KS11": "한국 코스피"}


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    d = config_mod.cache_dir(cfg) / "stocks"
    F = sd.update(d, "world", [t for t, _, _ in WORLD], start="1927-01-01", log=log)
    names = {t: n for t, n, _ in WORLD}
    rows, events, now = [], [], []
    for tk in F["close"].columns:
        c = F["close"][tk].dropna()
        c = c[c > 0]
        if len(c) < 252 * 10:
            continue
        dd = c / c.cummax() - 1
        f1 = c.shift(-252) / c - 1
        f3 = (c.shift(-756) / c) ** (1 / 3) - 1
        now.append((names.get(tk, tk), float(dd.iloc[-1]), c.index[-1].date()))
        for hi, lo in BANDS:
            m = (dd <= hi) & (dd > lo)
            rows.append({"tk": tk, "band": f"{hi:.0%}~{lo:.0%}" if lo > -1 else f"{hi:.0%} 이하",
                         "n1": int((m & f1.notna()).sum()), "f1": f1[m].median(), "p1": (f1[m] > 0).mean(),
                         "f3": f3[m].median(), "p3": (f3[m] > 0).mean(), "w3": f3[m].min()})
        rows.append({"tk": tk, "band": "(기준) 아무 날", "n1": int(f1.notna().sum()), "f1": f1.median(), "p1": (f1 > 0).mean(),
                     "f3": f3.median(), "p3": (f3 > 0).mean(), "w3": f3.min()})
        for thr in (-0.20, -0.30):
            armed = True
            for i, (dt, x) in enumerate(dd.items()):
                if x >= -0.001:
                    armed = True
                if armed and x <= thr:
                    armed = False
                    after = c.iloc[i:]
                    peak = c.iloc[:i + 1].max()
                    rec = after[after >= peak]
                    low = after.iloc[:756].min() / c.iloc[i] - 1
                    events.append({"tk": tk, "thr": thr, "date": dt.date(), "f1": f1.iat[i], "f3": f3.iat[i], "low": low,
                                   "rec_y": (rec.index[0] - dt).days / 365 if len(rec) else np.nan})
    R, E = pd.DataFrame(rows), pd.DataFrame(events)
    # ---- 생존 편향·통화 보강: 25개 + 문제 시장, 현지 통화 vs 달러
    X = sd.update(d, "troubled", [t for t, _, _ in TROUBLED] + sorted({fx for _, _, fx in TROUBLED}), start="1990-01-01", log=log)
    ext = []
    for tk, nm, fx in TROUBLED:
        if tk not in X["close"].columns or X["close"][tk].dropna().size < 252 * 5:
            ext.append({"name": nm, "have": False})
            continue
        c = X["close"][tk].dropna()
        c = c[c > 0]
        fxs = X["close"][fx].dropna() if fx in X["close"].columns else None
        out = {"name": nm, "have": True, "from": c.index[0].year, "to": c.index[-1].date()}
        for cur, series in (("현지", c), ("달러", (c / fxs.reindex(c.index).ffill()).dropna() if fxs is not None and fxs.size > 252 * 5 else None)):
            if series is None or series.size < 252 * 5:
                continue
            dd = series / series.cummax() - 1
            f1 = series.shift(-252) / series - 1
            m30 = (dd <= -0.30) & f1.notna()
            out[cur] = {"dd_now": float(dd.iloc[-1]), "base_p1": float((f1.dropna() > 0).mean()),
                        "p1_30": float((f1[m30] > 0).mean()) if m30.any() else np.nan, "f1_30": float(f1[m30].median()) if m30.any() else np.nan,
                        "maxdd": float(dd.min()), "n30": int(m30.sum())}
        ext.append(out)
    log(f"  보강 시장 {sum(1 for x in ext if x['have'])}/{len(ext)}개 데이터 있음")
    pct = lambda x: "-" if pd.isna(x) else f"{x * 100:+.0f}%"  # noqa: E731
    L = [f"# 지수가 고점 대비 빠졌을 때 사면 ({pd.Timestamp.now(tz='Asia/Seoul').date()})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         "- 세계 대표 지수 25개(성과로 고르지 않음), 지수 가격(배당 빠짐), 비용 없음(한 번 사서 들고 있는 경우라 영향 작음).",
         "- '3년 뒤'는 연복리. 날짜가 겹치는 구간 통계라 실제 독립 표본은 '하락장 횟수'만큼입니다(2절 사건 수 참고).", "",
         "## 0. 지금 위치", "", "| 지수 | 고점 대비 | 기준일 |", "|---|---|---|"]
    for n, x, dt in sorted(now, key=lambda z: z[1]):
        L.append(f"| {n} | {pct(x)} | {dt} |")
    L += ["", "## 1. 고점 대비 구간별, 그날 샀다면", ""]
    for tk, nm in FOCUS.items():
        sub = R[R["tk"] == tk]
        if sub.empty:
            continue
        L += [f"### {nm}", "", "| 고점 대비 | 날 수 | 1년 뒤 (중앙) | 1년 뒤 오른 비율 | 3년 뒤 연복리 (중앙) | 3년 뒤 오른 비율 | 3년 최악 (연) |", "|---|---|---|---|---|---|---|"]
        for _, r in sub.iterrows():
            L.append(f"| {r['band']} | {r['n1']} | {pct(r['f1'])} | {r['p1']:.0%} | {pct(r['f3'])} | {r['p3']:.0%} | {pct(r['w3'])} |")
        L.append("")
    agg = R.groupby("band", sort=False)[["f1", "p1", "f3", "p3"]].median()
    L += ["### 25개 지수 묶음 (지수별 값의 중앙값)", "", "| 고점 대비 | 1년 뒤 (중앙) | 1년 뒤 오른 비율 | 3년 뒤 연복리 (중앙) | 3년 뒤 오른 비율 |", "|---|---|---|---|---|"]
    for b, r in agg.iterrows():
        L.append(f"| {b} | {pct(r['f1'])} | {r['p1']:.0%} | {pct(r['f3'])} | {r['p3']:.0%} |")
    L += ["", "## 2. 신고가 뒤 처음 −20%·−30%에 닿은 날 샀다면", ""]
    for thr in (-0.20, -0.30):
        e = E[E["thr"] == thr]
        L += [f"### 처음 {thr:.0%} (사건 {len(e)}번, 지수 {e['tk'].nunique()}개)", "",
              f"- 1년 뒤 중앙 {pct(e['f1'].median())} (오른 비율 {(e['f1'] > 0).mean():.0%}), 3년 뒤 연복리 중앙 {pct(e['f3'].median())} (오른 비율 {(e['f3'] > 0).mean():.0%})",
              f"- 산 뒤 3년 안에 더 빠진 폭 중앙 {pct(e['low'].median())}, 최악 {pct(e['low'].min())}",
              f"- 전 고점 회복까지 중앙 {e['rec_y'].median():.1f}년 (회복 못 한 사건 {int(e['rec_y'].isna().sum())}번)", ""]
        for tk, nm in FOCUS.items():
            x = E[(E["thr"] == thr) & (E["tk"] == tk)]
            if x.empty:
                continue
            L += [f"{nm}:", "", "| 날짜 | 1년 뒤 | 3년 뒤 (연) | 그 뒤 더 빠진 폭 | 고점 회복까지 |", "|---|---|---|---|---|"]
            for _, r in x.iterrows():
                rec = "-" if pd.isna(r["rec_y"]) else f"{r['rec_y']:.1f}년"
                L.append(f"| {r['date']} | {pct(r['f1'])} | {pct(r['f3'])} | {pct(r['low'])} | {rec} |")
            L.append("")
    L += ["", "## 3. 생존 편향·통화 보강 — 망가졌거나 닫혔던 시장, 달러 기준", "",
          "| 시장 | 기록 | 지금 고점 대비 (현지 / 달러) | 역사상 최대 하락 (현지 / 달러) | 고점 대비 −30% 이하에서 산 날 1년 뒤 오른 비율 (현지 / 달러) | 평소 (현지 / 달러) |", "|---|---|---|---|---|---|"]
    for x in ext:
        if not x["have"]:
            L.append(f"| {x['name']} | 데이터 없음 (yfinance) | - | - | - | - |")
            continue
        lo, us = x.get("현지", {}), x.get("달러", {})
        g = lambda dct, k, f=pct: f(dct[k]) if dct and k in dct and not pd.isna(dct[k]) else "-"  # noqa: E731
        pr = lambda v: f"{v:.0%}"  # noqa: E731
        L.append(f"| {x['name']} | {x['from']}~{x['to']} | {g(lo, 'dd_now')} / {g(us, 'dd_now')} | {g(lo, 'maxdd')} / {g(us, 'maxdd')} | "
                 f"{g(lo, 'p1_30', pr)} / {g(us, 'p1_30', pr)} | {g(lo, 'base_p1', pr)} / {g(us, 'base_p1', pr)} |")
    L += ["", "- 러시아는 2022-02 이후 외국인 거래가 막혀, 그 전에 산 외국인은 사실상 회수 불가 — 지수 숫자에 나타나지 않는 손실입니다.", ""]
    p = Path(config_mod.ROOT) / "docs" / f"stocks-dip-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    log(f"  → {p}")
    return p
