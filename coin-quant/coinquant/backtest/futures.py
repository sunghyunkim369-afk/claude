"""무기한 선물 롱/숏 백테스트 (숏 검증용).

현물 엔진(engine.py)과 같은 규칙: t일 종가로 판단 → t+1일 시가 체결, 매매마다 수수료+슬리피지.
선물만의 것
- 수량이 음수면 숏. 평가금 = 현금 + Σ 수량×가격 (숏 판 돈은 현금에 들어옴)
- 펀딩비: 그날 펀딩 합계 f 만큼, 보유 명목금액 × f 를 롱이 내고 숏이 받음 (f 가 음수면 반대)
- 청산(격리 증거금, 레버리지 L): 숏은 그날 고가가 평균 진입가 × (1 + 1/L − 유지증거금률)에 닿으면 그 가격에 강제 청산
  (롱은 저가가 진입가 × (1 − 1/L + 유지증거금률)). 1배면 가격이 약 2배가 돼야 청산.
- 상장폐지(선물 페어 종료): 마지막 종가에 정리
'목표 비중'은 평가금 대비 명목금액 비율. −0.3 이면 평가금의 30%만큼 숏.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FutRules:
    rebalance_days: int = 1
    min_trade: float = 0.01
    leverage: float = 1.0
    maint_margin: float = 0.005
    exec_lag: int = 0
    max_gross: float = 1.0


@dataclass
class FutResult:
    equity: pd.Series
    weights: pd.DataFrame
    trades: pd.DataFrame
    fees: float
    funding_pnl: pd.Series         # 날짜별 펀딩 손익 (평가금 대비)
    liquidations: int
    turnover: pd.Series
    meta: dict = field(default_factory=dict)


def run_futures(target: pd.DataFrame, open_: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame,
                close: pd.DataFrame, funding: pd.DataFrame, rate: pd.Series, rules: FutRules | None = None,
                start=None, end=None) -> FutResult:
    rules = rules or FutRules()
    cols = list(target.columns)
    idx = close.loc[start:end].index
    C = close.reindex(index=idx, columns=cols).to_numpy(float)
    O = open_.reindex(index=idx, columns=cols).to_numpy(float)
    Hh = high.reindex(index=idx, columns=cols).to_numpy(float)
    Lw = low.reindex(index=idx, columns=cols).to_numpy(float)
    Fd = funding.reindex(index=idx, columns=cols).fillna(0.0).to_numpy(float)
    W = target.reindex(index=close.index, columns=cols).loc[idx].fillna(0.0).to_numpy(float)
    R = rate.reindex(cols).to_numpy(float)
    n, m = C.shape
    qty = np.zeros(m)
    entry = np.full(m, np.nan)
    cash = 1.0
    last = np.full(m, np.nan)
    eq = np.empty(n)
    wts = np.zeros((n, m))
    fpnl = np.zeros(n)
    turn = np.zeros(n)
    fees = 0.0
    liq = 0
    open_tr: dict[int, list] = {}
    trades = []
    L, mm = rules.leverage, rules.maint_margin

    def close_trade(j, i, reason, px):
        tr = open_tr.pop(j, None)
        if tr is not None:
            side = "숏" if tr[3] < 0 else "롱"
            ret = tr[2] / tr[1] if tr[1] > 0 else np.nan     # 손익 / 진입 명목금액
            trades.append((cols[j], side, tr[0], idx[i], ret, tr[4], reason))

    def trade(j, dq, px, i):
        """수량 dq 체결 (가격 px). 방향이 바뀌면 '청산 + 새 진입' 두 번으로 나눔."""
        nonlocal cash, fees
        if dq == 0:
            return
        old = qty[j]
        if old != 0 and np.sign(old + dq) != np.sign(old) and old + dq != 0:
            trade(j, -old, px, i)
            trade(j, dq + old, px, i)
            return
        val = abs(dq) * px
        fee = val * R[j]
        cash -= dq * px + fee
        fees += fee
        new = old + dq
        if old == 0:                                  # 새 진입
            entry[j] = px
            open_tr[j] = [idx[i], val, -fee, np.sign(new), 0.0]
        elif abs(new) > abs(old):                     # 늘리기
            entry[j] = (entry[j] * abs(old) + px * abs(dq)) / abs(new)
            open_tr[j][1] += val
            open_tr[j][2] -= fee
        else:                                         # 줄이기·청산: 실현손익 = (체결가 − 진입가) × 줄인 수량(부호 포함)
            open_tr[j][2] += (px - entry[j]) * (-dq) - fee
        qty[j] = new
        if new == 0:
            entry[j] = np.nan
            close_trade(j, i, "신호", px)

    for i in range(n):
        # 1) 상장폐지: 마지막 종가로 정리
        dead = (qty != 0) & np.isnan(C[i]) & ~np.isnan(last)
        for j in np.flatnonzero(dead):
            trade(j, -qty[j], last[j], i)
            if j in open_tr:
                close_trade(j, i, "폐지", last[j])
        # 2) 리밸런스 (어제 종가 판단 → 오늘 시가)
        if i > 0 and (i - 1) % rules.rebalance_days == 0:
            px = O[i] if rules.exec_lag == 0 else C[i]
            ok = ~np.isnan(px) & (px > 0) & ~np.isnan(C[i - 1])
            equity = cash + np.nansum(np.where(ok, qty * px, qty * np.nan_to_num(last)))
            w = np.where(ok, W[i - 1], 0.0)
            gross = np.abs(w).sum()
            if gross > rules.max_gross:
                w = w * rules.max_gross / gross
            traded = 0.0
            if equity > 0:
                for j in np.flatnonzero(ok):
                    want_q = w[j] * equity / px[j]
                    dq = want_q - qty[j]
                    to_zero = w[j] == 0 and qty[j] != 0
                    flip = qty[j] != 0 and np.sign(want_q) != np.sign(qty[j]) and want_q != 0
                    from_zero = qty[j] == 0 and want_q != 0      # 새 진입·전량 청산·방향 전환은 항상, 크기 조정만 최소 비중 적용
                    if abs(dq * px[j]) < rules.min_trade * equity and not to_zero and not flip and not from_zero:
                        continue
                    trade(j, dq, px[j], i)
                    traded += abs(dq * px[j])
                turn[i] = traded / equity
            # 펀딩: 오늘 들고 있는 포지션 기준
            f = qty * np.nan_to_num(px) * Fd[i]
            fsum = -np.nansum(f)          # 롱은 냄, 숏은 받음 (f>0)
            cash += fsum
            fpnl[i] = fsum / equity if equity > 0 else 0.0
            for j in np.flatnonzero((qty != 0) & (Fd[i] != 0)):
                if j in open_tr:
                    open_tr[j][2] += -f[j]
                    open_tr[j][4] += -f[j]
        elif i > 0:
            px = O[i]
            f = qty * np.nan_to_num(px) * Fd[i]
            cash += -np.nansum(f)
            eqp = cash + np.nansum(qty * np.nan_to_num(last))
            fpnl[i] = -np.nansum(f) / eqp if eqp > 0 else 0.0
            for j in np.flatnonzero((qty != 0) & (Fd[i] != 0)):
                if j in open_tr:
                    open_tr[j][2] += -f[j]
                    open_tr[j][4] += -f[j]
        # 3) 청산 확인 (그날 고가·저가)
        for j in np.flatnonzero(qty != 0):
            if np.isnan(entry[j]):
                continue
            if qty[j] < 0:
                lp = entry[j] * (1 + 1 / L - mm)
                if not np.isnan(Hh[i, j]) and Hh[i, j] >= lp:
                    trade(j, -qty[j], lp, i)
                    liq += 1
                    if trades and trades[-1][0] == cols[j]:
                        trades[-1] = trades[-1][:6] + ("청산",)
            else:
                lp = entry[j] * (1 - 1 / L + mm)
                if not np.isnan(Lw[i, j]) and Lw[i, j] <= lp:
                    trade(j, -qty[j], lp, i)
                    liq += 1
                    if trades and trades[-1][0] == cols[j]:
                        trades[-1] = trades[-1][:6] + ("청산",)
        # 4) 종가 평가
        okc = ~np.isnan(C[i])
        last = np.where(okc, C[i], last)
        val = qty * np.nan_to_num(last)
        eq[i] = cash + val.sum()
        wts[i] = val / eq[i] if eq[i] > 0 else 0.0
        if eq[i] <= 0:      # 전부 잃음
            eq[i:] = 0.0
            wts[i:] = 0.0
            break
    for j in list(open_tr):
        open_tr[j][2] += qty[j] * (np.nan_to_num(last[j]) - entry[j]) if not np.isnan(entry[j]) else 0.0
        close_trade(j, n - 1, "진행 중", last[j])
    tr = pd.DataFrame(trades, columns=["asset", "side", "entry", "exit", "ret", "funding", "reason"])
    return FutResult(pd.Series(eq, index=idx), pd.DataFrame(wts, index=idx, columns=cols), tr, fees,
                     pd.Series(fpnl, index=idx), liq, pd.Series(turn, index=idx))
