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
    p = Path(config_mod.ROOT) / "docs" / f"stocks-dip-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    log(f"  → {p}")
    return p
