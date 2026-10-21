"""백테스트 엔진.

타이밍 (미래 데이터 금지)
- t일 종가(UTC 00:00)까지의 정보로 목표 비중 w[t] 를 정합니다.
- 체결은 t+1일 시가. 시가 = 직전 종가와 같은 시각이라 실제로는 종가 직후에 주문하는 것과 같습니다.
- exec_lag=1 로 두면 하루 더 늦게(t+1일 종가) 체결해서, 늦게 주문했을 때 얼마나 나빠지는지 봅니다.

비용
- 매매 금액마다 수수료(fee) + 슬리피지(코어/기타 따로). 사고팔 때 모두 냅니다.

상장폐지 (마지막 거래일 다음 날 가격이 없어짐)
- delist_mode="last": 마지막 종가에 비용을 내고 팔린 것으로 처리
- delist_mode="zero": 남은 수량을 0원으로 처리
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Costs:
    fee: float = 0.0005
    slip_core: float = 0.001
    slip_other: float = 0.003
    core: tuple = ("BTC", "ETH", "SOL")

    def per_asset(self, assets) -> np.ndarray:
        return np.array([self.fee + (self.slip_core if a in self.core else self.slip_other) for a in assets])


@dataclass
class Rules:
    rebalance_days: int = 1
    max_weight: float = 1.0
    max_total: float = 1.0
    max_orders: int = 10_000
    min_trade: float = 0.0
    delist_mode: str = "last"
    exec_lag: int = 0
    always_last: frozenset = frozenset()   # 이름 교체 코인: 0원 모드에서도 마지막 가격


@dataclass
class Result:
    equity: pd.Series
    weights: pd.DataFrame          # 종가 기준 실제 비중
    trades: pd.DataFrame           # 진입~청산 단위 거래 기록
    costs_paid: float
    turnover: pd.Series            # 하루 매매 금액 / 평가금
    orders: pd.Series              # 하루 주문 건수
    meta: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        return self.equity.pct_change().fillna(0.0)


def apply_caps(w: np.ndarray, max_weight: float, max_total: float) -> np.ndarray:
    """한 코인 최대 비중 → 남는 비중은 현금. 총합이 한도를 넘으면 비례 축소."""
    w = np.clip(np.nan_to_num(w, nan=0.0), 0.0, max_weight)
    tot = w.sum()
    if tot > max_total:
        w = w * (max_total / tot)
    return w


def run(target: pd.DataFrame, open_: pd.DataFrame, close: pd.DataFrame,
        costs: Costs | None = None, rules: Rules | None = None, tradable: pd.DataFrame | None = None,
        start=None, end=None, initial: float = 1.0) -> Result:
    costs = costs or Costs()
    rules = rules or Rules()
    cols = list(target.columns)
    close = close.reindex(columns=cols)
    open_ = open_.reindex(columns=cols)
    idx = close.loc[start:end].index
    target = target.reindex(index=close.index, columns=cols).loc[idx]
    C = close.loc[idx].to_numpy(float)
    if rules.exec_lag == 0:
        P = open_.loc[idx].to_numpy(float)        # t+1 시가
    else:
        P = C.copy()                               # t+1 종가 (하루 늦게)
    TR = (tradable.reindex(index=idx, columns=cols).to_numpy(bool) if tradable is not None
          else ~np.isnan(C))
    W = target.to_numpy(float)
    rate = costs.per_asset(cols)
    n, m = C.shape

    qty = np.zeros(m)
    cash = initial
    last_px = np.full(m, np.nan)
    eq = np.empty(n)
    wts = np.zeros((n, m))
    turn = np.zeros(n)
    nord = np.zeros(n, dtype=int)
    total_cost = 0.0
    # 거래 기록: 진입일, 들어간 돈, 나온 돈
    open_tr: dict[int, list] = {}
    trades: list[tuple] = []

    def close_trade(j, day, reason):
        tr = open_tr.pop(j, None)
        if tr is not None:
            trades.append((cols[j], tr[0], idx[day], tr[1], tr[2], tr[2] / tr[1] - 1 if tr[1] > 0 else np.nan, reason))

    for i in range(n):
        # 1) 상장폐지 처리: 어제는 가격이 있었는데 오늘 없음
        dead = (qty > 0) & np.isnan(C[i]) & ~np.isnan(last_px)
        for j in np.flatnonzero(dead):
            if rules.delist_mode == "last" or cols[j] in rules.always_last:
                proceeds = qty[j] * last_px[j] * (1 - rate[j])
                total_cost += qty[j] * last_px[j] * rate[j]
            else:
                proceeds = 0.0
            cash += proceeds
            if j in open_tr:
                open_tr[j][2] += proceeds
            qty[j] = 0.0
            close_trade(j, i, "delist")

        # 2) 리밸런스: 어제 종가로 정한 목표를 오늘 체결
        if i > 0 and (i - 1) % rules.rebalance_days == 0:
            px = P[i] if rules.exec_lag == 0 else C[i]
            can = TR[i] & ~np.isnan(px) & (px > 0)
            px0 = np.where(can, px, 0.0)
            hold_val = np.where(can, qty * px0, np.nan_to_num(qty * last_px))
            equity = cash + hold_val.sum()
            if equity > 0:
                w = apply_caps(W[i - 1], rules.max_weight, rules.max_total)
                w = np.where(np.isnan(C[i - 1]), 0.0, w)
                want = w * equity
                delta = np.where(can, want - hold_val, 0.0)
                full_exit = (w == 0) & (qty > 0) & can
                small = np.abs(delta) < rules.min_trade * equity
                delta = np.where(small & ~full_exit, 0.0, delta)
                order_idx = np.flatnonzero(delta != 0)
                if len(order_idx) > rules.max_orders:
                    order_idx = order_idx[np.argsort(-np.abs(delta[order_idx]))[: rules.max_orders]]
                sells = [j for j in order_idx if delta[j] < 0]
                buys = [j for j in order_idx if delta[j] > 0]
                traded = 0.0
                for j in sells:
                    val = min(-delta[j], qty[j] * px0[j])
                    if full_exit[j]:
                        val = qty[j] * px0[j]
                    q = val / px0[j]
                    qty[j] = 0.0 if full_exit[j] else qty[j] - q
                    fee = val * rate[j]
                    cash += val - fee
                    total_cost += fee
                    traded += val
                    if j in open_tr:
                        open_tr[j][2] += val - fee
                    if qty[j] <= 1e-15:
                        qty[j] = 0.0
                        close_trade(j, i, "signal")
                need = sum(delta[j] * (1 + rate[j]) for j in buys)
                scale = min(1.0, cash / need) if need > 0 else 0.0
                for j in buys:
                    val = delta[j] * scale
                    fee = val * rate[j]
                    if cash < val + fee:
                        val = cash / (1 + rate[j])
                        fee = val * rate[j]
                    if val <= 0:
                        continue
                    if qty[j] == 0 and j not in open_tr:
                        open_tr[j] = [idx[i], 0.0, 0.0]
                    qty[j] += val / px0[j]
                    cash -= val + fee
                    total_cost += fee
                    traded += val
                    open_tr[j][1] += val + fee
                turn[i] = traded / equity
                nord[i] = len(sells) + len(buys)

        # 3) 종가 평가
        ok = ~np.isnan(C[i])
        last_px = np.where(ok, C[i], last_px)
        val = np.nan_to_num(qty * last_px)
        eq[i] = cash + val.sum()
        wts[i] = val / eq[i] if eq[i] > 0 else 0.0

    # 기간 끝에 남은 거래는 평가금으로 마감 (비용 없이)
    for j in list(open_tr):
        open_tr[j][2] += qty[j] * np.nan_to_num(last_px[j])
        close_trade(j, n - 1, "open")

    tr = pd.DataFrame(trades, columns=["asset", "entry", "exit", "invested", "received", "ret", "reason"])
    return Result(
        equity=pd.Series(eq, index=idx),
        weights=pd.DataFrame(wts, index=idx, columns=cols),
        trades=tr,
        costs_paid=total_cost,
        turnover=pd.Series(turn, index=idx),
        orders=pd.Series(nord, index=idx),
    )
