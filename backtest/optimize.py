"""규칙 조정과 검증: 기간을 앞(학습)·뒤(검증)로 나눠서
앞 기간에서만 규칙 조합을 고르고, 고른 규칙을 손대지 않은 뒤 기간에서 평가해요.
같은 데이터로 고르고 평가하면 결과가 부풀려지기 때문이에요 (과최적화).

실행: python optimize.py   → results/optimize.csv, results/validation.txt
"""

import argparse
import copy
import itertools
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest import Backtest, buy_and_hold, metrics  # noqa: E402
from data import load_all, market_of  # noqa: E402
from indicators import add_indicators  # noqa: E402
from main import load_config  # noqa: E402
from strategy import add_signals  # noqa: E402

# 고를 후보. 지표 기간은 건드리지 않고, 진입·청산 방식만 바꿔요.
GRID = {
    "entry.cross_lookback": [1, 3, 5],
    "entry.volume_filter": [True, False],
    "entry.adx_min": [20, 25],
    "exit.macd_dead_cross": [True, False],
    "exit.ema_exit": [False, True],
    "exit.trail_atr": [3.0, 4.0],
}
MIN_TRADES = 10


def with_params(cfg, params):
    c = copy.deepcopy(cfg)
    for key, v in params.items():
        sect, name = key.split(".")
        c[sect][name] = v
    return c


def evaluate(cfg, ind, start, end):
    """시장별 포트폴리오 성과. ind: 지표까지 붙은 {symbol: DataFrame}."""
    out = {}
    for market in ("KR", "US"):
        mf = {s: add_signals(f, cfg["entry"], cfg["exit"]) for s, f in ind.items() if market_of(s) == market}
        if not mf:
            continue
        eq, tr = Backtest(mf, market, cfg, start, end).run(cfg["capital"][market])
        m = metrics(eq["equity"], tr)
        m["평균투자비중"] = ((eq["equity"] - eq["cash"]) / eq["equity"]).mean()
        out[market] = m
    return out


def score(res):
    """두 시장 샤프지수 평균. 거래가 너무 적으면 운으로 보고 제외해요."""
    if any(m.get("거래횟수", 0) < MIN_TRADES for m in res.values()):
        return float("-inf")
    return sum(m["샤프지수"] for m in res.values()) / len(res)


def bh(ind, cfg, start, end):
    out = {}
    for market in ("KR", "US"):
        mf = {s: f for s, f in ind.items() if market_of(s) == market}
        if mf:
            out[market] = metrics(buy_and_hold(mf, market, cfg, start, end, cfg["capital"][market]))
    return out


def row(label, res):
    r = {"규칙": label}
    for market, m in res.items():
        for k in ("총수익률", "CAGR", "MDD", "샤프지수", "거래횟수", "승률", "평균투자비중"):
            if k in m:
                r[f"{market} {k}"] = m[k]
    return r


def fmt(df):
    shown = df.copy()
    for c in shown.columns:
        if any(k in c for k in ("수익률", "CAGR", "MDD", "승률", "비중")):
            shown[c] = shown[c].map(lambda v: "" if pd.isna(v) else f"{v * 100:.1f}%")
        elif "샤프" in c:
            shown[c] = shown[c].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
        elif "거래" in c:
            shown[c] = shown[c].map(lambda v: "" if pd.isna(v) else f"{int(v)}")
    return shown.to_string(index=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    val = cfg.get("validation", {})
    out_dir = os.path.join(HERE, cfg["report"].get("output_dir", "results"))
    cache = os.path.join(HERE, cfg["report"]["cache_dir"]) if cfg["report"].get("cache_dir") else None

    symbols = list(dict.fromkeys([str(s) for s in cfg["symbols"] + val.get("extra_symbols", [])]))
    print(f"데이터 받는 중... ({len(symbols)}종목)")
    raw, start, end = load_all(symbols, cfg["period"], cache)
    ind = {s: add_indicators(f, cfg["indicators"]) for s, f in raw.items()}
    split = start + (end - start) * float(val.get("train_ratio", 0.6))
    print(f"학습 기간: {start:%Y-%m-%d} ~ {split:%Y-%m-%d} / 검증 기간: {split:%Y-%m-%d} ~ {end:%Y-%m-%d}")

    keys = list(GRID)
    results = []
    for values in itertools.product(*GRID.values()):
        params = dict(zip(keys, values))
        res = evaluate(with_params(cfg, params), ind, start, split)
        results.append({**params, "score": score(res), **row("", res)})
    table = pd.DataFrame(results).drop(columns=["규칙"]).sort_values("score", ascending=False)
    os.makedirs(out_dir, exist_ok=True)
    table.to_csv(os.path.join(out_dir, "optimize.csv"), index=False, encoding="utf-8-sig")

    spec = {k: (cfg[k.split(".")[0]].get(k.split(".")[1])) for k in keys}
    spec.update({"entry.cross_lookback": 1, "entry.volume_filter": True, "entry.adx_min": 25,
                 "exit.macd_dead_cross": True, "exit.ema_exit": False, "exit.trail_atr": 3.0})
    best = {k: table.iloc[0][k] for k in keys}
    best = {k: (v.item() if hasattr(v, "item") else v) for k, v in best.items()}

    lines = [f"학습 기간 {start:%Y-%m-%d} ~ {split:%Y-%m-%d}, 검증 기간 {split:%Y-%m-%d} ~ {end:%Y-%m-%d}",
             f"종목 {len(raw)}개 (국내 {sum(market_of(s) == 'KR' for s in raw)}, 해외 {sum(market_of(s) == 'US' for s in raw)})",
             f"후보 {len(table)}개 중 학습 기간 점수(두 시장 샤프 평균, 시장별 거래 {MIN_TRADES}건 이상) 1위:",
             "  " + ", ".join(f"{k}={v}" for k, v in best.items()), "",
             "학습 기간 상위 5개:", fmt(table.head(5)), ""]
    for label, (s, e) in {"학습 기간": (start, split), "검증 기간 (고를 때 안 본 데이터)": (split, end),
                          "전체 기간": (start, end)}.items():
        rows = [row("요청 규칙", evaluate(with_params(cfg, spec), ind, s, e)),
                row("조정 규칙", evaluate(with_params(cfg, best), ind, s, e)),
                row("매수 후 보유", bh(ind, cfg, s, e))]
        lines += [f"[{label}]", fmt(pd.DataFrame(rows)), ""]
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out_dir, "validation.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
