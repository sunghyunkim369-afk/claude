"""실행: python main.py [--config config.yaml] [--no-pandas-ta]

국내(원화)와 해외(달러)는 따로 운용·계산해요 (환율 환산 없음).
결과는 config 의 report.output_dir (기본 results/) 에 저장돼요.
"""

import argparse
import os
import sys

import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest import Backtest, buy_and_hold, metrics  # noqa: E402
from data import load_all, market_of  # noqa: E402
from indicators import add_indicators, engine_name  # noqa: E402
from report import equity_chart, fmt_table, save, symbol_grid  # noqa: E402
from strategy import add_signals  # noqa: E402

CURRENCY = {"KR": "KRW", "US": "USD"}


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def prepare(raw, cfg, use_pandas_ta=True):
    return {s: add_signals(add_indicators(df, cfg["indicators"], use_pandas_ta), cfg["entry"], cfg["exit"])
            for s, df in raw.items()}


def exposure(eq):
    return ((eq["equity"] - eq["cash"]) / eq["equity"]).mean()


def run(cfg, frames, start, end, log=print):
    """frames: 지표·신호가 붙은 {symbol: DataFrame}. 결과 dict 를 돌려줘요."""
    rf = cfg["report"].get("risk_free", 0.0)
    rows, all_trades, equities, curves, grids = [], [], {}, {}, {}
    for market in ("KR", "US"):
        mf = {s: f for s, f in frames.items() if market_of(s) == market}
        if not mf:
            continue
        capital = cfg["capital"][market]

        # 포트폴리오 (최대 보유 종목 수 적용)
        eq, trades = Backtest(mf, market, cfg, start, end).run(capital)
        bh = buy_and_hold(mf, market, cfg, start, end, capital)
        m, mb = metrics(eq["equity"], trades, rf), metrics(bh, None, rf)
        rows.append({"구분": f"{market} 포트폴리오", **m, "평균투자비중": exposure(eq),
                     **{f"B&H {k}": v for k, v in mb.items()}})
        all_trades.append(trades)
        equities[market] = eq
        curves[market] = {"Strategy": eq["equity"], "Buy & Hold (equal weight)": bh}

        # 종목별: 같은 규칙으로 그 종목 하나만 운용
        for s, f in mf.items():
            e1, t1 = Backtest({s: f}, market, cfg, start, end).run(capital)
            b1 = buy_and_hold({s: f}, market, cfg, start, end, capital)
            m1, mb1 = metrics(e1["equity"], t1, rf), metrics(b1, None, rf)
            rows.append({"구분": f"  {s}", **m1, "평균투자비중": exposure(e1),
                         **{f"B&H {k}": v for k, v in mb1.items()}})
            grids[s] = (e1["equity"], b1)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return {"rows": rows, "trades": trades, "equities": equities, "curves": curves, "grids": grids}


def main(argv=None):
    ap = argparse.ArgumentParser(description="국내·해외 주식 추세추종 백테스트")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--no-pandas-ta", action="store_true", help="pandas-ta 대신 내장 지표 계산 사용")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    out_dir = os.path.join(HERE, cfg["report"].get("output_dir", "results"))
    cache = cfg["report"].get("cache_dir")
    cache = os.path.join(HERE, cache) if cache else None

    print("데이터 받는 중...")
    raw, start, end = load_all(cfg["symbols"], cfg["period"], cache)
    if not raw:
        sys.exit("받은 데이터가 없어요. 인터넷 연결과 종목코드를 확인하세요.")
    use_ta = not args.no_pandas_ta
    print(f"지표 계산: {engine_name(use_ta)}")
    frames = prepare(raw, cfg, use_ta)

    res = run(cfg, frames, start, end)
    summary, text = fmt_table(res["rows"])
    print(f"\n기간: {start:%Y-%m-%d} ~ {end:%Y-%m-%d}  (국내는 원화, 해외는 달러 기준 따로 계산)")
    print("종목별 행은 그 종목 하나만 같은 규칙으로 운용한 결과예요. 평균투자비중이 낮으면 B&H(100% 투자)보다 수익률이 낮게 나오는 게 자연스러워요.\n")
    print(text)

    save(out_dir, summary, res["trades"], res["equities"])
    for market, curves in res["curves"].items():
        equity_chart(os.path.join(out_dir, f"equity_{market}.png"),
                     f"{market} portfolio ({start:%Y-%m-%d} ~ {end:%Y-%m-%d})", curves, CURRENCY[market])
    symbol_grid(os.path.join(out_dir, "equity_by_symbol.png"), res["grids"])
    print(f"\n저장: {out_dir}/ (summary.csv, trades.csv, equity_*.csv, equity_*.png)")


if __name__ == "__main__":
    main()
