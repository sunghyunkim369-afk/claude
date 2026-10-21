"""지정가 매수 검증: `python -m coinquant limit-entry`

체결 방식: 신호가 난 날 종가 × (1 − 할인율)에 지정가 매수 → 다음 날 저가가 거기까지 오면 체결(시가가 이미 더 낮으면 시가에 체결),
안 오면 그 신호는 포기. 매도는 지금처럼 다음 날 시가(시장가).
- '한 번만 시도': 안 닿으면 신호가 꺼졌다 다시 켜질 때까지 기다림 (사용자 방식)
- '켜져 있는 동안 매일 다시': 신호가 켜져 있으면 다음 날 같은 방식으로 다시 지정가
- 체결 판정: '닿으면 체결' / '0.1% 뚫어야 체결'(대기 순번 때문에 닿기만 하면 안 될 수 있어서 보수적으로)
업비트 수수료는 지정가·시장가 모두 0.05%, 지정가는 슬리피지 없음, 시장가는 슬리피지(코어 0.1%, 기타 0.3%).
포트폴리오는 대상 코인마다 같은 몫(매일 몫을 맞추는 비용은 근사로 뺌 — 시장가 결과를 기존 엔진과 비교해 확인).
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, strategy
from ..backtest import metrics
from .indicator_validation import START, Ctx, to_md


def sleeve(pos, o, h, l, c, *, method="market", disc=0.0, atr_k=None, atr=None, retry=False, eps=0.0,
           fee=0.0005, slip=0.001):
    """코인 하나의 하루 수익률(그 코인 몫 기준)과 진입 시도 기록.

    pos: 그날 종가 판단 '보유하고 싶음'(0/1). 반환: (일 수익률 배열, 시도 기록 리스트)
    """
    n = len(c)
    ret = np.zeros(n)
    held = False
    armed = True          # '한 번만 시도' 모드: 신호가 꺼졌다 켜져야 다시 시도
    attempts = []
    for t in range(n - 1):
        want = pos[t] > 0 and not np.isnan(c[t])
        if not want:
            armed = True
        if held:
            if np.isnan(c[t + 1]):                     # 상장폐지 등: 마지막 가격으로 정리
                held = False
                continue
            if not want:                               # 시장가 매도 (다음 날 시가)
                ret[t + 1] = o[t + 1] * (1 - slip) * (1 - fee) / c[t] - 1
                held = False
            else:
                ret[t + 1] = c[t + 1] / c[t] - 1
            continue
        if want and (armed or retry) and not np.isnan(o[t + 1]):
            if method == "market":
                fill = o[t + 1] * (1 + slip)
                ok = True
            else:
                d = disc if atr_k is None else atr_k * atr[t] / c[t]
                L = c[t] * (1 - d)
                ok = l[t + 1] <= L * (1 - eps)
                fill = min(o[t + 1], L)
            # 같은 신호를 시장가로 샀다면의 거래 수익 (놓친 거래가 좋은 거래였는지 보려고)
            x = t + 1
            while x < n - 1 and pos[x] > 0 and not np.isnan(c[x]):
                x += 1
            exit_px = o[x + 1] * (1 - slip) if x + 1 < n and not np.isnan(o[x + 1]) else c[x]
            mkt = exit_px * (1 - fee) / (o[t + 1] * (1 + slip)) * (1 - fee) - 1
            lim = exit_px * (1 - fee) / fill * (1 - fee) - 1
            attempts.append((t, ok, mkt, lim if ok else np.nan))
            armed = False
            if ok:
                held = True
                ret[t + 1] = (1 - fee) * c[t + 1] / fill - 1
    return ret, attempts


def portfolio(ctx: Ctx, pos: pd.DataFrame, members: pd.DataFrame, cfg, **kw):
    cols = list(members.columns)
    rets, att = {}, []
    core = set(ctx.core)
    for a in cols:
        p = (pos[a].fillna(0) * members[a].fillna(False)).to_numpy(float)
        slip = cfg["costs"]["slippage_core"] if a in core else cfg["costs"]["slippage_other"]
        r, at = sleeve(p, ctx.p.open[a].to_numpy(float), ctx.p.high[a].to_numpy(float), ctx.p.low[a].to_numpy(float),
                       ctx.p.close[a].to_numpy(float), fee=cfg["costs"]["fee"], slip=slip, atr=ctx.F.atr(14)[a].to_numpy(float), **kw)
        rets[a] = r
        att += [(a, ctx.p.close.index[t], ok, m, li) for t, ok, m, li in at]
    R = pd.DataFrame(rets, index=ctx.p.close.index)
    n = members.sum(axis=1).replace(0, np.nan)
    port = (R * members.shift(1).fillna(False)).sum(axis=1) / n.shift(1)
    eq = (1 + port.fillna(0)).cumprod()
    return eq, pd.DataFrame(att, columns=["asset", "date", "filled", "mkt_ret", "lim_ret"])


METHODS = [("시장가 (다음 날 시가, 지금 방식)", dict(method="market")),
           ("지정가 = 신호 날 종가", dict(method="limit", disc=0.0)),
           ("지정가 −0.5%", dict(method="limit", disc=0.005)),
           ("지정가 −1%", dict(method="limit", disc=0.01)),
           ("지정가 −2%", dict(method="limit", disc=0.02)),
           ("지정가 −3%", dict(method="limit", disc=0.03)),
           ("지정가 −5%", dict(method="limit", disc=0.05)),
           ("지정가 −0.5×ATR(변동성 맞춤)", dict(method="limit", atr_k=0.5))]


def systems(ctx):
    mt = strategy.multitrend(ctx.F)
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    return {"중기 (확정 공식)": (M.state & ~(L.score <= -2)).astype(float),
            "단기 추세": S.state.astype(float),
            "단기 + 중기 상승일 때만 진입": None}   # 아래에서 short_swing 의 정의를 그대로 씀


def evaluate(ctx, cfg, start, end, universes, label) -> tuple[pd.DataFrame, pd.DataFrame]:
    from .short_swing import swing_sim
    sysd = systems(ctx)
    mt = strategy.multitrend(ctx.F)
    S, M = mt.h["단기"], mt.h["중기"]
    sysd["단기 + 중기 상승일 때만 진입"] = swing_sim((S.score >= 2) & (M.score >= 2), S.score <= 0, ctx.F.c)
    rows, fills = [], []
    for u in universes:
        mem = ctx.members(u)
        for sname, pos in sysd.items():
            for mname, kw in METHODS:
                for retry in ((False, True) if kw["method"] == "limit" else (False,)):
                    for eps in ((0.0, 0.001) if kw["method"] == "limit" and kw.get("disc") == 0.01 else (0.0,)):
                        eq, att = portfolio(ctx, pos, mem, cfg, retry=retry, eps=eps, **kw)
                        e = eq.loc[pd.Timestamp(start) - pd.Timedelta(days=1):end]
                        e = e / e.iloc[0]
                        a = att[(att["date"] >= pd.Timestamp(start)) & (att["date"] <= pd.Timestamp(end))]
                        s = metrics.summarize(e)
                        m = "켜져 있는 동안 매일 다시" if retry else ("한 번만 시도" if kw["method"] == "limit" else "-")
                        r = {"구간": label, "대상": u, "방식": sname, "체결": mname, "재시도": m,
                             "체결 판정": "0.1% 뚫어야" if eps else ("닿으면" if kw["method"] == "limit" else "-"),
                             "연복리(CAGR)": s["연복리(CAGR)"], "MDD": s["MDD"], "샤프": s["샤프"], "월하위10%": s["월하위10%"],
                             "진입 시도": len(a), "체결률": a["filled"].mean() if len(a) else np.nan,
                             "체결된 거래 평균(시장가 기준)": a.loc[a["filled"], "mkt_ret"].mean() if a["filled"].any() else np.nan,
                             "놓친 거래 평균(시장가였다면)": a.loc[~a["filled"], "mkt_ret"].mean() if (~a["filled"]).any() else np.nan,
                             "놓친 거래 중 +20% 이상": (a.loc[~a["filled"], "mkt_ret"] >= 0.2).sum() if len(a) else 0,
                             "체결 가격 이득(평균)": (a.loc[a["filled"], "lim_ret"] - a.loc[a["filled"], "mkt_ret"]).mean()
                             if kw["method"] == "limit" and a["filled"].any() else np.nan}
                        rows.append(r)
    return pd.DataFrame(rows), pd.DataFrame(fills)


def bootstrap_sharpe_diff(r_a: pd.Series, r_b: pd.Series, n=2000, block=30, seed=0) -> tuple[float, float, float]:
    """블록 부트스트랩: 샤프(a) − 샤프(b) 의 90% 구간과 0보다 클 확률."""
    x = pd.concat([r_a, r_b], axis=1).dropna().to_numpy()
    T = len(x)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        idx = []
        while len(idx) < T:
            s = rng.integers(0, T)
            idx.extend(range(s, min(s + block, T)))
        y = x[np.array(idx[:T])]
        sa = y[:, 0].mean() / y[:, 0].std() * np.sqrt(365) if y[:, 0].std() > 0 else 0
        sb = y[:, 1].mean() / y[:, 1].std() * np.sqrt(365) if y[:, 1].std() > 0 else 0
        out.append(sa - sb)
    out = np.array(out)
    return float(np.quantile(out, 0.05)), float(np.quantile(out, 0.95)), float((out > 0).mean())


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    cf = copy.deepcopy(cfg)
    cf["validation"]["holdout_start"] = "2100-01-01"
    ctx = Ctx(cf, cache, lambda *a: None)     # 전체 데이터 (구간은 아래에서 나눠 평가)
    dev_end = pd.Timestamp(cfg["validation"]["holdout_start"]) - pd.Timedelta(days=1)
    uni = ["BTC", "ETH", "SOL", "BTC·ETH 균등", "코어3 균등", "기타 상위30 균등"]
    dev, _ = evaluate(ctx, cfg, START, dev_end, uni, "개발 2018-03~2024-12")
    log("  개발 구간 완료")
    ref, _ = evaluate(ctx, cfg, dev_end + pd.Timedelta(days=1), ctx.p.close.index[-1], ["BTC·ETH 균등", "코어3 균등"], "2025-01~현재(이미 본 구간)")
    log("  2025 이후 참고 완료")
    # 근사 확인: 시장가 버전 vs 기존 엔진 (코어3, 확정 공식)
    from ..backtest import signals as sg
    from .indicator_validation import _eq_from, _start
    mem = ctx.members("코어3 균등")
    pos = systems(ctx)["중기 (확정 공식)"]
    eng = ctx.run(sg.weights_slots(pos[list(mem.columns)], mem)).equity.loc[:dev_end]
    check = {"기존 엔진 CAGR": metrics.cagr(eng.loc[START:]), "이 검증의 시장가 CAGR":
             dev[(dev["대상"] == "코어3 균등") & (dev["방식"] == "중기 (확정 공식)") & (dev["체결"].str.startswith("시장가"))]["연복리(CAGR)"].iloc[0]}
    # 중기는 얼마나 의미가 있나: 샤프 차이 부트스트랩 (시장가, 엔진)
    boots = []
    for u in ("BTC·ETH 균등", "코어3 균등"):
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        f = _eq_from(ctx.run(sg.weights_slots(pos[cols], mem)), st)
        h = _eq_from(ctx.run(sg.weights_slots(pd.DataFrame(1.0, index=mem.index, columns=cols), mem)), st)
        for lab, a, z in (("개발 2018-03~2024-12", st, dev_end), ("2025-01~현재", dev_end + pd.Timedelta(days=1), None),
                          ("전체", st, None)):
            ra, rb = f.loc[a:z].pct_change().dropna(), h.loc[a:z].pct_change().dropna()
            lo, hi, pr = bootstrap_sharpe_diff(ra, rb)
            boots.append({"대상": u, "구간": lab, "중기 샤프": metrics.sharpe(ra), "계속 보유 샤프": metrics.sharpe(rb),
                          "샤프 차이 90% 구간": f"{lo:+.2f} ~ {hi:+.2f}", "중기가 나을 확률": pr,
                          "중기 MDD": metrics.max_drawdown(f.loc[a:z]), "보유 MDD": metrics.max_drawdown(h.loc[a:z])})
    boots = pd.DataFrame(boots)
    log("  부트스트랩 완료")
    md = render(dev, ref, boots, check, time.time() - t0, holding_stats(ctx))
    path = config_mod.ROOT / "docs" / f"limit-entry-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    pd.concat([dev, ref]).to_csv(config_mod.ROOT / "reports" / "research" / "limit_entry.csv", index=False)
    boots.to_csv(config_mod.ROOT / "reports" / "research" / "midterm_bootstrap.csv", index=False)
    log(f"완료: {path}")
    return path


def holding_stats(ctx) -> pd.DataFrame:
    """중기·단기의 실제 보유 기간 (BTC·ETH·SOL, 전체 기간)."""
    mt = strategy.multitrend(ctx.F)
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    rows = []
    for name, pos in (("중기 (확정 공식)", (M.state & ~(L.score <= -2))), ("단기 추세", S.state)):
        days = []
        for a in ctx.core:
            p = pos[a].loc[START:].astype(int)
            d = p.diff().fillna(p)
            ent, ex = p.index[d > 0], p.index[d < 0]
            for e in ent:
                x = ex[ex > e]
                days.append(((x[0] if len(x) else p.index[-1]) - e).days)
        dd = pd.Series(days)
        rows.append({"방식": name, "거래 수": len(dd), "보유일 중앙값": int(dd.median()), "하위 25%": int(dd.quantile(0.25)),
                     "상위 25%": int(dd.quantile(0.75)), "상위 10%": int(dd.quantile(0.9)), "평균 보유일": int(round(dd.mean())),
                     "최장": int(dd.max()), "7일 이하 비율": (dd <= 7).mean(),
                     "30일 이상 비율": (dd >= 30).mean()})
    return pd.DataFrame(rows)


def render(dev, ref, boots, check, secs, hold=None) -> str:
    o = ["# 지정가 매수 검증 + 중기는 얼마나 의미가 있나\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 조건\n",
         "- 매수: 신호 난 날 종가 × (1 − 할인율)에 지정가 → 다음 날 저가가 닿으면 체결(시가가 더 낮으면 시가에), 안 닿으면 포기. 매도: 다음 날 시가(시장가).",
         "- '한 번만 시도' = 안 닿으면 신호가 꺼졌다 다시 켜질 때까지 안 삼(말씀하신 방식). '매일 다시' = 신호가 켜져 있는 동안 매일 같은 방식으로 다시 걸기.",
         "- 업비트 수수료 0.05%(지정가·시장가 같음), 지정가는 슬리피지 없음. 일봉 고가·저가로 판정하므로 장중 순서(먼저 오르고 내렸는지)는 알 수 없음.",
         f"- 근사 확인: 같은 공식 시장가 결과가 기존 엔진 {check['기존 엔진 CAGR']:.1%} vs 이 검증 {check['이 검증의 시장가 CAGR']:.1%} (코어3, 개발 구간). "
         "몫을 매일 맞추는 비용을 빼서 생기는 차이입니다.",
         f"- 계산 {secs / 60:.1f}분\n",
         "## 1. 체결 방식별 성과 (개발 구간, 한 번만 시도 · 닿으면 체결)\n"]
    cols = ["방식", "체결", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "진입 시도", "체결률", "체결된 거래 평균(시장가 기준)",
            "놓친 거래 평균(시장가였다면)", "놓친 거래 중 +20% 이상", "체결 가격 이득(평균)"]
    d1 = dev[(dev["재시도"] != "켜져 있는 동안 매일 다시") & (dev["체결 판정"] != "0.1% 뚫어야")]
    for u in d1["대상"].unique():
        o += [f"### {u}\n", to_md(d1[d1["대상"] == u], cols), ""]
    o += ["## 2. '한 번만 시도' vs '켜져 있는 동안 매일 다시' · 체결 판정 보수적으로 (개발 구간, 코어3·BTC·ETH)\n"]
    d2 = dev[dev["대상"].isin(["코어3 균등", "BTC·ETH 균등"]) & dev["체결"].isin(["지정가 −1%", "지정가 −2%", "시장가 (다음 날 시가, 지금 방식)"])]
    o += [to_md(d2, ["대상", "방식", "체결", "재시도", "체결 판정", "연복리(CAGR)", "MDD", "샤프", "체결률", "놓친 거래 평균(시장가였다면)"]), ""]
    o += ["## 3. 참고: 2025-01 ~ 현재 (이미 본 구간 — 이걸로 고르면 안 됨)\n"]
    r1 = ref[(ref["재시도"] != "켜져 있는 동안 매일 다시") & (ref["체결 판정"] != "0.1% 뚫어야")]
    o += [to_md(r1, ["대상", "방식", "체결", "연복리(CAGR)", "MDD", "샤프", "체결률", "놓친 거래 평균(시장가였다면)"]), ""]
    o += ["## 4. 중기(확정 공식)는 얼마나 의미가 있나 — 계속 보유와 샤프 차이 (블록 부트스트랩 2,000회)\n",
          "날짜를 30일 덩어리로 다시 뽑아 같은 계산을 2,000번 해서, 차이가 우연으로도 나올 수 있는 범위를 봤습니다. "
          "'90% 구간'이 0을 넘으면 우연으로 보기 어렵습니다.\n",
          to_md(boots, ["대상", "구간", "중기 샤프", "계속 보유 샤프", "샤프 차이 90% 구간", "중기가 나을 확률", "중기 MDD", "보유 MDD"]), ""]
    if hold is not None:
        o += ["## 5. '중기'는 실제로 얼마나 들고 있나 (BTC·ETH·SOL, 2018-03 ~ 현재)\n",
              "판정 기준은 EMA 20/50일, 55일 채널, 50일 회귀(몇 주~2달용). 들어가서 실패하면 1주일 안에 나오고, 맞으면 1~2달(길면 4~7달) 갑니다.\n",
              to_md(hold, ["방식", "거래 수", "보유일 중앙값", "하위 25%", "상위 25%", "상위 10%", "평균 보유일", "최장", "7일 이하 비율", "30일 이상 비율"]), ""]
    o += conclusions(dev, boots)
    return "\n".join(o)


def conclusions(dev, boots) -> list[str]:
    def g(u, sname, mname, retry="한 번만 시도", k="연복리(CAGR)"):
        m = dev[(dev["대상"] == u) & (dev["방식"] == sname) & (dev["체결"] == mname) & (dev["체결 판정"] != "0.1% 뚫어야")]
        if mname.startswith("시장가"):
            m = m[m["재시도"] == "-"]
        else:
            m = m[m["재시도"] == retry]
        return m.iloc[0][k] if len(m) else np.nan

    def pct(v):
        return "-" if pd.isna(v) else f"{v * 100:.0f}%"

    MK, MID, SH = "시장가 (다음 날 시가, 지금 방식)", "중기 (확정 공식)", "단기 추세"
    b = boots.set_index(["대상", "구간"])
    o = ["## 6. 결론\n",
         f"**1) '안 닿으면 포기'는 코어에서 손해였습니다.** 코어3 중기: 시장가 {pct(g('코어3 균등', MID, MK))} → −1% 지정가·한 번만 시도 "
         f"{pct(g('코어3 균등', MID, '지정가 −1%'))} → −2% {pct(g('코어3 균등', MID, '지정가 −2%'))}. "
         f"놓친 거래도 시장가였다면 평균 {pct(g('코어3 균등', MID, '지정가 −1%', k='놓친 거래 평균(시장가였다면)'))}의 수익 거래였고, "
         f"BTC·ETH에서는 놓친 거래({pct(g('BTC·ETH 균등', MID, '지정가 −1%', k='놓친 거래 평균(시장가였다면)'))})가 체결된 거래"
         f"({pct(g('BTC·ETH 균등', MID, '지정가 −1%', k='체결된 거래 평균(시장가 기준)'))})보다 오히려 좋았습니다 — 강한 추세는 되돌림 없이 올라가서 지정가에 안 걸리는 경우가 많습니다.",
         f"**2) 대신 '신호가 켜져 있는 동안 매일 다시 지정가'로 하면 시장가와 같거나 조금 나았습니다.** 코어3 중기 −1%: {pct(g('코어3 균등', MID, '지정가 −1%', '켜져 있는 동안 매일 다시'))}, "
         f"−2%: {pct(g('코어3 균등', MID, '지정가 −2%', '켜져 있는 동안 매일 다시'))} (시장가 {pct(g('코어3 균등', MID, MK))}). 놓쳐도 다음 날 다시 거니까 강한 추세를 하루 이틀 늦게라도 탑니다.",
         f"**3) 단기는 지정가로도 살아나지 않았습니다.** 코어3 단기: 시장가 {pct(g('코어3 균등', SH, MK))}, −1% 한 번만 {pct(g('코어3 균등', SH, '지정가 −1%'))}, "
         f"−1% 매일 다시 {pct(g('코어3 균등', SH, '지정가 −1%', '켜져 있는 동안 매일 다시'))}. 매일 다시 걸어야 시장가 수준이고, 그래도 중기보다 MDD가 깊고 거래·비용이 많습니다. "
         "단기의 가치는 여전히 '중기와 섞어 분산'(70:30) 정도입니다.",
         f"**4) 알트(기타)는 지정가가 도움이 됐습니다.** 중기 −3% 한 번만: 연복리 {pct(g('기타 상위30 균등', MID, '지정가 −3%'))}, MDD {pct(g('기타 상위30 균등', MID, '지정가 −3%', k='MDD'))} "
         f"(시장가 {pct(g('기타 상위30 균등', MID, MK))}, {pct(g('기타 상위30 균등', MID, MK, k='MDD'))}). 알트는 장중 출렁임이 커서 싸게 사는 효과가 놓치는 손해보다 컸습니다.",
         f"**5) 중기는 얼마나 의미가 있나**: 계속 보유 대비 샤프 차이 90% 구간이 코어3 개발 구간 {b.loc[('코어3 균등', '개발 2018-03~2024-12'), '샤프 차이 90% 구간']}, "
         f"전체 {b.loc[('코어3 균등', '전체'), '샤프 차이 90% 구간']}로 0을 넘어 우연으로 보기 어렵고(나을 확률 {pct(b.loc[('코어3 균등', '전체'), '중기가 나을 확률'])}), "
         f"BTC·ETH는 {pct(b.loc[('BTC·ETH 균등', '전체'), '중기가 나을 확률'])}로 경계선입니다. MDD는 모든 구간에서 보유의 절반 수준이었습니다. "
         "2025년 이후만 따로 보면 기간이 짧아 구간이 너무 넓어서(대략 −0.8 ~ +1.6) 판단할 수 없습니다. "
         "정리: **중기의 의미는 '수익을 크게 늘리는 것'보다 '비슷하거나 조금 나은 위험 대비 수익으로 큰 하락을 절반으로 줄이는 것'**에 있습니다.\n",
         "**제안**",
         "- 매수 주문: 코어는 '신호 난 날 종가 −1% 지정가, 신호가 켜져 있는 동안 매일 다시 걸기'. 안 닿으면 그날은 보내고 다음 날 같은 방식으로 다시 겁니다. "
         "(완전히 포기하는 방식은 강한 추세를 놓쳐 손해였습니다.)",
         "- 알트는 −3% 정도 지정가가 데이터상 나았습니다(한 번만 시도로도).",
         "- 매도는 지금처럼 다음 날 시가 시장가(손절이 늦어지면 손실이 커지므로 지정가로 기다리지 않음).",
         "- 이 체결 규칙은 2025년 이후(이미 본 구간)를 보고 정한 게 아니라 개발 구간 결과로 정한 것입니다. 2025년 이후 참고 표(3절)는 방향이 섞여 있어 판단에 쓰지 않았습니다.",
         ]
    return o
