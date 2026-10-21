"""다중 기간 추세 판정 (스윙 매매용).

기간 3개 × 근거 3개로 판정하고, 판정마다 근거 숫자를 같이 돌려줍니다.

기간
- 단기 (며칠~2주):  EMA 8/21,   20일 채널,  14일 회귀
- 중기 (몇 주~2달): EMA 20/50,  55일 채널,  50일 회귀
- 장기 (몇 달 이상): EMA 50/200, 150일 채널, 150일 회귀

근거 (각 −1 / 0 / +1)
1. 이동평균: 빠른선 > 느린선 이고 느린선이 올라가는 중이면 +1, 반대(아래 + 내려가는 중)면 −1, 섞이면 0
2. 채널 위치: 최근 N일 고가~저가 사이에서 지금 가격 위치. 위쪽 40%(0.6 이상)면 +1, 아래쪽 40%면 −1
3. 회귀 추세선: 로그 가격 회귀 기울기 방향 + 설명력(R²) 0.3 이상이어야 인정 (들쭉날쭉한 움직임은 추세로 안 봄)

점수 = 세 근거 합 (−3 ~ +3). +2 이상 '상승', −2 이하 '하락', 나머지 '횡보·불명확'.
매매용 상태는 들락날락을 줄이려고 히스테리시스를 둡니다: 점수 +2 이상에서 켜고, 0 이하로 떨어져야 끕니다.

모든 값은 그날 종가까지의 데이터만 씁니다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind

HORIZONS = {
    "단기": {"fast": 8, "slow": 21, "chan": 20, "reg": 14, "rise": 3, "desc": "며칠~2주"},
    "중기": {"fast": 20, "slow": 50, "chan": 55, "reg": 50, "rise": 5, "desc": "몇 주~2달"},
    "장기": {"fast": 50, "slow": 200, "chan": 150, "reg": 150, "rise": 10, "desc": "몇 달 이상"},
}
R2_MIN = 0.3
ON_AT, OFF_AT = 2, 0


@dataclass
class HorizonTrend:
    score: pd.DataFrame        # −3 ~ +3
    ma: pd.DataFrame           # 근거별 −1/0/+1
    chan: pd.DataFrame
    reg: pd.DataFrame
    # 근거 숫자
    fast: pd.DataFrame
    slow: pd.DataFrame
    chan_pos: pd.DataFrame     # 0~1
    chan_hi: pd.DataFrame
    chan_lo: pd.DataFrame
    slope: pd.DataFrame        # 하루 로그 기울기
    r2: pd.DataFrame
    state: pd.DataFrame        # 히스테리시스 적용 보유 상태 (True/False)


def hysteresis(score: pd.DataFrame, on_at=ON_AT, off_at=OFF_AT) -> pd.DataFrame:
    s = score.to_numpy(float)
    out = np.zeros(s.shape, dtype=bool)
    cur = np.zeros(s.shape[1], dtype=bool)
    for i in range(len(s)):
        row = s[i]
        cur = np.where(np.isnan(row), False, np.where(row >= on_at, True, np.where(row <= off_at, False, cur)))
        out[i] = cur
    return pd.DataFrame(out, index=score.index, columns=score.columns)


def horizon_trend(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame, p: dict) -> HorizonTrend:
    fast = ind.ema(close, p["fast"])
    slow = ind.ema(close, p["slow"])
    rising = slow > slow.shift(p["rise"])
    falling = slow < slow.shift(p["rise"])
    ma = ((fast > slow) & rising).astype(int) - ((fast < slow) & falling).astype(int)
    hi = high.rolling(p["chan"], min_periods=p["chan"]).max()
    lo = low.rolling(p["chan"], min_periods=p["chan"]).min()
    pos = (close - lo) / (hi - lo).replace(0, np.nan)
    chan = (pos >= 0.6).astype(int) - (pos <= 0.4).astype(int)
    slope, _, _ = ind.regression_slope(close, p["reg"])
    r2 = ind.regression_r2(close, p["reg"])
    reg = ((slope > 0) & (r2 >= R2_MIN)).astype(int) - ((slope < 0) & (r2 >= R2_MIN)).astype(int)
    ready = slow.notna() & pos.notna() & r2.notna() & slow.shift(p["rise"]).notna()
    score = (ma + chan + reg).where(ready)
    return HorizonTrend(score, ma.where(ready), chan.where(ready), reg.where(ready), fast, slow, pos, hi, lo, slope, r2,
                        hysteresis(score))


class MultiTrend:
    """세 기간 추세를 한 번에. Features 처럼 한 번 계산해서 여러 곳에서 씀."""

    def __init__(self, close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame):
        self.close = close
        self.h = {name: horizon_trend(close, high, low, p) for name, p in HORIZONS.items()}

    def label(self, score: float) -> str:
        if score is None or np.isnan(score):
            return "판단 불가(데이터 부족)"
        return "상승" if score >= ON_AT else "하락" if score <= -ON_AT else "횡보·불명확"

    # ---------- 매매 방식 (보유 비율 0~1)
    def systems(self) -> dict[str, pd.DataFrame]:
        S, M, L = (self.h[k].state for k in ("단기", "중기", "장기"))
        Ls = self.h["장기"].score
        Ms = self.h["중기"].score
        return {
            "단기만": S.astype(float),
            "중기만": M.astype(float),
            "장기만": L.astype(float),
            "아무 기간이나 상승이면 보유(OR)": (S | M | L).astype(float),
            "3단 분할(기간마다 1/3씩)": (S.astype(float) + M.astype(float) + L.astype(float)) / 3,
            "계층(장기 하락 아님 + 중기 보유 + 단기로 진입)": _layered(S, M, Ls),
            "3단 분할 + 장기 하락이면 단기 몫만": _tranche_capped(S, M, L, Ls),
            "중기 보유 + 장기 하락이면 중단": (M & ~(Ls <= -ON_AT)).astype(float),
        }

    def snapshot(self, date, asset) -> list[dict]:
        """리포트용: 기간마다 판정과 근거 숫자."""
        rows = []
        c = self.close.at[date, asset]
        for name, t in self.h.items():
            p = HORIZONS[name]
            sc = t.score.at[date, asset]
            rows.append({
                "기간": f"{name}({p['desc']})", "판정": self.label(sc), "점수": sc,
                "보유 상태(히스테리시스)": "켜짐" if bool(t.state.at[date, asset]) else "꺼짐",
                "이동평균": f"EMA{p['fast']} {num(t.fast.at[date, asset])} {'>' if t.fast.at[date, asset] > t.slow.at[date, asset] else '<'} "
                        f"EMA{p['slow']} {num(t.slow.at[date, asset])} ({_sign(t.ma.at[date, asset])})",
                "채널": f"{p['chan']}일 범위 {num(t.chan_lo.at[date, asset])}~{num(t.chan_hi.at[date, asset])} 중 "
                      f"{t.chan_pos.at[date, asset] * 100:.0f}% 위치 ({_sign(t.chan.at[date, asset])})",
                "회귀": f"{p['reg']}일 기울기 {np.expm1(t.slope.at[date, asset]) * 100:+.2f}%/일, R² {t.r2.at[date, asset]:.2f} "
                      f"({_sign(t.reg.at[date, asset])})",
                # 청산 기준 가격: 채널 아래쪽 40% 선 (여기 아래로 닫히면 채널 근거가 −1)
                "채널 하단 40%선": t.chan_lo.at[date, asset] + 0.4 * (t.chan_hi.at[date, asset] - t.chan_lo.at[date, asset]),
                "느린 EMA": t.slow.at[date, asset],
                "종가": c,
            })
        return rows

    def combo(self) -> pd.DataFrame:
        """날짜·코인별 '단기/중기/장기' 판정 조합 문자열 (예: '상승/상승/횡보')."""
        labs = []
        for k in ("단기", "중기", "장기"):
            s = self.h[k].score
            labs.append(pd.DataFrame(np.select([s >= ON_AT, s <= -ON_AT, s.notna()], ["상승", "하락", "횡보"], default=""),
                                     index=s.index, columns=s.columns))
        out = labs[0] + "/" + labs[1] + "/" + labs[2]
        return out.where(~(labs[0] == "") & ~(labs[1] == "") & ~(labs[2] == ""), "")


def num(v) -> str:
    """가격 표시: 1,000 이상은 천 단위 쉼표, 작은 값은 유효숫자 4자리."""
    return "-" if v is None or pd.isna(v) else (f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}")


def _sign(v) -> str:
    return "−" if pd.isna(v) else {1: "+1", 0: "0", -1: "−1"}.get(int(v), "0")


def _layered(S, M, Ls) -> pd.DataFrame:
    """진입: 장기 하락 아님 + 중기 점수 상승 + 단기 켜짐. 유지: 중기 켜짐(히스테리시스) 동안. 청산: 중기 꺼짐 또는 장기 하락."""
    s, m, l = S.to_numpy(bool), M.to_numpy(bool), Ls.to_numpy(float)
    out = np.zeros(s.shape, dtype=float)
    cur = np.zeros(s.shape[1], dtype=bool)
    for i in range(len(s)):
        long_down = np.nan_to_num(l[i], nan=-9) <= -ON_AT
        enter = s[i] & m[i] & ~long_down
        cur = np.where(enter, True, np.where(~m[i] | long_down, False, cur))
        out[i] = cur
    return pd.DataFrame(out, index=S.index, columns=S.columns)


def _tranche_capped(S, M, L, Ls) -> pd.DataFrame:
    w = (S.astype(float) + M.astype(float) + L.astype(float)) / 3
    long_down = Ls <= -ON_AT
    return w.where(~long_down, S.astype(float) / 3)


# ---------------------------------------------------------------- 지그재그: 사후에 본 '실제 추세 구간'

def zigzag(close: pd.Series, thr: float) -> list[tuple]:
    """로그 기준 thr 이상 되돌림이 나와야 방향이 바뀌는 지그재그.

    (시작일, 끝일, 방향 +1/−1, 로그수익) 목록. 마지막 미완성 구간은 뺍니다. 사후에 본 정의라 '평가'에만 씁니다.
    """
    x = np.log(close.dropna())
    v, idx = x.to_numpy(), x.index
    legs: list[tuple] = []
    if len(v) < 3:
        return legs
    trend, piv, ext, lo, hi = 0, 0, 0, 0, 0
    for i in range(1, len(v)):
        if trend == 0:
            lo = i if v[i] < v[lo] else lo
            hi = i if v[i] > v[hi] else hi
            if v[i] - v[lo] >= thr:
                trend, piv, ext = 1, lo, i
            elif v[hi] - v[i] >= thr:
                trend, piv, ext = -1, hi, i
        elif trend == 1:
            if v[i] > v[ext]:
                ext = i
            elif v[ext] - v[i] >= thr:
                legs.append((idx[piv], idx[ext], 1, v[ext] - v[piv]))
                trend, piv, ext = -1, ext, i
        else:
            if v[i] < v[ext]:
                ext = i
            elif v[i] - v[ext] >= thr:
                legs.append((idx[piv], idx[ext], -1, v[ext] - v[piv]))
                trend, piv, ext = 1, ext, i
    return legs
