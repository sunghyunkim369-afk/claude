"""기관식 개선 검증 — 변동성 맞추기·여러 속도 추세 섞기: `python -m coinquant risk-overlay`

확정 공식(중기 추세 보유 + 장기 하락이면 중단, 켜면 그 코인 몫 100% / 끄면 0%)에 기관이 흔히 붙이는 두 가지를 더했을 때 나아지나?
1. 변동성 맞추기: 비중 = 신호 × min(1, 목표 변동성 ÷ 최근 30일 변동성). 목표 연 40·60·80% (미리 정한 세 값, 고르지 않고 모두 보고).
2. 여러 속도 섞기: (가) 단기·중기·장기 추세 상태 평균 (나) (가) + 장기 −2 이하면 0 (확정 공식의 폭락 멈춤 유지)
   (다) 시계열 모멘텀 30·90·180일 (지난 수익이 플러스면 1) 평균.
3. 둘을 같이: 변동성 60% × (나).

데이터·비용·유니버스는 기존 검증과 같음(상장폐지 포함 패널, 개발 구간만, 판단 종가 → 다음 날 시가).
채택 기준(미리 정함, 개발 구간):
 ① 코어3에서 샤프가 확정 공식보다 높을 확률(30일 블록 부트스트랩) ≥ 90%
 ② 기타 상위30(그 시점, 상폐 포함)에서 샤프가 확정 공식보다 낮지 않음
 ③ 코어3 MDD 가 확정 공식보다 깊지 않음
 ④ 하루 늦게 체결해도 연복리가 원래의 70% 이상
2025-01 이후는 확정 공식 최종 검증 때 이미 연 구간이라 '참고'로만 따로 표시.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, indicators as ind, strategy
from ..backtest import metrics, signals as sg
from .indicator_validation import Ctx, _eq_from, _on_all, _start
from .short_swing import run_all

UNIVERSES = ["BTC", "ETH", "SOL", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"]
BASE = "(기준) 확정 공식"


def systems(F) -> dict[str, pd.DataFrame]:
    mt = strategy.multitrend(F)
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    c = F.c
    base = (M.state & ~(L.score <= -2)).astype(float)
    vol = ind.realized_vol(c, 30, 365)
    scale = {t: (t / vol).clip(upper=1.0).fillna(0.0) for t in (0.4, 0.6, 0.8)}
    ens_a = (S.state.astype(float) + M.state.astype(float) + L.state.astype(float)) / 3
    ens_b = ens_a.where(~(L.score <= -2), 0.0)
    tsm = sum((c / c.shift(n) - 1 > 0).astype(float) for n in (30, 90, 180)) / 3
    tsm = tsm.where(c.shift(180).notna(), 0.0)
    out = {BASE: base}
    for t, sc in scale.items():
        out[f"1. 변동성 맞추기 (목표 연 {t:.0%})"] = base * sc
    out["2가. 여러 속도 섞기: 단기·중기·장기 평균"] = ens_a
    out["2나. 여러 속도 섞기 + 장기 −2 이하면 0"] = ens_b
    out["2다. 시계열 모멘텀 30·90·180일 평균"] = tsm
    out["3. 변동성 60% × 2나"] = ens_b * scale[0.6]
    return {k: v.where(c.notna(), 0.0) for k, v in out.items()}


def boot_prob(a: pd.Series, b: pd.Series, n: int = 1000, block: int = 30, seed: int = 3) -> tuple[float, float, float]:
    """두 곡선 일수익의 샤프 차이(a − b): 이길 확률, 90% 구간 (같은 날짜 블록을 같이 뽑음)."""
    ra, rb = a.pct_change(), b.pct_change()
    df = pd.concat([ra, rb], axis=1).dropna()
    x = df.to_numpy()
    T = len(x)
    rng = np.random.default_rng(seed)
    k = int(np.ceil(T / block))
    d = []
    for _ in range(n):
        idx = (rng.integers(0, T - block, k)[:, None] + np.arange(block)).ravel()[:T]
        s = x[idx]
        sh = s.mean(0) / s.std(0) * np.sqrt(365)
        d.append(sh[0] - sh[1])
    d = np.array(d)
    return float((d > 0).mean()), float(np.quantile(d, 0.05)), float(np.quantile(d, 0.95))


def main(cfg: dict | None = None, log=print) -> Path:
    t0 = time.time()
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    ctx = Ctx(cfg, cache, log)
    sysd = systems(ctx.F)
    perf, curves, _ = run_all(ctx, cfg, sysd, UNIVERSES)
    for (u, name), eq in curves.items():
        if name != "보유":
            ctx.trials.add("risk-overlay", name, u, eq)
    log(f"  개발 구간 성과 ({time.time() - t0:.0f}초)")
    # 투자 비중 (평균 보유 비율)
    expo = {}
    for u in UNIVERSES:
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        for name, pos in sysd.items():
            w = sg.weights_slots(pos[cols].fillna(0.0), mem)
            expo[(u, name)] = float(w.sum(axis=1).loc[st:].mean())
    # 부트스트랩: 확정 공식 대비
    boot = {}
    for u in ("코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"):
        for name in sysd:
            if name == BASE:
                continue
            boot[(u, name)] = boot_prob(curves[(u, name)], curves[(u, BASE)])
    log(f"  부트스트랩 ({time.time() - t0:.0f}초)")
    # 참고: 2025 이후 (이미 본 구간)
    cfg_f = copy.deepcopy(cfg)
    cfg_f["validation"]["holdout_start"] = "2100-01-01"
    ctx_f = Ctx(cfg_f, cache, lambda *a: None)
    sys_f = systems(ctx_f.F)
    hs = pd.Timestamp(cfg["validation"]["holdout_start"]) - pd.Timedelta(days=1)
    ref = []
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        mem = ctx_f.members(u)
        cols = list(mem.columns)
        for name, pos in {"(기준) 계속 보유": _on_all(ctx_f)[cols].astype(float), **sys_f}.items():
            eq = ctx_f.run(sg.weights_slots(pos[cols].fillna(0.0), mem)).equity.loc[hs:]
            s = metrics.summarize(eq, ctx_f.btc.loc[hs:])
            ref.append({"대상": u, "방식": name, "총수익": eq.iloc[-1] / eq.iloc[0] - 1, "MDD": s["MDD"], "샤프": s["샤프"]})
    ref = pd.DataFrame(ref)
    log(f"  2025 이후 참고 ({time.time() - t0:.0f}초)")
    md = render(perf, expo, boot, ref, sysd, ctx, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"risk-overlay-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"  → {path}")
    return path


def render(perf, expo, boot, ref, sysd, ctx, secs) -> str:
    P = lambda x, d=1: "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{d}f}%"  # noqa: E731
    F2 = lambda x: "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.2f}"  # noqa: E731
    g = lambda u, n, k: perf[(perf["대상"] == u) & (perf["방식"] == n)][k].iloc[0]  # noqa: E731
    today = pd.Timestamp.now(tz="Asia/Seoul").date()
    L = [f"# 변동성 맞추기·여러 속도 섞기 검증 ({today})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         "## 0. 조건", "",
         "- 기존 검증과 같은 데이터·엔진: 상장폐지 포함 패널, 개발 구간(2018-03 ~ 2024-12)만, 판단 종가 → 다음 날 시가, 비용 업비트 0.05% + 슬리피지(코어 0.1%, 기타 0.3%).",
         "- 숫자(목표 변동성 40·60·80%, 30일 변동성, 30·90·180일)는 흔히 쓰는 값을 미리 정해 넣었고, 셋 다 보고합니다(좋은 것만 고르지 않음).",
         "- 채택 기준(미리 정함): ① 코어3 샤프가 확정 공식보다 높을 확률 ≥ 90% (30일 블록 부트스트랩 1000번) ② 기타 상위30 샤프가 확정 공식보다 낮지 않음 "
         "③ 코어3 MDD 가 더 깊지 않음 ④ 하루 늦게 체결해도 연복리 70% 이상 유지.",
         f"- 계산 {secs / 60:.1f}분.", "",
         "## 1. 개발 구간 결과", ""]
    for u in UNIVERSES:
        L += [f"### {u}", "", "| 방식 | 연복리 | MDD | 샤프 | 월하위10% | 평균 투자 비중 | 연간 비용 | 하루 늦게 연복리 | 시대(수익/MDD) |", "|---|---|---|---|---|---|---|---|---|"]
        L.append(f"| (참고) 계속 보유 | {P(g(u, '(기준) 계속 보유', '연복리(CAGR)'))} | {P(g(u, '(기준) 계속 보유', 'MDD'), 0)} | {F2(g(u, '(기준) 계속 보유', '샤프'))} | - | 100% | - | - | - |")
        for n in sysd:
            L.append(f"| {n} | {P(g(u, n, '연복리(CAGR)'))} | {P(g(u, n, 'MDD'), 0)} | {F2(g(u, n, '샤프'))} | {P(g(u, n, '월하위10%'))} | "
                     f"{expo[(u, n)]:.0%} | {P(g(u, n, '연간 비용 부담(추정)'))} | {P(g(u, n, 'CAGR(하루 늦게 체결)'))} | {g(u, n, '시대(수익/MDD)')} |")
        L.append("")
    L += ["## 2. 확정 공식 대비 샤프 차이 (부트스트랩)", "",
          "| 방식 | 코어3: 나을 확률 (90% 구간) | BTC·ETH: 나을 확률 (90% 구간) | 기타 상위30: 나을 확률 (90% 구간) | 판정 ①②③④ |", "|---|---|---|---|---|"]
    passed = []
    for n in sysd:
        if n == BASE:
            continue
        cells = []
        for u in ("코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"):
            p, lo, hi = boot[(u, n)]
            cells.append(f"{p:.0%} ({lo:+.2f} ~ {hi:+.2f})")
        c1 = boot[("코어3 균등", n)][0] >= 0.9
        c2 = g("기타 상위30 균등", n, "샤프") >= g("기타 상위30 균등", BASE, "샤프")
        c3 = g("코어3 균등", n, "MDD") >= g("코어3 균등", BASE, "MDD")
        c4 = g("코어3 균등", n, "CAGR(하루 늦게 체결)") >= 0.7 * g("코어3 균등", n, "연복리(CAGR)")
        ok = c1 and c2 and c3 and c4
        if ok:
            passed.append(n)
        L.append(f"| {'✅ ' if ok else ''}{n} | " + " | ".join(cells) + f" | {''.join('○' if c else '×' for c in (c1, c2, c3, c4))} |")
    L += ["", f"**통과: {', '.join(passed) if passed else '없음'}**", "",
          f"## 3. 참고: 2025-01 ~ 현재 (확정 공식 최종 검증 때 이미 연 구간 — 깨끗한 검증 아님)", "",
          "| 대상 | 방식 | 총수익 | MDD | 샤프 |", "|---|---|---|---|---|"]
    for _, r in ref.iterrows():
        L.append(f"| {r['대상']} | {r['방식']} | {P(r['총수익'])} | {P(r['MDD'], 0)} | {F2(r['샤프'])} |")
    L += ["", "## 4. 결론", "", "(실행 결과를 보고 사람이 씀)", ""]
    return "\n".join(L)
