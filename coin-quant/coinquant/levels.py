"""매물대·수평 저항/지지선 (그날까지의 데이터만 씀).

1) 매물대(거래량 분포): 최근 window 일 동안 하루 대표가격((고+저+종)/3)에 그날 거래대금을 쌓은 가격대별 히스토그램(로그 가격, bins 칸).
   평균의 hvn_mult 배 이상 거래가 몰린 칸을 '강한 매물대'로 보고 붙어 있는 칸끼리 묶음.
2) 수평선: 최근 lookback 일의 고점·저점(앞뒤 k 일보다 높/낮은 봉, k 일 뒤에야 확정 → 미래 데이터 아님)을
   가격이 가까운 것끼리(허용폭 tol) 묶은 선. 부딪힌 횟수 = 묶인 고점·저점 수. 고점이 저점이 되는 '역할 전환'도 같은 선.

결과는 날짜 × 코인 표: 위쪽 가장 가까운 저항(아래 경계·위 경계·강도), 아래쪽 가장 가까운 지지.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def volume_zones(high: pd.Series, low: pd.Series, close: pd.Series, qv: pd.Series, window=180, bins=40,
                 hvn_mult=1.5) -> pd.DataFrame:
    """날짜별: 위쪽 매물대(res_lo, res_hi, res_share), 아래쪽 매물대(sup_lo, sup_hi, sup_share), 지금 가격이 매물대 안인지."""
    H, L, C, V = (s.to_numpy(float) for s in (high, low, close, qv))
    tp = np.log((H + L + C) / 3)
    n = len(C)
    out = np.full((n, 7), np.nan)
    for t in range(window, n):
        sl = slice(t - window + 1, t + 1)
        x, w = tp[sl], V[sl]
        ok = ~np.isnan(x) & ~np.isnan(w) & (w > 0)
        if ok.sum() < window * 0.6 or np.isnan(C[t]):
            continue
        lo, hi = np.nanmin(np.log(L[sl])), np.nanmax(np.log(H[sl]))
        if not hi > lo:
            continue
        hist, edges = np.histogram(x[ok], bins=bins, range=(lo, hi), weights=w[ok])
        strong = hist >= hvn_mult * hist.mean()
        zones = []
        i = 0
        while i < bins:
            if strong[i]:
                j = i
                while j + 1 < bins and strong[j + 1]:
                    j += 1
                zones.append((edges[i], edges[j + 1], hist[i:j + 1].sum() / hist.sum()))
                i = j + 1
            else:
                i += 1
        lc = np.log(C[t])
        above = [z for z in zones if z[0] > lc]
        below = [z for z in zones if z[1] < lc]
        inside = any(z[0] <= lc <= z[1] for z in zones)
        if above:
            z = min(above, key=lambda z: z[0])
            out[t, 0:3] = np.exp(z[0]), np.exp(z[1]), z[2]
        if below:
            z = max(below, key=lambda z: z[1])
            out[t, 3:6] = np.exp(z[0]), np.exp(z[1]), z[2]
        out[t, 6] = float(inside)
    return pd.DataFrame(out, index=close.index, columns=["res_lo", "res_hi", "res_share", "sup_lo", "sup_hi", "sup_share", "in_zone"])


def pivot_lines(high: pd.Series, low: pd.Series, close: pd.Series, atr: pd.Series, k=5, lookback=365, min_touch=3,
                tol_pct=0.01, tol_atr=0.5) -> pd.DataFrame:
    """날짜별: 위쪽 가장 가까운 '강한 선'(부딪힘 min_touch 회 이상)과 아래쪽 가장 가까운 강한 선, 그리고 약한 선(2회)."""
    H, L, C, A = (s.to_numpy(float) for s in (high, low, close, atr))
    n = len(C)
    piv = []   # (확정일, 피벗일, 가격)
    for j in range(k, n - k):
        if np.isnan(H[j]) or np.isnan(L[j]):
            continue
        if H[j] == np.nanmax(H[j - k:j + k + 1]):
            piv.append((j + k, j, H[j]))
        if L[j] == np.nanmin(L[j - k:j + k + 1]):
            piv.append((j + k, j, L[j]))
    piv.sort()
    out = np.full((n, 8), np.nan)
    p_conf = np.array([p[0] for p in piv]) if piv else np.array([])
    p_day = np.array([p[1] for p in piv]) if piv else np.array([])
    p_px = np.array([p[2] for p in piv]) if piv else np.array([])
    for t in range(n):
        if np.isnan(C[t]) or not len(piv):
            continue
        m = (p_conf <= t) & (p_day >= t - lookback)
        if m.sum() < 2:
            continue
        px = np.sort(p_px[m])
        tol = max(tol_pct, tol_atr * (A[t] / C[t]) if not np.isnan(A[t]) else tol_pct)
        lines = []           # (중심가, 아래, 위, 횟수)
        grp = [px[0]]
        for v in px[1:]:
            if v <= np.mean(grp) * (1 + tol):
                grp.append(v)
            else:
                lines.append((np.mean(grp), grp[0], grp[-1], len(grp)))
                grp = [v]
        lines.append((np.mean(grp), grp[0], grp[-1], len(grp)))
        c = C[t]
        for s_i, thr in ((0, min_touch), (4, 2)):
            up = [x for x in lines if x[3] >= thr and x[1] > c]
            dn = [x for x in lines if x[3] >= thr and x[2] < c]
            if up:
                x = min(up, key=lambda x: x[1])
                out[t, s_i], out[t, s_i + 1] = x[1], x[3]
            if dn:
                x = max(dn, key=lambda x: x[2])
                out[t, s_i + 2], out[t, s_i + 3] = x[2], x[3]
    return pd.DataFrame(out, index=close.index, columns=["res", "res_touch", "sup", "sup_touch",
                                                         "res2", "res2_touch", "sup2", "sup2_touch"])


def all_levels(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, qv: pd.DataFrame, atr: pd.DataFrame,
               assets=None) -> dict[str, pd.DataFrame]:
    """코인별로 계산해 날짜 × 코인 표들로."""
    assets = assets or list(close.columns)
    cols: dict[str, dict] = {}
    for a in assets:
        z = volume_zones(high[a], low[a], close[a], qv[a])
        p = pivot_lines(high[a], low[a], close[a], atr[a])
        for df, pre in ((z, "vz_"), (p, "pl_")):
            for c in df.columns:
                cols.setdefault(pre + c, {})[a] = df[c]
    return {k: pd.DataFrame(v) for k, v in cols.items()}
