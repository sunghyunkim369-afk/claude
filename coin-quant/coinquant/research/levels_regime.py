"""매물대·수평선 이론: 현물 ETF 승인(2024-01-10) 전후 비교 — `python -m coinquant levels-regime`

질문: 예전엔 선에서 딱딱 반응했는데, ETF 이후엔 '안 지키는 척(살짝 이탈·속임수)하다가 결국 뚫고 가는' 경우가 많아졌나?
정의는 levels.py 그대로(바꾸지 않음). 사건은 '어제까지 그려진 선' 기준.
'속임수' 비율은 사건 뒤 N일을 보고 판정하는 '측정용' 값이라 매매 신호가 아님. 매매 변형(이탈 후 3일 복귀 못 하면 매도)은 그날까지 데이터만 씀.
③ 구간은 최종 검증 구간(2025~)을 포함 — 공식 성과가 아니라 시장 성질 비교용. 이걸 보고 공식을 바꾸면 모의 운용으로 다시 확인해야 함.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, strategy
from ..backtest import metrics, signals as sg
from .indicator_validation import START, Ctx, to_md
from .levels_study import compute_levels
from .short_swing import swing_sim

PERIODS = [("① 2018-03~2021-12", "2018-03-01", "2021-12-31"),
           ("② 2022-01~ETF 전", "2022-01-01", "2024-01-09"),
           ("③ ETF 후 2024-01-10~", "2024-01-10", None)]
ETF = pd.Timestamp("2024-01-10")


def _any_future(cond_fn, c: pd.DataFrame, n: int) -> pd.DataFrame:
    """t+1 ~ t+n 중 하나라도 cond(그날 종가) 가 참인지. 끝부분(n일이 안 남은 날)은 NaN."""
    hits = [cond_fn(c.shift(-k)) for k in range(1, n + 1)]
    out = hits[0].copy()
    for h_ in hits[1:]:
        out = out | h_
    return out.astype(float).where(c.shift(-n).notna())


def measures(ctx: Ctx, L: dict, cols: list[str]) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """{이름: (사건 마스크, 결과 값(0/1 또는 수익))} — 결과는 사건 뒤를 본 '측정값'."""
    c, h, l_ = ctx.F.c[cols], ctx.F.h[cols], ctx.F.l[cols]
    y = {k: v.reindex(columns=cols).shift(1) for k, v in L.items()}
    S, R = y["pl_sup"], y["pl_res"]
    zs, zr_lo, zr_hi = y["vz_sup_lo"], y["vz_res_lo"], y["vz_res_hi"]
    out = {}
    # 선이 잘 지켜졌나
    touch_s = (l_ <= S * 1.005) & (c >= S * 0.99)
    out["[선] 지지선 터치 후 5일 버팀 비율"] = (touch_s, 1 - _any_future(lambda x: x < S * 0.99, c, 5))
    touch_r = (h >= R * 0.995) & (c <= R * 1.01)
    out["[선] 저항선 터치 후 5일 되밀림 비율 (못 뚫음)"] = (touch_r, 1 - _any_future(lambda x: x > R * 1.01, c, 5))
    # 속임수
    bd = c < S * 0.99
    bu = c > R * 1.01
    rj = (h >= R * 0.995) & (c < R)
    for n in (3, 5):
        out[f"[선] 지지 이탈 후 {n}일 안에 복귀 (가짜 이탈)"] = (bd, _any_future(lambda x: x > S, c, n))
        out[f"[선] 저항 돌파 후 {n}일 안에 되돌아옴 (가짜 돌파)"] = (bu, _any_future(lambda x: x < R, c, n))
    for m in (10, 20):
        out[f"[선] 저항에서 밀린 뒤 {m}일 안에 결국 돌파"] = (rj, _any_future(lambda x: x > R * 1.01, c, m))
    zbd = c < zs
    zbu = c > zr_hi
    zrj = (h >= zr_lo) & (c < zr_lo)
    out["[매물대] 지지 매물대 이탈 후 3일 안에 복귀 (가짜 이탈)"] = (zbd, _any_future(lambda x: x > zs, c, 3))
    out["[매물대] 위 매물대 돌파 후 3일 안에 되돌아옴 (가짜 돌파)"] = (zbu, _any_future(lambda x: x < zr_hi, c, 3))
    out["[매물대] 매물대에서 밀린 뒤 20일 안에 결국 돌파"] = (zrj, _any_future(lambda x: x > zr_hi, c, 20))
    near = ((c / y["pl_sup"] - 1).abs() < 0.01) | ((c / y["pl_res"] - 1).abs() < 0.01)
    has = y["pl_sup"].notna() | y["pl_res"].notna()
    out["[선] 강한 선 ±1% 안에서 마감한 날 비율"] = (has, near.astype(float).where(has))
    # 이후 20일 수익 (사건일 종가 기준)
    f20 = c.shift(-20) / c - 1
    for nm, ev in (("[선] 지지선 이탈", bd), ("[선] 저항선 돌파", bu), ("[선] 저항선에서 밀림", rj), ("[선] 지지선 터치(버팀)", touch_s),
                   ("[매물대] 지지 매물대 이탈", zbd), ("[매물대] 위 매물대 돌파", zbu), ("[매물대] 매물대에서 밀림", zrj)):
        out[f"이후20일 수익: {nm}"] = (ev, f20)
    # 가짜 이탈이 '확인된 뒤'(3일째 종가) 사면? — 3일째까지만 보고 아는 정보라 미래 데이터 아님
    rec3 = _any_future(lambda x: x > S, c, 3)
    out["이후20일 수익: 지지 이탈 → 3일 안 복귀 확인 시점부터"] = (bd & (rec3 == 1), c.shift(-23) / c.shift(-3) - 1)
    out["이후20일 수익: 지지 이탈 → 3일 안 복귀 못 함 확인 시점부터"] = (bd & (rec3 == 0), c.shift(-23) / c.shift(-3) - 1)
    return {k: (m.fillna(False).astype(bool), v) for k, (m, v) in out.items()}


def summarize_by_period(meas, members, group) -> tuple[pd.DataFrame, dict]:
    rows, raw = [], {}
    for name, (ev, val) in meas.items():
        e = ev & members.reindex(index=ev.index, columns=ev.columns).fillna(False)
        df = val.where(e).stack().dropna().rename("v").reset_index()
        df.columns = ["date", "asset", "v"]
        df = df[df["date"] >= pd.Timestamp(START)]
        raw[name] = df
        for pn, a, z in PERIODS:
            d = df[(df["date"] >= pd.Timestamp(a)) & ((df["date"] <= pd.Timestamp(z)) if z else True)]
            r = {"그룹": group, "측정": name, "기간": pn, "사례 수": len(d), "서로 다른 날짜": d["date"].nunique()}
            if name.startswith("이후20일"):
                r.update({"중앙값": d["v"].median(), "하위10%": d["v"].quantile(0.1), "오른 비율": (d["v"] > 0).mean()})
            else:
                r["비율"] = d["v"].mean()
            rows.append(r)
    return pd.DataFrame(rows), raw


def boot_diff(df: pd.DataFrame, stat="mean", n=2000, block=30, seed=0) -> tuple[float, float, float, float]:
    """ETF 후 − ETF 전 차이와 90% 구간. 날짜를 30일 덩어리로 묶어 기간 안에서 다시 뽑음(같은 날 여러 코인은 한 덩어리)."""
    rng = np.random.default_rng(seed)
    f = (lambda x: x.mean()) if stat == "mean" else (lambda x: x.median())
    pre, post = df[df["date"] < ETF], df[df["date"] >= ETF]
    if len(pre) < 10 or len(post) < 10:
        return np.nan, np.nan, np.nan, np.nan
    def blocks(d):  # noqa: E306
        k = ((d["date"] - d["date"].min()).dt.days // block).to_numpy()
        return [d["v"].to_numpy()[k == b] for b in np.unique(k)]
    bp, bq = blocks(pre), blocks(post)
    obs = f(post["v"]) - f(pre["v"])
    diffs = []
    for _ in range(n):
        a = np.concatenate([bp[i] for i in rng.integers(0, len(bp), len(bp))])
        b = np.concatenate([bq[i] for i in rng.integers(0, len(bq), len(bq))])
        diffs.append(f(pd.Series(b)) - f(pd.Series(a)))
    diffs = np.array(diffs)
    return float(obs), float(np.quantile(diffs, 0.05)), float(np.quantile(diffs, 0.95)), float((diffs > 0).mean())


def exit_after_failed_recovery(c: pd.DataFrame, level: pd.DataFrame, n=3, reset: pd.DataFrame | None = None) -> pd.DataFrame:
    """지지선을 −1% 아래로 마감한 뒤 n일 안에 선 위로 복귀하지 못하면 n일째 종가부터 '매도(보유 금지)' 신호,
    깨진 선 위로 다시 마감하거나 reset(예: 추세 신호 꺼짐)이 참인 날까지 유지. 그날까지 데이터만 씀."""
    C, Lv = c.to_numpy(float), level.to_numpy(float)
    Rs = reset.reindex_like(c).fillna(False).to_numpy(bool) if reset is not None else np.zeros(C.shape, dtype=bool)
    out = np.zeros(C.shape, dtype=bool)
    lvl = np.full(C.shape[1], np.nan)   # 지켜보는 깨진 선
    age = np.zeros(C.shape[1])
    blocked = np.zeros(C.shape[1], dtype=bool)
    for t in range(len(C)):
        active = ~np.isnan(lvl)
        rec = active & (C[t] > lvl)
        lvl = np.where(rec, np.nan, lvl)
        blocked &= ~rec
        age = np.where(active & ~rec, age + 1, 0)
        blocked |= (age >= n) & ~np.isnan(lvl)
        blocked &= ~Rs[t]
        lvl = np.where(Rs[t], np.nan, lvl)
        out[t] = blocked
        brk = np.isnan(lvl) & (C[t] < Lv[t] * 0.99)
        lvl = np.where(brk, Lv[t], lvl)
        age = np.where(brk, 0, age)
    return pd.DataFrame(out, index=c.index, columns=c.columns)


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    cf = copy.deepcopy(cfg)
    cf["validation"]["holdout_start"] = "2100-01-01"
    ctx = Ctx(cf, cache, lambda *a: None)
    oth = ctx.members("기타 상위30 균등")
    oth_cols = [c for c in oth.columns[oth.any()] if c not in ctx.core]
    cols = ctx.core + oth_cols
    L = compute_levels(ctx, cols)
    log(f"  선·매물대 계산 {time.time() - t0:.0f}초")
    tabs, boots = [], []
    for gname, gcols, mem in (("BTC", ["BTC"], ctx.alive[["BTC"]]), ("코어(BTC·ETH·SOL)", ctx.core, ctx.alive[ctx.core]),
                              ("기타 상위30(그 시점)", oth_cols, oth[oth_cols])):
        meas = measures(ctx, L, gcols)
        tab, raw = summarize_by_period(meas, mem, gname)
        tabs.append(tab)
        for name, df in raw.items():
            stat = "median" if name.startswith("이후20일") else "mean"
            obs, lo, hi, pr = boot_diff(df, stat)
            boots.append({"그룹": gname, "측정": name, "ETF 후 − 전": obs, "90% 구간": f"{lo * 100:+.1f}%p ~ {hi * 100:+.1f}%p"
                          if not np.isnan(lo) else "-", "ETF 후가 클 확률": pr})
    tab = pd.concat(tabs)
    boot = pd.DataFrame(boots)
    log("  측정 완료")
    # 미리 정한 매매 변형: 지지 이탈 '즉시' 매도 vs '3일 안 복귀 못 하면' 매도
    c = ctx.F.c[cols]
    mt = strategy.multitrend(ctx.F)
    base = (mt.h["중기"].state & ~(mt.h["장기"].score <= -2))[cols]
    sup = L["pl_sup"].reindex(columns=cols).shift(1)
    # 매도 신호가 켜져 있는 동안은 다시 사지 않음 (안 그러면 다음 날 바로 재진입해 사고팔기만 반복)
    x_now = c < sup * 0.99
    x_3d = exit_after_failed_recovery(c, sup, 3, reset=~base)  # 추세가 한 번 꺼지면 다음 추세는 새로 시작
    var = {"확정 공식": base.astype(float),
           "+ 지지선 이탈 즉시 매도": swing_sim(base & ~x_now, ~base | x_now, c),
           "+ 지지선 이탈 후 3일 안에 복귀 못 하면 매도": swing_sim(base & ~x_3d, ~base | x_3d, c)}
    prow = []
    for u in ("BTC", "BTC·ETH 균등", "코어3 균등"):
        mem = ctx.members(u)
        ucols = list(mem.columns)
        for name, pos in var.items():
            eq = ctx.run(sg.weights_slots(pos[ucols].fillna(0.0), mem)).equity
            r = {"대상": u, "방식": name}
            for pn, a, z in PERIODS:
                seg = eq.loc[pd.Timestamp(a) - pd.Timedelta(days=1):z]
                r[pn] = seg.iloc[-1] / seg.iloc[0] - 1
                r[pn + " MDD"] = metrics.max_drawdown(seg)
            prow.append(r)
    perf = pd.DataFrame(prow)
    log("  매매 변형 완료")
    out = config_mod.ROOT / "reports" / "research"
    tab.to_csv(out / "levels_regime.csv", index=False)
    boot.to_csv(out / "levels_regime_boot.csv", index=False)
    perf.to_csv(out / "levels_regime_variants.csv", index=False)
    md = render(tab, boot, perf, time.time() - t0, ctx.p.close.index[-1])
    path = config_mod.ROOT / "docs" / f"levels-regime-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def render(tab, boot, perf, secs, last) -> str:
    def piv(g, names, val):
        d = tab[(tab["그룹"] == g) & tab["측정"].isin(names)]
        p = d.pivot_table(index="측정", columns="기간", values=val, sort=False)
        n = d.pivot_table(index="측정", columns="기간", values="서로 다른 날짜", sort=False)
        p = p[[x[0] for x in PERIODS if x[0] in p.columns]]
        p.columns = [f"{c} {val}" for c in p.columns]
        p["서로 다른 날짜(①/②/③)"] = n.apply(lambda r: "/".join(str(int(r[x[0]])) if x[0] in r and not pd.isna(r[x[0]]) else "-" for x in PERIODS), axis=1)
        return p.reset_index()

    rate_names = [m for m in tab["측정"].unique() if not m.startswith("이후20일")]
    ret_names = [m for m in tab["측정"].unique() if m.startswith("이후20일")]
    o = [f"# 매물대·수평선 이론: 현물 ETF 승인 전후 비교 ({last.date()}까지)\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 조건\n",
         "- 정의는 지난 검증(`docs/levels-2026-10-21.md`, `coinquant/levels.py`)과 같음: 매물대 180일·40칸·평균 1.5배, "
         "수평선 = 1년 고점·저점(앞뒤 5일) 1% 안 묶음, 3회 이상 = 강한 선. 정의는 바꾸지 않았습니다.",
         "- 기간: ① 2018-03~2021-12, ② 2022-01~2024-01-09(ETF 승인 전), ③ 2024-01-10~현재(ETF 승인 후).",
         "- '속임수' 비율(가짜 이탈·가짜 돌파·밀린 뒤 결국 돌파)은 사건 뒤 며칠을 보고 판정한 **측정값**입니다(매매 신호 아님).",
         "- ③은 최종 검증 구간(2025~)을 포함합니다. 공식 성과가 아니라 '시장 성질이 바뀌었나' 확인용이며, 이 결과로 공식을 바꾸면 모의 운용으로 다시 확인해야 합니다.",
         "- 'ETF 후가 클 확률'과 90% 구간: 날짜를 30일 덩어리로 묶어 각 기간 안에서 2,000번 다시 뽑아 본 결과(같은 날 여러 코인은 한 덩어리). "
         "90% 구간이 0을 넘지 않으면 차이가 우연일 수 있습니다.",
         f"- 계산 {secs / 60:.1f}분\n"]
    for g in tab["그룹"].unique():
        o += [f"## {g}\n", "### 선이 지켜졌나 · 속임수 비율\n", to_md(piv(g, rate_names, "비율"), list(piv(g, rate_names, "비율").columns)), "",
              "### 사건 뒤 20일 수익 중앙값\n", to_md(piv(g, ret_names, "중앙값"), list(piv(g, ret_names, "중앙값").columns)), "",
              "### ETF 후 − ETF 전 (①+②) 차이\n",
              to_md(boot[boot["그룹"] == g], ["측정", "ETF 후 − 전", "90% 구간", "ETF 후가 클 확률"]), ""]
    o += ["## 매매 변형 (미리 정한 것): 지지선 이탈 '즉시' 매도 vs '3일 안 복귀 못 하면' 매도\n",
          "③ 구간 숫자는 이미 본 구간이라 참고만 하세요.\n",
          to_md(perf, ["대상", "방식"] + [c for p in PERIODS for c in (p[0], p[0] + " MDD")]), ""]
    return "\n".join(o)
