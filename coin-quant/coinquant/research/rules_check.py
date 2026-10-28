"""코인 '개인 판단 체크리스트' 규칙 검증 — `python -m coinquant rules-check`

체크리스트에 '검증한 규칙 아님'으로 붙어 있던 것들을 결과 보기 전에 정한 방식으로 시험:
  1. 큰 일정(FOMC) 2일 안에 매수 신호 → '절반 먼저, 나머지는 일정 뒤' / '일정 뒤 전부'
     + FOMC 앞뒤 이틀 가격 변동이 평소보다 큰지 (무작위 날짜와 비교)
  2. 4년 주기 달력: 반감기 후 18~30개월 (예전 '사이클 달력 규칙'의 현금 구간)에 공식 매수 막기 / 아예 현금
  3. '이번 주기 최저 아래로 마감하면 경고': 그런 날 이후 20·60·120일 수익이 평소보다 나쁜가
  4. 위험 뉴스(해킹·사고·규제) → 진입 보류: 과거 뉴스 기록이 없어 검증 불가 (문서에 명시)
데이터: extended.py 와 같음 (BTC 2010~ Coin Metrics + 바이낸스, ETH 2015~). 평가 2014-01 ~ 2024-12, 2025~ 는 이미 본 구간으로 따로.
FOMC 날짜: 연준 공개 일정의 결정일(회의 둘째 날, 임시회의 포함)을 손으로 입력 (이 환경에서 연준 사이트가 막혀 있음).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..trend import MultiTrend
from .cycle import HALVINGS
from .extended import fmt, positions, run, spliced

FOMC = """
2013-01-30 2013-03-20 2013-05-01 2013-06-19 2013-07-31 2013-09-18 2013-10-30 2013-12-18
2014-01-29 2014-03-19 2014-04-30 2014-06-18 2014-07-30 2014-09-17 2014-10-29 2014-12-17
2015-01-28 2015-03-18 2015-04-29 2015-06-17 2015-07-29 2015-09-17 2015-10-28 2015-12-16
2016-01-27 2016-03-16 2016-04-27 2016-06-15 2016-07-27 2016-09-21 2016-11-02 2016-12-14
2017-02-01 2017-03-15 2017-05-03 2017-06-14 2017-07-26 2017-09-20 2017-11-01 2017-12-13
2018-01-31 2018-03-21 2018-05-02 2018-06-13 2018-08-01 2018-09-26 2018-11-08 2018-12-19
2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11
2020-01-29 2020-03-03 2020-03-15 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16
"""
FOMC_DATES = pd.DatetimeIndex(sorted(pd.to_datetime(FOMC.split())))
START, END = "2014-01-01", "2024-12-31"
PCT = {"연복리", "MDD", "총수익", "투자 비중", "평균", "중앙값", "하위10%", "오른 비율", "차이(규칙−공식) 평균", "차이 중앙값",
       "평소 평균", "FOMC 평균", "평소", "FOMC 창"}


# ---------------------------------------------------------------- 1. FOMC
def fomc_volatility(c: pd.Series, n_perm: int = 2000, seed: int = 0) -> dict:
    """FOMC 결정일(UTC 그날 봉)과 다음 날 2일 수익의 크기 vs 무작위 2일 창."""
    r2 = np.log(c).diff(2).shift(-1)            # 결정일 D 전날 종가 → D+1 종가
    r2 = r2.loc[START:END].dropna()
    ev = r2.reindex(FOMC_DATES).dropna()
    ev = ev[(ev.index >= START) & (ev.index <= END)]
    rng = np.random.default_rng(seed)
    allv = r2.to_numpy()
    obs = np.abs(ev).mean()
    perm = np.array([np.abs(rng.choice(allv, len(ev), replace=False)).mean() for _ in range(n_perm)])
    return {"FOMC 수": len(ev), "평소 |2일 변동| 평균": np.abs(allv).mean(), "FOMC 창 |2일 변동| 평균": obs,
            "무작위보다 큰 확률": (perm < obs).mean(), "FOMC 창 평균 수익": ev.mean(), "평소 평균 수익": allv.mean()}


def event_positions(base: pd.DataFrame, mode: str, days: int = 2) -> pd.DataFrame:
    """매수 신호가 켜진 날 t 뒤 days 일 안(t < D ≤ t+days)에 FOMC 결정일 D 가 있으면:
    half = D 전날까지 절반만, D 종가부터 전부 / wait = D 전날까지 0, D 종가부터 전부. (D 종가 = 발표 뒤, UTC 18~19시 발표)"""
    pos = base.astype(float).copy()
    idx = base.index
    fomc = set(FOMC_DATES)
    for a in base.columns:
        b = base[a].fillna(False).astype(bool).to_numpy()
        on = np.flatnonzero(b & ~np.r_[False, b[:-1]])
        for i in on:
            hit = [k for k in range(1, days + 1) if i + k < len(idx) and idx[i + k] in fomc]
            if not hit:
                continue
            j = i + hit[0]
            seg = slice(i, j)                    # t ~ D-1
            keep = b[i:j]
            pos.iloc[seg, pos.columns.get_loc(a)] = np.where(keep, 0.5 if mode == "half" else 0.0, 0.0)
    return pos


def affected(base: pd.DataFrame, days: int = 2) -> list[tuple]:
    out = []
    fomc = set(FOMC_DATES)
    for a in base.columns:
        b = base[a].fillna(False).astype(bool).to_numpy()
        for i in np.flatnonzero(b & ~np.r_[False, b[:-1]]):
            if any(i + k < len(base) and base.index[i + k] in fomc for k in range(1, days + 1)):
                out.append((a, base.index[i]))
    return out


# ---------------------------------------------------------------- 2·3. 4년 주기
def halving_window(idx: pd.DatetimeIndex, m0: int = 18, m1: int = 30) -> pd.Series:
    w = pd.Series(False, index=idx)
    for h in HALVINGS:
        w |= (idx >= h + pd.DateOffset(months=m0)) & (idx < h + pd.DateOffset(months=m1))
    return w


def cycle_low_break(c: pd.Series) -> pd.Series:
    """그날까지 데이터로: 마지막 반감기 이후 최고가(주기 고점) 다음의 최저 종가를 새로 깨고 마감한 날."""
    c = c.dropna()
    out = pd.Series(False, index=c.index)
    for i, h in enumerate(HALVINGS):
        nxt = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else c.index[-1] + pd.Timedelta(days=1)
        seg = c.loc[h:nxt - pd.Timedelta(days=1)]
        if len(seg) < 30:
            continue
        peak_pos = seg.cummax()
        is_peak = seg >= peak_pos                  # 그날이 주기 최고가
        low_since_peak = seg.where(~is_peak).groupby(is_peak.cumsum()).cummin()
        prev_low = low_since_peak.groupby(is_peak.cumsum()).shift(1)
        brk = (seg < prev_low) & prev_low.notna() & (seg < peak_pos * 0.7)   # 고점 대비 30% 넘게 빠진 뒤의 새 저점
        out.loc[brk.index] = brk
    return out


def fwd_stats(c: pd.Series, mask: pd.Series, name: str) -> list[dict]:
    rows = []
    for h in (20, 60, 120):
        f = (c.shift(-h) / c - 1).loc[START:END]
        m = mask.reindex(f.index).fillna(False)
        for lab, sel in (("모든 날", f.notna()), (name, m)):
            v = f[sel].dropna()
            runs = int((sel & ~sel.shift(1, fill_value=False)).sum())
            rows.append({"기간(일)": h, "조건": lab, "날 수": len(v), "따로 떨어진 구간": runs if lab != "모든 날" else 1,
                         "중앙값": v.median(), "하위10%": v.quantile(0.1), "오른 비율": (v > 0).mean()})
    return rows


# ---------------------------------------------------------------- 실행
def perf(pos: pd.DataFrame, D: dict, cols: list[str], cost: float, a: str, z: str) -> dict:
    res = run(pos, D, cols, cost)
    eq = res.equity.loc[a:z]
    yrs = (eq.index[-1] - eq.index[0]).days / 365
    return {"총수익": eq.iloc[-1] / eq.iloc[0] - 1, "연복리": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1,
            "MDD": (eq / eq.cummax() - 1).min(), "투자 비중": res.weights.loc[a:z].sum(axis=1).mean(),
            "거래수": int(((res.trades["entry"] >= pd.Timestamp(a)) & (res.trades["entry"] <= pd.Timestamp(z))).sum())}


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    D = spliced(cache, ("BTC", "ETH"))
    c = D["close"]
    mt = MultiTrend(c, D["high"], D["low"])
    base = positions(mt, c)["A. 확정 공식"]
    cost = cfg["costs"]["fee"] + cfg["costs"]["slippage_core"]
    uni = {"BTC": ["BTC"], "BTC·ETH 균등": ["BTC", "ETH"]}

    # 1. FOMC
    vol = pd.DataFrame([{"코인": a, **fomc_volatility(c[a].dropna())} for a in ("BTC", "ETH")])
    variants = {"공식 그대로 (바로 전부)": base,
                "FOMC 2일 안이면 절반 먼저, 발표 뒤 나머지": event_positions(base.astype(bool), "half"),
                "FOMC 2일 안이면 발표 뒤에 전부": event_positions(base.astype(bool), "wait")}
    rows = []
    for u, cols in uni.items():
        for name, p in variants.items():
            for lab, a, z in (("개발 2014~2024", START, END), ("이미 본 2025~", "2025-01-01", None)):
                rows.append({"대상": u, "방식": name, "구간": lab, **perf(p, D, cols, cost, a, z or c.index[-1])})
    fomc_tab = pd.DataFrame(rows)
    aff = [x for x in affected(base.astype(bool)) if pd.Timestamp(START) <= x[1] <= pd.Timestamp(END)]
    # 영향받은 진입만: 신호일 종가 → FOMC 다음 날 종가, 바로 산 경우 vs 발표 뒤 산 경우 (차이 = 기다린 동안 놓친/피한 수익)
    pair = []
    for a, t in aff:
        d_ = next(x for x in FOMC_DATES if x > t)
        s = c[a]
        pair.append({"코인": a, "신호일": t.date(), "FOMC": d_.date(), "신호일→발표일 종가 변화": s.at[d_] / s.at[t] - 1})
    pair = pd.DataFrame(pair)
    log("  FOMC 완료")

    # 2. 주기 달력 필터
    win = halving_window(c.index)
    cyc_var = {"공식 그대로": base.astype(float),
               "반감기 후 18~30개월엔 새로 사지 않음": _block_entries(base.astype(bool), win),
               "반감기 후 18~30개월엔 현금 (있던 것도 정리)": base.astype(float).mul(~win, axis=0)}
    crow = []
    cyc_periods = [("2012 주기 창 2014-05~2015-05", "2014-05-28", "2015-05-28"), ("2016 주기 창 2018-01~2019-01", "2018-01-09", "2019-01-09"),
                   ("2020 주기 창 2021-11~2022-11", "2021-11-11", "2022-11-11"), ("개발 2014~2024 전체", START, END),
                   ("이미 본 2025~ (2024 주기 창 2025-10~)", "2025-01-01", None)]
    for u, cols in uni.items():
        for name, p in cyc_var.items():
            for lab, a, z in cyc_periods:
                crow.append({"대상": u, "방식": name, "구간": lab, **perf(p, D, cols, cost, a, z or c.index[-1])})
    cyc_tab = pd.DataFrame(crow)
    log("  주기 필터 완료")

    # 3. 주기 최저 이탈
    brk = cycle_low_break(c["BTC"])
    low_tab = pd.DataFrame(fwd_stats(c["BTC"], brk, "주기 최저를 새로 깨고 마감한 날"))
    on_after = base["BTC"].astype(bool) & brk.reindex(base.index).fillna(False).rolling(30, min_periods=1).max().astype(bool)
    n_on_after = int((on_after & ~on_after.shift(1, fill_value=False)).loc[START:END].sum())

    out = config_mod.ROOT / "reports" / "research"
    out.mkdir(parents=True, exist_ok=True)
    for nm, df in (("vol", vol), ("fomc", fomc_tab), ("fomc_pairs", pair), ("cycle", cyc_tab), ("cyclelow", low_tab)):
        df.to_csv(out / f"rules_check_{nm}.csv", index=False)
    md = render(vol, fomc_tab, pair, cyc_tab, low_tab, n_on_after, c.index[-1], time.time() - t0)
    path = config_mod.ROOT / "docs" / f"rules-check-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def _block_entries(base: pd.DataFrame, block: pd.Series) -> pd.DataFrame:
    """block 인 날에는 새로 켜지는 신호를 무시 (이미 갖고 있으면 공식대로 보유·매도)."""
    out = pd.DataFrame(0.0, index=base.index, columns=base.columns)
    B = block.reindex(base.index).fillna(False).to_numpy()
    for a in base.columns:
        b = base[a].fillna(False).to_numpy()
        held = False
        v = np.zeros(len(b))
        for i in range(len(b)):
            if held and not b[i]:
                held = False
            elif not held and b[i] and not B[i]:   # 막는 기간이 끝나면 신호가 켜져 있는 한 다시 삼
                held = True
            v[i] = held
        out[a] = v
    return out


def render(vol, fomc_tab, pair, cyc_tab, low_tab, n_on_after, last, secs) -> str:
    keep = ["대상", "방식", "구간", "총수익", "연복리", "MDD", "투자 비중", "거래수"]
    o = [f"# 코인 체크리스트 규칙 검증 ({last.date()}까지 데이터)\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 조건\n",
         "- 공식(중기 추세 보유 + 장기 하락이면 중단)은 그대로 두고, 체크리스트 규칙만 덧붙였을 때를 비교. 규칙은 결과를 보기 전에 정함.",
         "- 데이터: BTC 2010~ (Coin Metrics + 바이낸스), ETH 2015~. 개발 구간 2014~2024, 2025~ 는 이미 본 구간이라 참고.",
         "- 비용 매매마다 0.15%. 그날 종가 판단 → 다음 날 시가 체결.",
         "- FOMC 날짜는 연준 공개 일정의 결정일(임시회의 2020-03-03·03-15 포함)을 손으로 입력했습니다 (이 환경에서 연준 사이트 접속 불가).",
         f"- 계산 {secs:.0f}초\n",
         "## 1. 큰 일정(FOMC) 앞에서 '절반 먼저'\n",
         "### FOMC 앞뒤 이틀 가격 변동이 평소보다 큰가\n",
         "결정일 전날 종가 → 다음 날 종가(2일) 변동의 크기. '무작위보다 큰 확률' = 같은 개수의 아무 2일을 2,000번 뽑았을 때 FOMC 쪽이 더 컸던 비율 "
         "(95% 이상이면 FOMC 때 실제로 더 출렁인다고 볼 수 있음).\n",
         fmt(vol.assign(**{"무작위보다 큰 확률": vol["무작위보다 큰 확률"].map("{:.0%}".format)})
             .rename(columns={"평소 |2일 변동| 평균": "평소", "FOMC 창 |2일 변동| 평균": "FOMC 창",
                              "FOMC 창 평균 수익": "FOMC 평균", "평소 평균 수익": "평소 평균"}), PCT), "",
         f"### 공식 매수 신호가 FOMC 2일 전 안에 켜진 경우: {len(pair)}번 (2014~2024)\n",
         "신호일 종가 → 발표일 종가 변화 = 기다렸다면 그만큼 비싸게(+) 또는 싸게(−) 샀다는 뜻.\n",
         fmt(pair, {"신호일→발표일 종가 변화"}) if len(pair) else "- 없음", "",
         "### 전체 성과\n", fmt(fomc_tab[keep], PCT), "",
         "## 2. 4년 주기 달력 필터 (반감기 후 18~30개월)\n",
         "예전 사이클 분석(`docs/cycle-analysis-2026-10-21.md`)의 '사이클 달력 규칙' 현금 구간을 공식에 덧붙임. 이 구간은 과거 주기를 보고 정한 것이라 "
         "2014~2024 결과도 낙관적일 수 있습니다(표본 안).\n",
         fmt(cyc_tab[keep], PCT), "",
         "## 3. '이번 주기 최저 아래로 마감하면 경고'\n",
         "주기 고점(반감기 이후 그날까지의 최고가) 대비 30% 넘게 빠진 뒤, 고점 이후 최저 종가를 새로 깨고 마감한 날 이후의 BTC 수익.\n",
         fmt(low_tab, PCT), "",
         f"이런 날 뒤 30일 안에 공식 매수 신호가 새로 켜진 횟수 (2014~2024): {n_on_after}번\n",
         "## 4. 위험 뉴스(해킹·사고·규제) → 진입 보류\n",
         "- 날짜가 정확히 남은 과거 뉴스 기록이 없어 **검증할 수 없습니다.** 손으로 고른 사건 몇 개로 시험하면 결과를 보고 고르는 셈이 되어 의미가 없습니다.\n"]
    return "\n".join(o)
