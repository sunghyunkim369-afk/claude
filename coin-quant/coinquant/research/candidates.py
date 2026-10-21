"""완성 공식 후보 비교: `python -m coinquant candidates`

config.toml 의 비중·한도·리밸런스를 그대로 넣고, [formula] 만 바꿔 가며 개발 구간에서 비교합니다.
주의: 후보는 1차 검증 결과를 보고 고른 것이라 이 표는 낙관적입니다. 최종 판단은 워크포워드와 최종 검증 구간(한 번)으로.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from .. import config as config_mod, strategy
from ..backtest import engine, metrics, validate as V
from ..data.update import load_funding
from ..universe import Panel
from .indicator_validation import START, to_md

CANDIDATES = {
    "0. 기준: 코어 보유 + 기타 균등 (신호 없음)": {"core_signal": "보유", "others_signal": "보유", "others_select": "균등",
                                         "regime_filter": "없음"},
    "1. 국면만: BTC 200일선 아래면 전부 현금": {"core_signal": "보유", "others_signal": "보유", "others_select": "균등",
                                     "regime_filter": "BTC 200일선 위", "regime_applies_to": ["core", "others"]},
    "2. 추세(돌파): 코어 돈치안20 · 기타 MACD>0&히스토그램>0": {"core_signal": "돈치안20 돌파(10일 저가 청산)",
                                                    "others_signal": "MACD>0 & 히스토그램>0", "others_select": "균등",
                                                    "regime_filter": "없음"},
    "3. 추세 + 국면: 2번 + 기타는 BTC 200일선 위일 때만": {"core_signal": "돈치안20 돌파(10일 저가 청산)",
                                                "others_signal": "MACD>0 & 히스토그램>0", "others_select": "균등",
                                                "regime_filter": "BTC 200일선 위", "regime_applies_to": ["others"]},
    "4. 보수적 추세: 코어 EMA 정배열 · 기타 회귀90 추세선 위": {"core_signal": "EMA 정배열(가격>20>50>200)",
                                                 "others_signal": "회귀기울기90>0 & 추세선 위", "others_select": "균등",
                                                 "regime_filter": "없음"},
    "5. 느린 추세: 코어 EMA 20>50 · 기타 돈치안55": {"core_signal": "EMA 20>50", "others_signal": "돈치안55 돌파(20일 저가 청산)",
                                            "others_select": "균등", "regime_filter": "없음"},
    "6. 스윙(다중 기간): 코어 3단 분할+장기 하락이면 단기 몫만 · 기타 중기+장기 하락이면 중단 · 기타는 BTC 200일선 위일 때만": {
        "core_signal": "추세:3단 분할 + 장기 하락이면 단기 몫만", "others_signal": "추세:중기 보유 + 장기 하락이면 중단",
        "others_select": "균등", "regime_filter": "BTC 200일선 위", "regime_applies_to": ["others"]},
    "7. 스윙(중기 중심): 코어·기타 모두 중기 보유+장기 하락이면 중단 · 기타는 BTC 200일선 위일 때만": {
        "core_signal": "추세:중기 보유 + 장기 하락이면 중단", "others_signal": "추세:중기 보유 + 장기 하락이면 중단",
        "others_select": "균등", "regime_filter": "BTC 200일선 위", "regime_applies_to": ["others"]},
}


def run_formula(panel: Panel, cfg: dict, fm: dict, funding, delist_mode="last", exec_lag=0, core=None, rebalance=None):
    c = copy.deepcopy(cfg)
    c["formula"] = {**strategy.DEFAULT_FORMULA, **fm}
    if core is not None:
        c["universe"]["core"] = core
    W, info = strategy.target_weights(panel, c, None, funding)
    pf, cc = c["portfolio"], c["costs"]
    costs = engine.Costs(cc["fee"], cc["slippage_core"], cc["slippage_other"], tuple(info["core"]))
    cols = list(W.columns[(W != 0).any()])
    migrated = frozenset(panel.meta.index[panel.meta["migrated_to"].notna()])
    rules = engine.Rules(rebalance_days=rebalance or pf["rebalance_days"], max_weight=pf["max_weight_per_coin"],
                         max_total=pf["max_total_invest"], max_orders=pf["max_orders_per_day"],
                         min_trade=pf["min_trade_weight"], delist_mode=delist_mode, exec_lag=exec_lag, always_last=migrated)
    return engine.run(W[cols], panel.open[cols], panel.close[cols], costs, rules, panel.tradable[cols], start=START)


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    lock = V.HoldoutLock(cache / "holdout_log.jsonl", cfg["validation"]["holdout_start"])
    panel = Panel.load(cache / "panel").slice(None, lock.dev_end())   # 최종 검증 구간 제외
    funding = load_funding(cache)
    btc = panel.close["BTC"].loc[START:]
    rows, eras = [], {}
    for name, fm in CANDIDATES.items():
        res = run_formula(panel, cfg, fm, funding)
        s = metrics.summarize(res.equity, btc, res.trades, res.weights, res.turnover)
        s["CAGR(폐지=0원)"] = metrics.cagr(run_formula(panel, cfg, fm, funding, "zero").equity)
        s["CAGR(하루 늦게 체결)"] = metrics.cagr(run_formula(panel, cfg, fm, funding, exec_lag=1).equity)
        s["CAGR(코어=BTC·ETH만)"] = metrics.cagr(run_formula(panel, cfg, fm, funding, core=["BTC", "ETH"]).equity)
        s["MDD(코어=BTC·ETH만)"] = metrics.max_drawdown(run_formula(panel, cfg, fm, funding, core=["BTC", "ETH"]).equity)
        daily = run_formula(panel, cfg, fm, funding, rebalance=1).equity
        s["CAGR(매일 판단)"], s["MDD(매일 판단)"] = metrics.cagr(daily), metrics.max_drawdown(daily)
        s["후보"] = name
        rows.append(s)
        eras[name] = res.equity
        log(f"  {name}: CAGR {s['연복리(CAGR)']:.1%} MDD {s['MDD']:.1%}")
    df = pd.DataFrame(rows)
    pf = cfg["portfolio"]
    out = [f"## 9. 완성 공식 후보 비교 (개발 구간 {START} ~ {lock.dev_end().date()})\n",
           f"실제 설정 그대로: 코어 {pf['core_weight']:.0%} / 기타 {pf['other_weight']:.0%}, 한 코인 최대 {pf['max_weight_per_coin']:.0%}, "
           f"리밸런스 {pf['rebalance_days']}일, 하루 최대 주문 {pf['max_orders_per_day']}건, 비중 변화 {pf['min_trade_weight']:.0%} 미만은 주문 안 함. "
           "신호가 꺼진 몫은 현금.\n",
           "**주의: 후보는 1~8절 결과를 보고 고른 것이라 이 숫자는 낙관적입니다(같은 데이터로 고르고 같은 데이터로 평가).** "
           "'코어=BTC·ETH만' 열은 SOL을 코어로 고른 사후 선택 효과를 뺀 결과입니다.\n",
           to_md(df, ["후보", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)", "BTC대비 월승률",
                      "평균투자비중", "연회전율", "CAGR(폐지=0원)", "CAGR(하루 늦게 체결)", "CAGR(코어=BTC·ETH만)", "MDD(코어=BTC·ETH만)", "CAGR(매일 판단)", "MDD(매일 판단)"]), ""]
    for name, eq in eras.items():
        et = V.era_table(eq, {"BTC": btc})
        out += [f"### {name} — 시대별\n", to_md(et, ["시대", "수익", "MDD", "BTC 수익", "BTC MDD"]), ""]
    path = config_mod.ROOT / "reports" / "research" / "candidates.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    df.to_csv(path.with_suffix(".csv"), index=False)
    log(f"완료: {path}")
    return path


def sensitivity(cfg: dict | None = None, log=print) -> Path:
    """후보 신호의 숫자를 조금씩 바꿔 봄: 넓은 범위에서 비슷하면(고원) 믿을 만, 한 값에서만 좋으면(봉우리) 우연 의심."""
    from .. import indicators as ind
    from ..backtest import signals as sg
    from .indicator_validation import Ctx, _eq_from, _on_all, _start
    cfg = cfg or config_mod.load()
    ctx = Ctx(cfg, config_mod.cache_dir(cfg), log)
    F = ctx.F
    c = F.c
    fams = {}
    for n in (10, 15, 20, 30, 40, 55, 80):
        x = max(n // 2, 5)
        fams[("돈치안 돌파", f"{n}일 고가 돌파 / {x}일 저가 청산")] = lambda n=n, x=x: sg._state(
            c > ind.donchian_high(F.h, n), c < ind.donchian_low(F.l, x))
    for f_, s_ in ((10, 30), (15, 40), (20, 50), (30, 80), (50, 100), (50, 200)):
        fams[("EMA 교차", f"EMA {f_}>{s_}")] = lambda f_=f_, s_=s_: F.ema(f_) > F.ema(s_)
    for a, b, k in ((8, 17, 9), (8, 21, 5), (12, 26, 9), (19, 39, 9), (24, 52, 18)):
        def mac(a=a, b=b, k=k):
            line, sig, hist = ind.macd(c, a, b, k)
            return (line > 0) & (hist > 0)
        fams[("MACD>0&히스토그램>0", f"MACD({a},{b},{k})")] = mac
    for n in (10, 20, 30, 45, 60, 90):
        fams[("N일 수익>0", f"{n}일")] = lambda n=n: ind.ret(c, n) > 0
    rows = []
    for u in ("코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"):
        mem = ctx.members(u)
        cols = list(mem.columns)
        st = _start(ctx, mem)
        base = _eq_from(ctx.run(sg.weights_slots(_on_all(ctx)[cols], mem), rebalance_days=7), st)
        for (fam, name), fn in fams.items():
            on = fn()[cols].fillna(False).astype(bool)
            eq = _eq_from(ctx.run(sg.weights_slots(on, mem), rebalance_days=7), st)
            rows.append({"대상": u, "묶음": fam, "설정": name, "연복리(CAGR)": metrics.cagr(eq), "MDD": metrics.max_drawdown(eq),
                         "샤프": metrics.sharpe(eq.pct_change().dropna()),
                         "CAGR차(vs 보유)": metrics.cagr(eq) - metrics.cagr(base)})
        log(f"  민감도 {u} 완료")
    df = pd.DataFrame(rows)
    out = ["## 10. 파라미터 민감도 (주 1회 리밸런스)\n",
           "같은 신호의 숫자를 바꿔 봤을 때 결과가 넓게 비슷하면(고원) 믿을 만하고, 한 값에서만 튀면(봉우리) 우연일 가능성이 큽니다. "
           f"이 표의 {len(df)}개도 시험 개수에 들어갑니다.\n"]
    for u in df["대상"].unique():
        out += [f"### {u}\n", to_md(df[df["대상"] == u], ["묶음", "설정", "연복리(CAGR)", "MDD", "샤프", "CAGR차(vs 보유)"]), ""]
    path = config_mod.ROOT / "reports" / "research" / "sensitivity.md"
    path.write_text("\n".join(out), encoding="utf-8")
    df.to_csv(path.with_suffix(".csv"), index=False)
    log(f"완료: {path}")
    return path
