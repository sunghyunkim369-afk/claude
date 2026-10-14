"""대회용 공식 찾기 (BanKIS 대학생 모의투자대회, 약 5주 수익률 순위).

공식 (모멘텀 순환매, 판단은 종가 → 체결은 다음 날 시가):
  1. 후보: 종가 > EMA20 > EMA60 (상승 추세), RSI14 < rsi_max (과열 제외)
  2. 점수: 최근 lookback 일 수익률. 높은 순으로 top_k 종목이 목표
  3. rebalance_days 거래일마다: 목표에서 빠진 종목은 팔고 새 종목은 (총자산 / top_k) 만큼 매수.
     full_rebalance 면 남는 종목도 (총자산 / top_k) 로 다시 맞춰요 (매매금액이 늘어요)
  4. 보유 중 진입가 − stop_atr × ATR 아래로 내려가면 장중 손절 (0 이면 안 씀)

국내 수상 조건 (체결 기준): 매매금액 5억 원 이상, 매매일수 5영업일 이상,
코스피200·코스닥150 중 5종목 이상, OpenAPI 체결 5회 이상. 원금 1억 원.
→ 과거 '대회 길이 구간'마다 이 조건을 모두 채웠는지 세서, 채울 확률이 높은 조합 중에서 골라요.

검증: 과거 5년을 대회 길이(기본 26거래일) 구간으로 5거래일씩 밀며 잘라, 구간마다 처음부터 새로 운용.
앞 60% 구간에서 고르고, 뒤 40% 구간(고를 때 안 본 기간)에서 확인해요.

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
    "top_k": [5],
    "rebalance_days": [1, 2, 5],
    "full_rebalance": [False, True],
    "stop_atr": [0.0, 2.0],
    "rsi_max": [80],
}

# 수상 조건. 해외 부문 조건은 확인 전이라 국내와 같은 횟수 조건만 적용하고 금액 조건은 빼요.
RULES = {
    "KR": {"amount": 500_000_000, "days": 5, "symbols": 5, "orders": 5},
    "US": {"amount": 0, "days": 5, "symbols": 5, "orders": 5},
}


def prepare(raw, cfg, lookbacks):
    out = {}
    for s, f in raw.items():
        d = add_indicators(f, cfg["indicators"])
        for n in lookbacks:
            d[f"ret_{n}"] = d["Close"] / d["Close"].shift(n) - 1
        out[s] = d
    return out


class Rotation:
    def __init__(self, frames, market, cfg, params):
        self.frames = frames
        self.market = market
        self.p = params
        self.broker = SimulatedBroker(cfg["costs"], cfg["costs"]["slippage"])
        self.dates = sorted({d for f in frames.values() for d in f.index})
        self.bars = {}
        for s, f in frames.items():
            for d, row in zip(f.index, f.to_dict("records")):
                self.bars.setdefault(d, {})[s] = row
        self._rank = {}

    def targets(self, d):
        """그날 종가 기준 목표 종목 (그날까지 데이터만 사용). 날짜별로 한 번만 계산해 둬요."""
        if d not in self._rank:
            p = self.p
            cands = []
            for s, b in self.bars.get(d, {}).items():
                score = b.get(f"ret_{p['lookback']}")
                if score is None or np.isnan(score) or np.isnan(b["ema_slow"]):
                    continue
                if b["Close"] > b["ema_fast"] > b["ema_slow"] and b["rsi"] < p["rsi_max"]:
                    cands.append((score, s))
            cands.sort(reverse=True)
            self._rank[d] = [s for _, s in cands[:p["top_k"]]]
        return self._rank[d]

    def run(self, start, end, capital):
        """반환: (자산곡선, 통계 dict: orders, days, symbols, amount)."""
        days = [d for d in self.dates if start <= d <= end]
        cash, pos, pending, curve = float(capital), {}, None, []
        orders, traded_days, traded_syms, amount = 0, set(), set(), 0.0

        def record(fill, d):
            nonlocal orders, amount
            orders += 1
            amount += fill.gross
            traded_days.add(d)
            traded_syms.add(fill.order.symbol)

        for i, d in enumerate(days):
            bars = self.bars[d]
            if pending is not None:
                target, per = pending
                # 1) 목표에서 빠진 종목 매도
                for s in [s for s in pos if s not in target]:
                    if s in bars:
                        f = self.broker.submit(Order(s, self.market, "sell", pos[s]["qty"]), bars[s], d)
                        cash += f.gross - f.fee
                        record(f, d)
                        del pos[s]
                # 2) 다시 맞추기: 넘치는 만큼 매도
                if self.p["full_rebalance"]:
                    for s in list(pos):
                        if s in bars:
                            extra = pos[s]["qty"] - int(per // bars[s]["Open"])
                            if extra > 0:
                                f = self.broker.submit(Order(s, self.market, "sell", extra), bars[s], d)
                                cash += f.gross - f.fee
                                record(f, d)
                                pos[s]["qty"] -= f.qty
                # 3) 매수 (새 종목, 다시 맞추기면 모자란 종목도)
                for s in target:
                    if s not in bars:
                        continue
                    have = pos.get(s, {}).get("qty", 0)
                    if have and not self.p["full_rebalance"]:
                        continue
                    want = int(min(per, cash) // (bars[s]["Open"] * 1.01)) - have
                    if want <= 0:
                        continue
                    f = self.broker.submit(Order(s, self.market, "buy", want), bars[s], d, cash=cash)
                    if f:
                        cash -= f.gross + f.fee
                        record(f, d)
                        if s in pos:
                            pos[s]["qty"] += f.qty
                        else:
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
                            record(f, d)
                            del pos[s]
            value = cash + sum(p["qty"] * self._close(s, d) for s, p in pos.items())
            curve.append(value)
            if i < len(days) - 1 and i % self.p["rebalance_days"] == 0:
                pending = (self.targets(d), value / self.p["top_k"])
        # 대회 끝: 보유 종목은 종가로 평가 (대회 수익률은 평가금액 기준이라 팔지 않아요)
        stats = {"orders": orders, "days": len(traded_days), "symbols": len(traded_syms), "amount": amount}
        return pd.Series(curve, index=days), stats

    def _close(self, s, d):
        b = self.bars[d].get(s)
        return b["Close"] if b else self.frames[s].loc[:d, "Close"].iloc[-1]


def passes(stats, rule, capital_scale=1.0):
    return (stats["amount"] >= rule["amount"] * capital_scale and stats["days"] >= rule["days"]
            and stats["symbols"] >= rule["symbols"] and stats["orders"] >= rule["orders"])


def windows(dates, length, step):
    return [(dates[i], dates[i + length - 1]) for i in range(0, len(dates) - length + 1, step)]


def summarize(rets, bench, stats, rule, scale):
    r, b = np.array(rets), np.array(bench)
    ok = np.array([passes(s, rule, scale) for s in stats])
    return {"평균": r.mean(), "중앙값": np.median(r), "하위25%": np.percentile(r, 25), "최악": r.min(),
            "최고": r.max(), "기준초과확률": (r > b).mean(), "기준평균": b.mean(),
            "조건충족률": ok.mean(), "평균매매금액배수": np.mean([s["amount"] for s in stats]) / scale / 1e8 if scale else 0,
            "평균주문": np.mean([s["orders"] for s in stats]), "구간수": len(r)}


def fmt(df):
    shown = df.copy()
    for c in shown.columns:
        if c in ("평균", "중앙값", "하위25%", "최악", "최고", "기준초과확률", "기준평균", "조건충족률"):
            shown[c] = shown[c].map(lambda v: f"{v * 100:.1f}%")
        elif c in ("평균주문", "평균매매금액배수"):
            shown[c] = shown[c].map(lambda v: f"{v:.1f}")
    return shown.to_string(index=False)


def equal_weight_return(frames, start, end):
    """비교 기준: 매매 가능 종목 전체 동일비중 보유 수익률 (비용 제외)."""
    rets = []
    for f in frames.values():
        w = f[(f.index >= start) & (f.index <= end)]
        if len(w) > 1:
            rets.append(w["Close"].iloc[-1] / w["Open"].iloc[0] - 1)
    return float(np.mean(rets)) if rets else 0.0


def kr_symbols(cfg, log):
    try:
        import universe
        df = universe.load()
        if df is None:
            df = universe.build(log)
            df.to_csv(universe.PATH, index=False, encoding="utf-8-sig")
        log(f"국내 대회 종목: {len(df)}개 ({df['source'].iloc[0]})")
        return list(df["code"])
    except Exception as e:
        log(f"대회 종목 목록 실패, config 종목 사용: {e}")
        return [str(s) for s in cfg["symbols"] if market_of(str(s)) == "KR"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--days", type=int, default=26, help="대회 길이(거래일). 10/15~11/20 은 약 26일")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    val = cfg.get("validation", {})
    out_dir = os.path.join(HERE, cfg["report"].get("output_dir", "results"))
    cache = os.path.join(HERE, cfg["report"]["cache_dir"]) if cfg["report"].get("cache_dir") else None

    us = [str(s) for s in cfg["symbols"] + val.get("extra_symbols", []) if market_of(str(s)) == "US"]
    symbols = list(dict.fromkeys(kr_symbols(cfg, print) + us))
    raw, start, end = load_all(symbols, cfg["period"], cache, log=lambda *_: None)
    print(f"받은 데이터: 국내 {sum(market_of(s) == 'KR' for s in raw)}종목, 해외 {sum(market_of(s) == 'US' for s in raw)}종목")
    frames = prepare(raw, cfg, GRID["lookback"])
    ratio = float(val.get("train_ratio", 0.6))

    rows, lines, picked = [], [f"대회 길이 {args.days}거래일, 5거래일 간격으로 자른 구간마다 새로 운용", ""], {}
    for market in ("KR", "US"):
        mf = {s: f for s, f in frames.items() if market_of(s) == market}
        if not mf:
            continue
        cap = cfg["capital"][market]
        rule = RULES[market]
        scale = cap / 1e8 if market == "KR" else 1.0  # 국내 조건은 원금 1억 기준
        dates = [d for d in sorted({d for f in mf.values() for d in f.index}) if start <= d <= end]
        wins = windows(dates, args.days, 5)
        cut = int(len(wins) * ratio)
        bench = [equal_weight_return(mf, a, b) for a, b in wins]
        for values in itertools.product(*GRID.values()):
            params = dict(zip(GRID, values))
            rot = Rotation(mf, market, cfg, params)
            rets, stats = [], []
            for a, b in wins:
                curve, st = rot.run(a, b, cap)
                rets.append(curve.iloc[-1] / cap - 1)
                stats.append(st)
            for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
                rows.append({"시장": market, "구분": part, **params,
                             **summarize(rets[sl], bench[sl], stats[sl], rule, scale)})
        df = pd.DataFrame(rows)
        train = df[(df["시장"] == market) & (df["구분"] == "학습")]
        test = df[(df["시장"] == market) & (df["구분"] == "검증")]
        # 고르는 기준: 수상 조건을 90% 이상 채운 조합 중 중앙값 수익률 1위
        ok = train[train["조건충족률"] >= 0.9]
        best = (ok if len(ok) else train).sort_values("중앙값", ascending=False).iloc[0]
        key = {k: best[k] for k in GRID}
        picked[market] = {k: (v.item() if hasattr(v, "item") else v) for k, v in key.items()}
        test_best = test[np.logical_and.reduce([test[k] == v for k, v in key.items()])]
        cols = ["시장", "구분"]
        lines += [f"[{market}] 학습 구간 {cut}개 / 검증 구간 {len(wins) - cut}개 ({wins[cut][0]:%Y-%m-%d} 이후 시작)",
                  "학습 구간: 조건충족률 90% 이상 중 중앙값 상위 5개",
                  fmt((ok if len(ok) else train).sort_values("중앙값", ascending=False).head(5).drop(columns=cols)),
                  "고른 조합의 검증 구간 결과:", fmt(test_best.drop(columns=cols)),
                  "검증 구간 전체 조합(참고):", fmt(test.sort_values("중앙값", ascending=False).drop(columns=cols)), ""]
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "contest.csv"), index=False, encoding="utf-8-sig")
    lines.append("고른 조합: " + "; ".join(f"{m}: {k}" for m, k in picked.items()))
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out_dir, "contest.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
