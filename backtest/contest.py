"""대회용 공식 찾기: 짧은 기간(기본 5주) 수익률로 순위를 매기는 대회에 맞춘 모멘텀 순환매.

공식 (매주 첫 거래일 종가에 판단 → 다음 날 시가 체결):
  1. 후보: 종가 > EMA20 > EMA60 (상승 추세), RSI14 < rsi_max (과열 제외)
  2. 점수: 최근 lookback 일 수익률. 높은 순으로 top_k 종목
  3. 목표 종목에서 빠진 보유 종목은 팔고, 새로 들어온 종목은 (총자산 / top_k) 만큼 삼
  4. 보유 중 진입가 − stop_atr × ATR 아래로 내려가면 장중 손절 (stop_atr 가 0 이면 안 씀)

검증: 과거 5년에서 '5주 구간'을 5거래일 간격으로 잘라, 구간마다 처음부터 새로 운용해 수익률 분포를 봐요.
앞 60% 구간에서 조합을 고르고, 뒤 40% 구간(고를 때 안 본 기간)에서 다시 확인해요.

실행: python contest.py  → results/contest.csv, results/contest.txt
"""

import argparse
import itertools
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from broker import Order, SimulatedBroker  # noqa: E402
from data import load_all, market_of  # noqa: E402
from indicators import add_indicators  # noqa: E402
from main import load_config  # noqa: E402

GRID = {
    "lookback": [20, 60],
    "top_k": [3, 5],
    "stop_atr": [0.0, 2.0],
    "rsi_max": [80, 100],
}


def prepare(raw, cfg, lookbacks):
    out = {}
    for s, f in raw.items():
        d = add_indicators(f, cfg["indicators"])
        for n in lookbacks:
            d[f"ret_{n}"] = d["Close"] / d["Close"].shift(n) - 1
        out[s] = d
    return out


def is_rebalance_day(dates):
    """주의 첫 거래일."""
    s = pd.Series(dates)
    week = s.dt.isocalendar().week.values * 10000 + s.dt.isocalendar().year.values
    return pd.Series(np.r_[True, week[1:] != week[:-1]], index=dates)


class Rotation:
    def __init__(self, frames, market, cfg, params):
        self.frames = frames
        self.market = market
        self.p = params
        self.broker = SimulatedBroker(cfg["costs"], cfg["costs"]["slippage"])
        self.dates = sorted({d for f in frames.values() for d in f.index})
        self.rebalance = is_rebalance_day(self.dates)
        # 날짜별 막대를 미리 만들어 둬요 (빠르게)
        self.bars = {}
        for s, f in frames.items():
            for d, row in zip(f.index, f.to_dict("records")):
                self.bars.setdefault(d, {})[s] = row

    def targets(self, d):
        p = self.p
        cands = []
        for s, b in self.bars.get(d, {}).items():
            score = b.get(f"ret_{p['lookback']}")
            if score is None or np.isnan(score) or np.isnan(b["ema_slow"]):
                continue
            if b["Close"] > b["ema_fast"] > b["ema_slow"] and b["rsi"] < p["rsi_max"]:
                cands.append((score, s))
        cands.sort(reverse=True)
        return [s for _, s in cands[:p["top_k"]]]

    def run(self, start, end, capital):
        days = [d for d in self.dates if start <= d <= end]
        cash, pos, pending, fills, curve = float(capital), {}, None, 0, []
        for i, d in enumerate(days):
            bars = self.bars[d]
            # 시가: 지난 판단 실행 (매도 먼저)
            if pending is not None:
                target, budget = pending
                for s in [s for s in pos if s not in target]:
                    if s in bars:
                        f = self.broker.submit(Order(s, self.market, "sell", pos[s]["qty"]), bars[s], d)
                        cash += f.gross - f.fee
                        fills += 1
                        del pos[s]
                for s in target:
                    if s in pos or s not in bars or len(pos) >= self.p["top_k"]:
                        continue
                    est = bars[s]["Open"]
                    qty = int(min(budget, cash) // (est * 1.01))
                    f = self.broker.submit(Order(s, self.market, "buy", qty), bars[s], d, cash=cash) if qty > 0 else None
                    if f:
                        cash -= f.gross + f.fee
                        fills += 1
                        pos[s] = {"qty": f.qty, "stop": f.price - self.p["stop_atr"] * (bars[s]["atr"] or 0)}
                pending = None
            # 장중 손절
            if self.p["stop_atr"] > 0:
                for s in list(pos):
                    if s in bars:
                        f = self.broker.submit(Order(s, self.market, "sell", pos[s]["qty"], kind="stop",
                                                     stop_price=pos[s]["stop"]), bars[s], d)
                        if f:
                            cash += f.gross - f.fee
                            fills += 1
                            del pos[s]
            value = cash + sum(p["qty"] * self._close(s, d) for s, p in pos.items())
            curve.append(value)
            # 종가: 주 첫 거래일이면(또는 운용 첫날) 다음 날 시가에 낼 주문 결정
            if i < len(days) - 1 and (self.rebalance[d] or i == 0):
                pending = (self.targets(d), value / self.p["top_k"])
        # 마지막 날 종가에 정리한 것으로 계산 (매도 비용 포함)
        c = self.broker.costs[self.market]
        sell_rate = self.broker.slippage + c["sell_fee"] + c.get("sell_tax", 0)
        held = sum(p["qty"] * self._close(s, days[-1]) for s, p in pos.items())
        curve[-1] -= held * sell_rate
        return pd.Series(curve, index=days), fills

    def _close(self, s, d):
        b = self.bars[d].get(s)
        return b["Close"] if b else self.frames[s].loc[:d, "Close"].iloc[-1]


def equal_weight(frames, market, cfg, start, end, capital):
    """비교 기준: 같은 종목 전체를 첫날 똑같이 사서 보유."""
    from backtest import buy_and_hold
    return buy_and_hold(frames, market, cfg, start, end, capital)


def windows(dates, length, step):
    return [(dates[i], dates[i + length - 1]) for i in range(0, len(dates) - length + 1, step)]


def summarize(rets, bench, fills):
    r = np.array(rets)
    b = np.array(bench)
    return {"평균": r.mean(), "중앙값": np.median(r), "하위25%": np.percentile(r, 25), "최악": r.min(),
            "최고": r.max(), "기준초과확률": (r > b).mean(), "기준평균": b.mean(), "평균체결": np.mean(fills),
            # 대회 수상 조건: OpenAPI 체결 5회 이상 (분할 체결은 주문 1회). 여기선 주문 1건 = 1회
            "5회미만비율": (np.array(fills) < 5).mean(),
            "구간수": len(r)}


def fmt(df):
    shown = df.copy()
    for c in shown.columns:
        if c in ("평균", "중앙값", "하위25%", "최악", "최고", "기준초과확률", "기준평균", "5회미만비율"):
            shown[c] = shown[c].map(lambda v: f"{v * 100:.1f}%")
        elif c == "평균체결":
            shown[c] = shown[c].map(lambda v: f"{v:.1f}")
    return shown.to_string(index=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--days", type=int, default=26, help="대회 길이(거래일). 10/15~11/20 은 약 26일")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    val = cfg.get("validation", {})
    out_dir = os.path.join(HERE, cfg["report"].get("output_dir", "results"))
    cache = os.path.join(HERE, cfg["report"]["cache_dir"]) if cfg["report"].get("cache_dir") else None

    symbols = list(dict.fromkeys([str(s) for s in cfg["symbols"] + val.get("extra_symbols", [])]))
    raw, start, end = load_all(symbols, cfg["period"], cache, log=lambda *_: None)
    frames = prepare(raw, cfg, GRID["lookback"])
    ratio = float(val.get("train_ratio", 0.6))

    rows, lines = [], [f"대회 길이 {args.days}거래일, 5거래일 간격으로 자른 구간마다 새로 운용", ""]
    best_by_market = {}
    for market in ("KR", "US"):
        mf = {s: f for s, f in frames.items() if market_of(s) == market}
        if not mf:
            continue
        cap = cfg["capital"][market]
        dates = [d for d in sorted({d for f in mf.values() for d in f.index}) if start <= d <= end]
        wins = windows(dates, args.days, 5)
        cut = int(len(wins) * ratio)
        bench = [equal_weight(mf, market, cfg, a, b, cap) for a, b in wins]
        bench = [s.iloc[-1] / cap - 1 for s in bench]
        for values in itertools.product(*GRID.values()):
            params = dict(zip(GRID, values))
            rot = Rotation(mf, market, cfg, params)
            rets, fills = [], []
            for a, b in wins:
                curve, n = rot.run(a, b, cap)
                rets.append(curve.iloc[-1] / cap - 1)
                fills.append(n)
            for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
                rows.append({"시장": market, "구분": part, **params,
                             **summarize(rets[sl], bench[sl], fills[sl])})
        df = pd.DataFrame(rows)
        train = df[(df["시장"] == market) & (df["구분"] == "학습")]
        # 고르는 기준: 중앙값(평균보다 운 좋은 몇 구간에 덜 휘둘려요).
        # 수상 조건(체결 5회)을 못 채우는 구간이 10% 넘는 조합은 빼요.
        ok = train[train["5회미만비율"] <= 0.1]
        best = (ok if len(ok) else train).sort_values("중앙값", ascending=False).iloc[0]
        key = {k: best[k] for k in GRID}
        best_by_market[market] = key
        test = df[(df["시장"] == market) & (df["구분"] == "검증")]
        test_best = test[np.logical_and.reduce([test[k] == v for k, v in key.items()])]
        lines += [f"[{market}] 학습 구간 {cut}개 / 검증 구간 {len(wins) - cut}개 ({wins[cut][0]:%Y-%m-%d} 이후 시작)",
                  "학습 구간 중앙값 상위 5개:",
                  fmt(train.sort_values("중앙값", ascending=False).head(5).drop(columns=["시장", "구분"])),
                  "고른 조합의 검증 구간 결과:", fmt(test_best.drop(columns=["시장", "구분"])),
                  "검증 구간 전체 조합(참고):",
                  fmt(test.sort_values("중앙값", ascending=False).drop(columns=["시장", "구분"])), ""]
    df = pd.DataFrame(rows)
    os.makedirs(out_dir, exist_ok=True)
    df.to_csv(os.path.join(out_dir, "contest.csv"), index=False, encoding="utf-8-sig")
    lines.append("고른 조합: " + "; ".join(f"{m}: {k}" for m, k in best_by_market.items()))
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out_dir, "contest.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
