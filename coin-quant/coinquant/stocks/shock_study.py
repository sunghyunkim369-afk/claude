"""대형주 하루 급락(실적 쇼크 같은 날) 뒤 흐름 — `python -m coinquant stocks-shock` (GitHub Actions 에서)

질문: 구글(10/29 −13%)처럼 대형주가 하루에 10% 넘게 떨어진 뒤, 보통 더 빠지나(실적 발표 후 표류) 되돌아오나?
사건: 종가 기준 하루 −10% 이하 + 같은 날 S&P 500 은 −3% 보다 덜 빠짐(시장 전체 폭락이 아닌 그 종목 일).
대상: 지금 S&P 500 구성 종목(2000~) — ⚠ 생존 편향: 그 뒤 망해서 빠진 종목이 없어 '되돌아옴'이 부풀려짐.
'초대형'은 그날 직전 20일 평균 거래대금 상위 50. 같은 종목 사건은 60거래일 안에 다시 나면 첫 번째만.
수익은 사건 당일 종가에서 n거래일 뒤 종가, 'S&P 대비'는 같은 기간 S&P 500 수익을 뺀 값.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from . import data as sd, universe as su

H = (1, 5, 20, 60, 120, 250)


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    d = config_mod.cache_dir(cfg) / "stocks"
    us_u = su.us_universe()
    S = sd.update(d, "us", list(us_u["ticker"]), start="2000-01-01", log=log)
    I = sd.update(d, "shock_idx", ["^GSPC"], start="1999-01-01", log=log)
    c, v = S["close"], S["volume"]
    ix = I["close"]["^GSPC"].reindex(c.index).ffill()
    r, ri = c.pct_change(), ix.pct_change()
    dv = (c * v).shift(1).rolling(20, min_periods=15).mean()
    rank = dv.rank(axis=1, ascending=False)
    ev = (r <= -0.10) & (ri > -0.03).to_numpy()[:, None]
    rows = []
    for t in c.columns:
        days = list(ev.index[ev[t].fillna(False)])
        last = None
        for dt in days:
            i = c.index.get_loc(dt)
            if last is not None and i - last < 60:
                continue
            last = i
            row = {"ticker": t, "date": dt, "drop": float(r.at[dt, t]), "mega": bool(rank.at[dt, t] <= 50)}
            for n in H:
                if i + n < len(c) and not np.isnan(c[t].iat[i + n]):
                    row[f"r{n}"] = float(c[t].iat[i + n] / c[t].iat[i] - 1)
                    row[f"x{n}"] = row[f"r{n}"] - float(ix.iat[i + n] / ix.iat[i] - 1)
            rows.append(row)
    E = pd.DataFrame(rows)
    log(f"  사건 {len(E)}개 (초대형 {int(E['mega'].sum())}개)")
    # 비교: 아무 날이나 (같은 종목들, 같은 기간)
    base = {n: (c.shift(-n) / c - 1).stack().median() for n in (20, 60, 250)}

    def table(df, title):
        L = [f"### {title} (사건 {len(df)}개)", "", "| 기간 | 수익 중앙값 | 오른 비율 | S&P 대비 중앙값 | S&P 보다 나은 비율 | 사건 수 |", "|---|---|---|---|---|---|"]
        for n in H:
            a, x = df.get(f"r{n}"), df.get(f"x{n}")
            if a is None or a.dropna().empty:
                continue
            a, x = a.dropna(), x.dropna()
            L.append(f"| {n}거래일 | {a.median():+.1%} | {(a > 0).mean():.0%} | {x.median():+.1%} | {(x > 0).mean():.0%} | {len(a)} |")
        return L + [""]

    L = ["# 대형주 하루 급락(−10% 이하) 뒤 흐름 (" + str(pd.Timestamp.now(tz='Asia/Seoul').date()) + ")", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 원금 손실 가능성이 있습니다.", "",
         "- 사건: 종가 기준 하루 −10% 이하, 같은 날 S&P 500 은 −3% 보다 덜 빠짐. 지금 S&P 500 구성 종목, 2000년~.",
         "- ⚠ 생존 편향: 지금 남아 있는 종목만이라 '나중에 회복'이 실제보다 좋게 나옵니다. 특히 1년 뒤 숫자는 그만큼 낮춰서 보세요.",
         f"- 비교(아무 날이나 산 경우 같은 종목들 중앙값): 20일 {base[20]:+.1%}, 60일 {base[60]:+.1%}, 250일 {base[250]:+.1%}", ""]
    L += table(E, "전체")
    L += table(E[E["mega"]], "초대형 (거래대금 상위 50)")
    L += table(E[E["drop"] <= -0.12], "−12% 이하로 더 크게 빠진 날")
    L += table(E[E["mega"] & (E["drop"] <= -0.12)], "초대형 + −12% 이하")
    big = E[E["mega"]].sort_values("drop").head(25)
    L += ["### 초대형주 급락 큰 순서 25건", "", "| 종목 | 날짜 | 그날 | 20일 뒤 | 60일 뒤 | 250일 뒤 | 250일 S&P 대비 |", "|---|---|---|---|---|---|---|"]
    f = lambda x: "-" if pd.isna(x) else f"{x:+.0%}"  # noqa: E731
    for _, e in big.iterrows():
        L.append(f"| {e['ticker']} | {e['date']:%Y-%m-%d} | {e['drop']:+.1%} | {f(e.get('r20'))} | {f(e.get('r60'))} | {f(e.get('r250'))} | {f(e.get('x250'))} |")
    g = c["GOOGL"] if "GOOGL" in c.columns else None
    if g is not None:
        rg = g.pct_change()
        L += ["", "### 구글(GOOGL) 하루 −7% 이하였던 날", "", "| 날짜 | 그날 | S&P 그날 | 20일 뒤 | 60일 뒤 | 250일 뒤 |", "|---|---|---|---|---|---|"]
        for dt in rg.index[rg <= -0.07]:
            i = g.index.get_loc(dt)
            fw = [g.iat[i + n] / g.iat[i] - 1 if i + n < len(g) else np.nan for n in (20, 60, 250)]
            L.append(f"| {dt:%Y-%m-%d} | {rg.at[dt]:+.1%} | {ri.at[dt]:+.1%} | " + " | ".join(f(x) for x in fw) + " |")
    p = Path(config_mod.ROOT) / "docs" / f"stock-shock-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    log(f"  → {p}")
    return p
