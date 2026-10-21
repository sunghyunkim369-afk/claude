"""지표 검증 실행: `python -m coinquant validate`

최종 검증 구간(holdout_start 이후)은 여기서 잘라내고 절대 보지 않습니다.
결과: docs/validation-YYYY-MM-DD.md (표) + reports/research/*.csv (전체 숫자)
"""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..backtest import engine, metrics, signals as sg, validate as V
from ..data.update import load_funding
from ..universe import Panel, core_assets, eligible_others

START = "2018-03-01"   # BTC·ETH 200일 지표가 처음 계산되는 시점 근처


class Ctx:
    def __init__(self, cfg: dict, cache: Path, log=print):
        self.cfg = cfg
        self.log = log
        self.lock = V.HoldoutLock(cache / "holdout_log.jsonl", cfg["validation"]["holdout_start"])
        full = Panel.load(cache / "panel")
        self.p = p = full.slice(None, self.lock.dev_end())          # ← 최종 검증 구간 잘라냄
        self.core = core_assets(p, cfg)
        self.elig = {m: eligible_others(p, cfg, m) for m in ("mcap", "volume")}
        others = sorted(set().union(*[set(e.columns[e.any()]) for e in self.elig.values()]))
        self.cols = self.core + [c for c in others if c not in self.core]
        self.F = sg.Features(p, self.cols)
        self.alive = p.close[self.cols].notna()
        c = cfg["costs"]
        self.costs = engine.Costs(c["fee"], c["slippage_core"], c["slippage_other"], tuple(self.core))
        self.migrated = frozenset(p.meta.index[p.meta["migrated_to"].notna()])
        self.base_rules = engine.Rules(rebalance_days=1, min_trade=cfg["portfolio"]["min_trade_weight"],
                                       always_last=self.migrated)
        self.funding = load_funding(cache)
        self.trials = V.TrialLog()
        self.btc = self.run(self.members("BTC")).equity

    def members(self, u: str) -> pd.DataFrame:
        if u in self.core:
            return self.alive[[u]]
        if u == "코어3 균등":
            return self.alive[self.core]
        if u == "BTC·ETH 균등":   # SOL 을 코어로 고른 것 자체가 사후 선택이라 비교용
            return self.alive[[c for c in self.core if c in ("BTC", "ETH")]]
        if u == "기타 상위30 균등":
            return self.elig["mcap"].reindex(columns=self.cols, fill_value=False)
        if u == "기타 상위30 균등(거래대금 순위)":
            return self.elig["volume"].reindex(columns=self.cols, fill_value=False)
        raise KeyError(u)

    def run(self, w: pd.DataFrame, **kw) -> engine.Result:
        w = w.loc[:, (w != 0).any()] if (w != 0).any().any() else w.iloc[:, :1]
        rules = replace(self.base_rules, **kw)
        return engine.run(w, self.p.open[w.columns], self.p.close[w.columns], self.costs, rules,
                          self.p.tradable[w.columns], start=START)


def _on_all(ctx: Ctx) -> pd.DataFrame:
    return pd.DataFrame(True, index=ctx.alive.index, columns=ctx.cols)


def _summ(ctx: Ctx, res: engine.Result, start: pd.Timestamp) -> dict:
    eq = _eq_from(res, start)
    return metrics.summarize(eq, ctx.btc, res.trades, res.weights.loc[eq.index], res.turnover.loc[eq.index],
                             res.costs_paid)


def _eq_from(res: engine.Result, start: pd.Timestamp) -> pd.Series:
    """대상 코인이 처음 생긴 날 전날부터 평가 (같은 대상의 모든 신호를 같은 기간으로 비교)."""
    return res.equity.loc[start - pd.Timedelta(days=1):]


def _start(ctx: Ctx, mem: pd.DataFrame) -> pd.Timestamp:
    """대상이 생기고, 200일 지표가 계산되는 첫날."""
    m = mem.loc[START:]
    ok = m.any(axis=1) & (ctx.F.sma(200)[list(mem.columns)].notna() & m).any(axis=1)
    return ok.index[ok.to_numpy()][0] + pd.Timedelta(days=1)


# ---------------------------------------------------------------- 1. 타이밍 신호

TIMING_UNIVERSES = ["BTC", "ETH", "SOL", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"]


def timing_tests(ctx: Ctx, n_random: int = 40) -> tuple[pd.DataFrame, dict]:
    sigs = sg.timing_signals(ctx.F)
    rows, curves = [], {}
    for u in TIMING_UNIVERSES:
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        base_res = ctx.run(sg.weights_slots(_on_all(ctx)[cols], mem))
        base_eq = _eq_from(base_res, st)
        bs = _summ(ctx, base_res, st)
        bs.update({"대상": u, "신호": "(신호 없음: 계속 보유)"})
        if u.startswith("기타"):
            z = ctx.run(sg.weights_slots(_on_all(ctx)[cols], mem), delist_mode="zero")
            bs["CAGR(폐지=0원)"] = metrics.cagr(_eq_from(z, st))
        rows.append(bs)
        curves[(u, "보유")] = base_eq
        ctx.trials.add("timing", "보유", u, base_eq)
        for name, fn in sigs.items():
            if name.startswith("BTC대비") and u == "BTC":
                continue
            t0 = time.time()
            on = fn()[cols].fillna(False).astype(bool)
            w = sg.weights_slots(on, mem)
            res = ctx.run(w)
            eq = _eq_from(res, st)
            s = _summ(ctx, res, st)
            s.update({"대상": u, "신호": name})
            s["CAGR차(vs 보유)"] = s["연복리(CAGR)"] - bs["연복리(CAGR)"]
            s["MDD차(vs 보유)"] = s["MDD"] - bs["MDD"]
            s["샤프차(vs 보유)"] = s["샤프"] - bs["샤프"]
            cons = V.era_consistency(eq, base_eq)
            s["시대수"] = cons["시대수"]
            s["수익우위시대"] = cons["수익우위시대"]
            s["MDD우위시대"] = cons["MDD우위시대"]
            if u.startswith("기타"):
                z = ctx.run(w, delist_mode="zero")
                s["CAGR(폐지=0원)"] = metrics.cagr(_eq_from(z, st))
            else:
                lag = ctx.run(w, exec_lag=1)
                s["CAGR(하루 늦게 체결)"] = metrics.cagr(_eq_from(lag, st))
                # 무작위 타이밍 위약: 보유 비율·평균 보유기간이 같은 무작위 신호
                srs = []
                for k in range(n_random):
                    ron = pd.DataFrame({c: sg.random_timing(on[c][mem[c]].loc[st:], seed=1000 * k + j)
                                        for j, c in enumerate(cols)}).reindex(on.index).fillna(False).astype(bool)
                    rres = ctx.run(sg.weights_slots(ron, mem))
                    srs.append(metrics.sharpe(_eq_from(rres, st).pct_change().dropna()))
                s["무작위 타이밍 대비 샤프 백분위"] = float((np.array(srs) < s["샤프"]).mean())
            rows.append(s)
            curves[(u, name)] = eq
            ctx.trials.add("timing", name, u, eq)
            ctx.log(f"   [{u}] {name}: CAGR {s['연복리(CAGR)']:.1%} MDD {s['MDD']:.1%} ({time.time() - t0:.1f}s)")
    return pd.DataFrame(rows), curves


# ---------------------------------------------------------------- 2. 기타 그룹 코인 고르기

def selection_tests(ctx: Ctx, ks=(5, 10), rebalance=7) -> tuple[pd.DataFrame, pd.DataFrame]:
    scores = sg.selection_scores(ctx.F)
    rows, rand_rows = [], []
    draws = int(ctx.cfg["validation"]["random_placebo_draws"])
    for method, u in (("mcap", "기타 상위30 균등"), ("volume", "기타 상위30 균등(거래대금 순위)")):
        mem = ctx.members(u)
        st = _start(ctx, mem)
        base = ctx.run(sg.weights_slots(_on_all(ctx), mem), rebalance_days=rebalance)
        base_eq = _eq_from(base, st)
        b = _summ(ctx, base, st)
        b.update({"순위기준": method, "방법": "(위약) 상위30 전체 균등", "K": 30})
        rows.append(b)
        for k in ks:
            n_draw = draws if k == ks[0] else max(draws // 4, 20)
            rnd = []
            for d in range(n_draw):
                rr = ctx.run(sg.weights_random(mem, k, rebalance, seed=d), rebalance_days=rebalance)
                e = _eq_from(rr, st)
                rnd.append({"순위기준": method, "K": k, "draw": d, "CAGR": metrics.cagr(e),
                            "MDD": metrics.max_drawdown(e), "샤프": metrics.sharpe(e.pct_change().dropna())})
            rnd = pd.DataFrame(rnd)
            rand_rows.append(rnd)
            r = {"순위기준": method, "방법": f"(위약) 무작위 {k}개 × {n_draw}회 중앙값", "K": k,
                 "연복리(CAGR)": rnd["CAGR"].median(), "MDD": rnd["MDD"].median(), "샤프": rnd["샤프"].median(),
                 "무작위 하위10% CAGR": rnd["CAGR"].quantile(0.1), "무작위 상위10% CAGR": rnd["CAGR"].quantile(0.9)}
            rows.append(r)
            for name, fn in scores.items():
                sc = fn().reindex(columns=mem.columns)
                w = sg.weights_topk(sc, mem, k)
                res = ctx.run(w, rebalance_days=rebalance)
                eq = _eq_from(res, st)
                s = _summ(ctx, res, st)
                s.update({"순위기준": method, "방법": name, "K": k})
                s["무작위 대비 CAGR 백분위"] = float((rnd["CAGR"] < s["연복리(CAGR)"]).mean())
                s["무작위 대비 샤프 백분위"] = float((rnd["샤프"] < s["샤프"]).mean())
                s["CAGR차(vs 전체균등)"] = s["연복리(CAGR)"] - b["연복리(CAGR)"]
                cons = V.era_consistency(eq, base_eq)
                s["시대수"], s["수익우위시대"], s["MDD우위시대"] = cons["시대수"], cons["수익우위시대"], cons["MDD우위시대"]
                z = ctx.run(w, rebalance_days=rebalance, delist_mode="zero")
                s["CAGR(폐지=0원)"] = metrics.cagr(_eq_from(z, st))
                rows.append(s)
                ctx.trials.add("selection", f"{name} K{k}", u, eq)
                ctx.log(f"   [{method} K={k}] {name}: CAGR {s['연복리(CAGR)']:.1%} (무작위 백분위 {s['무작위 대비 CAGR 백분위']:.0%})")
    return pd.DataFrame(rows), pd.concat(rand_rows)


# ---------------------------------------------------------------- 3. 시장 국면 필터

def regime_tests(ctx: Ctx) -> pd.DataFrame:
    regs = sg.regime_filters(ctx.p, ctx.funding)
    rows = []
    for u in ("BTC", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"):
        mem = ctx.members(u)
        on_all = _on_all(ctx)[list(mem.columns)]
        st = _start(ctx, mem)
        base = ctx.run(sg.weights_slots(on_all, mem))
        base_eq = _eq_from(base, st)
        b = _summ(ctx, base, st)
        b.update({"대상": u, "국면 필터": "(필터 없음)"})
        rows.append(b)
        for name, reg in regs.items():
            if "도미넌스" in name and not u.startswith("기타"):
                continue
            on = on_all.mul(reg.reindex(on_all.index).fillna(False), axis=0).astype(bool)
            res = ctx.run(sg.weights_slots(on, mem))
            eq = _eq_from(res, st)
            s = _summ(ctx, res, st)
            s.update({"대상": u, "국면 필터": name})
            s["CAGR차(vs 필터없음)"] = s["연복리(CAGR)"] - b["연복리(CAGR)"]
            s["MDD차(vs 필터없음)"] = s["MDD"] - b["MDD"]
            cons = V.era_consistency(eq, base_eq)
            s["시대수"], s["수익우위시대"], s["MDD우위시대"] = cons["시대수"], cons["수익우위시대"], cons["MDD우위시대"]
            rows.append(s)
            ctx.trials.add("regime", name, u, eq)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 4. 리밸런스 주기

def cadence_tests(ctx: Ctx, picks: dict[str, list[str]]) -> pd.DataFrame:
    sigs = sg.timing_signals(ctx.F)
    rows = []
    for u, names in picks.items():
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        for name in ["보유"] + names:
            on = _on_all(ctx)[cols] if name == "보유" else sigs[name]()[cols].fillna(False).astype(bool)
            w = sg.weights_slots(on, mem)
            for d in (1, 3, 7):
                res = ctx.run(w, rebalance_days=d)
                s = _summ(ctx, res, st)
                s.update({"대상": u, "신호": name, "리밸런스(일)": d})
                rows.append(s)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 5. 급락 다음 날 처리

def crash_study(ctx: Ctx, horizons=(7, 30)) -> pd.DataFrame:
    p = ctx.p
    C = p.close[ctx.cols]
    O = p.open[ctx.cols]
    r = C.pct_change(fill_method=None)
    mem_core = ctx.members("코어3 균등")
    mem_oth = ctx.members("기타 상위30 균등")
    rows = []
    for grp, mem, thr in (("코어", mem_core, -0.10), ("기타", mem_oth, -0.15), ("기타", mem_oth, -0.25)):
        cols = list(mem.columns[mem.any()])
        cost = ctx.costs.per_asset(cols)
        ev = (r[cols] <= thr) & mem[cols].reindex(r.index).fillna(False)
        ev = ev.loc[START:]
        for t, a in zip(*np.where(ev.to_numpy())):
            d = ev.index[t]
            a_name = cols[a]
            c = C[a_name]
            i = c.index.get_loc(d)
            p0 = c.iloc[i]                            # 급락일 종가 = 다음 날 시가 시각
            for H in horizons:
                if i + H >= len(c):
                    continue
                path = c.iloc[i + 1: i + H + 1]
                last_valid = path.dropna()
                died = path.isna().any()
                hold_last = (last_valid.iloc[-1] if len(last_valid) else p0) / p0 - 1
                hold_zero = -1.0 if died else hold_last
                # (b) 반등 기다리기: 5일 안에 첫 상승 마감일 다음 날 시가에 매도, 없으면 6일째 시가
                ex = None
                for k in range(1, 6):
                    if i + k + 1 >= len(c) or np.isnan(c.iloc[i + k]):
                        break
                    if c.iloc[i + k] > c.iloc[i + k - 1]:
                        ex = O[a_name].iloc[i + k + 1]
                        break
                if ex is None:
                    ex = O[a_name].iloc[min(i + 6, len(c) - 1)]
                wait = (ex / p0 - 1 if not np.isnan(ex) else hold_last) - 2 * cost[a]
                rows.append({"그룹": grp, "기준": f"{int(-thr * 100)}% 이상 급락", "날짜": d, "코인": a_name, "H": H,
                             "보유": hold_last, "보유(폐지=0원)": hold_zero,
                             "바로 매도": -2 * cost[a], "반등 기다려 매도": wait, "급락일수익": r[a_name].iloc[i]})
    return pd.DataFrame(rows)


def crash_table(ev: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (g, b, H), df in ev.groupby(["그룹", "기준", "H"]):
        for pol in ("보유", "보유(폐지=0원)", "바로 매도", "반등 기다려 매도"):
            x = df[pol]
            out.append({"그룹": g, "기준": b, "기간(일)": H, "처리": pol, "사건수": len(x), "서로 다른 날짜": df["날짜"].nunique(),
                        "평균": x.mean(), "중앙값": x.median(), "하위10%": x.quantile(0.1), "최악": x.min(),
                        "보유보다 나음 비율": (x > df["보유"]).mean() if pol != "보유" else np.nan})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 6. 워크포워드

def walkforward(ctx: Ctx, u: str, curves: dict, how="sharpe") -> tuple[pd.DataFrame, dict]:
    """학습 구간 성적으로 신호 하나를 고르고, 바로 다음 검증 구간에만 적용. 검증 구간끼리 이어 붙여 평가."""
    vcfg = ctx.cfg["validation"]
    sigs = sg.timing_signals(ctx.F)
    mem = ctx.members(u)
    cols = list(mem.columns)
    names = ["보유"] + [n for n in sigs if (u, n) in curves]
    first = curves[(u, "보유")].index[0]
    wins = V.wf_windows(max(first, pd.Timestamp(START)).strftime("%Y-%m-%d"), ctx.lock.dev_end().strftime("%Y-%m-%d"),
                        vcfg["wf_train_months"], vcfg["wf_test_months"])
    pieces, choices = [], []
    for tr0, tr1, te0, te1 in wins:
        sc = {n: V.score(curves[(u, n)].loc[tr0:tr1], how) for n in names}
        best = max(sc, key=lambda k: -np.inf if np.isnan(sc[k]) else sc[k])
        on = _on_all(ctx)[cols] if best == "보유" else sigs[best]()[cols].fillna(False).astype(bool)
        pieces.append(sg.weights_slots(on, mem).loc[te0:te1])
        choices.append({"학습": f"{tr0.date()}~{tr1.date()}", "검증": f"{te0.date()}~{te1.date()}", "고른 신호": best,
                        "학습 샤프": sc[best], "후보 수": len(names)})
    if not pieces:
        return pd.DataFrame(choices), {}
    w = pd.concat(pieces)
    w = w.reindex(ctx.alive.index).fillna(0.0)
    oos0, oos1 = wins[0][2], wins[-1][3]
    res = ctx.run(w)
    eq = res.equity.loc[oos0 - pd.Timedelta(days=1):oos1]
    base = curves[(u, "보유")].loc[oos0 - pd.Timedelta(days=1):oos1]
    btc = ctx.btc.loc[oos0 - pd.Timedelta(days=1):oos1]
    out = {"워크포워드": metrics.summarize(eq, btc), "같은 대상 보유": metrics.summarize(base, btc),
           "BTC 보유": metrics.summarize(btc), "기간": f"{oos0.date()} ~ {oos1.date()}"}
    return pd.DataFrame(choices), out


# ---------------------------------------------------------------- 표 만들기

PCT = {"총수익", "연복리(CAGR)", "MDD", "월평균", "월중앙값", "월하위10%", "월승률", "최악월", "BTC보유CAGR",
       "초과CAGR(vs BTC)", "BTC대비 월승률", "거래승률", "거래평균", "거래중앙값", "거래하위10%", "최악거래",
       "평균투자비중", "CAGR차(vs 보유)", "MDD차(vs 보유)", "CAGR(폐지=0원)", "CAGR(하루 늦게 체결)",
       "무작위 타이밍 대비 샤프 백분위", "무작위 대비 CAGR 백분위", "무작위 대비 샤프 백분위", "CAGR차(vs 전체균등)",
       "CAGR차(vs 필터없음)", "MDD차(vs 필터없음)", "무작위 하위10% CAGR", "무작위 상위10% CAGR", "평균", "중앙값",
       "하위10%", "최악", "보유보다 나음 비율", "수익", "누적비용(초기자본대비)", "연간 비용 부담(추정)", "CAGR(코어=BTC·ETH만)", "MDD(코어=BTC·ETH만)", "BTC 수익", "BTC MDD", "보유 수익", "보유 MDD", "보유", "바로 매도", "반등 기다려 매도"}
NUM = {"샤프", "샤프차(vs 보유)", "학습 샤프", "연회전율", "최고 연환산 샤프", "중앙값 샤프"}


def to_md(df: pd.DataFrame, cols: list[str]) -> str:
    cols = [c for c in cols if c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in PCT:
                cells.append(metrics.fmt(v, "pct"))
            elif c in NUM:
                cells.append(metrics.fmt(v, "num"))
            elif isinstance(v, float) and not np.isnan(v) and float(v).is_integer():
                cells.append(str(int(v)))
            elif isinstance(v, float) and np.isnan(v):
                cells.append("-")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(cfg: dict | None = None, quick: bool = False, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    out_dir = config_mod.ROOT / "reports" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ctx = Ctx(cfg, cache, log)
    log(f"개발 구간: {START} ~ {ctx.lock.dev_end().date()} (최종 검증 구간 {cfg['validation']['holdout_start']}~ 은 잠금)")

    log("1) 타이밍 신호")
    timing, curves = timing_tests(ctx, n_random=10 if quick else 40)
    timing.to_csv(out_dir / "timing.csv", index=False)
    log("2) 기타 그룹 코인 고르기")
    if quick:
        ctx.cfg = {**cfg, "validation": {**cfg["validation"], "random_placebo_draws": 20}}
    sel, rnd = selection_tests(ctx)
    sel.to_csv(out_dir / "selection.csv", index=False)
    rnd.to_csv(out_dir / "selection_random.csv", index=False)
    log("3) 시장 국면 필터")
    reg = regime_tests(ctx)
    reg.to_csv(out_dir / "regime.csv", index=False)
    log("4) 급락 다음 날 처리")
    ev = crash_study(ctx)
    ev.to_csv(out_dir / "crash_events.csv", index=False)
    crash = crash_table(ev)
    log("5) 워크포워드")
    wf = {u: walkforward(ctx, u, curves) for u in ("코어3 균등", "BTC·ETH 균등", "기타 상위30 균등")}
    log("6) 리밸런스 주기")
    picks = {}
    for u in ("코어3 균등", "기타 상위30 균등"):
        t = timing[(timing["대상"] == u) & timing["신호"].ne("(신호 없음: 계속 보유)")]
        picks[u] = list(t.sort_values("샤프", ascending=False)["신호"].head(2))
    cad = cadence_tests(ctx, picks)
    cad.to_csv(out_dir / "cadence.csv", index=False)
    era_curves = {}
    for u in ("BTC", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"):
        era_curves[u] = {"보유": curves[(u, "보유")]}
        for n in picks.get(u, []) or list(timing[timing["대상"] == u].sort_values("샤프", ascending=False)["신호"].head(2)):
            if (u, n) in curves:
                era_curves[u][n] = curves[(u, n)]

    import pickle
    with open(out_dir / "results.pkl", "wb") as f:   # 표만 다시 만들 때: rerender()
        pickle.dump({"timing": timing, "sel": sel, "reg": reg, "crash": crash, "wf": wf, "cad": cad,
                     "era_curves": era_curves, "secs": time.time() - t0, "trials": ctx.trials.rows}, f)
    md = render(ctx, timing, sel, reg, crash, wf, cad, era_curves, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"validation-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path} ({time.time() - t0:.0f}초)")
    return path


def rerender(cfg: dict | None = None) -> Path:
    """저장된 결과로 문서만 다시 만들기."""
    import pickle
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    r = pickle.load(open(config_mod.ROOT / "reports" / "research" / "results.pkl", "rb"))
    ctx = Ctx(cfg, cache)
    ctx.trials.rows = r["trials"]
    md = render(ctx, r["timing"], r["sel"], r["reg"], r["crash"], r["wf"], r["cad"], r["era_curves"], r["secs"])
    path = config_mod.ROOT / "docs" / f"validation-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    return path


def render(ctx, timing, sel, reg, crash, wf, cad, era_curves, secs) -> str:
    from .render import render_md
    return render_md(ctx, timing, sel, reg, crash, wf, cad, era_curves, secs)
