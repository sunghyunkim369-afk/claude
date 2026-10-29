"""선물 단기 검증 — 유명 트레이더·교과서 방식 + 파생상품 지표: `python -m coinquant futures-lab`

질문: 무기한 선물(롱·숏)로 며칠~몇 주 단위 매매를 하면, 비용·펀딩비를 내고도 계속 보유보다 나은 방식이 있나?

데이터: 바이낸스 USDⓈ-M 무기한 선물 1시간봉(2020-01~, SOL 2020-09~) → 4시간봉·일봉, 8시간 펀딩비.
대상: BTC·ETH·SOL 각각, 그리고 3개 균등(주 판정).
구간: 개발 2020-01 ~ 2024-12 (방식 판정), 확인 2025-01 ~ 현재 (개발 판정을 통과한 것만, 한 번만 봄).

규칙(미리 정함, 이 데이터로 숫자를 고르지 않음):
- 숫자는 원저자·교과서에 나온 값 그대로 (터틀 20/10·55/20·2N, 슈퍼트렌드 10/3, MACD 12/26/9, 코너스 RSI2 10/90·200/5 등).
- 봉 종가에 판단 → 그 종가(=다음 봉 시가)에 체결. 손절·추적 손절은 봉 안에서 닿으면 그 가격(갭이면 시가)에 나감.
- 비용: 한 번 사거나 팔 때 수수료 0.055% + 슬리피지 0.03% = 0.085% (비용 2배로도 따로 계산). 펀딩비는 실제 기록대로 냄/받음.
- 레버리지 1배(포지션 = 그 코인 몫 자금) 기준으로 비교하고, 통과한 것만 2·3·5·10배와 청산 위험을 따로 봄.

개발 구간 통과 기준(3개 균등, 미리 정함):
 1) 비용 2배에서도 연복리 > 0
 2) 무작위 타이밍(포지션 순서를 통째로 시간 이동, 100번)보다 샤프 상위 10%
 3) 한 봉 늦게 체결해도 연복리 > 0 이고 원래의 절반 이상
 4) 코인 3개 중 2개 이상에서 연복리 > 0
 5) 샤프가 같은 기간 선물 롱 계속 보유보다 높음
확인 구간 통과: 연복리 > 0 이고 샤프 ≥ 0.5.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, indicators as ind
from ..backtest import metrics
from ..trend import MultiTrend

SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
DEV_END = pd.Timestamp("2024-12-31 23:59")
HOLD_START = pd.Timestamp("2025-01-01")
FEE, SLIP = 0.00055, 0.0003
COST = FEE + SLIP
BPY = {"4h": 6 * 365, "1d": 365}
TF_DELTA = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
MMR = 0.005   # 유지 증거금률(대략) — 청산 계산용


# ---------------------------------------------------------------- 데이터

def load_1h(cache: Path, log=print) -> dict[str, pd.DataFrame]:
    from ..data import binance_public as bp
    out = {}
    d = cache / "binance" / "um_1h"
    d.mkdir(parents=True, exist_ok=True)
    for a, s in SYMS.items():
        p = d / f"{s}.csv.gz"
        if not p.exists():
            log(f"  {s} 1시간봉 받는 중")
            bp.fetch_um_klines(s, "1h").to_csv(p, index=False)
        out[a] = pd.read_csv(p, parse_dates=["time"]).set_index("time")
    return out


def bars(h1: dict[str, pd.DataFrame], tf: str) -> dict[str, pd.DataFrame]:
    """넓은 표(행=봉 시작 시각, 열=코인). 시간이 다 차지 않은 봉은 버림."""
    rule = {"4h": "4h", "1d": "1D"}[tf]
    need = {"4h": 4, "1d": 24}[tf]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "quote_volume": "sum", "taker_buy_quote": "sum"}
    per = {}
    for a, df in h1.items():
        g = df.resample(rule, label="left", closed="left")
        x = g.agg(agg)
        x = x[g.size() >= need].dropna(subset=["close"])
        per[a] = x
    return {k: pd.DataFrame({a: per[a][k] for a in per}).sort_index() for k in agg}


def funding_bars(cache: Path, index: pd.DatetimeIndex, tf: str) -> pd.DataFrame:
    """봉마다 낸 펀딩비 합 (펀딩 시각 T 는 T 에 끝나는 봉에 붙임)."""
    out = pd.DataFrame(0.0, index=index, columns=list(SYMS))
    rule = {"4h": "4h", "1d": "1D"}[tf]
    for a, s in SYMS.items():
        p = cache / "binance" / f"funding_{s}.csv"
        if not p.exists():
            continue
        f = pd.read_csv(p, parse_dates=["time"])
        b = (f["time"] - pd.Timedelta(microseconds=1)).dt.floor(rule)
        out[a] = f.groupby(b)["rate"].sum().reindex(index).fillna(0.0)
    return out


def daily_to(index: pd.DatetimeIndex, daily: pd.DataFrame, tf: str) -> pd.DataFrame:
    """일봉 값을 tf 봉에 붙임: 그 봉이 끝나는 시각에 이미 끝난 날의 값만 (미래 정보 없음)."""
    known = daily.copy()
    known.index = known.index + pd.Timedelta(days=1)          # 그날 종가는 다음 날 00:00 에 앎
    x = known.reindex(index + TF_DELTA[tf], method="ffill")
    x.index = index
    return x


# ---------------------------------------------------------------- 지표

def supertrend(h, l_, c, n=10, m=3.0) -> pd.DataFrame:
    atr = ind.atr(h, l_, c, n)
    hl2 = (h + l_) / 2
    UB, LB = (hl2 + m * atr).to_numpy(), (hl2 - m * atr).to_numpy()
    C = c.to_numpy()
    out = np.full(C.shape, np.nan)
    for j in range(C.shape[1]):
        fu = fl = np.nan
        d = 0
        for i in range(1, len(C)):
            if np.isnan(UB[i, j]) or np.isnan(C[i, j]):
                continue
            pu, pl = fu, fl
            fu = UB[i, j] if (np.isnan(pu) or UB[i, j] < pu or C[i - 1, j] > pu) else pu
            fl = LB[i, j] if (np.isnan(pl) or LB[i, j] > pl or C[i - 1, j] < pl) else pl
            if d == 0:
                d = 1 if C[i, j] > hl2.iat[i, j] else -1
            elif d == -1 and not np.isnan(pu) and C[i, j] > pu:
                d = 1
            elif d == 1 and not np.isnan(pl) and C[i, j] < pl:
                d = -1
            out[i, j] = d
    return pd.DataFrame(out, index=c.index, columns=c.columns)


def bollinger(c, n=20, k=2.0):
    mid = ind.sma(c, n)
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def cross_up(a, b):
    return (a > b) & (a.shift(1) <= b.shift(1))


def past_quantile(x: pd.DataFrame, n: int, q: float) -> pd.DataFrame:
    """직전 n개(오늘 제외)의 분위수."""
    return x.shift(1).rolling(n, min_periods=n // 2).quantile(q)


# ---------------------------------------------------------------- 방식 정의

@dataclass
class Spec:
    name: str
    tf: str
    origin: str
    el: pd.DataFrame
    es: pd.DataFrame
    xl: pd.DataFrame
    xs: pd.DataFrame
    stop: pd.DataFrame | None = None     # 진입 때 손절 거리(가격)
    trail: pd.DataFrame | None = None    # 추적 손절 거리(가격), 종가 기준으로 끌어올림
    max_bars: int | None = None
    kind: str = "추세"
    extra: dict = field(default_factory=dict)


def specs(B: dict, tf: str, D: dict) -> list[Spec]:
    o, h, l_, c, v = B["open"], B["high"], B["low"], B["close"], B["volume"]
    F = lambda x: x.fillna(False).astype(bool)   # noqa: E731
    S: list[Spec] = []
    atr20 = ind.atr(h, l_, c, 20)
    # 1. 터틀 (리처드 데니스·커티스 페이스 『터틀의 방식』): 20봉 돌파 / 10봉 반대 이탈 청산 / 2N 손절, 55/20
    for nin, nout, nm in ((20, 10, "터틀 1 (20봉 돌파·10봉 청산·2N 손절)"), (55, 20, "터틀 2 (55봉 돌파·20봉 청산·2N 손절)")):
        S.append(Spec(nm, tf, "Dennis·Faith, Way of the Turtle", F(c > ind.donchian_high(h, nin)), F(c < ind.donchian_low(l_, nin)),
                      F(c < ind.donchian_low(l_, nout)), F(c > ind.donchian_high(h, nout)), stop=2 * atr20))
    # 2. 클레나우 『Following the Trend』: 50/100 EMA 추세 필터 + 50봉 신고가 진입 + 3 ATR(100) 추적 손절
    e50, e100 = ind.ema(c, 50), ind.ema(c, 100)
    atr100 = ind.atr(h, l_, c, 100)
    nev = F(c * 0 > 1)
    S.append(Spec("클레나우 (50/100 EMA 필터·50봉 신고가·3ATR 추적 손절)", tf, "Andreas Clenow, Following the Trend",
                  F((e50 > e100) & (c >= c.rolling(50).max())), F((e50 < e100) & (c <= c.rolling(50).min())),
                  nev, nev, trail=3 * atr100))
    # 3. 슈퍼트렌드 (10, 3) — 트레이딩뷰에서 가장 많이 쓰는 추세 지표, 항상 롱 또는 숏
    st = supertrend(h, l_, c)
    S.append(Spec("슈퍼트렌드 (ATR 10·3배, 항상 롱/숏)", tf, "Olivier Seban, TradingView 기본값", F(st == 1), F(st == -1), F(st == -1), F(st == 1)))
    # 4. MACD 12/26/9 교차 + 200 EMA 필터 (유튜브에서 흔한 'MACD + 200EMA' 방식)
    line, sig, _ = ind.macd(c)
    e200 = ind.ema(c, 200)
    up, dn = cross_up(line, sig), cross_up(sig, line)
    S.append(Spec("MACD 교차 + 200 EMA 필터", tf, "Gerald Appel MACD + 200EMA 필터", F(up & (c > e200)), F(dn & (c < e200)), F(dn), F(up)))
    # 5. EMA 9/21 교차 (항상 롱/숏)
    e9, e21 = ind.ema(c, 9), ind.ema(c, 21)
    S.append(Spec("EMA 9/21 교차 (항상 롱/숏)", tf, "흔한 단기 이동평균 교차", F(e9 > e21), F(e9 < e21), F(e9 < e21), F(e9 > e21)))
    # 6. 볼린저 스퀴즈 돌파 (존 볼린저): 밴드 폭이 직전 120봉 하위 10% 안(최근 5봉) → 밴드 밖 마감 방향, 중심선 복귀 청산
    mid, ub, lb = bollinger(c)
    bw = (ub - lb) / mid
    sq = (bw <= past_quantile(bw, 120, 0.10)).rolling(5, min_periods=1).max().astype(bool)
    S.append(Spec("볼린저 스퀴즈 돌파 (20·2, 폭 하위 10%)", tf, "John Bollinger, Bollinger on Bollinger Bands",
                  F(sq & (c > ub)), F(sq & (c < lb)), F(c < mid), F(c > mid)))
    # 7. 코너스 RSI(2) 역추세: 200선 위 RSI2<10 매수 → 5선 위 마감 청산 (숏은 반대)
    r2 = ind.rsi(c, 2)
    s200, s5 = ind.sma(c, 200), ind.sma(c, 5)
    S.append(Spec("코너스 RSI(2) 눌림·반등 (200선 필터)", tf, "Larry Connors, Short Term Trading Strategies That Work",
                  F((r2 < 10) & (c > s200)), F((r2 > 90) & (c < s200)), F(c > s5), F(c < s5), kind="역추세"))
    # 8. 볼린저 역추세: 아래 밴드 밖 마감 매수 / 위 밴드 밖 숏 → 중심선에서 청산
    S.append(Spec("볼린저 밴드 역추세 (밴드 밖 → 중심선)", tf, "교과서 평균회귀", F(c < lb), F(c > ub), F(c >= mid), F(c <= mid), kind="역추세"))
    # 9. 테이커(시장가) 매수 비중 — 주문 흐름: 직전 1년(4h는 90일) 상위 10% → 롱, 하위 10% → 숏, 하루 보유
    tbr = B["taker_buy_quote"] / B["quote_volume"].replace(0, np.nan)
    n_q = 365 if tf == "1d" else 6 * 90
    hold = 1 if tf == "1d" else 6
    S.append(Spec("테이커 매수 비중 극단 따라가기 (하루)", tf, "주문 흐름(order flow) 지표", F(tbr > past_quantile(tbr, n_q, 0.9)),
                  F(tbr < past_quantile(tbr, n_q, 0.1)), nev, nev, max_bars=hold, kind="파생 지표"))
    # 10. 우리 단기 점수를 롱·숏으로 (+2 롱, −2 숏, 0 이면 청산)
    mt = MultiTrend(c, h, l_)
    Ssc = mt.h["단기"].score
    S.append(Spec("우리 단기 점수 롱·숏 (+2 롱 / −2 숏 / 0 청산)", tf, "이 프로그램의 단기 점수", F(Ssc >= 2), F(Ssc <= -2), F(Ssc <= 0), F(Ssc >= 0)))
    if tf == "1d":
        Msc = mt.h["중기"].score
        S.append(Spec("우리 중기 점수 롱·숏 (+2 롱 / −2 숏 / 0 청산)", tf, "이 프로그램의 중기 점수", F(Msc >= 2), F(Msc <= -2), F(Msc <= 0), F(Msc >= 0)))
        # 11. 시계열 모멘텀 (Moskowitz·Ooi·Pedersen 2012, 코인: Liu·Tsyvinski 2021): 지난 N일 수익 부호
        for n in (30, 7):
            m = c / c.shift(n) - 1
            S.append(Spec(f"시계열 모멘텀 {n}일 (부호대로 롱/숏)", tf, "Moskowitz·Ooi·Pedersen 2012 / Liu·Tsyvinski 2021",
                          F(m > 0), F(m < 0), F(m <= 0), F(m >= 0)))
        # 12. 펀딩비 역발상: 하루 펀딩 합이 직전 1년 상위 5% → 숏 3일, 하위 5% → 롱 3일
        fd = B["funding"]
        S.append(Spec("펀딩비 극단 역발상 (3일)", tf, "파생상품 쏠림 지표", F(fd < past_quantile(fd, 365, 0.05)),
                      F(fd > past_quantile(fd, 365, 0.95)), nev, nev, max_bars=3, kind="파생 지표"))
    else:
        # 13. 거래량 급증 돌파 (4h): 20봉 신고가 + 거래량 2배 → 10봉 반대 이탈 청산, 2 ATR 손절
        vr = v / v.shift(1).rolling(20, min_periods=20).mean()
        S.append(Spec("거래량 급증 돌파 (20봉·2배·2ATR 손절)", tf, "흔한 돌파 매매", F((c > ind.donchian_high(h, 20)) & (vr > 2)),
                      F((c < ind.donchian_low(l_, 20)) & (vr > 2)), F(c < ind.donchian_low(l_, 10)), F(c > ind.donchian_high(h, 10)),
                      stop=2 * atr20))
        # 14. 삼중 스크린식 (알렉산더 엘더 『주식시장에서 살아남는 심리투자 법칙』): 일봉 추세 방향으로만, 4h RSI 눌림에서 진입
        dmt = MultiTrend(D["close"], D["high"], D["low"])
        tide_up = daily_to(c.index, dmt.h["중기"].state.astype(float), tf) > 0.5
        tide_dn = daily_to(c.index, dmt.h["중기"].score, tf) <= -2
        r14 = ind.rsi(c, 14)
        atr14 = ind.atr(h, l_, c, 14)
        S.append(Spec("삼중 스크린식 (일봉 중기 추세 + 4h RSI 40/60 눌림)", tf, "Alexander Elder, Trading for a Living",
                      F(tide_up & (r14 < 40)), F(tide_dn & (r14 > 60)), F((r14 > 60) | ~tide_up), F((r14 < 40) | ~tide_dn),
                      stop=2 * atr14))
    return S


# ---------------------------------------------------------------- 체결 시뮬레이션

def sim(o, h, l_, c, fund, el, es, xl, xs, stop=None, trail=None, max_bars=None, cost=COST, delay=0):
    """한 코인. 반환: (봉별 순수익, 포지션 배열, 거래 목록[(진입i, 청산i, 방향, 수익, 최대 역행)])."""
    if delay:
        el, es, xl, xs = (np.concatenate([np.zeros(delay, bool), x[:-delay]]) for x in (el, es, xl, xs))
    n = len(c)
    r = np.zeros(n)
    P = np.zeros(n)
    trades = []
    pos, ent_px, ent_i, stp, ext = 0, 0.0, 0, np.nan, 0.0
    for t in range(1, n):
        if np.isnan(c[t]) or np.isnan(c[t - 1]):
            continue
        stopped = False
        if pos != 0:
            ext = min(ext, l_[t] / ent_px - 1) if pos > 0 else max(ext, h[t] / ent_px - 1)
            if not np.isnan(stp) and ((pos > 0 and l_[t] <= stp) or (pos < 0 and h[t] >= stp)):
                px = min(o[t], stp) if pos > 0 else max(o[t], stp)
                r[t] = pos * (px / c[t - 1] - 1) - cost - pos * fund[t]
                trades.append((ent_i, t, pos, pos * (px / ent_px - 1) - 2 * cost, abs(ext)))
                pos, stopped = 0, True
            else:
                r[t] = pos * (c[t] / c[t - 1] - 1) - pos * fund[t]
                if trail is not None and not np.isnan(trail[t]):
                    stp = max(stp if not np.isnan(stp) else -np.inf, c[t] - trail[t]) if pos > 0 else \
                        min(stp if not np.isnan(stp) else np.inf, c[t] + trail[t])
                out = (pos > 0 and xl[t]) or (pos < 0 and xs[t]) or (max_bars is not None and t - ent_i >= max_bars)
                if out:
                    r[t] -= cost
                    trades.append((ent_i, t, pos, pos * (c[t] / ent_px - 1) - 2 * cost, abs(ext)))
                    pos = 0
        if pos == 0 and not stopped:
            side = 1 if el[t] else (-1 if es[t] else 0)
            if side:
                pos, ent_px, ent_i, ext = side, c[t], t, 0.0
                r[t] -= cost
                stp = np.nan
                if stop is not None and not np.isnan(stop[t]):
                    stp = c[t] - side * stop[t]
                if trail is not None and not np.isnan(trail[t]):
                    stp = c[t] - side * trail[t]
        P[t] = pos
    if pos != 0:   # 끝까지 들고 있는 거래
        trades.append((ent_i, n - 1, pos, pos * (c[n - 1] / ent_px - 1) - cost, abs(ext)))
    return r, P, trades


def larry_williams(h1: dict[str, pd.DataFrame], D: dict, cache: Path, k=0.5, ma_filter=False, long_only=False,
                   cost=COST, delay_hours=0, rng=None):
    """래리 윌리엄스 변동성 돌파 (한국에선 강환국 책으로 유명): 오늘 시가 ± k × 어제 범위를 넘는 순간 진입 → 다음 날 시가(UTC 00:00) 청산.
    1시간봉으로 언제 닿았는지 찾음. 같은 시간에 위·아래 둘 다 닿으면 더 나쁜 쪽으로 계산. 펀딩은 진입 뒤 08·16시분.
    rng 를 주면 위약: 같은 날 수·방향 비율로 무작위 날·무작위 시각에 진입."""
    out_r = pd.DataFrame(0.0, index=D["close"].index, columns=list(SYMS))
    out_p = pd.DataFrame(0.0, index=D["close"].index, columns=list(SYMS))
    trades_all = {}
    for a in SYMS:
        df = h1[a]
        fr = pd.read_csv(cache / "binance" / f"funding_{SYMS[a]}.csv", parse_dates=["time"]).set_index("time")["rate"]
        fr.index = fr.index.round("1h")
        f8 = fr[fr.index.hour == 8].groupby(fr[fr.index.hour == 8].index.floor("1D")).sum()
        f16 = fr[fr.index.hour == 16].groupby(fr[fr.index.hour == 16].index.floor("1D")).sum()
        fund_at = lambda d, i: (f8.get(d, 0.0) if i < 8 else 0.0) + (f16.get(d, 0.0) if i < 16 else 0.0)  # noqa: E731
        day = df.index.floor("1D")
        O = D["open"][a]
        Hd, Ld, Cd = D["high"][a], D["low"][a], D["close"][a]
        rng_prev = (Hd - Ld).shift(1)
        ma5 = Cd.rolling(5).mean().shift(1)
        pc = Cd.shift(1)
        trades = []
        groups = {d: g for d, g in df.groupby(day)}
        for d in Cd.index:
            if d not in groups or np.isnan(rng_prev.get(d, np.nan)) or np.isnan(Cd.get(d, np.nan)):
                continue
            g = groups[d]
            if len(g) < 24:
                continue
            up, dn = O[d] + k * rng_prev[d], O[d] - k * rng_prev[d]
            okL = (not ma_filter) or pc[d] > ma5[d]
            okS = (not long_only) and ((not ma_filter) or pc[d] < ma5[d])
            hh, ll, oo = g["high"].to_numpy(), g["low"].to_numpy(), g["open"].to_numpy()
            iL = np.argmax(hh >= up) if okL and (hh >= up).any() else 99
            iS = np.argmax(ll <= dn) if okS and (ll <= dn).any() else 99
            if iL == 99 and iS == 99:
                continue
            cands = []
            if iL <= iS and iL != 99:
                cands.append((1, iL, max(up, oo[iL])))
            if iS <= iL and iS != 99:
                cands.append((-1, iS, min(dn, oo[iS])))
            res = []
            for side, i, px in cands:
                i2 = i + delay_hours
                if i2 >= 24:
                    continue
                if delay_hours:
                    px = oo[i2]
                mae = (ll[i2:].min() / px - 1) if side > 0 else (hh[i2:].max() / px - 1)
                res.append((side * (Cd[d] / px - 1) - 2 * cost - side * fund_at(d, i2), side, i2, abs(mae)))
            if not res:
                continue
            ret, side, i2, mae = min(res)
            trades.append((d, side, ret, i2, mae))
        if rng is not None and trades:   # 위약: 같은 거래 수·방향, 무작위 날·시각
            days = [d for d in Cd.index if d in groups and len(groups[d]) == 24 and not np.isnan(rng_prev.get(d, np.nan))]
            pick = rng.choice(len(days), size=min(len(trades), len(days)), replace=False)
            new = []
            for (d0, side, _, _, _), j in zip(trades, pick):
                d = days[j]
                i2 = int(rng.integers(0, 24))
                px = groups[d]["open"].iloc[i2]
                new.append((d, side, side * (Cd[d] / px - 1) - 2 * cost - side * fund_at(d, i2), i2, 0.0))
            trades = new
        for d, side, ret, i2, _ in trades:
            out_r.at[d, a] = ret
            out_p.at[d, a] = side * (24 - i2) / 24
        trades_all[a] = trades
    return out_r, out_p, trades_all


# ---------------------------------------------------------------- 성적

def perf(r: pd.Series, bpy: int) -> dict:
    r = r.dropna()
    if len(r) < 10:
        return {"cagr": np.nan, "mdd": np.nan, "sharpe": np.nan}
    eq = (1 + r).cumprod()
    yrs = len(r) / bpy
    cagr = float(eq.iloc[-1] ** (1 / yrs) - 1) if eq.iloc[-1] > 0 else -1.0
    sd = r.std()
    return {"cagr": cagr, "mdd": float((eq / eq.cummax() - 1).min()), "sharpe": float(r.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0}


def port(R: pd.DataFrame, C: pd.DataFrame) -> pd.Series:
    """3개 균등 (그 시각에 데이터가 있는 코인끼리)."""
    return R.where(C.notna()).mean(axis=1)


def replay(P: pd.DataFrame, ret: pd.DataFrame, fund: pd.DataFrame, cost=COST) -> pd.DataFrame:
    """포지션 배열 → 종가 대 종가 수익 (위약 비교용, 손절도 종가 근사)."""
    Pp = P.shift(1).fillna(0)
    return Pp * ret.fillna(0) - P.diff().abs().fillna(P.abs()) * cost - Pp * fund


def placebo(P: pd.DataFrame, ret: pd.DataFrame, fund: pd.DataFrame, C: pd.DataFrame, bpy: int, mask, n=100, seed=7) -> tuple[float, float]:
    """포지션 순서를 코인마다 무작위로 시간 이동 (보유 비율·보유 길이·롱숏 비율 그대로, 타이밍만 깨짐)."""
    rng = np.random.default_rng(seed)
    real = perf(port(replay(P, ret, fund), C)[mask], bpy)["sharpe"]
    out = []
    Pv = P.to_numpy()
    for _ in range(n):
        Q = np.zeros_like(Pv)
        for j in range(Pv.shape[1]):
            valid = np.where(C.iloc[:, j].notna().to_numpy())[0]
            if len(valid) < 2 * bpy // 12:
                continue
            seg = Pv[valid, j]
            sh = rng.integers(bpy // 12, len(seg) - bpy // 12)
            Q[valid, j] = np.roll(seg, sh)
        Qd = pd.DataFrame(Q, index=P.index, columns=P.columns)
        out.append(perf(port(replay(Qd, ret, fund), C)[mask], bpy)["sharpe"])
    out = np.array(out)
    return float((out < real).mean()), float(np.median(out))


@dataclass
class Result:
    name: str
    tf: str
    origin: str
    kind: str
    R: pd.DataFrame
    P: pd.DataFrame
    trades: dict
    R2: pd.DataFrame | None = None     # 비용 2배
    Rd: pd.DataFrame | None = None     # 한 봉(1시간) 늦게
    pl: tuple = (np.nan, np.nan)


def run_spec(sp: Spec, B: dict) -> Result:
    cols = list(SYMS)
    get = lambda X: X[cols].to_numpy(float) if isinstance(X, pd.DataFrame) else None  # noqa: E731
    O, H, L, C, FU = (B[k][cols].to_numpy(float) for k in ("open", "high", "low", "close", "funding"))
    sig = [sp.el[cols].to_numpy(bool), sp.es[cols].to_numpy(bool), sp.xl[cols].to_numpy(bool), sp.xs[cols].to_numpy(bool)]
    stp, trl = get(sp.stop), get(sp.trail)
    outs = {}
    for tag, kw in (("base", {}), ("cost2", {"cost": 2 * COST}), ("delay", {"delay": 1})):
        R = np.zeros_like(C)
        P = np.zeros_like(C)
        T = {}
        for j, a in enumerate(cols):
            r, p, tr = sim(O[:, j], H[:, j], L[:, j], C[:, j], FU[:, j], *(s[:, j] for s in sig),
                           stop=None if stp is None else stp[:, j], trail=None if trl is None else trl[:, j], max_bars=sp.max_bars, **kw)
            R[:, j], P[:, j], T[a] = r, p, tr
        outs[tag] = (pd.DataFrame(R, index=B["close"].index, columns=cols), pd.DataFrame(P, index=B["close"].index, columns=cols), T)
    return Result(sp.name, sp.tf, sp.origin, sp.kind, outs["base"][0], outs["base"][1], outs["base"][2], outs["cost2"][0], outs["delay"][0])


def trade_stats(trades: dict, index: pd.DatetimeIndex, lo, hi, tf: str) -> dict:
    rows = [(index[i], index[x], s, r, m) for a in trades for (i, x, s, r, m) in trades[a]]
    rows = [t for t in rows if lo <= t[1] <= hi]
    if not rows:
        return {"n": 0}
    rr = np.array([t[3] for t in rows])
    side = np.array([t[2] for t in rows])
    hrs = np.array([(t[1] - t[0]) / pd.Timedelta(hours=1) for t in rows]) + (4 if tf == "4h" else 24)
    win, loss = rr[rr > 0], rr[rr <= 0]
    yrs = (hi - lo) / pd.Timedelta(days=365)
    return {"n": len(rr), "per_yr": len(rr) / max(yrs, 1e-9) / 3, "win": float((rr > 0).mean()),
            "avg_w": float(win.mean()) if len(win) else 0.0, "avg_l": float(loss.mean()) if len(loss) else 0.0,
            "pf": float(win.sum() / -loss.sum()) if loss.sum() < 0 else np.inf, "hold_h": float(np.median(hrs)),
            "long_sum": float(rr[side > 0].sum()), "short_sum": float(rr[side < 0].sum()), "mae": np.array([t[4] for t in rows]),
            "rets": rr}


# ---------------------------------------------------------------- 실행·보고

def pct(x, d=1):
    return "-" if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else f"{x * 100:+.{d}f}%"


def f2(x):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else ("∞" if np.isinf(x) else f"{x:.2f}")


def run(cfg: dict | None = None, log=print) -> Path:
    t0 = time.time()
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    h1 = load_1h(cache, log)
    TB = {}
    for tf in ("1d", "4h"):
        B = bars(h1, tf)
        B["funding"] = funding_bars(cache, B["close"].index, tf)
        TB[tf] = B
    D = TB["1d"]
    log(f"  데이터: 1시간봉 {min(len(x) for x in h1.values()):,}~{max(len(x) for x in h1.values()):,}개, "
        f"{D['close'].index[0]:%Y-%m-%d} ~ {D['close'].index[-1]:%Y-%m-%d}")
    results: list[Result] = []
    for tf in ("1d", "4h"):
        B = TB[tf]
        for sp in specs(B, tf, D):
            results.append(run_spec(sp, B))
            log(f"  {tf} {sp.name} ({time.time() - t0:.0f}초)")
    # 래리 윌리엄스 3종 (일봉, 1시간봉으로 체결 시각 찾음)
    LW = [("래리 윌리엄스 변동성 돌파 k=0.5 (롱·숏)", {}), ("래리 윌리엄스 k=0.5 + 5일선 필터 (롱·숏)", {"ma_filter": True}),
          ("래리 윌리엄스 k=0.5 + 5일선, 롱만 (강환국식)", {"ma_filter": True, "long_only": True})]
    lw_extra = {}
    for nm, kw in LW:
        R, P, T = larry_williams(h1, D, cache, **kw)
        R2, _, _ = larry_williams(h1, D, cache, cost=2 * COST, **kw)
        Rd, _, _ = larry_williams(h1, D, cache, delay_hours=1, **kw)
        idx = D["close"].index
        Tt = {a: [(idx.get_loc(d), idx.get_loc(d), s, r, m) for d, s, r, _, m in T[a]] for a in T}
        res = Result(nm, "1d", "Larry Williams, Long-Term Secrets to Short-Term Trading / 강환국", "변동성 돌파", R, P, Tt, R2, Rd)
        lw_extra[nm] = kw
        results.append(res)
        log(f"  1d {nm} ({time.time() - t0:.0f}초)")

    # 계속 보유 (선물 롱 1배, 펀딩 포함)
    bench = {}
    for tf in ("1d", "4h"):
        B = TB[tf]
        ret = B["close"].pct_change()
        bench[tf] = ret - B["funding"]
    dev_m = {tf: TB[tf]["close"].index <= DEV_END for tf in TB}
    hold_m = {tf: TB[tf]["close"].index >= HOLD_START for tf in TB}

    rows = []
    for res in results:
        B = TB[res.tf]
        C = B["close"]
        bpy = BPY[res.tf]
        m = dev_m[res.tf]
        pr = perf(port(res.R, C)[m], bpy)
        p2 = perf(port(res.R2, C)[m], bpy)
        pdl = perf(port(res.Rd, C)[m], bpy)
        coins = {a: perf(res.R[a].where(C[a].notna())[m], bpy) for a in SYMS}
        pos_coins = sum(1 for a in SYMS if coins[a]["cagr"] > 0)
        bh = perf(port(bench[res.tf], C)[m], bpy)
        if res.name.startswith("래리"):
            rng = np.random.default_rng(11)
            sims = []
            for _ in range(20):
                Rp, _, _ = larry_williams(h1, D, cache, rng=rng, **lw_extra[res.name])
                sims.append(perf(port(Rp, C)[m], bpy)["sharpe"])
            sims = np.array(sims)
            res.pl = (float((sims < pr["sharpe"]).mean()), float(np.median(sims)))
        else:
            res.pl = placebo(res.P, C.pct_change(), B["funding"], C, bpy, m)
        ts = trade_stats(res.trades, C.index, C.index[0], DEV_END, res.tf)
        crit = [p2["cagr"] > 0, res.pl[0] >= 0.9, pdl["cagr"] > 0 and pdl["cagr"] >= 0.5 * pr["cagr"], pos_coins >= 2,
                pr["sharpe"] > bh["sharpe"]]
        fund_drag = float((res.P.shift(1).fillna(0) * B["funding"]).where(C.notna()).mean(axis=1)[m].sum() / (m.sum() / bpy))
        rows.append({"res": res, "pr": pr, "p2": p2, "pdl": pdl, "coins": coins, "pos_coins": pos_coins, "bh": bh, "ts": ts,
                     "crit": crit, "pass": all(crit), "fund": fund_drag,
                     "expo": float((res.P.abs().where(C.notna()).mean(axis=1))[m].mean())})
        log(f"  판정 {res.tf} {res.name}: {'통과' if all(crit) else '탈락'} ({time.time() - t0:.0f}초)")

    # 여러 개 중 최고를 고른 효과 (Deflated Sharpe)
    sr_all = [r["pr"]["sharpe"] / np.sqrt(BPY[r["res"].tf]) for r in rows if not np.isnan(r["pr"]["sharpe"])]
    best = max(rows, key=lambda r: r["pr"]["sharpe"] if not np.isnan(r["pr"]["sharpe"]) else -9)
    rb = port(best["res"].R, TB[best["res"].tf]["close"])[dev_m[best["res"].tf]].dropna()
    dsr = metrics.deflated_sharpe(best["pr"]["sharpe"] / np.sqrt(BPY[best["res"].tf]), sr_all, len(rb),
                                  float(rb.skew()), float(rb.kurt() + 3))

    # 확인 구간 + 레버리지 + 확정 공식과 섞기 (통과한 것만, 없으면 개발 구간 샤프 상위 3개를 '참고'로)
    passed = [r for r in rows if r["pass"]]
    focus = passed if passed else sorted(rows, key=lambda r: -np.nan_to_num(r["pr"]["sharpe"], nan=-9))[:3]
    hold_rows = []
    for r in focus:
        res = r["res"]
        C = TB[res.tf]["close"]
        hm = hold_m[res.tf]
        hp = perf(port(res.R, C)[hm], BPY[res.tf])
        hb = perf(port(bench[res.tf], C)[hm], BPY[res.tf])
        hcoins = {a: perf(res.R[a].where(C[a].notna())[hm], BPY[res.tf]) for a in SYMS}
        hts = trade_stats(res.trades, C.index, HOLD_START, C.index[-1], res.tf)
        hold_rows.append({"r": r, "hp": hp, "hb": hb, "hcoins": hcoins, "hts": hts, "ok": hp["cagr"] > 0 and hp["sharpe"] >= 0.5})

    lev_rows = []
    for r in focus:
        res = r["res"]
        C = TB[res.tf]["close"]
        ts_all = trade_stats(res.trades, C.index, C.index[0], C.index[-1], res.tf)
        pr_all = port(res.R, C)
        for L in (1, 2, 3, 5, 10):
            thr = 1 / L - MMR
            liq = int((ts_all["mae"] >= thr).sum()) if ts_all.get("n") else 0
            dv = perf((pr_all * L).clip(lower=-1)[dev_m[res.tf]], BPY[res.tf])
            hv = perf((pr_all * L).clip(lower=-1)[hold_m[res.tf]], BPY[res.tf])
            lev_rows.append((res.name, res.tf, L, dv, hv, liq, ts_all.get("n", 0), float(np.max(ts_all["mae"])) if ts_all.get("n") else 0))

    # 확정 공식(일봉, 현물 근사) 70% + 선물 방식 30%
    Dc = D["close"]
    mt = MultiTrend(Dc, D["high"], D["low"])
    M, Lg = mt.h["중기"], mt.h["장기"]
    base = (M.state & ~(Lg.score <= -2)).to_numpy()
    Ms = M.score.to_numpy()
    W = np.zeros(base.shape)
    for j in range(base.shape[1]):
        on = False
        for i in range(base.shape[0]):
            on = bool(base[i, j]) and (on or Ms[i, j] >= 2)
            W[i, j] = 1.0 if on else 0.0
    Wf = pd.DataFrame(W, index=Dc.index, columns=Dc.columns)
    spot_cost = cfg["costs"].get("fee", 0.0005) + 0.001
    formula_r = port(Wf.shift(1).fillna(0) * Dc.pct_change().fillna(0) - Wf.diff().abs().fillna(Wf) * spot_cost, Dc)
    short_state = mt.h["단기"].state.astype(float)
    shortmode_r = port(short_state.shift(1).fillna(0) * Dc.pct_change().fillna(0) - short_state.diff().abs().fillna(short_state) * spot_cost, Dc)
    combo = []
    for nm, fr in [("확정 공식만 (현물)", formula_r), ("확정 공식 70% + 단기 모드(현물) 30%", 0.7 * formula_r + 0.3 * shortmode_r)]:
        combo.append((nm, perf(fr[Dc.index <= DEV_END], 365), perf(fr[Dc.index >= HOLD_START], 365), np.nan))
    for r in focus:
        res = r["res"]
        C = TB[res.tf]["close"]
        pr_ = port(res.R, C)
        daily = (1 + pr_.fillna(0)).groupby(pr_.index.floor("1D")).prod() - 1
        daily = daily.reindex(Dc.index).fillna(0)
        mix = 0.7 * formula_r + 0.3 * daily
        corr = float(formula_r[Dc.index <= DEV_END].corr(daily[Dc.index <= DEV_END]))
        combo.append((f"확정 공식 70% + {res.name} [{res.tf}] 30%", perf(mix[Dc.index <= DEV_END], 365),
                      perf(mix[Dc.index >= HOLD_START], 365), corr))

    path = render(rows, hold_rows, lev_rows, combo, passed, dsr, best, TB, time.time() - t0)
    log(f"  → {path} ({time.time() - t0:.0f}초)")
    return path


def render(rows, hold_rows, lev_rows, combo, passed, dsr, best, TB, secs) -> Path:
    today = pd.Timestamp.now(tz="Asia/Seoul").date()
    D = TB["1d"]["close"]
    L = [f"# 선물 단기 검증 — 유명 방식·지표 {len(rows)}개 ({today})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다. "
         "선물은 레버리지·청산 때문에 원금 전부를 잃을 수 있습니다.", "",
         "## 0. 조건", "",
         f"- 데이터: 바이낸스 USDⓈ-M 무기한 선물 1시간봉 → 4시간봉·일봉 ({D.index[0]:%Y-%m} ~ {D.index[-1]:%Y-%m-%d}), 8시간 펀딩비 실제 기록.",
         f"- 개발 구간 2020-01 ~ 2024-12 (판정), 확인 구간 2025-01 ~ {D.index[-1]:%Y-%m} (개발 판정 통과한 것만, 한 번만 봄).",
         f"- 비용: 한 번 사고팔 때마다 {COST:.3%} (수수료 {FEE:.3%} + 슬리피지 {SLIP:.2%}), 펀딩비 냄/받음, 레버리지 1배.",
         "- 숫자는 원저자·교과서 값 그대로 넣었고 이 데이터로 고르지 않았습니다. 판단은 봉 종가, 체결은 그 종가(=다음 봉 시가). "
         "손절은 봉 안에서 닿으면 그 가격(갭이면 시가).",
         "- 통과 기준(미리 정함, 3개 균등·개발 구간): ① 비용 2배에도 연복리 > 0 ② 무작위 타이밍보다 샤프 상위 10% "
         "③ 한 봉 늦어도 연복리 > 0·절반 이상 ④ 코인 3개 중 2개 이상 연복리 > 0 ⑤ 선물 롱 계속 보유보다 샤프 높음.",
         f"- 계산 {secs / 60:.1f}분.", "",
         "## 1. 개발 구간 결과 (3개 균등)", "",
         "| 방식 | 봉 | 출처 | 연복리 | MDD | 샤프 | 비용 2배 | 한 봉 늦게 | 무작위 대비 | +코인 수 | 연 거래(코인당) | 승률 | 평균 이익 | 평균 손실 | 손익비 | 보유(중앙) | 롱 손익합 | 숏 손익합 | 투자 비중 | 펀딩(연) | 기준 ①②③④⑤ |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    bh_done = set()
    for r in sorted(rows, key=lambda r: (r["res"].tf, -np.nan_to_num(r["pr"]["sharpe"], nan=-9))):
        res, pr, ts = r["res"], r["pr"], r["ts"]
        if res.tf not in bh_done:
            bh = r["bh"]
            L.append(f"| (기준) 선물 롱 계속 보유 | {res.tf} | - | {pct(bh['cagr'])} | {pct(bh['mdd'], 0)} | {f2(bh['sharpe'])} | - | - | - | - | - | - | - | - | - | - | - | - | 100% | - | - |")
            bh_done.add(res.tf)
        hold = ts.get("hold_h", np.nan)
        hold_s = "-" if np.isnan(hold) else (f"{hold:.0f}시간" if hold < 48 else f"{hold / 24:.0f}일")
        L.append(f"| {'✅ ' if r['pass'] else ''}{res.name} | {res.tf} | {res.origin} | {pct(pr['cagr'])} | {pct(pr['mdd'], 0)} | {f2(pr['sharpe'])} | "
                 f"{pct(r['p2']['cagr'])} | {pct(r['pdl']['cagr'])} | {r['res'].pl[0]:.0%} | {r['pos_coins']}/3 | "
                 f"{ts.get('per_yr', 0):.0f} | {ts.get('win', 0):.0%} | {pct(ts.get('avg_w'))} | {pct(ts.get('avg_l'))} | {f2(ts.get('pf', np.nan))} | {hold_s} | "
                 f"{pct(ts.get('long_sum'), 0)} | {pct(ts.get('short_sum'), 0)} | {r['expo']:.0%} | {pct(-r['fund'])} | "
                 f"{''.join('○' if c else '×' for c in r['crit'])} |")
    L += ["", "- '롱/숏 손익합'은 거래 수익률을 단순히 더한 값(코인 3개 합)이라 어느 쪽에서 벌고 잃었는지만 봅니다. "
          "'펀딩(연)'은 펀딩비로 받은(+)·낸(−) 연평균. '무작위 대비'는 같은 보유 비율·보유 길이·롱숏 비율을 시간만 무작위로 옮긴 100번보다 샤프가 높은 비율.",
          f"- 여러 방식 중 최고를 고른 효과를 빼면(Deflated Sharpe): 개발 구간 샤프 1위 '{best['res'].name} [{best['res'].tf}]'(샤프 {f2(best['pr']['sharpe'])})가 "
          f"우연이 아닐 확률 {dsr:.0%} (95% 이상이어야 믿을 만).", ""]
    L += ["### 코인별 연복리 (개발 구간)", "", "| 방식 | 봉 | BTC | ETH | SOL |", "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["res"].tf, -np.nan_to_num(r["pr"]["sharpe"], nan=-9))):
        L.append(f"| {r['res'].name} | {r['res'].tf} | " + " | ".join(f"{pct(r['coins'][a]['cagr'])} (MDD {pct(r['coins'][a]['mdd'], 0)})" for a in SYMS) + " |")
    L += ["", f"## 2. 확인 구간 (2025-01 ~ {D.index[-1]:%Y-%m}) — " + ("개발 판정 통과한 것" if passed else "통과한 방식이 없어 개발 샤프 상위 3개를 참고로"), "",
          "| 방식 | 봉 | 연복리 | MDD | 샤프 | 같은 기간 선물 롱 보유 | BTC | ETH | SOL | 거래 수 | 승률 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for h in hold_rows:
        res = h["r"]["res"]
        L.append(f"| {res.name} | {res.tf} | {pct(h['hp']['cagr'])} | {pct(h['hp']['mdd'], 0)} | {f2(h['hp']['sharpe'])} | "
                 f"{pct(h['hb']['cagr'])} (샤프 {f2(h['hb']['sharpe'])}) | " + " | ".join(pct(h["hcoins"][a]["cagr"]) for a in SYMS) +
                 f" | {h['hts'].get('n', 0)} | {h['hts'].get('win', 0):.0%} | {'통과' if h['ok'] else '탈락'}{'' if passed else ' (참고)'} |")
    L += ["", "## 3. 레버리지와 청산 위험 (위 방식들, 3개 균등)", "",
          "- 연복리·MDD는 매 봉 같은 배율로 맞춘다고 가정(실제론 더 나쁠 수 있음). '청산 거래'는 코인 몫 전부를 격리 증거금으로 넣었을 때 "
          f"진입 뒤 가장 불리했던 움직임이 1/배율 − {MMR:.1%}를 넘은 거래 수(그 거래에서 그 몫을 전부 잃음).", "",
          "| 방식 | 봉 | 배율 | 개발 연복리 | 개발 MDD | 확인 연복리 | 확인 MDD | 청산 거래 / 전체 | 거래 중 최악 역행 |", "|---|---|---|---|---|---|---|---|---|"]
    for nm, tf, Lv, dv, hv, liq, n, worst in lev_rows:
        L.append(f"| {nm} | {tf} | {Lv}배 | {pct(dv['cagr'])} | {pct(dv['mdd'], 0)} | {pct(hv['cagr'])} | {pct(hv['mdd'], 0)} | {liq} / {n} | {pct(-worst, 0)} |")
    L += ["", "## 4. 확정 공식(현물)과 섞으면 (70:30, 미리 정한 비율)", "",
          "| 조합 | 개발 연복리 | 개발 MDD | 개발 샤프 | 확인 연복리 | 확인 MDD | 확인 샤프 | 공식과 상관 |", "|---|---|---|---|---|---|---|---|"]
    for nm, dv, hv, corr in combo:
        L.append(f"| {nm} | {pct(dv['cagr'])} | {pct(dv['mdd'], 0)} | {f2(dv['sharpe'])} | {pct(hv['cagr'])} | {pct(hv['mdd'], 0)} | {f2(hv['sharpe'])} | {f2(corr)} |")
    L += ["", "- 확정 공식은 선물 일봉 가격으로 근사(업비트 현물·김치 프리미엄 없음), 비용 0.15%.", "",
          "## 5. 결론", "", "(실행 결과를 보고 사람이 씀)", ""]
    out = Path(config_mod.ROOT) / "docs" / f"futures-lab-{today}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out
