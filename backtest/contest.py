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
    "rebalance_days": [2, 5],
    "full_rebalance": [False],
    "stop_atr": [0.0],
    "rsi_max": [80],
    # 시장 국면(레짐) 필터: 시장이 나쁜 날엔 새로 사지 않고 현금으로
    #   none: 안 씀 / breadth40·breadth50: EMA60 위에 있는 종목 비율이 40%·50% 미만이면 나쁜 날
    #   index20: 대상 종목 동일비중 지수가 그 지수의 EMA20 아래면 나쁜 날
    "regime": ["none", "breadth40", "breadth50", "index20"],
    # 나쁜 날 행동: cash = 전부 팔고 현금, top2 = 상위 2종목만 (나머지 현금)
    "regime_off": ["cash", "top2"],
}

# 입상 노리기(--aggressive): 평균보다 '잘 풀린 구간'의 수익률(상위 10%)이 큰 공식을 찾아요.
# 종목 수를 줄이고(집중), 더 짧은 기간의 급등주를 쫓는 조합까지 넣어요.
AGG_GRID = {
    "lookback": [5, 10, 20],
    "top_k": [1, 2, 3, 5],
    "rebalance_days": [1, 2, 5],
    "full_rebalance": [False],
    "stop_atr": [0.0],
    "rsi_max": [101],          # 과열 제외 안 함 (급등주를 쫓아요)
    "regime": ["none", "index20"],
    "regime_off": ["cash"],
}
# 학계 방식 모멘텀 비교(--momentum): 최근 skip 거래일은 빼고 그 전 lookback 거래일 수익률로 순위
#   (Jegadeesh & Titman 1993 등. 1개월 이하는 오히려 되돌아가는 경향(단기 반전)이 있어서 최근 1개월을 빼요)
#   skip=0, lookback=20 은 지금 쓰는 입상형과 같은 조합이라 기준으로 같이 돌려요.
MOM_GRID = {
    "lookback": [20, 60, 120],
    "skip": [0, 20],
    "top_k": [2, 3, 5],
    "rebalance_days": [2, 5],
    "full_rebalance": [False],
    "stop_atr": [0.0],
    "rsi_max": [101],
    "trend_filter": [True, False],  # 종가 > EMA20 > EMA60 조건을 쓸지
    "regime": ["none"],
    "regime_off": ["cash"],
}
# 익절·손절 검증(--exits): 지금 입상형(20일 상위 3종목, 2일 교체)에 종목별 % 익절·손절을 붙여 비교
#   tp_pct: 산 가격 대비 +x% 에 닿으면 그 가격에 장중 매도 / sl_pct: −x% 에 닿으면 장중 매도
#   팔고 난 돈은 다음 리밸런스까지 현금. 같은 날 둘 다 닿으면 손절이 먼저라고 (보수적으로) 봐요.
EXIT_GRID = {
    "lookback": [20],
    "skip": [0],
    "top_k": [3],
    "rebalance_days": [2],
    "full_rebalance": [False],
    "stop_atr": [0.0],
    "rsi_max": [101],
    "trend_filter": [True],
    "regime": ["none"],
    "regime_off": ["cash"],
    "tp_pct": [0.0, 0.10, 0.15, 0.25, 0.40],
    "sl_pct": [0.0, 0.07, 0.10, 0.15],
}
TAIL_GOAL = 0.30  # 5주에 +30% 이상이면 '대박 구간'으로 셈

# 수상 조건. 해외 부문 조건은 확인 전이라 국내와 같은 횟수 조건만 적용하고 금액 조건은 빼요.
RULES = {
    "KR": {"amount": 500_000_000, "days": 5, "symbols": 5, "orders": 5},
    "US": {"amount": 0, "days": 5, "symbols": 5, "orders": 5},
}


def prepare(raw, cfg, lookbacks, skips=(0,)):
    out = {}
    for s, f in raw.items():
        d = add_indicators(f, cfg["indicators"])
        for n in lookbacks:
            d[f"ret_{n}"] = d["Close"] / d["Close"].shift(n) - 1
            for k in skips:
                if k:
                    # k 거래일 전 종가 기준 n 거래일 수익률 (최근 k 일은 안 봐요)
                    d[f"mom_{n}_{k}"] = d["Close"].shift(k) / d["Close"].shift(k + n) - 1
        out[s] = d
    return out


def score_key(p):
    return f"mom_{p['lookback']}_{p['skip']}" if p.get("skip") else f"ret_{p['lookback']}"


def build_bars(frames):
    """{날짜: {종목: 그날 값}} — 조합마다 다시 만들지 않도록 시장별로 한 번만 만들어요."""
    bars = {}
    for s, f in frames.items():
        for d, row in zip(f.index, f.to_dict("records")):
            bars.setdefault(d, {})[s] = row
    return bars


def build_regime(bars):
    """{날짜: {"breadth": EMA60 위 종목 비율, "index_up": 동일비중 지수 > 지수 EMA20}} (그날 종가까지)."""
    dates = sorted(bars)
    out, level, prev = {}, 1.0, None
    idx = []
    for d in dates:
        day = bars[d]
        valid = [b for b in day.values() if b["ema_slow"] == b["ema_slow"]]
        breadth = sum(b["Close"] > b["ema_slow"] for b in valid) / len(valid) if valid else 1.0
        if prev is not None:
            rets = [b["Close"] / prev[s]["Close"] - 1 for s, b in day.items() if s in prev]
            if rets:
                level *= 1 + sum(rets) / len(rets)
        idx.append(level)
        prev = day
        out[d] = {"breadth": breadth, "index": level}
    ema = pd.Series(idx, index=dates).ewm(span=20, adjust=False, min_periods=20).mean()
    for d in dates:
        e = ema[d]
        out[d]["index_up"] = True if e != e else out[d]["index"] > e
    return out


class Rotation:
    def __init__(self, frames, market, cfg, params, bars=None, regime=None):
        self.frames = frames
        self.market = market
        self.p = params
        self.broker = SimulatedBroker(cfg["costs"], cfg["costs"]["slippage"])
        self.bars = bars if bars is not None else build_bars(frames)
        self.dates = sorted(self.bars)
        self.regime = regime if regime is not None else build_regime(self.bars)
        self._rank = {}

    def risk_on(self, d):
        """그날 종가까지로 본 시장 국면. 레짐 필터를 안 쓰면 항상 True."""
        mode = self.p.get("regime", "none")
        if mode == "none" or self.regime is None:
            return True
        r = self.regime.get(d)
        if r is None:
            return True
        if mode.startswith("breadth"):
            return r["breadth"] >= int(mode[7:]) / 100
        return r["index_up"]

    def targets(self, d):
        """그날 종가 기준 목표 종목 (그날까지 데이터만 사용). 날짜별로 한 번만 계산해 둬요."""
        if not self.risk_on(d):
            if self.p.get("regime_off", "cash") == "cash":
                return []
            return self._ranked(d)[:2]
        return self._ranked(d)

    def _ranked(self, d):
        if d not in self._rank:
            p = self.p
            cands = []
            for s, b in self.bars.get(d, {}).items():
                score = b.get(score_key(p))
                if score is None or not (score == score) or not (b["ema_slow"] == b["ema_slow"]):
                    continue
                trend_ok = b["Close"] > b["ema_fast"] > b["ema_slow"] if p.get("trend_filter", True) else True
                if trend_ok and b["rsi"] < p["rsi_max"]:
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
                            pos[s] = {"qty": f.qty, "stop": f.price - self.p["stop_atr"] * (bars[s]["atr"] or 0),
                                      "entry": f.price}
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
            # 장중 % 손절·익절 (산 가격 기준)
            sl, tp = self.p.get("sl_pct", 0.0), self.p.get("tp_pct", 0.0)
            if sl or tp:
                for s in list(pos):
                    b = bars.get(s)
                    if b is None or "entry" not in pos[s]:
                        continue
                    e = pos[s]["entry"]
                    if sl and b["Low"] <= e * (1 - sl):
                        f = self.broker.submit(Order(s, self.market, "sell", pos[s]["qty"], kind="stop",
                                                     stop_price=e * (1 - sl)), b, d)
                    elif tp and b["High"] >= e * (1 + tp):
                        raw = max(b["Open"], e * (1 + tp))  # 시가부터 넘었으면 시가에, 아니면 익절가에
                        f = self.broker.submit(Order(s, self.market, "sell", pos[s]["qty"]),
                                               {**b, "Open": raw}, d)
                    else:
                        f = None
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
            "평균주문": np.mean([s["orders"] for s in stats]), "구간수": len(r),
            "상위10%": np.percentile(r, 90), "상위5%": np.percentile(r, 95), "하위10%": np.percentile(r, 10),
            "+30%확률": (r >= TAIL_GOAL).mean()}


def fmt(df):
    shown = df.copy()
    for c in shown.columns:
        if c in ("평균", "중앙값", "하위25%", "최악", "최고", "기준초과확률", "기준평균", "조건충족률",
                 "상위10%", "상위5%", "하위10%", "+30%확률"):
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


def build_states(regime, n=20):
    """날짜별 시장 상태 (그날 종가까지):
    vol: 동일비중 지수 일간 수익률의 최근 n일 표준편차(연환산)
    er: 추세 효율 = |n일 순변화| / n일 동안 매일 움직인 폭의 합 (1 이면 한 방향, 0 에 가까우면 오락가락)
    flips: 최근 n일 중 전날과 방향이 바뀐 날 비율
    vol_pct, er_pct: 과거 전체(그날까지) 중 지금 값의 백분위 → 상대적으로 높은지 낮은지"""
    dates = sorted(regime)
    idx = pd.Series([regime[d]["index"] for d in dates], index=dates)
    r = idx.pct_change()
    lr = np.log(idx).diff()
    vol = r.rolling(n).std() * np.sqrt(252)
    er = lr.rolling(n).sum().abs() / lr.abs().rolling(n).sum()
    sign = np.sign(r)
    flips = (sign != sign.shift(1)).astype(float).where(sign.shift(1).notna()).rolling(n).mean()
    out = {}
    for i, d in enumerate(dates):
        if np.isnan(vol.iloc[i]) or np.isnan(er.iloc[i]):
            continue
        past_v, past_e = vol.iloc[: i + 1].dropna(), er.iloc[: i + 1].dropna()
        out[d] = {"vol": float(vol.iloc[i]), "er": float(er.iloc[i]), "flips": float(flips.iloc[i]),
                  "breadth": regime[d]["breadth"],
                  "vol_pct": float((past_v <= vol.iloc[i]).mean()), "er_pct": float((past_e <= er.iloc[i]).mean())}
    return out


def state_label(st):
    """오락가락(변동성 높음 + 추세 효율 낮음) / 추세장 / 보통."""
    if st is None:
        return None
    if st["vol_pct"] >= 0.67 and st["er_pct"] <= 0.5:
        return "변동 큰 오락가락장"
    if st["er_pct"] >= 0.67:
        return "한 방향 추세장"
    return "보통"


def state_report(wins, combos, bench, states, rule, scale, cut, min_samples=8):
    """구간 시작 시점 시장 상태별로 어떤 공식이 잘 됐는지, 그리고
    '지금 상태와 같았던 과거 구간(이미 끝난 것만)에서 가장 좋았던 공식'을 쓰는 방식의 성적."""
    labels = [state_label(states.get(a)) for a, _ in wins]
    names = list(combos)
    lines = ["구간 시작 때 시장 상태별 공식 성적 (중앙값, 학습 구간):"]
    rows = []
    for lab in ("변동 큰 오락가락장", "한 방향 추세장", "보통"):
        idx = [i for i in range(cut) if labels[i] == lab]
        if not idx:
            continue
        ranked = sorted(names, key=lambda nm: -np.median([combos[nm][0][i] for i in idx]))
        for nm in ranked[:3]:
            rows.append({"상태": lab, "구간수": len(idx), "공식": nm,
                         "중앙값": np.median([combos[nm][0][i] for i in idx]),
                         "평균": np.mean([combos[nm][0][i] for i in idx])})
    lines.append(fmt(pd.DataFrame(rows)))

    chosen = []
    for i, (a, b) in enumerate(wins):
        lab = labels[i]
        past = [j for j in range(i) if wins[j][1] < a and labels[j] == lab]
        if lab is None or len(past) < min_samples:
            continue
        best, best_sc = None, None
        for nm in names:
            rets, stats = combos[nm]
            if np.mean([passes(stats[j], rule, scale) for j in past]) < 0.9:
                continue
            sc = np.median([rets[j] for j in past])
            if best_sc is None or sc > best_sc:
                best, best_sc = nm, sc
        if best:
            chosen.append((i, best))
    table = []
    for part, keep in (("학습", lambda i: i < cut), ("검증", lambda i: i >= cut)):
        sel = [(i, nm) for i, nm in chosen if keep(i)]
        if sel:
            table.append({"방식": "상태 맞춤", "구분": part,
                          **summarize([combos[nm][0][i] for i, nm in sel], [bench[i] for i, _ in sel],
                                      [combos[nm][1][i] for i, nm in sel], rule, scale)})
    lines += ["지금과 같은 상태였던 과거 구간(이미 끝난 것, 8개 이상)에서 중앙값 1위 공식을 쓰는 방식:",
              fmt(pd.DataFrame(table)) if table else "(표본 부족)"]
    return lines


def current_state_lines(market, states, dates):
    d = max(states)
    st = states[d]
    hist = [states[x] for x in sorted(states)]
    return [f"[{market}] 지금 시장 상태 ({d:%Y-%m-%d} 종가 기준, 대상 종목 동일비중 지수)",
            f"  변동성(20일, 연환산) {st['vol'] * 100:.1f}%  → 지난 5년 중 상위 {(1 - st['vol_pct']) * 100:.0f}%",
            f"  추세 효율(20일) {st['er']:.2f}  → 지난 5년 중 하위 {st['er_pct'] * 100:.0f}% (낮을수록 오락가락)",
            f"  방향 바뀐 날 비율(20일) {st['flips'] * 100:.0f}% (5년 평균 {np.nanmean([h['flips'] for h in hist]) * 100:.0f}%)",
            f"  EMA60 위 종목 비율 {st['breadth'] * 100:.0f}%",
            f"  판정: {state_label(st)}", ""]


def combo_name(p):
    return f"{p['lookback']}일·{p['rebalance_days']}일교체·{p['regime']}·{p['regime_off']}"


def adaptive_report(wins, combos, bench, rule, scale, cut, recents=(6, 12, 24)):
    """'최근 추세 분석 후 공식 고르기' 검증: 각 구간이 시작하기 전에 이미 끝난 최근 N개 구간에서
    성적(평균 수익률)이 가장 좋았던 조합을 골라 그 구간에 쓴다고 가정해요 (미래 데이터 안 씀).
    수상 조건 충족률이 90% 미만이었던 조합은 고르지 않아요."""
    names = list(combos)
    out = ["최근 성적으로 공식을 바꿔 가는 방식 (구간 시작 전에 끝난 최근 N개 구간 평균 수익률 1위 조합 사용):"]
    table = []
    for n in recents:
        chosen_rets, chosen_stats, chosen_bench, picks = [], [], [], []
        for i, (a, b) in enumerate(wins):
            past = [j for j in range(i) if wins[j][1] < a][-n:]
            if len(past) < n:
                continue
            best, best_score = None, None
            for name in names:
                rets, stats = combos[name]
                ok = np.mean([passes(stats[j], rule, scale) for j in past])
                if ok < 0.9:
                    continue
                sc = np.mean([rets[j] for j in past])
                if best_score is None or sc > best_score:
                    best, best_score = name, sc
            if best is None:
                continue
            chosen_rets.append((i, combos[best][0][i]))
            chosen_stats.append((i, combos[best][1][i]))
            chosen_bench.append((i, bench[i]))
            picks.append(best)
        for part, keep in (("학습", lambda i: i < cut), ("검증", lambda i: i >= cut)):
            r = [v for i, v in chosen_rets if keep(i)]
            if not r:
                continue
            st = [v for i, v in chosen_stats if keep(i)]
            bm = [v for i, v in chosen_bench if keep(i)]
            table.append({"방식": f"최근 {n}구간", "구분": part, **summarize(r, bm, st, rule, scale),
                          "자주 고른 조합": pd.Series(picks).value_counts().index[0]})
    for name in names:
        rets, stats = combos[name]
        if name.endswith("none·cash"):
            for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
                table.append({"방식": f"고정: {name}", "구분": part,
                              **summarize(rets[sl], bench[sl], stats[sl], rule, scale), "자주 고른 조합": ""})
    return out + [fmt(pd.DataFrame(table))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--days", type=int, default=26, help="대회 길이(거래일). 10/15~11/20 은 약 26일")
    ap.add_argument("--aggressive", action="store_true", help="입상 노리기: 상위 10% 수익률 기준으로 고르기")
    ap.add_argument("--markets", default="KR,US", help="검증할 시장 (예: US)")
    ap.add_argument("--momentum", action="store_true", help="학계 방식 모멘텀(최근 1개월 제외)과 지금 입상형 비교")
    ap.add_argument("--exits", action="store_true", help="지금 입상형에 % 익절·손절을 붙여 비교")
    ap.add_argument("--us-list", default=None, help="미국 종목 목록으로 쓸 config 키 (예: us_aggressive)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    val = cfg.get("validation", {})
    out_dir = os.path.join(HERE, cfg["report"].get("output_dir", "results"))
    cache = os.path.join(HERE, cfg["report"]["cache_dir"]) if cfg["report"].get("cache_dir") else None

    markets = [m.strip().upper() for m in args.markets.split(",")]
    if args.us_list:
        us = [str(s) for s in cfg[args.us_list]]
    else:
        us = [str(s) for s in cfg["symbols"] + val.get("extra_symbols", []) if market_of(str(s)) == "US"]
    kr = kr_symbols(cfg, print) if "KR" in markets else []
    symbols = list(dict.fromkeys(kr + (us if "US" in markets else [])))
    raw, start, end = load_all(symbols, cfg["period"], cache, log=lambda *_: None)
    print(f"받은 데이터: 국내 {sum(market_of(s) == 'KR' for s in raw)}종목, 해외 {sum(market_of(s) == 'US' for s in raw)}종목")
    grid = EXIT_GRID if args.exits else MOM_GRID if args.momentum else AGG_GRID if args.aggressive else GRID
    metric = "상위10%" if (args.aggressive or args.momentum or args.exits) else "중앙값"
    tag = ("contest_exits" if args.exits else "contest_momentum" if args.momentum else
           "contest_aggressive" if args.aggressive else "contest") + \
        ("" if markets == ["KR", "US"] else "_" + "_".join(markets))
    if args.us_list:
        tag += "_" + args.us_list
    frames = prepare(raw, cfg, sorted(set(GRID["lookback"]) | set(AGG_GRID["lookback"]) | set(MOM_GRID["lookback"])),
                     MOM_GRID["skip"])
    ratio = float(val.get("train_ratio", 0.6))

    rows, lines, picked = [], [f"대회 길이 {args.days}거래일, 5거래일 간격으로 자른 구간마다 새로 운용", ""], {}
    for market in markets:
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
        bars = build_bars(mf)
        regime = build_regime(bars)
        combos = {}  # 조합 이름 → (구간별 수익률, 구간별 조건 통계)
        for values in itertools.product(*grid.values()):
            params = dict(zip(grid, values))
            if params["regime"] == "none" and params["regime_off"] != "cash":
                continue  # 레짐 필터를 안 쓰면 나쁜 날 행동은 의미 없어요
            rot = Rotation(mf, market, cfg, params, bars, regime)
            rets, stats = [], []
            for a, b in wins:
                curve, st = rot.run(a, b, cap)
                rets.append(curve.iloc[-1] / cap - 1)
                stats.append(st)
            combos[combo_name(params)] = (rets, stats)
            for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
                rows.append({"시장": market, "구분": part, **params,
                             **summarize(rets[sl], bench[sl], stats[sl], rule, scale)})
        states = build_states(regime)
        now_lines = current_state_lines(market, states, dates)
        if args.aggressive or args.momentum or args.exits:
            adaptive_lines = []
        else:
            adaptive_lines = adaptive_report(wins, combos, bench, rule, scale, cut)
            adaptive_lines += [""] + state_report(wins, combos, bench, states, rule, scale, cut)
        df = pd.DataFrame(rows)
        train = df[(df["시장"] == market) & (df["구분"] == "학습")]
        test = df[(df["시장"] == market) & (df["구분"] == "검증")]
        # 고르는 기준: 수상 조건을 90% 이상 채운 조합 중 중앙값 수익률 1위
        ok = train[train["조건충족률"] >= 0.9]
        best = (ok if len(ok) else train).sort_values(metric, ascending=False).iloc[0]
        key = {k: best[k] for k in grid}
        picked[market] = {k: (v.item() if hasattr(v, "item") else v) for k, v in key.items()}
        test_best = test[np.logical_and.reduce([test[k] == v for k, v in key.items()])]
        cols = ["시장", "구분"]
        lines += now_lines + [f"[{market}] 학습 구간 {cut}개 / 검증 구간 {len(wins) - cut}개 ({wins[cut][0]:%Y-%m-%d} 이후 시작)",
                  f"학습 구간: 조건충족률 90% 이상 중 {metric} 상위 5개",
                  fmt((ok if len(ok) else train).sort_values(metric, ascending=False).head(5).drop(columns=cols)),
                  "고른 조합의 검증 구간 결과:", fmt(test_best.drop(columns=cols)),
                  "검증 구간 전체 조합(참고):", fmt(test.sort_values(metric, ascending=False).drop(columns=cols)), "",
                  *adaptive_lines, ""]
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, f"{tag}.csv"), index=False, encoding="utf-8-sig")
    lines.append("고른 조합: " + "; ".join(f"{m}: {k}" for m, k in picked.items()))
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out_dir, f"{tag}.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
