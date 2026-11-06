"""나스닥 하락 매수 알고리즘 검토 — `python -m coinquant nasdaq-dip` (GitHub Actions 에서)

질문: 나스닥이 고점 대비 얼마 빠졌을 때 사면 그냥 보유·매달 적립보다 나은가? 어떤 방식이 근거가 있나?
데이터: 나스닥 종합(^IXIC, 1971~, 배당 빠짐), 나스닥 100(^NDX, 1985~), QQQ(배당 포함, 1999~). 현금은 미 3개월 국채(^IRX) 이자.
체결: 그날 종가로 판단 → 다음 날 종가 체결, 매매마다 0.1%. 규칙·숫자는 미리 정함(−20/−30/−40%), 민감도는 따로 표.

방식
 A. 그냥 보유
 B1. 사다리 매수 후 계속 보유: 현금으로 시작, 신고가 뒤 −20%·−30%·−40%에 1/3씩 → 한 번 사면 계속 보유 (기다리는 동안 국채 이자)
 B2. 사다리 매수 → 전고점 회복하면 다시 현금 (하락장만 노리는 방식)
 C. 하락장 + 추세 확인: −20% 이상 빠진 하락장 안에서 추세 공식(중기)이 다시 켜지면 전부 매수 → 이후 공식대로
 D. 추세 공식만 (참고)
 E. 매달 적립: E0 매달 같은 금액 매수 / E1 절반은 매수·절반은 현금으로 모았다가 −20% 하락장에 모두 투입 / E2 −20%에서 2배, −30%에서 3배로 늘려 사기(현금 모아 둔 데서)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from . import data as sd, validate as V

COST = 0.001
TD = 252


def rf_daily(irx: pd.Series, index) -> pd.Series:
    return (irx.reindex(index).ffill() / 100 / TD).fillna(0.0)


def run_w(c: pd.Series, w: pd.Series, rf: pd.Series) -> pd.Series:
    """비중 w(그날 종가 판단) → 다음 날부터 적용. 일 수익(현금 부분은 국채 이자)."""
    w = w.reindex(c.index).fillna(0.0)
    held = w.shift(1).fillna(0.0)
    r = c.pct_change().fillna(0.0)
    turn = held.diff().abs().fillna(held.abs())
    return held * r + (1 - held) * rf - turn * COST


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / TD
    return {"cagr": eq.iloc[-1] ** (1 / yrs) - 1, "mdd": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(TD)) if r.std() > 0 else 0.0}


def ladder(c: pd.Series, levels=(-0.2, -0.3, -0.4), reset_at_high=False) -> pd.Series:
    dd = c / c.cummax() - 1
    w, out = 0.0, []
    for x, ath in zip(dd.to_numpy(), (dd >= -1e-9).to_numpy()):
        if reset_at_high and ath and w > 0:
            w = 0.0
        hit = sum(1 for lv in levels if x <= lv) / len(levels)
        w = max(w, hit)
        out.append(w)
    return pd.Series(out, index=c.index)


def dip_trend(c: pd.Series, h: pd.Series, l_: pd.Series, thr=-0.2) -> pd.Series:
    pos, _ = V.formula_position(c.to_frame("x"), h.to_frame("x"), l_.to_frame("x"))
    f = pos["x"].to_numpy()
    dd = (c / c.cummax() - 1).to_numpy()
    armed, on, out = False, False, []
    for i in range(len(c)):
        if dd[i] <= thr:
            armed = True
        if armed and f[i] > 0 and not on:
            on, armed = True, False
        if on and f[i] == 0:
            on = False
        out.append(1.0 if on else 0.0)
    return pd.Series(out, index=c.index)


def dca(c: pd.Series, rf: pd.Series, mode: str) -> tuple[float, float]:
    """매달 1씩 넣을 때 마지막 평가액과 넣은 돈. mode: E0 / E1 / E2."""
    c = c.dropna()
    dd = c / c.cummax() - 1
    month_first = c.groupby(c.index.to_period("M")).head(1).index
    units, cash, paid = 0.0, 0.0, 0.0
    rfa = rf.reindex(c.index).fillna(0.0)
    for i, d in enumerate(c.index):
        cash *= 1 + rfa.iat[i]
        if d in month_first:
            paid += 1
            if mode == "E0":
                units += (1 - COST) / c.iat[i]
            elif mode == "E1":
                units += 0.5 * (1 - COST) / c.iat[i]
                cash += 0.5
            elif mode == "E2":
                cash += 1
                mult = 3 if dd.iat[i] <= -0.3 else (2 if dd.iat[i] <= -0.2 else 1)
                amt = min(cash, mult * 1.0) if dd.iat[i] <= -0.2 else min(cash, 0.5)
                units += amt * (1 - COST) / c.iat[i]
                cash -= amt
        if mode == "E1" and dd.iat[i] <= -0.2 and cash > 0:
            units += cash * (1 - COST) / c.iat[i]
            cash = 0.0
    return units * c.iat[-1] + cash, paid


def episodes(c: pd.Series, thr=-0.2) -> list[dict]:
    dd = c / c.cummax() - 1
    out, i, n = [], 0, len(c)
    while i < n:
        if dd.iat[i] <= thr:
            j = i
            while j > 0 and dd.iat[j - 1] < 0:
                j -= 1
            peak = c.iloc[:i + 1].idxmax()
            k = i
            while k < n and dd.iat[k] < 0:
                k += 1
            seg = c.iloc[j:k]
            trough = seg.idxmin()
            out.append({"peak": peak.date(), "hit20": c.index[i].date(), "trough": trough.date(), "depth": float(dd.loc[trough]),
                        "hit_to_trough": float(c.loc[trough] / c.iat[i] - 1), "rec": c.index[k].date() if k < n else None,
                        "rec_y": (c.index[k] - c.index[i]).days / 365 if k < n else None})
            i = k
        i += 1
    return out


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    d = config_mod.cache_dir(cfg) / "stocks"
    F = sd.update(d, "nasdaq", ["^IXIC", "^NDX", "QQQ", "^IRX"], start="1960-01-01", log=log)
    irx = F["close"]["^IRX"].dropna()
    pct = lambda x: "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.1f}%"  # noqa: E731
    L = [f"# 나스닥 하락 매수 알고리즘 검토 ({pd.Timestamp.now(tz='Asia/Seoul').date()})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         "- 체결: 종가 판단 → 다음 날 종가, 매매 0.1%. 현금은 미 3개월 국채 이자. 나스닥 종합·100 은 배당 빠짐(보유 쪽이 실제보다 조금 불리), QQQ 는 배당 포함.", ""]
    for tk, nm, start in (("^IXIC", "나스닥 종합 (1974~, 배당 빠짐)", "1974-01-01"), ("^NDX", "나스닥 100 (1988~, 배당 빠짐)", "1988-01-01"),
                          ("QQQ", "QQQ (1999-03~, 배당 포함)", "1999-03-10")):
        if tk not in F["close"].columns:
            continue
        c = F["close"][tk].dropna()
        h = F["high"][tk].reindex(c.index).where(lambda x: x > 0).fillna(c)
        l_ = F["low"][tk].reindex(c.index).where(lambda x: x > 0).fillna(c)
        rf = rf_daily(irx, c.index)
        W = {"A. 그냥 보유": pd.Series(1.0, index=c.index),
             "B1. −20·−30·−40% 사다리 → 계속 보유": ladder(c),
             "B2. 사다리 → 전고점이면 현금": ladder(c, reset_at_high=True),
             "C. −20% 하락장 + 추세 공식 다시 켜지면": dip_trend(c, h, l_),
             "D. 추세 공식만 (참고)": V.formula_position(c.to_frame("x"), h.to_frame("x"), l_.to_frame("x"))[0]["x"]}
        R = {k: run_w(c, w, rf).loc[start:] for k, w in W.items()}
        L += [f"## {nm}", "", "| 방식 | 연복리 | MDD | 샤프 | 투자 비중 | 닷컴 2000-03~2002-10 | 금융위기 2007-10~2009-03 | 2022 | 2009~2024 |", "|---|---|---|---|---|---|---|---|---|"]
        for k, r in R.items():
            st = stats(r)
            seg = lambda a, b: pct(float((1 + r.loc[a:b]).prod() - 1)) if len(r.loc[a:b]) > 20 else "-"  # noqa: E731
            L.append(f"| {k} | {pct(st['cagr'])} | {pct(st['mdd'])} | {st['sharpe']:.2f} | {W[k].loc[start:].mean():.0%} | "
                     f"{seg('2000-03-10', '2002-10-09')} | {seg('2007-10-31', '2009-03-09')} | {seg('2022-01-01', '2022-12-31')} | {seg('2009-03-10', '2024-12-31')} |")
        # 민감도: 사다리 시작점·간격
        L += ["", "기준 바꿔 보기 (B1 계속 보유형, 연복리 / MDD):", "", "| 첫 매수 | 단계 | 연복리 | MDD | 보유 대비 연복리 차 |", "|---|---|---|---|---|"]
        a = stats(R["A. 그냥 보유"])
        for first in (-0.1, -0.15, -0.2, -0.25, -0.3, -0.4):
            for step in (0.1,):
                lv = (first, first - step, first - 2 * step)
                st = stats(run_w(c, ladder(c, lv), rf).loc[start:])
                L.append(f"| {first:.0%} | {first:.0%}/{first - step:.0%}/{first - 2 * step:.0%} | {pct(st['cagr'])} | {pct(st['mdd'])} | {pct(st['cagr'] - a['cagr'])} |")
        # 적립식
        L += ["", "매달 적립 (같은 돈을 넣었을 때 마지막 평가액, E0 = 1.00 기준):", "", "| 시작 | 기간 | E0 매달 매수 | E1 절반 모아 −20%에 투입 | E2 하락장에 2·3배 |", "|---|---|---|---|---|"]
        cs = c.loc[start:]
        starts = [y for y in range(cs.index[0].year, cs.index[-1].year - 9, 5)]
        rows = []
        for y in starts + ["전체"]:
            seg = cs if y == "전체" else cs.loc[f"{y}-01-01":f"{y + 9}-12-31"]
            if len(seg) < TD * 5:
                continue
            v = {m: dca(seg, rf, m)[0] for m in ("E0", "E1", "E2")}
            rows.append((y, v))
            L.append(f"| {y} | {'전체' if y == '전체' else f'{y}~{y + 9}'} | 1.00 | {v['E1'] / v['E0']:.2f} | {v['E2'] / v['E0']:.2f} |")
        # 10년 굴린 창: E1·E2 가 E0 를 이긴 비율
        win1 = win2 = n = 0
        for y in range(cs.index[0].year, cs.index[-1].year - 9):
            seg = cs.loc[f"{y}-01-01":f"{y + 9}-12-31"]
            if len(seg) < TD * 8:
                continue
            v = {m: dca(seg, rf, m)[0] for m in ("E0", "E1", "E2")}
            n += 1
            win1 += v["E1"] > v["E0"]
            win2 += v["E2"] > v["E0"]
        if n:
            L += ["", f"10년 구간 {n}개(1년씩 옮김) 중 E1 이 매달 매수보다 나은 구간 {win1 / n:.0%}, E2 는 {win2 / n:.0%}.", ""]
        # 지금 사기 vs −20%·−30% 기다리기 — 시작 해마다 10년 뒤 평가액 (기다리는 동안 국채 이자)
        L += ["", "지금 사기 vs 기다리기 (시작 해 1월마다, 10년 뒤 평가액 비율 = 기다리기 ÷ 지금 사기):", "",
              "| 기다리는 기준 | 10년 구간 수 | 기다린 쪽이 나은 비율 | 비율 중앙 | 최악 | 최고 | 10년 안에 끝내 못 산 비율 |", "|---|---|---|---|---|---|---|"]
        ddc = c / c.cummax() - 1
        for thr in (-0.1, -0.2, -0.3):
            ratios, never = [], 0
            for y in range(cs.index[0].year, cs.index[-1].year - 9):
                seg = c.loc[f"{y}-01-01":f"{y + 9}-12-31"]
                if len(seg) < TD * 9:
                    continue
                rfs = rf.reindex(seg.index).fillna(0.0)
                now_val = seg.iat[-1] / seg.iat[0]
                hit = np.flatnonzero((ddc.reindex(seg.index) <= thr).to_numpy())
                if len(hit) == 0:
                    never += 1
                    ratios.append(float((1 + rfs).prod()) / now_val)
                    continue
                i = hit[0]
                cash = float((1 + rfs.iloc[:i + 1]).prod())
                ratios.append(cash * seg.iat[-1] / seg.iat[i] / now_val)
            if ratios:
                r_ = np.array(ratios)
                L.append(f"| 고점 대비 {thr:.0%} | {len(r_)} | {(r_ > 1).mean():.0%} | {np.median(r_):.2f} | {r_.min():.2f} | {r_.max():.2f} | {never / len(r_):.0%} |")
        if tk == "^IXIC":
            L += ["", "### 나스닥 종합의 −20% 이상 하락장 전부", "", "| 고점 | −20% 닿은 날 | 바닥 | 고점 대비 최대 | −20% 날부터 바닥까지 더 | 전고점 회복 | −20% 날부터 회복까지 |", "|---|---|---|---|---|---|---|"]
            for e in episodes(c):
                ry = "-" if e["rec_y"] is None else f"{e['rec_y']:.1f}년"
                L.append(f"| {e['peak']} | {e['hit20']} | {e['trough']} | {pct(e['depth'])} | {pct(e['hit_to_trough'])} | {e['rec'] or '아직'} | {ry} |")
            L.append("")
    L += ["## 결론", "", "(실행 결과를 보고 사람이 씀)", ""]
    p = Path(config_mod.ROOT) / "docs" / f"nasdaq-dip-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    log(f"  → {p}")
    return p
