"""돌파 검증: `python -m coinquant breakout`

질문: '저항(직전 고가)을 못 뚫으면 싸도 안 사고, 뚫으면 비싸도 산다'가 데이터로 맞나? 공식에 넣으면 나아지나?
1. 사건 분석: 돌파한 날 / 돌파 실패한 날(저항 근처까지 갔다가 아래 마감) / 저항 아래 5~15%(싸게 살 수 있는 날) 이후 5·20·60일 수익
2. 공식 비교: 확정 공식(중기)에 '돌파 후에만 진입', '돌파 실패 직후 진입 금지', '52주 고가 근처에서만 진입'을 붙였을 때
개발 구간(2018-03 ~ 2024-12)만 사용. 저항 = 직전 N일 고가(오늘 제외). 판단은 종가, 체결은 다음 날 시가.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, indicators as ind, strategy
from ..backtest import metrics, signals as sg, validate as V
from .indicator_validation import START, Ctx, _eq_from, _start, to_md
from .short_swing import swing_sim
from .trend_study import trade_stats

LOOKBACKS = [(20, "20일 고가"), (55, "55일 고가"), (100, "100일 고가"), (365, "52주 고가")]


def events(ctx: Ctx, groups: dict[str, pd.DataFrame], horizons=(5, 20, 60)) -> pd.DataFrame:
    c, h, l_, v = ctx.F.c, ctx.F.h, ctx.F.l, ctx.F.v
    mt = strategy.multitrend(ctx.F)
    mid_on = mt.h["중기"].state
    volr = ind.volume_ratio(v, 20)
    fwd = {H: c.shift(-H) / c - 1 for H in horizons}
    rows = []
    for gname, mem in groups.items():
        cols = list(mem.columns)
        m = mem[cols].reindex(c.index).fillna(False).loc[START:]
        base = {H: fwd[H][cols].loc[START:].where(m).stack().dropna() for H in horizons}   # pandas 3: stack 이 빈 값을 안 버림
        for N, label in LOOKBACKS:
            R = h[cols].shift(1).rolling(N, min_periods=N).max()
            cc = c[cols]
            above = cc > R
            brk = above & ~above.shift(1, fill_value=False)
            rej = (h[cols] >= R * 0.99) & (cc < R) & ~brk
            cheap = (cc >= R * 0.85) & (cc < R * 0.95)
            # 가짜 돌파: 돌파 후 5일 안에 종가가 다시 그 저항 아래
            back = pd.concat([(cc.shift(-k) < R) for k in range(1, 6)]).groupby(level=0).max().reindex(cc.index)
            kinds = {
                "돌파한 날 (첫 종가 마감)": brk,
                "돌파 + 거래량 1.5배": brk & (volr[cols] > 1.5),
                "돌파 + 중기 상승 중": brk & mid_on[cols],
                "돌파 실패 (고가가 저항 −1% 이내까지 갔다가 아래 마감)": rej,
                "저항 아래 5~15% (싸게 살 수 있는 날)": cheap,
                "저항 아래 5~15% + 중기 상승 중": cheap & mid_on[cols],
            }
            for kname, ev in kinds.items():
                e = ev.loc[START:].where(m).fillna(False).astype(bool)
                for H in horizons:
                    x = fwd[H][cols].loc[START:].where(e).stack().dropna()
                    b = base[H]
                    r = {"그룹": gname, "저항": label, "사건": kname, "기간(일)": H, "사례 수": len(x),
                         "서로 다른 날짜": int(e.any(axis=1).sum()),
                         "중앙값": x.median(), "평균": x.mean(), "하위10%": x.quantile(0.1), "오른 비율": (x > 0).mean(),
                         "전체 중앙값 대비": x.median() - b.median(), "전체 오른 비율 대비": (x > 0).mean() - (b > 0).mean()}
                    if kname.startswith("돌파한 날") and H == 5:
                        r["가짜 돌파 비율(5일 안에 다시 아래)"] = back.loc[START:].astype(float).where(e).stack().dropna().mean()
                    rows.append(r)
    return pd.DataFrame(rows)


def variants(ctx: Ctx) -> dict[str, pd.DataFrame]:
    c, h = ctx.F.c, ctx.F.h
    mt = strategy.multitrend(ctx.F)
    M, L = mt.h["중기"], mt.h["장기"]
    base = M.state & ~(L.score <= -2)

    def recent(x: pd.DataFrame, k: int) -> pd.DataFrame:
        return x.astype(float).rolling(k, min_periods=1).max().astype(bool)

    def brk(N):
        R = h.shift(1).rolling(N, min_periods=N).max()
        return c > R

    def rej(N):
        R = h.shift(1).rolling(N, min_periods=N).max()
        return (h >= R * 0.99) & (c < R)

    hi52 = h.shift(1).rolling(365, min_periods=200).max()
    return {
        "확정 공식 (중기 보유 + 장기 하락이면 중단)": base.astype(float),
        "A. 확정 공식 + 최근 10일 안에 55일 고가 돌파가 있어야 진입": swing_sim(base & recent(brk(55), 10), ~base, c),
        "B. 확정 공식 + 최근 10일 안에 20일 고가 돌파가 있어야 진입": swing_sim(base & recent(brk(20), 10), ~base, c),
        "C. 확정 공식 + 최근 10일 안에 55일 고가 돌파 실패가 있으면 진입 금지": swing_sim(base & ~recent(rej(55) & ~brk(55), 10), ~base, c),
        "D. 확정 공식 + 52주 고가의 90% 이상일 때만 진입": swing_sim(base & (c >= hi52 * 0.9), ~base, c),
        "E. 돌파 단독: 55일 고가 돌파 + 장기 하락 아님 → 중기 꺼지면 매도": swing_sim(brk(55) & ~(L.score <= -2), ~M.state | (L.score <= -2), c),
    }


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    ctx = Ctx(cfg, cache, log)
    groups = {"코어(BTC·ETH·SOL)": ctx.alive[ctx.core], "기타 상위30(그 시점)": ctx.members("기타 상위30 균등").drop(columns=ctx.core, errors="ignore")}
    ev = events(ctx, groups)
    log("  사건 분석 완료")
    var = variants(ctx)
    rows = []
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        base_eq = None
        for name, pos in var.items():
            w = sg.weights_slots(pos[cols].fillna(0.0), mem)
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
            rows.append(s)
            ctx.trials.add("breakout", name, u, eq)
    perf = pd.DataFrame(rows)
    log("  공식 비교 완료")
    out = config_mod.ROOT / "reports" / "research"
    ev.to_csv(out / "breakout_events.csv", index=False)
    perf.to_csv(out / "breakout_variants.csv", index=False)
    md = render(ctx, ev, perf, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"breakout-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def render(ctx, ev, perf, secs) -> str:
    o = [f"# 돌파 검증 — '못 뚫으면 싸도 안 사고, 뚫으면 비싸도 산다' (개발 구간 2018-03 ~ {ctx.lock.dev_end().date()})\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 정의\n",
         "- 저항 = 직전 N일 최고가(오늘 제외). N = 20일·55일·100일·365일(52주).",
         "- 돌파한 날 = 종가가 저항 위로 처음 마감한 날. 돌파 실패 = 그날 고가가 저항 −1% 이내까지 갔는데 종가는 저항 아래.",
         "- 싸게 살 수 있는 날 = 종가가 저항 아래 5~15%. 이후 수익은 그날 종가 기준(다음 날 시가 체결과 거의 같음), 비용 미포함.",
         "- 같은 날 여러 코인이 같이 움직이고 기간이 겹쳐서 실제 독립 표본은 '서로 다른 날짜' 수보다도 적습니다.",
         f"- 계산 {secs / 60:.1f}분, 공식 비교 시험 {ctx.trials.count('breakout')}개.\n",
         "## 1. 사건 분석: 이후 20일 수익\n"]
    cols = ["저항", "사건", "사례 수", "서로 다른 날짜", "중앙값", "평균", "하위10%", "오른 비율", "전체 중앙값 대비", "전체 오른 비율 대비"]
    for g in ev["그룹"].unique():
        o += [f"### {g}\n", to_md(ev[(ev["그룹"] == g) & (ev["기간(일)"] == 20)], cols), ""]
    o += ["## 2. 사건 분석: 이후 5일·60일 (코어)\n"]
    e2 = ev[(ev["그룹"] == ev["그룹"].unique()[0]) & ev["기간(일)"].isin([5, 60]) & ev["저항"].isin(["55일 고가", "52주 고가"])]
    o += [to_md(e2, ["저항", "사건", "기간(일)", "사례 수", "중앙값", "하위10%", "오른 비율", "전체 중앙값 대비"]), ""]
    fb = ev[ev["사건"].str.startswith("돌파한 날") & (ev["기간(일)"] == 5)]
    o += ["### 가짜 돌파 비율 (돌파 후 5일 안에 종가가 다시 저항 아래)\n",
          to_md(fb, ["그룹", "저항", "사례 수", "가짜 돌파 비율(5일 안에 다시 아래)"]), ""]
    o += ["## 3. 확정 공식에 돌파 조건을 붙이면 (개발 구간, 매일 판단, 비용 포함)\n"]
    pc = ["방식", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "평균투자비중", "거래수", "거래승률", "손익비", "평균 보유일",
          "CAGR(하루 늦게 체결)", "시대(수익/MDD) vs 확정 공식"]
    for u in perf["대상"].unique():
        o += [f"### {u}\n", to_md(perf[perf["대상"] == u], pc), ""]
    o += conclusions(ev, perf)
    return "\n".join(o)


def conclusions(ev, perf) -> list[str]:
    def e(g, R, k, H=20, col="중앙값"):
        m = ev[(ev["그룹"].str.startswith(g)) & (ev["저항"] == R) & (ev["사건"].str.startswith(k)) & (ev["기간(일)"] == H)]
        return m.iloc[0][col] if len(m) else np.nan

    def p(u, n, col):
        m = perf[(perf["대상"] == u) & perf["방식"].str.startswith(n)]
        return m.iloc[0][col] if len(m) else np.nan

    def pct(v, sign=True):
        return "-" if pd.isna(v) else (f"{v * 100:+.1f}%" if sign else f"{v * 100:.0f}%")

    o = ["## 4. 결론\n"]
    rows = []
    for g in ("코어", "기타"):
        for R in ("20일 고가", "55일 고가", "52주 고가"):
            rows.append(f"| {g} | {R} | {pct(e(g, R, '돌파한 날'))} ({pct(e(g, R, '돌파한 날', col='오른 비율'), False)}) | "
                        f"{pct(e(g, R, '돌파 실패'))} ({pct(e(g, R, '돌파 실패', col='오른 비율'), False)}) | "
                        f"{pct(e(g, R, '저항 아래 5~15% (싸게'))} ({pct(e(g, R, '저항 아래 5~15% (싸게', col='오른 비율'), False)}) |")
    o += ["이후 20일 수익 중앙값 (오른 비율):\n", "| 그룹 | 저항 | 돌파한 날 | 돌파 실패한 날 | 저항 아래 5~15% |", "|---|---|---|---|---|"] + rows + [""]
    base = "확정 공식"
    lines = []
    for u in ("BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"):
        b = (p(u, base, "샤프"), p(u, base, "MDD"), p(u, base, "연복리(CAGR)"))
        better = [n for n in ("A.", "B.", "C.", "D.", "E.") if p(u, n, "샤프") > b[0] + 0.05]
        lines.append(f"- {u}: 확정 공식 샤프 {b[0]:.2f} / MDD {pct(b[1])} / 연복리 {pct(b[2])} → 샤프가 0.05 넘게 좋아진 변형: "
                     + (", ".join(f"{n[0]}({p(u, n, '샤프'):.2f}, MDD {pct(p(u, n, 'MDD'))}, 연복리 {pct(p(u, n, '연복리(CAGR)'))})" for n in better) or "없음"))
    o += ["**공식에 붙였을 때 (3절 요약)**\n"] + lines + [""]
    o += [
        f"- **'저항 근처·위에서 사는 게 싸게 사는 것보다 낫다'는 코어에서 맞았습니다.** 55일 고가 돌파한 날 이후 20일 중앙값 {pct(e('코어', '55일 고가', '돌파한 날'))}, "
        f"저항 아래 5~15%(싸게 살 수 있는 날) {pct(e('코어', '55일 고가', '저항 아래 5~15% (싸게'))}; 52주 고가 기준 {pct(e('코어', '52주 고가', '돌파한 날'))} vs "
        f"{pct(e('코어', '52주 고가', '저항 아래 5~15% (싸게'))}. 앞서 지정가 검증(강한 추세는 되돌림 없이 감)·눌림목 검증(효과 없음)과 같은 방향입니다.",
        f"- **'못 뚫었으면 안 사는 게 이득'은 데이터와 맞지 않았습니다.** 돌파 실패한 날(고가가 저항 근처까지 갔다가 아래 마감) 이후도 "
        f"{pct(e('코어', '55일 고가', '돌파 실패'))}(오른 비율 {pct(e('코어', '55일 고가', '돌파 실패', col='오른 비율'), False)})로 돌파한 날과 거의 같았습니다. "
        f"저항을 '시험하러 올라왔다'는 것 자체가 힘이 있다는 뜻이라, 뚫었는지 여부보다 '저항 근처까지 올라왔는지'가 더 중요했습니다.",
        f"- **가짜 돌파가 많습니다.** 코어 돌파의 약 {pct(ev[(ev['그룹'].str.startswith('코어')) & (ev['저항'] == '55일 고가') & ev['사건'].str.startswith('돌파한 날') & (ev['기간(일)'] == 5)]['가짜 돌파 비율(5일 안에 다시 아래)'].iloc[0], False)}가 "
        "5일 안에 종가가 다시 저항 아래로 내려왔습니다. 돌파 하나만 보고 '오를 확률이 많이 올라간다'고 보긴 어렵습니다.",
        f"- **거래량이 붙은 돌파**는 더 좋았습니다(55일 고가 + 거래량 1.5배: {pct(e('코어', '55일 고가', '돌파 + 거래량'))}, 오른 비율 "
        f"{pct(e('코어', '55일 고가', '돌파 + 거래량', col='오른 비율'), False)}). 다만 서로 다른 날짜 {int(e('코어', '55일 고가', '돌파 + 거래량', col='서로 다른 날짜'))}일로 표본이 작고, "
        "여러 조건 중 하나라 우연일 수 있습니다.",
        "- **알트(기타)**는 돌파·실패·싼 날 모두 이후 수익이 0% 근처로 차이가 없었습니다.",
        "- **확정 공식에 돌파 조건을 붙여도 코어에서는 나아지지 않았습니다.** 확정 공식의 근거 하나가 이미 '55일 범위 안에서 위쪽 40%에 있을 것'(채널 위치)이라 "
        "돌파 기준과 상당 부분 겹치기 때문으로 보입니다. 알트에서만 '52주 고가의 90% 이상일 때만 진입'(D)이 MDD를 크게 줄였는데, 한 대상에서만이라 우연일 수 있습니다.\n",
        "**제안**",
        "- 공식은 그대로 둡니다. '저항 근처에서 산다'는 생각은 이미 확정 공식(채널 위치 근거)에 들어가 있고, '돌파 실패면 안 산다'는 데이터가 지지하지 않았습니다.",
        "- 리포트의 매매 계획에 '55일·52주 고가까지 거리, 최근 돌파/돌파 실패 여부, 거래량 배수'를 참고 정보로 보여주는 건 도움이 됩니다(원하시면 추가).",
        "- 알트에 'D(52주 고가 90% 이상일 때만)'를 넣는 건 후보로 남겨 두고, 넣기로 하면 새 공식 버전으로 모의 운용부터 확인하길 권합니다.",
    ]
    return o
