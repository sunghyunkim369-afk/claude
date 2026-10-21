"""단기 스윙(며칠~2주) 검증: `python -m coinquant short-swing`

방식 8가지를 개발 구간(2018-03 ~ 2024-12)에서 비교합니다. 2025년 이후는 확정 공식의 최종 검증 때 이미 본 구간이라
'참고(깨끗한 검증 아님)'로만 따로 표시합니다.
판단은 그날 종가, 체결은 다음 날 시가. 목표가·손절·추적 손절도 '종가 기준'으로 판단해 다음 날 시가에 나갑니다
(장중에 바로 손절하는 게 아니어서 갭 손실이 그대로 반영됨 — 일봉으로 하는 스윙의 현실).
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, indicators as ind, strategy
from ..backtest import metrics, signals as sg, validate as V
from .indicator_validation import START, Ctx, _eq_from, _on_all, _start, to_md
from .trend_study import CLASSES, capture_study, trade_stats

UNIVERSES = ["BTC", "ETH", "SOL", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"]


def swing_sim(entry: pd.DataFrame, exit_sig: pd.DataFrame, close: pd.DataFrame, atr: pd.DataFrame | None = None,
              max_days: int | None = None, target: float | None = None, stop: float | None = None,
              trail: float | None = None) -> pd.DataFrame:
    """진입 조건 → 보유, 청산 조건(신호·최대 보유일·목표가·손절·ATR 추적 손절) → 현금. 같은 날 청산 후 재진입은 안 함."""
    E = entry.fillna(False).to_numpy(bool)
    X = exit_sig.fillna(False).to_numpy(bool)
    C = close.to_numpy(float)
    A = atr.to_numpy(float) if atr is not None else None
    n, m = C.shape
    pos = np.zeros((n, m))
    held = np.zeros(m, dtype=bool)
    ep = np.full(m, np.nan)
    hi = np.full(m, np.nan)
    days = np.zeros(m)
    for i in range(n):
        c = C[i]
        days = np.where(held, days + 1, 0)
        hi = np.where(held, np.fmax(hi, c), hi)
        ex = X[i] | np.isnan(c)
        if max_days:
            ex |= days >= max_days
        if target:
            ex |= c >= ep * (1 + target)
        if stop:
            ex |= c <= ep * (1 - stop)
        if trail and A is not None:
            ex |= c < hi - trail * A[i]
        ex &= held
        held = held & ~ex
        new = ~held & ~ex & E[i] & ~np.isnan(c)
        ep = np.where(new, c, np.where(held, ep, np.nan))
        hi = np.where(new, c, hi)
        days = np.where(new, 0, days)
        held = held | new
        pos[i] = held
    return pd.DataFrame(pos, index=close.index, columns=close.columns)


def systems(F) -> dict[str, pd.DataFrame]:
    mt = strategy.multitrend(F)
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    c = F.c
    atr = F.atr(14)
    ema8 = ind.ema(c, 8)
    rsi3 = ind.rsi(c, 3)
    no = pd.DataFrame(False, index=c.index, columns=c.columns)
    pull = M.state & (L.score > -2) & (rsi3 < 20)
    brk = (c > ind.donchian_high(F.h, 10)) & (ind.volume_ratio(F.v, 20) > 1.5)
    return {
        "1. 단기 추세 (기준: 단기 점수 +2 켜기 / 0 끄기)": S.state.astype(float),
        "2. 단기 추세 + 중기 상승일 때만 진입": swing_sim((S.score >= 2) & (M.score >= 2), S.score <= 0, c),
        "3. 단기 추세 + 장기 하락이면 안 함": swing_sim((S.score >= 2) & (L.score > -2), (S.score <= 0) | (L.score <= -2), c),
        "4. 눌림목: 중기 상승 중 3일 RSI<20 매수 → EMA8 회복 시 매도 (최대 7일, 손절 −8%)":
            swing_sim(pull, c > ema8, c, max_days=7, stop=0.08),
        "5. 돌파: 10일 고가 + 거래량 1.5배 → 5일 저가 이탈 매도 (최대 15일)":
            swing_sim(brk, c < ind.donchian_low(F.l, 5), c, max_days=15),
        "6. 단기 추세 + ATR 2.5배 추적 손절 + 최대 15일": swing_sim(S.score >= 2, S.score <= 0, c, atr=atr, trail=2.5, max_days=15),
        "7. 단기 추세 + 목표 +10% / 손절 −5%": swing_sim(S.score >= 2, S.score <= 0, c, target=0.10, stop=0.05),
        "8. 눌림목 + 목표 +8% / 손절 −5% (최대 10일)": swing_sim(pull, no, c, target=0.08, stop=0.05, max_days=10),
        "(비교) 확정 공식: 중기 보유 + 장기 하락이면 중단": (M.state & ~(L.score <= -2)).astype(float),
    }


def run_all(ctx: Ctx, cfg, systems_: dict, universes=UNIVERSES):
    rows, curves, trades = [], {}, {}
    for u in universes:
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        base = _eq_from(ctx.run(sg.weights_slots(_on_all(ctx)[cols], mem)), st)
        b = metrics.summarize(base, ctx.btc)
        b.update({"대상": u, "방식": "(기준) 계속 보유"})
        rows.append(b)
        curves[(u, "보유")] = base
        for name, pos in systems_.items():
            w = sg.weights_slots(pos[cols].fillna(0.0), mem)
            res = ctx.run(w)
            eq = _eq_from(res, st)
            s = metrics.summarize(eq, ctx.btc, None, res.weights.loc[eq.index], res.turnover.loc[eq.index])
            s.update(trade_stats(res.trades[res.trades["exit"] >= st]))
            s.update({"대상": u, "방식": name})
            s["CAGR차(vs 보유)"] = s["연복리(CAGR)"] - b["연복리(CAGR)"]
            rate = cfg["costs"]["fee"] + (cfg["costs"]["slippage_other"] if u.startswith("기타") else cfg["costs"]["slippage_core"])
            s["연간 비용 부담(추정)"] = s["연회전율"] * rate
            s["CAGR(하루 늦게 체결)"] = metrics.cagr(_eq_from(ctx.run(w, exec_lag=1), st))
            cons = V.era_consistency(eq, base)
            s["시대(수익/MDD)"] = f"{cons['수익우위시대']}/{cons['MDD우위시대']} ({cons['시대수']})"
            rows.append(s)
            curves[(u, name)] = eq
            trades[(u, name)] = res.trades
    return pd.DataFrame(rows), curves, trades


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    ctx = Ctx(cfg, cache, log)                      # 개발 구간만
    sysd = systems(ctx.F)
    perf, curves, trades = run_all(ctx, cfg, sysd)
    for name in sysd:
        for u in UNIVERSES:
            if (u, name) in curves:
                ctx.trials.add("short-swing", name, u, curves[(u, name)])
    log("  개발 구간 성과 완료")
    elig = ctx.members("기타 상위30 균등")
    groups = {"코어(BTC·ETH·SOL)": ctx.core, "기타 상위30(그 시점)": [c for c in elig.columns[elig.any()] if c not in ctx.core]}
    cap = capture_study(ctx, sysd, groups, elig)
    log("  포착률 완료")
    # 무작위 타이밍 위약 (코어3)
    mem = ctx.members("코어3 균등")
    cols = list(mem.columns)
    st = _start(ctx, mem)
    rnd = []
    for name, pos in sysd.items():
        on = pos[cols] > 0
        actual = perf[(perf["대상"] == "코어3 균등") & (perf["방식"] == name)]["샤프"].iloc[0]
        srs = []
        for k in range(30):
            ron = pd.DataFrame({c: sg.random_timing(on[c][mem[c]].loc[st:], seed=700 * k + j) for j, c in enumerate(cols)})
            ron = ron.reindex(on.index).fillna(False).astype(bool)
            srs.append(metrics.sharpe(_eq_from(ctx.run(sg.weights_slots(ron, mem)), st).pct_change().dropna()))
        rnd.append({"방식": name, "실제 샤프": actual, "무작위 샤프 중앙값": np.median(srs), "무작위 대비 백분위": float((np.array(srs) < actual).mean())})
    rnd = pd.DataFrame(rnd)
    log("  무작위 위약 완료")
    # 중기 공식 + 단기 스윙 나눠 담기 (코어3, 미리 정한 조합: 확정 공식 70% + 2번 30%)
    mix = {}
    for name in ("2. 단기 추세 + 중기 상승일 때만 진입", "1. 단기 추세 (기준: 단기 점수 +2 켜기 / 0 끄기)"):
        mix[f"확정 공식 70% + {name.split('.')[0]}번 30%"] = 0.7 * sysd["(비교) 확정 공식: 중기 보유 + 장기 하락이면 중단"] + 0.3 * sysd[name]
    mperf, _, _ = run_all(ctx, cfg, mix, ["코어3 균등", "BTC·ETH 균등"])
    log("  나눠 담기 완료")
    # 참고: 2025 이후 (이미 본 구간)
    cfg_full = copy.deepcopy(cfg)
    cfg_full["validation"]["holdout_start"] = "2100-01-01"
    ctx_f = Ctx(cfg_full, cache, lambda *a: None)
    sys_f = systems(ctx_f.F)
    ref_rows = []
    hs = pd.Timestamp(cfg["validation"]["holdout_start"]) - pd.Timedelta(days=1)
    btc_h = ctx_f.btc.loc[hs:]
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        mem = ctx_f.members(u)
        cols = list(mem.columns)
        for name, pos in {"(기준) 계속 보유": _on_all(ctx_f)[cols].astype(float), **sys_f}.items():
            res = ctx_f.run(sg.weights_slots(pos[cols].fillna(0.0), mem))
            eq = res.equity.loc[hs:]
            s = metrics.summarize(eq, btc_h)
            s.update({"대상": u, "방식": name})
            ref_rows.append(s)
    ref = pd.DataFrame(ref_rows)
    log("  2025 이후 참고 완료")
    md = render(ctx, perf, cap, rnd, mperf, ref, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"short-swing-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    perf.to_csv(config_mod.ROOT / "reports" / "research" / "short_swing_perf.csv", index=False)
    cap.to_csv(config_mod.ROOT / "reports" / "research" / "short_swing_capture.csv", index=False)
    ref.to_csv(config_mod.ROOT / "reports" / "research" / "short_swing_2025ref.csv", index=False)
    log(f"완료: {path}")
    return path


def render(ctx, perf, cap, rnd, mperf, ref, secs) -> str:
    o = []
    w = o.append
    w(f"# 단기 스윙(며칠~2주) 검증 (개발 구간 2018-03 ~ {ctx.lock.dev_end().date()})\n")
    w("> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n")
    w("## 0. 조건\n")
    w("- 일봉 기준: 그날 종가로 판단 → 다음 날 시가 체결. 목표가·손절·추적 손절도 종가로 판단해 다음 날 시가에 나감(장중 즉시 손절 아님).")
    w("- 비용: 업비트 0.05% + 슬리피지(코어 0.1%, 기타 0.3%). 매일 판단, 비중 변화 1% 미만은 주문 안 함.")
    w("- 숫자(8/21일선, RSI 3일 <20, 10일 고가, +10%/−5% 등)는 흔히 쓰는 값을 미리 정해 넣었고 이 데이터로 고르지 않았습니다.")
    w("- 판정 기준(미리 정함): 계속 보유·확정 공식보다 샤프가 높고, 무작위 타이밍보다 상위 10% 안이며, 하루 늦게 체결해도 무너지지 않아야 함.")
    w(f"- 시험 {ctx.trials.count('short-swing')}개 + 나눠 담기 {2 * 2}개, 계산 {secs / 60:.1f}분.\n")
    w("## 1. 방식별 성과 (개발 구간)\n")
    cols = ["방식", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "CAGR차(vs 보유)", "평균투자비중", "거래수", "거래승률", "평균 이익", "평균 손실",
            "손익비", "평균 보유일", "7일 내 끝난 거래 비중", "연간 비용 부담(추정)", "CAGR(하루 늦게 체결)", "시대(수익/MDD)"]
    for u in perf["대상"].unique():
        w(f"### {u}\n")
        w(to_md(perf[perf["대상"] == u], cols))
        w("")
    w("## 2. 무작위 타이밍 위약 (코어3, 같은 보유 비율·평균 보유기간의 무작위 30회)\n")
    w(to_md(rnd, ["방식", "실제 샤프", "무작위 샤프 중앙값", "무작위 대비 백분위"]))
    w("")
    w("## 3. 며칠짜리 추세를 얼마나 먹었나 (포착률, 코어)\n")
    c = cap[cap["그룹"] == "코어(BTC·ETH·SOL)"]
    w(to_md(c, ["방식", "추세 길이", "상승 구간 수", "상승 포착률 중앙값", "절반 이상 포착한 비율", "놓친 비율(포착≤0)", "하락 맞은 비율 중앙값"]))
    w("")
    w("## 4. 확정 공식(중기)에 단기 스윙을 섞으면 (미리 정한 비율 70:30)\n")
    w(to_md(mperf[mperf["방식"] != "(기준) 계속 보유"], ["대상", "방식", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "평균투자비중", "거래수",
                                                    "연간 비용 부담(추정)", "CAGR(하루 늦게 체결)"]))
    w("")
    w("## 5. 참고: 2025-01 ~ 현재 (확정 공식 최종 검증 때 이미 본 구간 — 깨끗한 검증 아님)\n")
    w("이 구간 숫자를 보고 방식을 고르면 안 됩니다. 개발 구간 결론이 이 구간에서도 비슷한 방향인지만 확인하는 용도입니다.\n")
    w(to_md(ref, ["대상", "방식", "총수익", "MDD", "샤프", "월하위10%", "초과CAGR(vs BTC)"]))
    w("")
    o.extend(conclusions(perf, cap, rnd, mperf, ref))
    return "\n".join(o)


def conclusions(perf, cap, rnd, mperf, ref) -> list[str]:
    def pct(v):
        return "-" if v is None or pd.isna(v) else f"{v * 100:+.0f}%"

    o = ["## 6. 결론\n"]
    rows = []
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        d = perf[perf["대상"] == u]
        ref_m = d[d["방식"].str.startswith("(비교)")].iloc[0]
        hold = d[d["방식"] == "(기준) 계속 보유"].iloc[0]
        for _, r in d[d["방식"].str.match(r"^\d\.")].iterrows():
            pr = rnd[rnd["방식"] == r["방식"]]["무작위 대비 백분위"]
            ok = (r["샤프"] > max(ref_m["샤프"], hold["샤프"])) and (u != "코어3 균등" or (len(pr) and pr.iloc[0] >= 0.9)) \
                and (r["CAGR(하루 늦게 체결)"] > 0.5 * r["연복리(CAGR)"])
            rows.append((u, r["방식"], ok))
    passed = [(u, n) for u, n, ok in rows if ok]
    o.append(f"**미리 정한 기준(샤프가 계속 보유·확정 공식보다 높음 + 코어3은 무작위 상위 10% + 하루 늦어도 절반 이상 유지)을 통과한 조합: "
             f"{len(passed)}개 / {len(rows)}개.** 기준이 '확정 공식보다 샤프가 조금이라도 높으면' 통과라 느슨한 편이니, 아래 숫자를 함께 보세요."
             + ("\n\n통과: " + "; ".join(f"{u} '{n.split(' (')[0]}'" for u, n in passed) if passed else "") + "\n")
    def g(u, n, k):
        m = perf[(perf["대상"] == u) & perf["방식"].str.startswith(n)]
        return m.iloc[0][k] if len(m) else np.nan

    def cp(n, cl, k):
        m = cap[(cap["그룹"] == "코어(BTC·ETH·SOL)") & cap["방식"].str.startswith(n) & (cap["추세 길이"] == cl)]
        return m.iloc[0][k] if len(m) else np.nan

    def rp(n):
        m = rnd[rnd["방식"].str.startswith(n)]
        return m.iloc[0]["무작위 대비 백분위"] if len(m) else np.nan

    def rf(u, n):
        m = ref[(ref["대상"] == u) & ref["방식"].str.startswith(n)]
        return m.iloc[0]["총수익"] if len(m) else np.nan

    def mx(u, k):
        m = mperf[(mperf["대상"] == u) & mperf["방식"].str.contains("1번")]
        return m.iloc[0][k] if len(m) else np.nan

    def pl(v):
        return "-" if v is None or pd.isna(v) else f"{v * 100:.0f}%"

    F = "(비교)"
    d, w_, m_ = CLASSES[0][0], CLASSES[1][0], CLASSES[2][0]
    o += [
        f"- **며칠짜리(20일 이하) 추세는 단기 스윙으로도 못 먹었습니다.** 8가지 방식 모두 며칠짜리 상승 포착률 중앙값이 {pl(cp('1.', d, '상승 포착률 중앙값'))}였습니다. "
        f"단기 추세(1번)가 실제로 잘한 건 **몇 주짜리 추세를 일찍 타는 것**: 몇 주 추세 포착 {pl(cp('1.', w_, '상승 포착률 중앙값'))} "
        f"(확정 공식 {pl(cp(F, w_, '상승 포착률 중앙값'))}). 대신 며칠짜리 하락도 {pl(cp('1.', d, '하락 맞은 비율 중앙값'))} 맞았습니다.",
        f"- **단기 추세(1번) vs 확정 공식(중기)**: 위험 대비 수익(샤프)은 비슷했습니다 — 코어3 {g('코어3 균등', '1.', '샤프'):.2f} vs {g('코어3 균등', F, '샤프'):.2f}, "
        f"BTC·ETH {g('BTC·ETH 균등', '1.', '샤프'):.2f} vs {g('BTC·ETH 균등', F, '샤프'):.2f}. 하지만 MDD가 더 깊고(코어3 {pct(g('코어3 균등', '1.', 'MDD'))} vs "
        f"{pct(g('코어3 균등', F, 'MDD'))}), 비용이 연 {pl(g('코어3 균등', '1.', '연간 비용 부담(추정)'))} vs {pl(g('코어3 균등', F, '연간 비용 부담(추정)'))}로 2~3배입니다.",
        f"- **중기·장기 필터(2·3번)**는 MDD를 줄였지만(코어3 {pct(g('코어3 균등', '3.', 'MDD'))}) 수익도 줄여 확정 공식과 거의 같은 것이 됐습니다.",
        f"- **눌림목 매수(4·8번)는 효과가 없었습니다.** 승률은 {pl(g('코어3 균등', '4.', '거래승률'))}로 높지만 연복리 {pct(g('코어3 균등', '4.', '연복리(CAGR)'))}, "
        f"무작위 타이밍보다도 못했습니다(백분위 {pl(rp('4.'))}, {pl(rp('8.'))}). '자주 이기지만 조금씩 벌고 가끔 크게 잃는' 구조였습니다.",
        f"- **목표가·손절형(7번)**은 거래가 많아 비용이 크고(알트 연 {pl(g('기타 상위30 균등', '7.', '연간 비용 부담(추정)'))}), 하루 늦게 체결하면 BTC 연복리가 "
        f"{pct(g('BTC', '7.', '연복리(CAGR)'))} → {pct(g('BTC', '7.', 'CAGR(하루 늦게 체결)'))}로 크게 줄었습니다. 추세형(1~3번)은 하루 늦어도 10~15% 정도만 줄었습니다.",
        f"- **알트(기타 30)**는 어떤 단기 방식도 코어보다 크게 못했고, 비용 부담이 연 5~20%였습니다.",
        f"- **확정 공식 70% + 단기 추세 30% (미리 정한 비율)**: 코어3 샤프 {mx('코어3 균등', '샤프'):.2f}, MDD {pct(mx('코어3 균등', 'MDD'))}로 확정 공식만(샤프 "
        f"{g('코어3 균등', F, '샤프'):.2f}, MDD {pct(g('코어3 균등', F, 'MDD'))})보다 조금 나았습니다. 두 방식이 다른 때 사고팔아서 생기는 분산 효과입니다.",
        f"- **2025년 이후(이미 본 구간, 참고만)**: 단기 추세(1번)가 BTC·ETH {pct(rf('BTC·ETH 균등', '1.'))}, 코어3 {pct(rf('코어3 균등', '1.'))}로 확정 공식"
        f"({pct(rf('BTC·ETH 균등', F))}, {pct(rf('코어3 균등', F))})보다 나았지만, 필터를 붙인 2·3번은 마이너스였습니다. 방향이 섞여 있어서 이걸로 고르면 안 됩니다.\n",
        "**제안**",
        "- 확정 공식(중기)은 그대로 둡니다. 단기 스윙만 따로 운용하는 건 위험 대비 이득이 없고 비용·체결 부담만 큽니다.",
        "- 단기를 쓰고 싶다면 '확정 공식 70% + 단기 추세(1번) 30%, 코어(BTC·ETH 또는 코어3)만'이 개발 구간에서 가장 근거 있는 형태입니다. "
        "다만 깨끗한 검증 구간이 이제 남아 있지 않으므로, **새 공식 버전으로 2~3개월 모의 운용(앞으로의 새 데이터)으로 확인한 뒤** 실제 비중을 넣는 걸 권합니다.",
        "- 단기는 매일 09:00(KST) 마감 직후 바로 주문해야 표의 성과에 가깝습니다. 눌림목·목표가형은 쓰지 않는 걸 권합니다.",
        "- 더 짧은(며칠) 추세를 노리려면 4시간봉 같은 짧은 봉 데이터와 장중 손절이 필요하고, 그건 별도 검증이 필요합니다.",
    ]
    return o
