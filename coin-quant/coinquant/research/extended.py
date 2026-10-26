"""확정 공식을 2018년 이전까지 넓혀 검증 + '확률 높을 때만 사기' 변형 — `python -m coinquant extended`

2018년 이전 가격: Coin Metrics 일별 종가 (BTC 2010-07~, ETH 2015-08~). 바이낸스(2017-08-17~)와 같은 날 종가 차이 중앙값 0.3%.
고가·저가가 없어서 그 구간은 고가 = 저가 = 종가, 시가 = 전날 종가로 둠 (채널 계산만 영향, 영향 크기는 2018~ 구간에서 따로 측정).
2013~2017 은 공식을 만들 때 한 번도 보지 않은 구간이라 '새 구간' 검증이 됨. 2011~12 는 거래량이 아주 작아 참고만.
'확률 높을 때만 사기' 변형은 결과를 보기 전에 2개만 정함:
  B. 중기 점수 +2 이상 & 장기 +3 일 때만 새로 삼 (파는 건 확정 공식과 같음)
  C. 단기·중기·장기 모두 +2 이상일 때만 새로 삼 (파는 건 같음)
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..backtest import engine, metrics
from ..trend import MultiTrend
from ..universe import Panel
from .short_swing import swing_sim

EVAL_START = "2011-03-01"
ERAS = [("2011~12 초기 (참고: 거래량 아주 작음)", "2011-03-01", "2012-12-31"),
        ("2013 버블·급락", "2013-01-01", "2013-12-31"),
        ("2014~15 Mt.Gox 붕괴·약세", "2014-01-01", "2015-12-31"),
        ("2016 반감기 전후", "2016-01-01", "2016-12-31"),
        ("2017 버블", "2017-01-01", "2017-12-31"),
        ("★ 새 구간 2013~2017 전체", "2013-01-01", "2017-12-31"),
        ("개발 구간 2018-03~2024", "2018-03-01", "2024-12-31"),
        ("최종 검증 2025~ (이미 본 구간)", "2025-01-01", None),
        ("전체 2013~현재", "2013-01-01", None)]
FWD_PERIODS = [("새 구간 2013~2017", "2013-01-01", "2017-12-31"), ("개발 2018-03~2024", "2018-03-01", "2024-12-31"),
               ("이미 본 2025~", "2025-01-01", None)]


def spliced(cache: Path, assets=("BTC", "ETH", "SOL")) -> dict[str, pd.DataFrame]:
    """바이낸스 이전은 Coin Metrics 종가로 이어 붙인 시가·고가·저가·종가. src: 'cm' 또는 'binance'."""
    p = Panel.load(cache / "panel")
    out = {k: {} for k in ("open", "high", "low", "close", "src")}
    for a in assets:
        b = pd.DataFrame({"open": p.open[a], "high": p.high[a], "low": p.low[a], "close": p.close[a]}).dropna()
        f = cache / "coinmetrics" / f"{a.lower()}.csv"
        if f.exists():
            cm = pd.read_csv(f, parse_dates=["date"]).set_index("date")["PriceUSD"].dropna()
            cm = cm[cm.index < b.index[0]]
            pre = pd.DataFrame({"close": cm, "high": cm, "low": cm})
            pre["open"] = cm.shift(1)
            d = pd.concat([pre, b])
        else:
            d = b
        d["open"] = d["open"].fillna(d["close"])
        for k in ("open", "high", "low", "close"):
            out[k][a] = d[k]
        out["src"][a] = pd.Series(np.where(d.index < b.index[0], "cm", "binance"), index=d.index)
    return {k: pd.DataFrame(v).sort_index() for k, v in out.items()}


def positions(mt: MultiTrend, close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    base = M.state & ~(L.score <= -2)
    entry_b = (M.score >= 2) & (L.score >= 3)
    entry_c = (S.score >= 2) & (M.score >= 2) & (L.score >= 2)
    return {"A. 확정 공식": base.astype(float),
            "B. 고확률만 (중기 +2↑ & 장기 +3)": swing_sim(base & entry_b, ~base, close),
            "C. 아주 엄격 (단기·중기·장기 모두 +2↑)": swing_sim(base & entry_c, ~base, close)}


def run(pos: pd.DataFrame, D: dict, cols: list[str], cost: float) -> engine.Result:
    alive = D["close"][cols].notna()
    n = alive.sum(axis=1).replace(0, np.nan)
    w = pos[cols].fillna(0.0).where(alive, 0.0).div(n, axis=0).fillna(0.0)
    costs = engine.Costs(fee=cost, slip_core=0.0, slip_other=0.0)
    return engine.run(w, D["open"][cols], D["close"][cols], costs, engine.Rules(rebalance_days=1), start=EVAL_START)


def era_rows(name: str, u: str, res: engine.Result, bench: pd.Series) -> list[dict]:
    rows = []
    for en, a, z in ERAS:
        eq = res.equity.loc[a:z]
        b = bench.loc[a:z]
        if len(eq) < 30:
            continue
        t = res.trades[(res.trades["entry"] >= pd.Timestamp(a)) & ((res.trades["entry"] <= pd.Timestamp(z)) if z else True)]
        rows.append({"대상": u, "방식": name, "구간": en, "총수익": eq.iloc[-1] / eq.iloc[0] - 1, "연복리": metrics.cagr(eq),
                     "MDD": metrics.max_drawdown(eq), "BTC 보유 총수익": b.iloc[-1] / b.iloc[0] - 1, "BTC 보유 MDD": metrics.max_drawdown(b),
                     "투자 비중": res.weights.loc[a:z].sum(axis=1).mean(), "거래수": len(t),
                     "거래 승률": (t["ret"] > 0).mean() if len(t) else np.nan,
                     "거래 중앙값": t["ret"].median() if len(t) else np.nan, "최악 거래": t["ret"].min() if len(t) else np.nan})
    return rows


def forward_table(mt: MultiTrend, close: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """상태별 이후 20·60일 수익 (그날 종가 기준). '지금 BTC 상태'가 과거에 어땠는지."""
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    base = M.state & ~(L.score <= -2)
    conds = {
        "모든 날 (아무 조건 없음)": close.notna(),
        "공식 보유 중": base,
        "공식 보유 중 & 중기 점수 +1 이하 (힘 빠짐)": base & (M.score <= 1),
        "★ 지금 BTC 상태: 보유 중 & 중기 +1 이하 & 단기 −2 이하 & 장기 +3": base & (M.score <= 1) & (S.score <= -2) & (L.score >= 3),
        "B 조건: 중기 +2↑ & 장기 +3": base & (M.score >= 2) & (L.score >= 3),
        "C 조건: 단기·중기·장기 모두 +2↑": base & (S.score >= 2) & (M.score >= 2) & (L.score >= 2),
    }
    rows = []
    for h in (20, 60):
        f = close[cols].shift(-h) / close[cols] - 1
        for cn, cond in conds.items():
            e = cond[cols].fillna(False).astype(bool)
            for pn, a, z in FWD_PERIODS:
                v = f.where(e).loc[a:z].stack().dropna()
                ee = e.loc[a:z]
                runs = int(((ee & ~ee.shift(1, fill_value=False)).sum()).sum())   # 조건이 새로 켜진 횟수
                rows.append({"기간(일)": h, "상태": cn, "구간": pn, "사례(날)": len(v), "따로 떨어진 구간 수": runs,
                             "중앙값": v.median() if len(v) else np.nan, "하위10%": v.quantile(0.1) if len(v) else np.nan,
                             "오른 비율": (v > 0).mean() if len(v) else np.nan})
    return pd.DataFrame(rows)


def hl_check(D: dict, cache: Path) -> pd.DataFrame:
    """고가·저가 대신 종가만 썼을 때 신호가 얼마나 달라지나 (바이낸스 구간 2018-03~2024)."""
    c = D["close"][["BTC", "ETH"]].loc["2017-08-17":]
    real = MultiTrend(c, D["high"][["BTC", "ETH"]].loc["2017-08-17":], D["low"][["BTC", "ETH"]].loc["2017-08-17":])
    conly = MultiTrend(c, c, c)
    rows = []
    for a in ("BTC", "ETH"):
        s1 = positions(real, c)["A. 확정 공식"][a].loc["2018-03-01":"2024-12-31"]
        s2 = positions(conly, c)["A. 확정 공식"][a].loc["2018-03-01":"2024-12-31"]
        rows.append({"코인": a, "같은 신호인 날": (s1 == s2).mean(), "보유 비중(고가·저가)": s1.mean(), "보유 비중(종가만)": s2.mean()})
    return pd.DataFrame(rows)


def fmt(df: pd.DataFrame, pct: set) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in pct:
                cells.append("-" if pd.isna(v) else f"{v * 100:+.1f}%" if c not in ("투자 비중", "거래 승률", "오른 비율", "같은 신호인 날",
                                                                                 "보유 비중(고가·저가)", "보유 비중(종가만)") else f"{v * 100:.0f}%")
            elif isinstance(v, (float, np.floating)):
                cells.append("-" if pd.isna(v) else f"{v:,.0f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


PCT = {"총수익", "연복리", "MDD", "BTC 보유 총수익", "BTC 보유 MDD", "투자 비중", "거래 승률", "거래 중앙값", "최악 거래",
       "중앙값", "하위10%", "오른 비율", "같은 신호인 날", "보유 비중(고가·저가)", "보유 비중(종가만)"}


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    D = spliced(cache)
    c = D["close"]
    mt = MultiTrend(c, D["high"], D["low"])
    pos = positions(mt, c)
    bench = run(pd.DataFrame(1.0, index=c.index, columns=["BTC"]), D, ["BTC"], 0.0015).equity
    fee = cfg["costs"]["fee"] + cfg["costs"]["slippage_core"]
    rows, stress = [], []
    for u, cols in (("BTC", ["BTC"]), ("BTC·ETH 균등 (ETH는 2016-03~)", ["BTC", "ETH"]), ("코어3 균등 (SOL은 2020-08~)", ["BTC", "ETH", "SOL"])):
        for name, p_ in pos.items():
            rows += era_rows(name, u, run(p_, D, cols, fee), bench)
            if u == "BTC":
                stress += era_rows(name, u, run(p_, D, cols, 0.005), bench)
    perf, stress = pd.DataFrame(rows), pd.DataFrame(stress)
    fwd = pd.concat([forward_table(mt, c, ["BTC"]).assign(대상="BTC"),
                     forward_table(mt, c, ["BTC", "ETH", "SOL"]).assign(대상="코어3 (있는 코인만)")])
    hl = hl_check(D, cache)
    now = {k: {h: int(mt.h[h].score[k].iloc[-1]) for h in ("단기", "중기", "장기")} | {"보유": bool(pos["A. 확정 공식"][k].iloc[-1])}
           for k in ("BTC", "ETH", "SOL")}
    out = config_mod.ROOT / "reports" / "research"
    out.mkdir(parents=True, exist_ok=True)
    perf.to_csv(out / "extended_perf.csv", index=False)
    stress.to_csv(out / "extended_stress.csv", index=False)
    fwd.to_csv(out / "extended_forward.csv", index=False)
    md = render(perf, stress, fwd, hl, now, D, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"extended-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def render(perf, stress, fwd, hl, now, D, secs) -> str:
    last = D["close"].index[-1].date()
    first = {a: D["close"][a].first_valid_index().date() for a in ("BTC", "ETH", "SOL")}
    keep = ["방식", "구간", "총수익", "MDD", "BTC 보유 총수익", "BTC 보유 MDD", "투자 비중", "거래수", "거래 승률", "거래 중앙값", "최악 거래"]
    o = [f"# 확정 공식 2018년 이전까지 넓힌 검증 + '확률 높을 때만 사기' ({last}까지)\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 데이터와 조건\n",
         f"- 가격 시작: BTC {first['BTC']}, ETH {first['ETH']}, SOL {first['SOL']}. 2017-08-17 이전은 Coin Metrics 일별 종가 "
         "(바이낸스와 같은 날 종가 차이 중앙값 0.3%), 이후는 바이낸스.",
         "- 2017-08 이전엔 고가·저가가 없어 고가 = 저가 = 종가로 계산 (채널 위치만 영향). 아래 표: 2018~2024 바이낸스 구간에서 두 방식 신호 비교.\n",
         fmt(hl, PCT), "",
         "- 체결: 판단은 그날 종가, 체결은 다음 날 시가(2017-08 이전은 전날 종가 = 다음 날 시가로 봄). 비용 매매마다 0.15%. "
         "2018년 이전 거래소 수수료·호가 차이가 더 컸으므로 BTC는 0.5% 비용으로도 계산 (아래 3절).",
         "- 공식·파라미터는 바꾸지 않음. B·C 변형은 결과를 보기 전에 2개만 정함 (파는 규칙은 확정 공식과 같고, 새로 사는 조건만 엄격).",
         "- 2025~ 는 이미 한 번 연 최종 검증 구간이라 참고만. 2013~2017 이 공식을 만들 때 안 본 '새 구간'입니다.",
         f"- 계산 {secs:.0f}초\n",
         "## 1. 지금 상태 (어제 종가 기준 점수)\n",
         "| 코인 | 단기 | 중기 | 장기 | 확정 공식 보유 |", "|---|---|---|---|---|"]
    for k, v in now.items():
        o.append(f"| {k} | {v['단기']:+d} | {v['중기']:+d} | {v['장기']:+d} | {'예' if v['보유'] else '아니오'} |")
    o += ["", "## 2. 구간별 성과 (비용 0.15%)\n"]
    for u in perf["대상"].unique():
        o += [f"### {u}\n", fmt(perf[perf["대상"] == u][keep], PCT), ""]
    o += ["## 3. BTC 비용 0.5% (2018년 이전 거래소 수준 가정)\n", fmt(stress[keep], PCT), ""]
    o += ["## 4. 상태별 이후 수익 — '지금 사도 되나'\n",
          "그날 종가에 샀다고 보고 20·60일 뒤 수익. '따로 떨어진 구간 수' = 그 상태가 새로 시작된 횟수 (같은 구간의 날들은 서로 겹쳐 독립이 아님).\n"]
    for u in fwd["대상"].unique():
        for h in (20, 60):
            d = fwd[(fwd["대상"] == u) & (fwd["기간(일)"] == h)].drop(columns=["대상", "기간(일)"])
            o += [f"### {u} — 이후 {h}일\n", fmt(d, PCT), ""]
    return "\n".join(o)
