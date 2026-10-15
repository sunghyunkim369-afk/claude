"""백테스트 엔진.

하루 순서 (시장·통화별로 따로 돌려요):
  1. 시가: 어제 종가에 나온 신호대로 주문 체결 (매도 먼저, 그다음 매수)
  2. 장중: 보유 종목은 어제까지 정해진 손절/트레일링 가격에 스탑 주문 → 저가가 닿으면 체결
  3. 종가: 평가금액 기록, 최고가·ATR 갱신, 내일 시가에 낼 주문(신호) 결정
그래서 신호와 수량은 모두 '그날 종가까지' 알 수 있는 값만 써요.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from broker import Order, SimulatedBroker
from strategy import initial_stop, stop_level


@dataclass
class Position:
    symbol: str
    qty: int
    entry_date: object
    entry_price: float
    entry_fee: float
    initial_stop: float
    highest: float          # 진입 후 고가 중 최고 (어제까지)
    atr: float              # 어제 ATR


class Backtest:
    def __init__(self, frames, market, cfg, start, end, broker=None):
        """frames: {symbol: 지표+신호가 붙은 DataFrame} (같은 시장만)."""
        self.frames = frames
        self.market = market
        self.cfg = cfg
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.broker = broker or SimulatedBroker(cfg["costs"], cfg["costs"]["slippage"])
        self.risk = cfg["risk"]
        self.exit_cfg = cfg["exit"]

    def run(self, capital):
        dates = sorted({d for f in self.frames.values() for d in f.index if self.start <= d <= self.end})
        cash = float(capital)
        positions = {}
        pending = []              # 내일 시가 주문
        trades, equity = [], []

        def close_trade(pos, fill, reason):
            nonlocal cash
            cash += fill.gross - fill.fee
            cost = pos.entry_price * fill.qty + pos.entry_fee
            pnl = fill.gross - fill.fee - cost
            trades.append({
                "market": self.market, "symbol": pos.symbol,
                "entry_date": pos.entry_date, "entry_price": pos.entry_price,
                "exit_date": fill.date, "exit_price": fill.price, "qty": fill.qty,
                "fees": pos.entry_fee + fill.fee, "pnl": pnl, "return": pnl / cost,
                "holding_days": (pd.Timestamp(fill.date) - pd.Timestamp(pos.entry_date)).days,
                "exit_reason": reason,
            })

        for d in dates:
            bars = {s: f.loc[d] for s, f in self.frames.items() if d in f.index}

            # 1. 시가 체결: 매도 먼저
            carry = []
            for o in sorted(pending, key=lambda o: o.side != "sell"):
                bar = bars.get(o.symbol)
                if bar is None:                  # 그 종목이 오늘 거래가 없으면(정지 등) 매도는 다음 날로
                    if o.side == "sell":
                        carry.append(o)
                    continue
                if o.side == "sell":
                    pos = positions.get(o.symbol)
                    if pos is None:
                        continue
                    fill = self.broker.submit(o, bar, d)
                    close_trade(pos, fill, o.reason)
                    del positions[o.symbol]
                else:
                    if o.symbol in positions or len(positions) >= self.risk["max_positions"]:
                        continue
                    fill = self.broker.submit(o, bar, d, cash=cash)
                    if fill is None:
                        continue
                    cash -= fill.gross + fill.fee
                    atr = o.meta["atr"]
                    positions[o.symbol] = Position(
                        o.symbol, fill.qty, d, fill.price, fill.fee,
                        initial_stop(fill.price, atr, self.exit_cfg), highest=fill.price, atr=atr)
            pending = carry

            # 2. 장중 스탑 (어제까지 정해진 가격)
            for s, pos in list(positions.items()):
                bar = bars.get(s)
                if bar is None:
                    continue
                level = stop_level(pos.initial_stop, pos.highest, pos.atr, self.exit_cfg)
                o = Order(s, self.market, "sell", pos.qty, kind="stop", stop_price=level)
                fill = self.broker.submit(o, bar, d)
                if fill is not None:
                    reason = "손절" if level <= pos.initial_stop else "트레일링 스탑"
                    close_trade(pos, fill, reason)
                    del positions[s]
                    pending = [p for p in pending if p.symbol != s]

            # 3. 종가: 평가, 갱신, 내일 주문
            value = cash
            for s, pos in positions.items():
                bar = bars.get(s)
                price = bar["Close"] if bar is not None else self.frames[s].loc[:d, "Close"].iloc[-1]
                value += pos.qty * price
                if bar is not None:
                    pos.highest = max(pos.highest, bar["High"])
                    if pd.notna(bar["atr"]):
                        pos.atr = bar["atr"]
            equity.append((d, value, cash, len(positions)))

            for s, pos in positions.items():
                bar = bars.get(s)
                if bar is not None and bool(bar["sell_signal"]) and not any(p.symbol == s for p in pending):
                    pending.append(Order(s, self.market, "sell", pos.qty, reason=bar.get("sell_reason") or "매도 신호"))

            if d == dates[-1]:
                break
            free = self.risk["max_positions"] - len(positions) + sum(1 for p in pending if p.side == "sell")
            candidates = [(s, b) for s, b in bars.items()
                          if s not in positions and bool(b["buy_signal"]) and pd.notna(b["atr"]) and b["atr"] > 0]
            candidates.sort(key=lambda x: -x[1]["rank"])
            for s, b in candidates[:max(free, 0)]:
                qty = self.size(value, b["Close"], b["atr"])
                if qty > 0:
                    pending.append(Order(s, self.market, "buy", qty, reason="매수 신호",
                                         meta={"atr": float(b["atr"]), "signal_close": float(b["Close"])}))

        # 기간 끝: 남은 종목은 마지막 종가에 판 것으로 계산 (비용 포함)
        if dates:
            last = dates[-1]
            for s, pos in list(positions.items()):
                close = self.frames[s].loc[:last, "Close"].iloc[-1]
                bar = {"Open": close, "High": close, "Low": close, "Close": close}
                fill = self.broker.submit(Order(s, self.market, "sell", pos.qty), bar, last)
                close_trade(pos, fill, "기간 종료")
            if positions:
                equity[-1] = (last, cash, cash, 0)

        eq = pd.DataFrame(equity, columns=["date", "equity", "cash", "positions"]).set_index("date")
        return eq, pd.DataFrame(trades)

    def size(self, equity, price, atr):
        """손절에 걸리면 총자본의 risk_per_trade 만큼 잃도록 수량을 정해요 (한 종목 최대 비중도 지켜요)."""
        risk_amount = equity * self.risk["risk_per_trade"]
        stop_distance = self.exit_cfg["stop_atr"] * atr
        by_risk = math.floor(risk_amount / stop_distance)
        by_weight = math.floor(equity * self.risk.get("max_position_pct", 1.0) / price)
        return max(0, min(by_risk, by_weight))


def buy_and_hold(frames, market, cfg, start, end, capital):
    """같은 기간 종목들을 첫날 시가에 똑같은 금액으로 사서 마지막 날까지 보유 (비용 포함)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    broker = SimulatedBroker(cfg["costs"], cfg["costs"]["slippage"])
    dates = sorted({d for f in frames.values() for d in f.index if start <= d <= end})
    per = capital / max(len(frames), 1)
    cash = float(capital)
    holdings = {}
    for s, f in frames.items():
        f = f[(f.index >= start) & (f.index <= end)]
        if f.empty:
            continue
        fill = broker.submit(Order(s, market, "buy", 10 ** 12), f.iloc[0], f.index[0], cash=per)
        if fill:
            cash -= fill.gross + fill.fee
            holdings[s] = fill.qty
    closes = pd.DataFrame({s: frames[s]["Close"] for s in holdings}).reindex(dates).ffill()
    value = cash + (closes * pd.Series(holdings)).sum(axis=1)
    # 마지막 날 매도 비용 반영
    c = cfg["costs"][market]
    sell_cost = (closes.iloc[-1] * pd.Series(holdings)).sum() * (cfg["costs"]["slippage"] + c["sell_fee"] + c.get("sell_tax", 0))
    value.iloc[-1] -= sell_cost
    return value


# ---------- 성과 지표 ----------

def metrics(equity, trades=None, risk_free=0.0):
    eq = equity.dropna()
    if len(eq) < 2:
        return {}
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    r = eq.pct_change().dropna()
    excess = r - risk_free / 252
    sharpe = excess.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    out = {"총수익률": total, "CAGR": cagr, "MDD": mdd, "샤프지수": sharpe}
    if trades is not None:
        out["거래횟수"] = len(trades)
        out["승률"] = (trades["pnl"] > 0).mean() if len(trades) else np.nan
    return out
