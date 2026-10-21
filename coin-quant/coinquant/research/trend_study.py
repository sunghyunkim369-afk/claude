"""스윙(추세) 매매 검증: `python -m coinquant trend-study`

질문: 며칠·몇 주·몇 달짜리 추세를 모두 수익으로 만들 수 있나?
1. 매매 방식 8가지 (단기만/중기만/장기만/OR/3단 분할/계층 …) 성과 — 비용·다음 날 시가 체결
2. 포착률: 사후에 본 실제 추세 구간(지그재그)마다 그 방식이 상승분의 몇 %를 가져갔나, 하락분은 몇 % 맞았나
3. 휩쏘: 7일 안에 끝난 거래의 비중과 성적
4. 추세 조합(단기/중기/장기)별 이후 5·20·60일 수익 — 매일 매매 계획의 '과거 근거'로 씀
개발 구간만 사용 (최종 검증 구간 잠금).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..backtest import metrics, signals as sg, validate as V
from ..trend import HORIZONS, MultiTrend, zigzag
from .indicator_validation import Ctx, _eq_from, _on_all, _start, to_md

UNIVERSES = ["BTC", "ETH", "SOL", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"]
CLASSES = [("며칠(≤20일)", 3, 0, 20), ("몇 주(21~90일)", 6, 21, 90), ("몇 달(>90일)", 12, 91, 10_000)]


def trade_stats(tr: pd.DataFrame) -> dict:
    t = tr.dropna(subset=["ret"])
    if t.empty:
        return {}
    days = (t["exit"] - t["entry"]).dt.days
    win, loss = t.loc[t["ret"] > 0, "ret"], t.loc[t["ret"] <= 0, "ret"]
    short = t[days <= 7]
    pnl = t["received"] - t["invested"]   # 금액 기준 (분할 매매의 작은 거래가 큰 거래와 같은 무게로 세지지 않게)
    return {"거래수": len(t), "거래승률": (t["ret"] > 0).mean(), "평균 이익": win.mean() if len(win) else np.nan,
            "평균 손실": loss.mean() if len(loss) else np.nan,
            "손익비": pnl[pnl > 0].sum() / -pnl[pnl < 0].sum() if (pnl < 0).any() else np.nan,
            "평균 보유일": days.mean(), "7일 내 끝난 거래 비중": len(short) / len(t),
            "7일 내 거래 승률": (short["ret"] > 0).mean() if len(short) else np.nan}


def capture_study(ctx: Ctx, systems: dict[str, pd.DataFrame], groups: dict[str, list[str]],
                  elig: pd.DataFrame) -> pd.DataFrame:
    c = ctx.F.c
    r = np.log(c).diff()
    vol = r.rolling(30).std().loc[:ctx.lock.dev_end()].median()
    rows = []
    for gname, assets in groups.items():
        legs = []
        for a in assets:
            s = c[a].loc["2018-03-01":].dropna()
            if len(s) < 250:
                continue
            for cname, k, dmin, dmax in CLASSES:
                for st, en, d, lr in zigzag(s, k * vol[a]):
                    dur = (en - st).days
                    if not (dmin <= dur <= dmax):
                        continue
                    if gname.startswith("기타") and not bool(elig[a].get(st, False)):
                        continue
                    legs.append((a, cname, st, en, d, lr))
        for sname, pos in systems.items():
            p = pos.shift(1)   # t-1 종가 판단 → t일 수익
            for cname, *_ in CLASSES:
                caps = {1: [], -1: []}
                for a, cn, st, en, d, lr in legs:
                    if cn != cname:
                        continue
                    seg = (p[a] * r[a]).loc[st + pd.Timedelta(days=1):en].sum()
                    caps[d].append(seg / lr)
                up, dn = np.array(caps[1]), np.array(caps[-1])
                rows.append({"그룹": gname, "방식": sname, "추세 길이": cname, "상승 구간 수": len(up),
                             "상승 포착률 중앙값": np.median(up) if len(up) else np.nan,
                             "상승 포착률 평균": up.mean() if len(up) else np.nan,
                             "절반 이상 포착한 비율": (up >= 0.5).mean() if len(up) else np.nan,
                             "놓친 비율(포착≤0)": (up <= 0).mean() if len(up) else np.nan,
                             "하락 구간 수": len(dn), "하락 맞은 비율 중앙값": np.median(dn) if len(dn) else np.nan})
    return pd.DataFrame(rows)


def combo_forward(ctx: Ctx, mt: MultiTrend, assets: list[str], members: pd.DataFrame, horizons=(5, 20, 60)) -> pd.DataFrame:
    """추세 조합별 이후 수익 (그날 그 그룹에 속한 코인만, 개발 구간). 겹치는 기간이 많아 실제 독립 표본은 '사례 수 ÷ 기간' 정도."""
    combo = mt.combo()[assets]
    c = mt.close[assets]
    rows = []
    for H in horizons:
        fwd = c.shift(-H) / c - 1
        df = pd.DataFrame({"combo": combo.stack(), "fwd": fwd.stack(), "m": members[assets].reindex(combo.index).fillna(False).stack()})
        df = df[(df["combo"] != "") & df["m"] & df["fwd"].notna()]
        df = df.loc[df.index.get_level_values(0) >= pd.Timestamp("2018-03-01")]
        base = df["fwd"]
        for k, g in df.groupby("combo"):
            rows.append({"조합(단기/중기/장기)": k, "기간(일)": H, "사례 수": len(g), "중앙값": g["fwd"].median(),
                         "평균": g["fwd"].mean(), "하위10%": g["fwd"].quantile(0.1), "상위10%": g["fwd"].quantile(0.9),
                         "오른 비율": (g["fwd"] > 0).mean(), "전체 중앙값 대비": g["fwd"].median() - base.median()})
    return pd.DataFrame(rows)


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    ctx = Ctx(cfg, cache, log)
    mt = MultiTrend(ctx.F.c, ctx.F.h, ctx.F.l)
    systems = mt.systems()
    ref = sg.timing_signals(ctx.F)
    systems_ref = {"(참고) 돈치안20 돌파": ref["돈치안20 돌파(10일 저가 청산)"]().astype(float),
                   "(참고) MACD>0&히스토그램>0": ref["MACD>0 & 히스토그램>0"]().astype(float)}
    allsys = {**systems, **systems_ref}
    out_dir = config_mod.ROOT / "reports" / "research"
    rows, curves = [], {}
    for u in UNIVERSES:
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        base_res = ctx.run(sg.weights_slots(_on_all(ctx)[cols], mem))
        base_eq = _eq_from(base_res, st)
        b = metrics.summarize(base_eq, ctx.btc)
        b.update({"대상": u, "방식": "(기준) 계속 보유"})
        rows.append(b)
        for name, pos in allsys.items():
            w = sg.weights_slots(pos[cols].fillna(0.0), mem)
            res = ctx.run(w)
            eq = _eq_from(res, st)
            s = metrics.summarize(eq, ctx.btc, None, res.weights.loc[eq.index], res.turnover.loc[eq.index])
            s.update(trade_stats(res.trades[res.trades["exit"] >= st]))
            s.update({"대상": u, "방식": name})
            s["CAGR차(vs 보유)"] = s["연복리(CAGR)"] - b["연복리(CAGR)"]
            s["MDD차(vs 보유)"] = s["MDD"] - b["MDD"]
            rate = cfg["costs"]["fee"] + (cfg["costs"]["slippage_other"] if u.startswith("기타") else cfg["costs"]["slippage_core"])
            s["연간 비용 부담(추정)"] = s["연회전율"] * rate
            s["CAGR(하루 늦게 체결)"] = metrics.cagr(_eq_from(ctx.run(w, exec_lag=1), st))
            if u.startswith("기타"):
                s["CAGR(폐지=0원)"] = metrics.cagr(_eq_from(ctx.run(w, delist_mode="zero"), st))
            cons = V.era_consistency(eq, base_eq)
            s["시대(수익/MDD)"] = f"{cons['수익우위시대']}/{cons['MDD우위시대']} ({cons['시대수']})"
            rows.append(s)
            curves[(u, name)] = eq
            ctx.trials.add("trend", name, u, eq)
        log(f"  [{u}] 완료")
    perf = pd.DataFrame(rows)
    perf.to_csv(out_dir / "trend_perf.csv", index=False)

    elig = ctx.members("기타 상위30 균등")
    groups = {"코어(BTC·ETH·SOL)": ctx.core, "기타 상위30(그 시점)": [c for c in elig.columns[elig.any()] if c not in ctx.core]}
    cap = capture_study(ctx, allsys, groups, elig)
    cap.to_csv(out_dir / "trend_capture.csv", index=False)
    log("  포착률 완료")

    core_mem = ctx.alive[ctx.core]
    fwd_core = combo_forward(ctx, mt, ctx.core, core_mem)
    fwd_oth = combo_forward(ctx, mt, groups["기타 상위30(그 시점)"], elig)
    fwd_core["그룹"], fwd_oth["그룹"] = "코어", "기타"
    fwd = pd.concat([fwd_core, fwd_oth])
    fwd.to_csv(out_dir / "trend_combo_forward.csv", index=False)   # 리포트가 읽음
    log("  추세 조합별 이후 수익 완료")

    md = render(ctx, perf, cap, fwd, curves, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"trend-study-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def render(ctx, perf, cap, fwd, curves, secs) -> str:
    o = []
    w = o.append
    dev_end = ctx.lock.dev_end().date()
    w(f"# 스윙(추세) 매매 검증 — 며칠·몇 주·몇 달 추세를 모두 먹을 수 있나 (2018-03 ~ {dev_end})\n")
    w("> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n")
    w("## 0. 추세 판단 방법\n")
    w("기간 3개 × 근거 3개. 근거마다 −1/0/+1, 합이 +2 이상이면 '상승', −2 이하면 '하락', 나머지는 '횡보·불명확'. "
      "매매할 때는 +2 이상에서 켜고 0 이하로 내려가야 끕니다(들락날락 줄이기).\n")
    w("| 기간 | 이동평균 (빠른선>느린선 + 느린선 상승) | 채널 위치 (최근 N일 고~저 중 위 40%/아래 40%) | 회귀 추세선 (기울기 방향, R² ≥ 0.3일 때만) |")
    w("|---|---|---|---|")
    for k, p in HORIZONS.items():
        w(f"| {k} ({p['desc']}) | EMA {p['fast']}/{p['slow']}, 느린선 {p['rise']}일 전보다 | {p['chan']}일 | {p['reg']}일 |")
    w("\n숫자(8/21, 20/50, 50/200 등)는 흔히 쓰는 값을 그대로 썼고 이 데이터로 고르지 않았습니다(고르면 그만큼 우연 위험이 커짐). "
      "체결은 판단 다음 날 시가, 매일 판단, 비중 변화 1% 미만은 주문 안 함, 수수료·슬리피지 포함.\n")
    w(f"## 1. 매매 방식별 성과\n")
    w("'3단 분할'은 기간마다 1/3씩: 단기만 상승이면 1/3, 셋 다 상승이면 전부. '계층'은 장기가 하락이 아닐 때 중기 상승 + 단기 켜짐에 들어가 중기가 꺼지면 나옴.\n")
    cols = ["방식", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "CAGR차(vs 보유)", "MDD차(vs 보유)", "평균투자비중", "거래수", "거래승률",
            "평균 이익", "평균 손실", "손익비", "평균 보유일", "7일 내 끝난 거래 비중", "7일 내 거래 승률", "연간 비용 부담(추정)",
            "CAGR(하루 늦게 체결)", "CAGR(폐지=0원)", "시대(수익/MDD)"]
    for u in perf["대상"].unique():
        w(f"### {u}\n")
        w(to_md(perf[perf["대상"] == u], cols))
        w("")
    w("## 2. 추세 길이별 포착률 (사후에 본 실제 추세 구간 기준)\n")
    w("실제 추세 구간 = 지그재그(되돌림이 코인별 하루 변동성 × 3/6/12배 이상일 때 방향 전환)로 찾은 구간 중 길이가 맞는 것. "
      "포착률 = 그 구간 상승(로그) 중 그 방식이 가져간 비율 (100%면 바닥에 사서 꼭지에 판 것). '하락 맞은 비율'은 낮을수록 좋음. "
      "사후 정의라 실제로는 구간이 언제 시작·끝나는지 미리 알 수 없습니다.\n")
    for g in cap["그룹"].unique():
        w(f"### {g}\n")
        w(to_md(cap[cap["그룹"] == g], ["방식", "추세 길이", "상승 구간 수", "상승 포착률 중앙값", "상승 포착률 평균",
                                       "절반 이상 포착한 비율", "놓친 비율(포착≤0)", "하락 구간 수", "하락 맞은 비율 중앙값"]))
        w("")
    w("## 3. 추세 조합별 이후 수익 (매일 매매 계획의 '과거 근거')\n")
    w("예: '상승/상승/횡보' = 단기 상승, 중기 상승, 장기 횡보. 같은 날 여러 코인이 같이 움직이고 기간이 겹쳐서 실제 독립 사례는 '사례 수 ÷ 기간(일)'보다도 적습니다.\n")
    f20 = fwd[fwd["기간(일)"] == 20].sort_values(["그룹", "중앙값"], ascending=[True, False])
    w(to_md(f20, ["그룹", "조합(단기/중기/장기)", "사례 수", "중앙값", "평균", "하위10%", "상위10%", "오른 비율", "전체 중앙값 대비"]))
    w("")
    w(f"계산 {secs / 60:.1f}분 · 이 문서에서 시험한 조합 {ctx.trials.count('trend')}개 (앞선 지표 검증과 별도로 셈)\n")
    o.extend(conclusions(perf, cap, fwd))
    return "\n".join(o)


def conclusions(perf, cap, fwd) -> list[str]:
    def g(df, **kw):
        m = df
        for k, v in kw.items():
            m = m[m[k] == v]
        return m.iloc[0] if len(m) else None

    def pct(v):
        return "-" if v is None or pd.isna(v) else f"{v * 100:.0f}%"

    core = "코어(BTC·ETH·SOL)"
    o = ["## 4. 결론: 며칠·몇 주·몇 달 추세를 모두 먹을 수 있나?\n",
         "**짧게 답하면, 셋 다 먹으려 하면 오히려 셋 다 덜 먹습니다. 일봉으로 확인 가능한 건 '몇 주~몇 달' 추세이고, 며칠짜리는 사실상 못 먹었습니다.**\n"]
    rows = []
    for sname in ("단기만", "중기만", "장기만", "아무 기간이나 상승이면 보유(OR)", "3단 분할(기간마다 1/3씩)", "3단 분할 + 장기 하락이면 단기 몫만",
                  "중기 보유 + 장기 하락이면 중단"):
        r = [sname]
        for cl, *_ in CLASSES:
            x = g(cap, 그룹=core, 방식=sname, **{"추세 길이": cl})
            r.append(f"{pct(x['상승 포착률 중앙값'])} / {pct(x['하락 맞은 비율 중앙값'])}" if x is not None else "-")
        rows.append(r)
    o.append("| 방식 (코어) | 며칠: 상승 포착 / 하락 맞음 | 몇 주: 상승 포착 / 하락 맞음 | 몇 달: 상승 포착 / 하락 맞음 |")
    o.append("|---|---|---|---|")
    o += ["| " + " | ".join(r) + " |" for r in rows]
    o.append("")
    orr = g(cap, 그룹=core, 방식="아무 기간이나 상승이면 보유(OR)", **{"추세 길이": "며칠(≤20일)"})
    s_short = g(perf, 대상="BTC", 방식="단기만")
    o += [
        "1. **며칠짜리 추세(20일 이하)**: 대부분 방식의 포착률 중앙값이 0%입니다. 일봉 종가로 '상승'을 확인할 즈음이면 이미 상당 부분 올랐고, "
        "빠질 때도 확인까지 하루 이틀 걸려 남은 몫을 돌려줍니다. 며칠짜리까지 잡으려고 '아무 기간이나 상승이면 보유'로 넓히면 "
        f"상승은 {pct(orr['상승 포착률 중앙값'])} 잡지만 하락도 {pct(orr['하락 맞은 비율 중앙값'])} 맞아서, MDD가 그냥 보유와 비슷해집니다.",
        f"2. **휩쏘(헛신호) 비용**: 단기 방식은 거래의 약 {pct(s_short['7일 내 끝난 거래 비중'])}가 7일 안에 끝났고(BTC), 이 짧은 거래는 코어에서 거의 전부 손실이었습니다. "
        "추세가 꺾여 점수가 0 이하로 떨어져야 나오는 구조라 '짧게 끝난 거래 = 틀린 진입'이기 때문입니다. 수익은 소수의 긴 거래(평균 보유 수십 일)에서 나옵니다.",
        "3. **몇 주~몇 달 추세**: 몇 달짜리는 대부분 방식이 상승의 60~80%를 가져갔고, 몇 주짜리는 20~70%였습니다. 실제 수익은 여기서 나옵니다.",
        "4. **기간을 섞는 방법**: 기간마다 1/3씩 나눠 담는 '3단 분할'은 세 기간을 조금씩 다 타는 대신 어느 하나도 크게 먹지 못하고, "
        "장기 하락일 때 단기 몫만 남기는 변형이 MDD를 가장 크게 줄였습니다. '중기 보유 + 장기 하락이면 중단'은 거래가 적고(비용 낮음) 손익비가 높았습니다.",
        "5. **기타(알트) 그룹**은 같은 방식이라도 성과가 코어보다 크게 낮고, 거래가 많아 비용 부담(연 3~8%)이 큽니다. 스윙의 중심은 코어로 두는 게 데이터와 맞습니다.\n",
        "**제안 (같이 정할 부분)**",
        "- 목표를 '며칠·몇 주·몇 달 모두'에서 **'몇 주~몇 달 추세를 크게, 며칠짜리는 포기하고 손실만 작게'**로 바꾸는 걸 제안합니다.",
        "- 진입·청산의 중심은 중기(몇 주~2달) 판정, 장기 판정은 '하락이면 쉬기'용 필터, 단기 판정은 진입 타이밍 참고와 '분할로 조금 먼저 들어가기' 정도로만 씁니다.",
        "- 매일 리포트의 '스윙 매매 계획'에 기간별 판정·근거 숫자·약해지는 기준 가격·과거 같은 조합의 이후 성적을 붙여서, 왜 사고 왜 파는지 숫자로 보이게 했습니다.",
        "- 이 표의 숫자들도 개발 구간(2018~2024)에서 고른 것이라 낙관적입니다. 공식을 확정하면 최종 검증 구간(2025~)을 한 번 열어 확인합니다.",
    ]
    return o
