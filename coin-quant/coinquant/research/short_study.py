"""숏(무기한 선물) 검증: `python -m coinquant short-study`

질문: 하락 추세에서 숏으로 돈을 벌 수 있나? 업비트 현물 롱에 선물 숏을 더하면 나아지나?

데이터: 바이낸스 USDⓈ-M 무기한 선물 일봉·펀딩비 (바이비트는 이 환경에서 접속이 막혀 대신 씀. 무기한 선물 가격은 거래소끼리
거의 같이 움직이지만 펀딩비는 조금씩 다름). 수수료는 바이비트 시장가 0.055% + 슬리피지(코어 0.1%, 기타 0.3%).
추세 판정은 현물 가격(롱과 같은 기준), 체결·손익·펀딩은 선물 가격. 개발 구간(~2024-12-31)만.
선물은 2019-09(BTC)부터라 2018 겨울은 '현물 가격으로 가정한 숏'(펀딩 0)으로만 참고 표시.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, strategy
from ..backtest import engine, metrics, signals as sg, validate as V
from ..backtest.futures import FutRules, run_futures
from ..trend import ON_AT, hysteresis
from ..universe import _split_gaps, strip_multiplier
from .indicator_validation import START, Ctx, to_md

BYBIT_TAKER = 0.00055
ERAS_SHORT = [
    ("2020-03 코로나 급락", "2020-02-10", "2020-04-30"),
    ("2020~21 강세", "2020-10-01", "2021-11-10"),
    ("2021-05 급락(중국 채굴 금지 등)", "2021-05-01", "2021-07-31"),
    ("2022 약세 전체", "2021-11-10", "2022-12-31"),
    ("2022-05 LUNA 붕괴", "2022-05-01", "2022-06-30"),
    ("2022-11 FTX 붕괴", "2022-11-01", "2022-12-31"),
    ("2023 회복", "2023-01-01", "2023-12-31"),
    ("2024-01 현물 ETF 승인(±3개월)", "2023-10-10", "2024-04-10"),
    ("2024 반감기 이후", "2024-04-20", "2024-12-31"),
]


# ---------------------------------------------------------------- 선물 패널

def load_futures(cache: Path, spot_close: pd.DataFrame, dev_end, gap_days=10) -> dict[str, pd.DataFrame]:
    """선물 일봉을 현물 패널과 같은 코인 이름으로. 이름이 같아도 가격 흐름이 다르면(심볼 재사용 등) 버림."""
    d = cache / "binance" / "um_1d"
    cols = {k: {} for k in ("open", "high", "low", "close", "funding")}
    r_spot = np.log(spot_close).diff()
    for p in sorted(d.glob("*.csv")):
        base = strip_multiplier(p.stem[:-4])
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
        df = df[(df["close"] > 0)].loc[:dev_end]
        if df.empty:
            continue
        for i, part in enumerate(_split_gaps(df, gap_days)):
            cands = [base if i == 0 else f"{base}#{i + 1}"] + [c for c in spot_close.columns if c.split("#")[0] == base]
            best, best_c = None, 0.9
            rf = np.log(part["close"]).diff()
            for a in dict.fromkeys(cands):
                if a not in spot_close:
                    continue
                ok = rf.notna() & r_spot[a].reindex(rf.index).notna()
                if ok.sum() < 20:
                    continue
                c = rf[ok].corr(r_spot[a].reindex(rf.index)[ok])
                if c > best_c:
                    best, best_c = a, c
            if best is None or best in cols["close"]:
                continue
            for k in ("open", "high", "low", "close", "funding"):
                cols[k][best] = part[k]
    idx = spot_close.index
    return {k: pd.DataFrame(v).reindex(idx) for k, v in cols.items()}


def liq_prices(fut: dict, spot_high: pd.DataFrame, spot_low: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """청산 판단용 고가·저가. 거래소는 거래가격 꼬리가 아니라 표시가격(여러 현물 거래소 지수) 기준으로 청산하므로
    현물 고가·저가로 근사하고, 현물이 없는 날만 선물 고가·저가를 씀. (예: 2020-03-13 ETH 선물 거래가격 꼬리는 몸통의 2.4배)"""
    h = spot_high.reindex(columns=fut["high"].columns).combine_first(fut["high"])
    l_ = spot_low.reindex(columns=fut["low"].columns).combine_first(fut["low"])
    return h, l_


# ---------------------------------------------------------------- 숏 방식 (음수 비중 = 숏 비율)

def short_systems(mt, F, btc_above200: pd.Series) -> dict[str, pd.DataFrame]:
    sc = {k: mt.h[k].score for k in ("단기", "중기", "장기")}
    down = {k: hysteresis(-s) for k, s in sc.items()}            # −2 이하에서 켜고 0 이상이면 끔
    Lup = sc["장기"] >= ON_AT
    Ldn = sc["장기"] <= -ON_AT
    c = F.c
    from .. import indicators as ind
    don = sg._state(c < ind.donchian_low(F.l, 20), c > ind.donchian_high(F.h, 10))
    mid_stop = down["중기"] & ~Lup
    bear = ~btc_above200.reindex(c.index).fillna(True).astype(bool)
    out = {
        "단기 하락 숏": down["단기"].astype(float),
        "중기 하락 숏": down["중기"].astype(float),
        "장기 하락 숏": down["장기"].astype(float),
        "중기 하락 숏 + 장기 상승이면 중단": mid_stop.astype(float),
        "중기·장기 모두 하락일 때만 숏": (down["중기"] & Ldn).astype(float),
        "3단 분할 숏(장기 상승이면 단기 몫만)": ((down["단기"].astype(float) + down["중기"].astype(float) + down["장기"].astype(float)) / 3)
        .where(~Lup, down["단기"].astype(float) / 3),
        "돈치안20 저가 이탈 숏(10일 고가 회복 시 환매)": don.astype(float),
        "중기 하락 숏 + 장기 상승이면 중단, BTC 200일선 아래일 때만": mid_stop.mul(bear, axis=0).astype(float),
        "[위약] 항상 숏": pd.DataFrame(1.0, index=c.index, columns=c.columns),
    }
    return {k: -v for k, v in out.items()}


def _rate(cols, core, cfg):
    c = cfg["costs"]
    return pd.Series({a: BYBIT_TAKER + (c["slippage_core"] if a in core else c["slippage_other"]) for a in cols})


def fut_summary(res, start, btc_eq) -> dict:
    eq = res.equity.loc[start - pd.Timedelta(days=1):]
    s = metrics.summarize(eq, btc_eq)
    yrs = max((eq.index[-1] - eq.index[0]).days / 365, 1e-9)
    t = res.trades[res.trades["exit"] >= start]
    if len(t):
        days = (t["exit"] - t["entry"]).dt.days
        s.update({"거래수": len(t), "거래승률": (t["ret"] > 0).mean(), "거래중앙값": t["ret"].median(), "최악거래": t["ret"].min(),
                  "평균 보유일": days.mean()})
    s["평균 숏 비중"] = -res.weights.loc[eq.index].clip(upper=0).sum(axis=1).mean()
    s["펀딩 손익(연, 평가금 대비)"] = res.funding_pnl.loc[eq.index].sum() / yrs
    s["청산 횟수"] = res.liquidations
    s["최악의 하루"] = eq.pct_change().min()
    return s


# ---------------------------------------------------------------- 실행

def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    ctx = Ctx(cfg, cache, log)
    p = ctx.p
    fut = load_futures(cache, p.close[ctx.cols], ctx.lock.dev_end())
    fcols = [c for c in ctx.cols if c in fut["close"].columns and fut["close"][c].notna().any()]
    fut["high"], fut["low"] = liq_prices(fut, p.high[fcols], p.low[fcols])
    log(f"  선물 연결: {len(fcols)}개 (현물 대상 {len(ctx.cols)}개 중)")
    mt = strategy.multitrend(ctx.F)
    btc_up = sg.regime_filters(p)["BTC 200일선 위"]
    shorts = short_systems(mt, ctx.F, btc_up)
    has_fut = fut["close"].notna()
    out_dir = config_mod.ROOT / "reports" / "research"
    rows, curves, trades_all = [], {}, {}

    def run_short(w, u_cols, lev=1.0, exec_lag=0):
        """lev 배: 같은 신호를 lev 배 명목금액으로 (증거금은 그대로, 청산가는 lev 배 기준)."""
        w = w[u_cols] * lev
        rate = _rate(u_cols, ctx.core, cfg)
        return run_futures(w, fut["open"][u_cols], fut["high"][u_cols], fut["low"][u_cols], fut["close"][u_cols],
                           fut["funding"][u_cols], rate, FutRules(leverage=lev, min_trade=cfg["portfolio"]["min_trade_weight"],
                                                                  exec_lag=exec_lag, max_gross=lev), start=START)

    universes = ["BTC", "ETH", "SOL", "코어3 균등", "BTC·ETH 균등", "기타 상위30 균등"]
    for u in universes:
        mem = ctx.members(u)
        u_cols = [c for c in mem.columns if c in fcols]
        if not u_cols:
            continue
        m = mem[u_cols] & has_fut[u_cols]
        first = m.index[m.any(axis=1).to_numpy()]
        st = max(first[0] + pd.Timedelta(days=1), pd.Timestamp(START)) if len(first) else None
        if st is None:
            continue
        btc_eq = ctx.btc.loc[st - pd.Timedelta(days=1):]
        for name, sw in shorts.items():
            w = sg.weights_slots(-sw[u_cols], m) * -1
            res = run_short(w, u_cols)
            s = fut_summary(res, st, btc_eq)
            s.update({"대상": u, "방식": name, "시작": st.date()})
            s["CAGR(하루 늦게 체결)"] = metrics.cagr(run_short(w, u_cols, exec_lag=1).equity.loc[st - pd.Timedelta(days=1):])
            r2 = run_short(w, u_cols, lev=2.0)
            s["2배 레버리지 CAGR"] = metrics.cagr(r2.equity.loc[st - pd.Timedelta(days=1):])
            s["2배 레버리지 MDD"] = metrics.max_drawdown(r2.equity.loc[st - pd.Timedelta(days=1):])
            s["2배 청산 횟수"] = r2.liquidations
            eq = res.equity.loc[st - pd.Timedelta(days=1):]
            rows.append(s)
            curves[(u, name)] = eq
            trades_all[(u, name)] = res.trades
            ctx.trials.add("short", name, u, eq)
        log(f"  [{u}] 숏 방식 {len(shorts)}개 완료")
    perf = pd.DataFrame(rows)
    perf.to_csv(out_dir / "short_perf.csv", index=False)

    # 무작위 타이밍 위약 (코어3): 같은 숏 비율·평균 보유기간
    rnd_rows = []
    mem = ctx.members("코어3 균등")
    u_cols = [c for c in mem.columns if c in fcols]
    m = mem[u_cols] & has_fut[u_cols]
    st = m.index[m.any(axis=1).to_numpy()][0] + pd.Timedelta(days=1)
    for name in ("중기 하락 숏 + 장기 상승이면 중단", "중기·장기 모두 하락일 때만 숏", "돈치안20 저가 이탈 숏(10일 고가 회복 시 환매)"):
        on = (-shorts[name][u_cols] > 0)
        actual = perf[(perf["대상"] == "코어3 균등") & (perf["방식"] == name)]["샤프"].iloc[0]
        srs = []
        for k in range(30):
            ron = pd.DataFrame({c: sg.random_timing(on[c][m[c]].loc[st:], seed=500 * k + j) for j, c in enumerate(u_cols)})
            ron = ron.reindex(on.index).fillna(False).astype(bool)
            w = -sg.weights_slots(ron, m)
            res = run_short(w, u_cols)
            srs.append(metrics.sharpe(res.equity.loc[st:].pct_change().dropna()))
        rnd_rows.append({"방식": name, "실제 샤프": actual, "무작위 샤프 중앙값": np.median(srs),
                         "무작위 대비 백분위": float((np.array(srs) < actual).mean())})
    rnd = pd.DataFrame(rnd_rows)
    log("  무작위 위약 완료")

    # 알트 '가장 약한 코인' 숏 (BTC 200일선 아래일 때만) vs 무작위
    sel_rows = []
    mem = ctx.members("기타 상위30 균등")
    o_cols = [c for c in mem.columns if c in fcols]
    m = mem[o_cols] & has_fut[o_cols]
    bear = ~btc_up.reindex(p.close.index).fillna(True).astype(bool)
    mb = m.mul(bear, axis=0).astype(bool)
    st_o = m.index[m.any(axis=1).to_numpy()][0] + pd.Timedelta(days=1)
    btc_eq = ctx.btc.loc[st_o - pd.Timedelta(days=1):]
    from .. import indicators as ind
    for name, sc in (("30일 수익률 하위 5개 숏", -ind.ret(ctx.F.c, 30)), ("BTC 대비 약세 + 중기 하락 5개 숏",
                                                                     -ind.ret(ctx.F.c, 30).where(-shorts["중기 하락 숏"] > 0))):
        w = -sg.weights_topk(sc[o_cols], mb, 5)
        res = run_futures(w, fut["open"][o_cols], fut["high"][o_cols], fut["low"][o_cols], fut["close"][o_cols], fut["funding"][o_cols],
                          _rate(o_cols, ctx.core, cfg), FutRules(rebalance_days=7), start=START)
        s = fut_summary(res, st_o, btc_eq)
        s["방식"] = name
        sel_rows.append(s)
        ctx.trials.add("short-select", name, "기타", res.equity.loc[st_o:])
    rnd_sel = []
    for k in range(60):
        w = -sg.weights_random(mb, 5, 7, seed=k)
        res = run_futures(w, fut["open"][o_cols], fut["high"][o_cols], fut["low"][o_cols], fut["close"][o_cols], fut["funding"][o_cols],
                          _rate(o_cols, ctx.core, cfg), FutRules(rebalance_days=7), start=START)
        e = res.equity.loc[st_o - pd.Timedelta(days=1):]
        rnd_sel.append({"CAGR": metrics.cagr(e), "MDD": metrics.max_drawdown(e), "샤프": metrics.sharpe(e.pct_change().dropna())})
    rnd_sel = pd.DataFrame(rnd_sel)
    for s in sel_rows:
        s["무작위 5개 대비 CAGR 백분위"] = float((rnd_sel["CAGR"] < s["연복리(CAGR)"]).mean())
    sel_rows.append({"방식": "(위약) 무작위 5개 숏 × 60회 중앙값", "연복리(CAGR)": rnd_sel["CAGR"].median(),
                     "MDD": rnd_sel["MDD"].median(), "샤프": rnd_sel["샤프"].median()})
    sel = pd.DataFrame(sel_rows)
    log("  알트 숏 고르기 완료")

    # 현물 롱(업비트) + 선물 숏 합치기
    combo = combine(ctx, cfg, fut, fcols, shorts, run_short)
    combo["ref2018"] = ref_2018(ctx, shorts)
    log("  롱+숏 합산 완료")
    md = render(ctx, cfg, perf, rnd, sel, combo, curves, time.time() - t0, len(fcols))
    path = config_mod.ROOT / "docs" / f"short-study-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def ref_2018(ctx, shorts) -> pd.DataFrame:
    """선물 데이터가 없는 2018 약세장: BTC·ETH 현물 가격으로 숏했다고 가정(펀딩 0, 비용은 같음). 참고용."""
    p = ctx.p
    cols = ["BTC", "ETH"]
    zero = pd.DataFrame(0.0, index=p.close.index, columns=cols)
    rows = []
    for name in ("중기 하락 숏", "중기 하락 숏 + 장기 상승이면 중단", "중기·장기 모두 하락일 때만 숏",
                 "돈치안20 저가 이탈 숏(10일 고가 회복 시 환매)", "[위약] 항상 숏"):
        w = sg.weights_slots(-shorts[name][cols], ctx.alive[cols]) * -1
        r = run_futures(w, p.open[cols], p.high[cols], p.low[cols], p.close[cols], zero,
                        pd.Series({c: BYBIT_TAKER + 0.001 for c in cols}), FutRules(), start="2018-03-01", end="2019-12-31")
        e = r.equity
        rows.append({"방식": name, "2018-03~12 (약세장)": e.loc[:"2018-12-31"].iloc[-1] / e.iloc[0] - 1,
                     "2019 (반등·횡보)": e.loc["2019-12-31"] / e.loc["2018-12-31"] - 1, "MDD": metrics.max_drawdown(e)})
    return pd.DataFrame(rows)


def combine(ctx, cfg, fut, fcols, shorts, run_short) -> dict:
    """업비트 현물 롱(설정의 공식) + 선물 숏(코어3) 을 자금 비율별로. 분기마다 두 계좌 비율 재조정(이체 비용 0.2%)."""
    from .candidates import run_formula
    from ..data.update import load_funding
    p = ctx.p
    long_res = run_formula(p, cfg, strategy.formula(cfg), load_funding(config_mod.cache_dir(cfg)), rebalance=1)
    mem = ctx.members("코어3 균등")
    u_cols = [c for c in mem.columns if c in fcols]
    m = mem[u_cols] & fut["close"][u_cols].notna()
    st = m.index[m.any(axis=1).to_numpy()][0] + pd.Timedelta(days=1)
    out = {"start": st, "rows": [], "eras": {}}
    btc = ctx.btc.loc[st - pd.Timedelta(days=1):]
    long_eq = long_res.equity.loc[st - pd.Timedelta(days=1):]
    short_name = "중기 하락 숏 + 장기 상승이면 중단"
    short_eq = run_short(sg.weights_slots(-shorts[short_name][u_cols], m) * -1, u_cols).equity.loc[st - pd.Timedelta(days=1):]
    rl, rs = long_eq.pct_change().fillna(0), short_eq.pct_change().fillna(0)
    for a in (1.0, 0.8, 0.7, 0.5):
        vals, L, S = [], a, 1 - a
        q = None
        for d in rl.index:
            L *= 1 + rl[d]
            S *= 1 + rs[d]
            if q is not None and d.quarter != q:     # 분기마다 비율 맞추기
                tot = L + S
                moved = abs(L - a * tot)
                tot -= moved * 0.002
                L, S = a * tot, (1 - a) * tot
            q = d.quarter
            vals.append(L + S)
        eq = pd.Series(vals, index=rl.index)
        s = metrics.summarize(eq, btc)
        s["구성"] = f"현물 롱 {a:.0%} + 선물 숏 {1 - a:.0%}" if a < 1 else "현물 롱 100% (지금 임시 공식)"
        out["rows"].append(s)
        out["eras"][s["구성"]] = eq
    # 한 계좌(선물)에서 롱·숏 모두: 중기 상승이면 롱, 중기 하락이면 숏
    long_on = strategy._on(strategy.formula(cfg)["core_signal"], ctx.F, u_cols)
    ls = sg.weights_slots(long_on[u_cols], m) - sg.weights_slots(-shorts[short_name][u_cols], m)
    res = run_short(ls, u_cols)
    eq = res.equity.loc[st - pd.Timedelta(days=1):]
    s = metrics.summarize(eq, btc)
    s["구성"] = "선물 한 계좌에서 롱·숏 (코어3, 롱은 펀딩비 냄)"
    s["펀딩 손익(연, 평가금 대비)"] = res.funding_pnl.loc[eq.index].sum() / max((eq.index[-1] - eq.index[0]).days / 365, 1e-9)
    out["rows"].append(s)
    out["eras"][s["구성"]] = eq
    out["short_name"] = short_name
    return out


def _era(eq: pd.Series) -> dict:
    out = {}
    for name, s, e in ERAS_SHORT:
        seg = eq.loc[s:e].dropna()
        if len(seg) >= 20:
            out[name] = seg.iloc[-1] / seg.iloc[0] - 1
    return out


def render(ctx, cfg, perf, rnd, sel, combo, curves, secs, n_fut) -> str:
    o = []
    w = o.append
    w(f"# 숏(무기한 선물) 검증 — 하락 추세에서 숏으로 벌 수 있나 (~{ctx.lock.dev_end().date()})\n")
    w("> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 선물·숏은 손실이 원금을 넘을 수 있는 구조(레버리지)이고, "
      "짧은 시간에 급등하면 강제 청산될 수 있습니다. 원금 손실 가능성이 있습니다.\n")
    w("## 0. 조건과 한계\n")
    w(f"- **데이터**: 바이낸스 USDⓈ-M 무기한 선물 일봉·펀딩비 ({n_fut}개 코인, 상장폐지 선물 포함). **바이비트 데이터가 아닙니다** — "
      "이 작업 환경에서 바이비트 접속이 막혀 있었습니다. 무기한 선물 가격은 거래소끼리 거의 같지만 펀딩비·수수료는 조금 다릅니다.")
    w(f"- **비용**: 바이비트 시장가 수수료 {BYBIT_TAKER:.3%} + 슬리피지(코어 {cfg['costs']['slippage_core']:.1%}, 기타 {cfg['costs']['slippage_other']:.1%}). "
      "펀딩비는 실제 기록대로 매일 정산 (양수면 숏이 받음).")
    w("- **판정·체결**: 추세 판정은 현물 가격(롱과 같은 기준·같은 날), 체결은 다음 날 선물 시가. 레버리지 1배(평가금만큼만 숏), "
      "격리 증거금 청산 반영(거래소처럼 표시가격 기준 — 현물 고가·저가로 근사). '2배' 열은 같은 신호를 2배 비중으로 했을 때.")
    w("- **기간**: 선물이 생긴 뒤(BTC 2019-09, ETH 2019-11, SOL 2020-09, 알트는 더 늦음) ~ 2024-12-31. 2018 겨울(가장 긴 약세장)은 선물 데이터가 없어 "
      "이 검증에 없습니다 → 약세장 표본이 2022년 한 번이 사실상 전부라는 점이 가장 큰 한계입니다.")
    w(f"- **최종 검증 구간(2025~)은 잠금 유지.** 계산 {secs / 60:.1f}분, 숏 관련 시험 {len([r for r in ctx.trials.rows if r['family'].startswith('short')])}개.\n")
    w("## 1. 숏 방식별 성과 (숏만 했을 때, 기준은 현금)\n")
    w("숏 전용 계좌의 성과입니다. 숏이 꺼져 있으면 현금(수익 0). '[위약] 항상 숏'은 아무 판단 없이 계속 숏 — 이것보다 나아야 판단이 의미 있습니다.\n")
    cols = ["방식", "시작", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "평균 숏 비중", "거래수", "거래승률",
            "거래중앙값", "최악거래", "평균 보유일", "펀딩 손익(연, 평가금 대비)", "최악의 하루", "청산 횟수", "CAGR(하루 늦게 체결)",
            "2배 레버리지 CAGR", "2배 레버리지 MDD", "2배 청산 횟수"]
    for u in perf["대상"].unique():
        w(f"### {u}\n")
        w(to_md(perf[perf["대상"] == u], cols))
        w("")
    w("### 무작위 타이밍 위약 (코어3, 같은 숏 비율·평균 보유기간의 무작위 숏 30회)\n")
    w(to_md(rnd, ["방식", "실제 샤프", "무작위 샤프 중앙값", "무작위 대비 백분위"]))
    w("")
    w("## 2. 시대별 (숏만)\n")
    pick = ["중기 하락 숏 + 장기 상승이면 중단", "중기·장기 모두 하락일 때만 숏", "돈치안20 저가 이탈 숏(10일 고가 회복 시 환매)", "[위약] 항상 숏"]
    rows = []
    for u in ("BTC", "코어3 균등", "기타 상위30 균등"):
        for n in pick:
            if (u, n) in curves:
                rows.append({"대상": u, "방식": n, **_era(curves[(u, n)])})
    et = pd.DataFrame(rows)
    w(to_md(et, ["대상", "방식"] + [e[0] for e in ERAS_SHORT if e[0] in et.columns]).replace("| - |", "| - |"))
    w("\n(숫자는 그 시대 수익률. +면 숏으로 벌었고 −면 잃음)\n")
    w("### 참고: 선물 데이터가 없는 2018 약세장 (BTC·ETH 현물 가격으로 숏했다고 가정, 펀딩 0)\n")
    w(to_md(combo["ref2018"], ["방식", "2018-03~12 (약세장)", "2019 (반등·횡보)", "MDD"]))
    w("")
    w("## 3. 알트에서 '가장 약한 코인' 숏 (BTC 200일선 아래일 때만, 주 1회)\n")
    w(to_md(sel, ["방식", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "거래수", "거래승률", "최악거래", "펀딩 손익(연, 평가금 대비)",
                  "청산 횟수", "무작위 5개 대비 CAGR 백분위"]))
    w("")
    w(f"## 4. 업비트 현물 롱 + 선물 숏 합치기 ({combo['start'].date()} ~)\n")
    w(f"현물 롱 = 지금 설정의 임시 공식(스윙 중기 중심, 매일 판단, 업비트 비용). 선물 숏 = 코어3 '{combo['short_name']}'. "
      "두 계좌 비율은 분기마다 맞추고, 옮기는 금액의 0.2%를 이체·환전 비용으로 뺐습니다(김치 프리미엄 변동은 미반영).\n")
    c = pd.DataFrame(combo["rows"])
    w(to_md(c, ["구성", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)", "펀딩 손익(연, 평가금 대비)"]))
    w("")
    rows = [{"구성": k, **_era(v)} for k, v in combo["eras"].items()]
    et = pd.DataFrame(rows)
    w(to_md(et, ["구성"] + [e[0] for e in ERAS_SHORT if e[0] in et.columns]))
    w("")
    o.extend(conclusions(perf, rnd, sel, c, et))
    return "\n".join(o)


def conclusions(perf, rnd, sel, c, et) -> list[str]:
    def g(u, n, k):
        m = perf[(perf["대상"] == u) & (perf["방식"] == n)]
        return m.iloc[0][k] if len(m) else np.nan

    def pct(v):
        return "-" if pd.isna(v) else f"{v * 100:+.0f}%"

    core = perf[(perf["대상"] == "코어3 균등") & ~perf["방식"].str.startswith("[")]
    best = core.sort_values("샤프", ascending=False).iloc[0]
    n_pos = int((perf[~perf["방식"].str.startswith("[")]["연복리(CAGR)"] > 0).sum())
    n_all = int((~perf["방식"].str.startswith("[")).sum())
    base, s20 = c.iloc[0], c.iloc[1]
    one = c[c["구성"].str.startswith("선물 한 계좌")].iloc[0]
    o = ["## 5. 결론\n",
         f"**짧게 답하면: 2020~2024년에는 추세 숏으로 돈을 벌지 못했습니다.** 숏 방식 {n_all}개(대상별) 중 연복리가 플러스인 것은 {n_pos}개였습니다.\n",
         f"- **코어(BTC·ETH·SOL)**: 가장 나았던 '{best['방식']}'도 연복리 {pct(best['연복리(CAGR)'])}, MDD {pct(best['MDD'])}. "
         "숏은 2022년(특히 LUNA 붕괴 때 +70~90%)에는 벌었지만, 강세장·회복장에서 추세가 잠깐 '하락'으로 바뀔 때마다 숏에 들어갔다가 반등에 손절하는 일이 반복됐습니다. "
         "2022년 안에서도 1~3월 반등에 숏 4번이 연속 손실이었습니다.",
         "- **왜 롱은 되고 숏은 안 되나**: 이 기간 BTC가 약 13배 올랐고, 코인의 하락장 반등은 빠르고 큽니다(숏 스퀴즈). 추세 롱은 '큰 상승을 오래 타서' 잦은 작은 손실을 메우는데, "
         "숏은 큰 하락이 짧고(몇 주) 반등이 거세서 그 비대칭이 반대로 작용합니다.",
         "- **선물이 없던 2018 약세장(현물 가격 가정)**에서는 중기 하락 숏이 2018년 3~12월 약 +68%였지만 2019년에 −18~−22%를 돌려줬습니다(2절 참고 표). "
         "긴 약세장이 오면 숏이 벌 수 있다는 근거는 이 한 번뿐입니다.",
         f"- **알트 '가장 약한 코인' 숏**: 연복리 {pct(sel.iloc[0]['연복리(CAGR)'])}로, 무작위 5개 숏(중앙값 {pct(sel.iloc[-1]['연복리(CAGR)'])})보다도 나빴습니다. "
         "가장 많이 빠진 알트가 오히려 크게 반등하는 경우가 많았습니다.",
         f"- **업비트 롱 + 선물 숏**: 롱만 {pct(base['연복리(CAGR)'])} / MDD {pct(base['MDD'])} → 숏 20%를 더하면 {pct(s20['연복리(CAGR)'])} / {pct(s20['MDD'])}. "
         "손실 폭은 조금 줄지만 수익이 더 크게 줄었습니다. 2022년만 보면 숏이 롱 손실을 메웠지만(4절 시대별), 나머지 기간에 그 이상을 잃었습니다.",
         f"- **선물 한 계좌에서 롱까지**: 롱을 선물로 들면 펀딩비를 계속 내야 해서(이 기간 연 {pct(one['펀딩 손익(연, 평가금 대비)'])} 수준) "
         f"연복리 {pct(one['연복리(CAGR)'])}, MDD {pct(one['MDD'])}로 크게 나빴습니다. **롱은 업비트 현물이 유리합니다.**",
         "- **레버리지 2배**는 모든 숏 방식에서 손실을 키웠고(1절 2배 열), 알트 숏은 2배에서 청산이 수십 번 났습니다.\n",
         "**제안**",
         "- 지금 데이터로는 숏을 공식에 넣는 걸 권하지 않습니다. 두 기간 모두 좋았던 숏 방식이 없었습니다: '중기 하락 숏'은 2018 약세장에 +68%였지만 2020~24년엔 연 −18~−28%, "
         "가장 보수적인 '중기·장기 모두 하락일 때만 숏'은 2020~24년 손실이 가장 작은 편(연 −18~−22%)이었지만 2018 약세장에서도 +3%에 그쳤습니다.",
         "- 그래도 쓰고 싶다면 '긴 약세장 보험' 용도로 BTC·ETH만, 1배, 전체 자금의 10~20% 이하로, 처음 몇 달은 모의 주문으로만 확인하세요. 기대 수익은 마이너스일 수 있다는 걸 전제로요.",
         "- 숏보다 데이터가 지지하는 건 '하락 추세에서는 현금으로 쉬기'입니다(롱 공식이 이미 그렇게 함). 2022 약세장(2021-11~2022-12) 롱 공식은 −20%로, 같은 기간 BTC 보유(−75%)보다 손실이 훨씬 작았습니다.",
         "- 국내 거주자의 해외(국내 미신고) 거래소 이용·선물 거래에는 규제와 세금 문제가 있을 수 있으니, 사용 전에 따로 확인하셔야 합니다.",
         ]
    return o
