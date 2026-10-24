"""매물대·수평 저항/지지선 검증: `python -m coinquant levels`

사건은 '어제까지 그려진' 매물대·선과 오늘 가격으로 판정 (미래 데이터 없음). 개발 구간(2018-03 ~ 2024-12)만.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, levels as lv, strategy
from ..backtest import metrics, signals as sg, validate as V
from .indicator_validation import START, Ctx, _eq_from, _start, to_md
from .short_swing import swing_sim
from .trend_study import trade_stats


def compute_levels(ctx: Ctx, cols: list[str]) -> dict[str, pd.DataFrame]:
    F = ctx.F
    return lv.all_levels(F.c, F.h, F.l, F.v, F.atr(14), cols)


def event_masks(ctx: Ctx, L: dict, cols: list[str]) -> dict[str, pd.DataFrame]:
    c, h, l_ = ctx.F.c[cols], ctx.F.h[cols], ctx.F.l[cols]
    y = {k: v.reindex(columns=cols).shift(1) for k, v in L.items()}      # 어제까지 그려진 선
    mid = strategy.multitrend(ctx.F).h["중기"].state[cols]
    near = lambda lvl: (lvl / c - 1 < 0.03) & (lvl > c)  # noqa: E731
    vz_res, vz_sup = y["vz_res_lo"], y["vz_sup_hi"]
    ev = {
        "[매물대] 위 매물대를 뚫고 마감": c > y["vz_res_hi"],
        "[매물대] 위 매물대에 닿았다가 아래 마감 (밀림)": (h >= vz_res) & (c < vz_res),
        "[매물대] 매물대 안에서 마감 (아직 못 뚫음)": (L["vz_in_zone"].reindex(columns=cols) == 1),
        "[매물대] 아래 지지 매물대에 닿고 위에서 마감 (지지)": (l_ <= vz_sup) & (c > vz_sup),
        "[매물대] 아래 지지 매물대를 깨고 마감 (이탈)": c < y["vz_sup_lo"],
        "[매물대] 바로 위 3% 안에 매물대": near(L["vz_res_lo"].reindex(columns=cols)),
        "[매물대] 위 10% 안에 매물대 없음 (위가 비어 있음)": (L["vz_res_lo"].reindex(columns=cols) / c - 1 > 0.10) | L["vz_res_lo"].reindex(columns=cols).isna(),
        "[강한 선 3회+] 저항선 돌파 (선 +1% 위 마감)": c > y["pl_res"] * 1.01,
        "[강한 선 3회+] 저항선에 닿았다가 아래 마감 (밀림)": (h >= y["pl_res"] * 0.995) & (c < y["pl_res"]),
        "[강한 선 3회+] 지지선에 닿고 위에서 마감 (지지)": (l_ <= y["pl_sup"] * 1.005) & (c > y["pl_sup"]),
        "[강한 선 3회+] 지지선 이탈 (선 −1% 아래 마감)": c < y["pl_sup"] * 0.99,
        "[강한 선 3회+] 바로 위 3% 안에 저항선": near(L["pl_res"].reindex(columns=cols)),
        "[약한 선 2회] 저항선 돌파": c > y["pl_res2"] * 1.01,
        "[약한 선 2회] 저항선에서 밀림": (h >= y["pl_res2"] * 0.995) & (c < y["pl_res2"]),
    }
    ev["[강한 선 3회+] 저항선 돌파 + 중기 상승 중"] = ev["[강한 선 3회+] 저항선 돌파 (선 +1% 위 마감)"] & mid
    ev["[매물대] 위 매물대 돌파 + 중기 상승 중"] = ev["[매물대] 위 매물대를 뚫고 마감"] & mid
    ev["[매물대] 지지 매물대에서 지지 + 중기 상승 중"] = ev["[매물대] 아래 지지 매물대에 닿고 위에서 마감 (지지)"] & mid
    return {k: v.fillna(False).astype(bool) for k, v in ev.items()}


def events(ctx, masks, cols, members, gname, horizons=(5, 20, 60)) -> list[dict]:
    c = ctx.F.c[cols]
    m = members.reindex(index=c.index, columns=cols).fillna(False).loc[START:]
    rows = []
    for H in horizons:
        fwd = (c.shift(-H) / c - 1).loc[START:]
        base = fwd.where(m).stack().dropna()
        rows.append({"그룹": gname, "사건": "(기준) 전체 날", "기간(일)": H, "사례 수": len(base), "서로 다른 날짜": int(m.any(axis=1).sum()),
                     "중앙값": base.median(), "평균": base.mean(), "하위10%": base.quantile(0.1), "오른 비율": (base > 0).mean(),
                     "전체 중앙값 대비": 0.0})
        for k, e in masks.items():
            ee = e.loc[START:] & m
            x = fwd.where(ee).stack().dropna()
            rows.append({"그룹": gname, "사건": k, "기간(일)": H, "사례 수": len(x), "서로 다른 날짜": int(ee.any(axis=1).sum()),
                         "중앙값": x.median(), "평균": x.mean(), "하위10%": x.quantile(0.1), "오른 비율": (x > 0).mean(),
                         "전체 중앙값 대비": x.median() - base.median()})
    return rows


def variants(ctx: Ctx, L: dict, cols: list[str]) -> dict[str, pd.DataFrame]:
    c = ctx.F.c[cols]
    mt = strategy.multitrend(ctx.F)
    M, Lg = mt.h["중기"], mt.h["장기"]
    base = (M.state & ~(Lg.score <= -2))[cols]
    g = lambda k: L[k].reindex(columns=cols)  # noqa: E731
    near_res = ((g("pl_res") / c - 1 < 0.03) & (g("pl_res") > c)) | ((g("vz_res_lo") / c - 1 < 0.03) & (g("vz_res_lo") > c))
    y = {k: v.reindex(columns=cols).shift(1) for k, v in L.items()}
    broke = (c > y["pl_res"] * 1.01) | (c > y["vz_res_hi"])
    recent_break = broke.astype(float).rolling(10, min_periods=1).max().astype(bool)
    sup_break = (c < y["pl_sup"] * 0.99) | (c < y["vz_sup_lo"])
    return {
        "확정 공식 (중기 보유 + 장기 하락이면 중단)": base.astype(float),
        "1. + 바로 위 3% 안에 강한 저항선·매물대 있으면 진입 금지": swing_sim(base & ~near_res, ~base, c),
        "2. + 최근 10일 안에 강한 저항선·매물대를 돌파했어야 진입": swing_sim(base & recent_break, ~base, c),
        "3. + 강한 지지선·지지 매물대 이탈하면 바로 매도": swing_sim(base & ~sup_break, ~base | sup_break, c),
        "4. 1번 + 3번": swing_sim(base & ~near_res & ~sup_break, ~base | sup_break, c),
    }


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    ctx = Ctx(cfg, cache, log)
    oth = ctx.members("기타 상위30 균등")
    oth_cols = [c for c in oth.columns[oth.any()] if c not in ctx.core]
    cols = ctx.core + oth_cols
    L = compute_levels(ctx, cols)
    log(f"  매물대·선 계산 완료 ({len(cols)}개 코인, {time.time() - t0:.0f}초)")
    rows = []
    for gname, gcols, mem in (("코어(BTC·ETH·SOL)", ctx.core, ctx.alive[ctx.core]), ("기타 상위30(그 시점)", oth_cols, oth[oth_cols])):
        masks = event_masks(ctx, L, gcols)
        rows += events(ctx, masks, gcols, mem, gname)
    ev = pd.DataFrame(rows)
    log("  사건 분석 완료")
    var = variants(ctx, L, cols)
    prow = []
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        mem = ctx.members(u)
        ucols = [c for c in mem.columns if c in cols]
        mem = mem[ucols]
        st = _start(ctx, mem)
        base_eq = None
        for name, pos in var.items():
            w = sg.weights_slots(pos[ucols].fillna(0.0), mem)
            res = ctx.run(w)
            eq = _eq_from(res, st)
            s = metrics.summarize(eq, ctx.btc, None, res.weights.loc[eq.index], res.turnover.loc[eq.index])
            s.update(trade_stats(res.trades[res.trades["exit"] >= st]))
            s.update({"대상": u, "방식": name})
            s["CAGR(하루 늦게 체결)"] = metrics.cagr(_eq_from(ctx.run(w, exec_lag=1), st))
            if base_eq is None:
                base_eq = eq
            else:
                cons = V.era_consistency(eq, base_eq)
                s["시대(수익/MDD) vs 확정 공식"] = f"{cons['수익우위시대']}/{cons['MDD우위시대']} ({cons['시대수']})"
            prow.append(s)
            ctx.trials.add("levels", name, u, eq)
    perf = pd.DataFrame(prow)
    log("  공식 비교 완료")
    out = config_mod.ROOT / "reports" / "research"
    ev.to_csv(out / "levels_events.csv", index=False)
    perf.to_csv(out / "levels_variants.csv", index=False)
    md = render(ctx, ev, perf, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"levels-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def render(ctx, ev, perf, secs) -> str:
    o = [f"# 매물대·수평 저항/지지선 검증 (개발 구간 2018-03 ~ {ctx.lock.dev_end().date()})\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 정의 (모두 '어제까지' 데이터로 그린 것)\n",
         "- **매물대**: 최근 180일 하루 대표가격((고+저+종)/3)에 거래대금을 쌓은 가격대별 분포(로그 가격 40칸). 평균의 1.5배 이상 몰린 칸을 묶어 매물대로 봄. "
         "현재가 위 가장 가까운 매물대 = 저항, 아래 = 지지.",
         "- **수평선**: 최근 1년 고점·저점(앞뒤 5일보다 높/낮은 봉, 5일 뒤 확정)을 가격이 1%(또는 변동성 ATR의 0.5배) 안이면 같은 선으로 묶음. "
         "부딪힌 횟수 3번 이상 = 강한 선, 2번 = 약한 선. 고점이 저점이 되는 역할 전환도 같은 선으로 셈.",
         "- 이후 수익은 그날 종가 기준(다음 날 시가 체결과 거의 같음), 비용 미포함. 같은 날 여러 코인이 함께 움직여 실제 독립 표본은 '서로 다른 날짜'보다 적음.",
         f"- 계산 {secs / 60:.1f}분, 공식 비교 시험 {ctx.trials.count('levels')}개.\n",
         "## 1. 사건 분석: 이후 20일 수익\n"]
    cols = ["사건", "사례 수", "서로 다른 날짜", "중앙값", "평균", "하위10%", "오른 비율", "전체 중앙값 대비"]
    for g in ev["그룹"].unique():
        o += [f"### {g}\n", to_md(ev[(ev["그룹"] == g) & (ev["기간(일)"] == 20)], cols), ""]
    o += ["## 2. 사건 분석: 이후 60일 (코어)\n",
          to_md(ev[(ev["그룹"].str.startswith("코어")) & (ev["기간(일)"] == 60)], cols), ""]
    o += ["## 3. 확정 공식에 매물대·선 조건을 붙이면 (개발 구간, 매일 판단, 비용 포함)\n"]
    pc = ["방식", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "평균투자비중", "거래수", "거래승률", "손익비", "평균 보유일",
          "CAGR(하루 늦게 체결)", "시대(수익/MDD) vs 확정 공식"]
    for u in perf["대상"].unique():
        o += [f"### {u}\n", to_md(perf[perf["대상"] == u], pc), ""]
    o += conclusions(ev, perf)
    return "\n".join(o)


def conclusions(ev, perf) -> list[str]:
    def e(g, k, H=20, col="중앙값"):
        m = ev[ev["그룹"].str.startswith(g) & ev["사건"].str.startswith(k) & (ev["기간(일)"] == H)]
        return m.iloc[0][col] if len(m) else np.nan

    def pct(v, sign=True):
        return "-" if pd.isna(v) else (f"{v * 100:+.1f}%" if sign else f"{v * 100:.0f}%")

    def row(g, k):
        return f"{pct(e(g, k))} ({pct(e(g, k, col='오른 비율'), False)}, {int(e(g, k, col='서로 다른 날짜')) if not pd.isna(e(g, k, col='서로 다른 날짜')) else 0}일)"

    items = ["(기준) 전체 날", "[매물대] 위 매물대를 뚫고", "[매물대] 위 매물대에 닿았다가", "[매물대] 매물대 안에서", "[매물대] 아래 지지 매물대에 닿고",
             "[매물대] 아래 지지 매물대를 깨고", "[매물대] 바로 위 3%", "[매물대] 위 10% 안에", "[강한 선 3회+] 저항선 돌파 (", "[강한 선 3회+] 저항선에 닿았다가",
             "[강한 선 3회+] 지지선에 닿고", "[강한 선 3회+] 지지선 이탈", "[강한 선 3회+] 바로 위 3%", "[약한 선 2회] 저항선 돌파", "[약한 선 2회] 저항선에서"]
    o = ["## 4. 결론\n", "이후 20일 수익 중앙값 (오른 비율, 서로 다른 날짜 수):\n", "| 사건 | 코어 | 기타 |", "|---|---|---|"]
    for k in items:
        o.append(f"| {k.rstrip(' (')} | {row('코어', k)} | {row('기타', k)} |")
    o.append("")
    base = "확정 공식"
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        d = perf[perf["대상"] == u]
        b = d[d["방식"].str.startswith(base)].iloc[0]
        better = d[(d["샤프"] > b["샤프"] + 0.05) & ~d["방식"].str.startswith(base)]
        o.append(f"- {u}: 확정 공식 샤프 {b['샤프']:.2f} / MDD {pct(b['MDD'])} → 샤프가 0.05 넘게 좋아진 변형: "
                 + (", ".join(f"{r['방식'].split('.')[0]}번({r['샤프']:.2f}, MDD {pct(r['MDD'])}, 연복리 {pct(r['연복리(CAGR)'])})" for _, r in better.iterrows()) or "없음"))
    o.append("")
    o += [
        "**해석**",
        f"- **매물대 돌파는 의미가 있어 보였습니다(코어).** 위 매물대를 뚫고 마감한 날 이후 20일 중앙값 {pct(e('코어', '[매물대] 위 매물대를 뚫고'))}, "
        f"아무 날 {pct(e('코어', '(기준)'))}. 매물대 '안'에 갇혀 있는 날은 {pct(e('코어', '[매물대] 매물대 안에서'))}로 가장 약했습니다.",
        f"- **수평 저항·지지선은 직관과 반대이거나 엇갈렸습니다.** 3번 이상 부딪힌 강한 저항선을 돌파한 날은 {pct(e('코어', '[강한 선 3회+] 저항선 돌파 ('))}로 "
        f"아무 날과 비슷했고, 오히려 그 선에서 밀린 날이 {pct(e('코어', '[강한 선 3회+] 저항선에 닿았다가'))}였습니다. "
        f"지지 매물대를 깬 날도 이후 {pct(e('코어', '[매물대] 아래 지지 매물대를 깨고'))}로 올랐습니다. "
        "이미 확정 공식이 쓰는 '추세가 살아 있으면 저항 근처까지 올라온다'는 효과가 섞여 있고, 표본(서로 다른 날짜 100~400일)이 작아 방향이 흔들립니다.",
        "- **알트(기타)**는 모든 경우가 아무 날과 비슷하게 마이너스였습니다. 선·매물대로 알트를 고르는 근거는 없었습니다.",
        "- **확정 공식에 붙이면 나아지지 않았습니다.** '바로 위 저항이 있으면 진입 금지'는 거의 차이 없음, '돌파 후에만 진입'은 진입이 늦어져 수익 감소, "
        "'지지 이탈 시 바로 매도'는 거래가 2~3배로 늘며 휩쏘가 많아져 가장 나빴습니다.\n",
        "**한계**: 선은 '고점·저점 앞뒤 5일, 1% 안이면 같은 선, 3번 이상 = 강함', 매물대는 '180일·40칸·평균 1.5배'로 정의했습니다. "
        "사람이 차트에 긋는 선과 다를 수 있고, 다른 정의(예: 주봉 고점, 더 긴 매물대 기간)로 바꾸면 결과가 달라질 수 있습니다. "
        "여러 정의를 계속 바꿔 가며 시험하면 그중 하나가 우연히 좋아 보이게 되므로, 바꿀 정의를 먼저 정하고 한 번만 시험하는 걸 권합니다.\n",
        "**제안**: 공식은 그대로 둡니다. 대신 매일 리포트의 매매 계획에 '위·아래 가장 가까운 매물대와 강한 선 가격'을 참고 정보로 보여줍니다 "
        "(지정가·손절 위치를 사람이 판단할 때 쓰도록. 예측력은 약하다는 표시와 함께).",
    ]
    return o
