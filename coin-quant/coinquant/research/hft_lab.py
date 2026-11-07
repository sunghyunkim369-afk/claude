"""고빈도(분봉 단타) 실험 — 1분·5분·15분봉으로 하루 여러 번 사고파는 방식이 비용 뒤에도 남는지.

진짜 '고빈도'(마이크로초 단위 호가 매매·시장 조성)는 거래소 옆 서버·수수료 리베이트가 필요해 개인 계정으로는 불가.
여기서 보는 건 개인이 API 로 할 수 있는 가장 빠른 쪽: 분봉 종가에 판단 → 다음 분봉 시가에 체결.

규칙(미리 정함, 결과 보고 바꾸지 않음)
- 데이터: 바이낸스 현물 1분봉 BTC·ETH·SOL (2021-01~), 테이커 매수량 포함. 5·15분봉은 1분봉을 묶어 만듦.
- 판단: 봉 종가까지의 값만. 체결: 다음 봉 시가. 한 번에 한 포지션(코인별), 코인 3개 똑같이 나눔.
- 비용(한 번 사거나 팔 때): 업비트 시장가 = 수수료 0.05% + 슬리피지(BTC 0.02%, ETH·SOL 0.04%),
  업비트 지정가(낙관: 다 체결된다고 침) = 0.05%, 바이낸스 선물(롱·숏) = 0.05% + 0.01~0.02%.
- 개발 구간 2021-01~2024-12 에서 방식마다 가장 나은 설정 1개를 고르고, 2025-01~ 은 그 1개만 확인.
- 우연 점검: 고른 설정과 같은 횟수·같은 보유 시간으로 무작위 시점에 산 200번과 비교(비용 빼기 전 거래당 수익).

방식 (교과서·유명 단타 방식)
A 평균회귀: 가격이 N봉 평균에서 k표준편차 아래 → 평균 복귀(z≥0) 또는 N봉 뒤 매도
B 코너스 RSI(2): RSI(2) 가 thr 아래(+선택: 200봉 평균 위) → 5봉 평균 위로 마감 또는 20봉 뒤 매도
C 거래량 돌파: N봉 최고가 돌파(+선택: 거래량 3배) → N/2봉 최저 아래 마감 또는 N봉 뒤 매도
D 주문 흐름: N봉 동안 테이커 매수 비율 > thr → 비율 0.5 아래 또는 N봉 뒤 매도
G 급락 반등: 5봉 하락이 평소의 k배 → T봉 뒤 매도
E 변동성 돌파(래리 윌리엄스, 업비트 자동매매로 유명): 오늘 시가 + k×어제 범위 돌파 시 매수 → 다음 날 09:00 매도
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

SYMS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}
SINCE = "2021-01"
DEV_END = pd.Timestamp("2024-12-31 23:59")
HOLD_START = pd.Timestamp("2025-01-01")
SLIP = {"BTC": 0.0002, "ETH": 0.0004, "SOL": 0.0004}
COSTS = {   # 한 번 사거나 팔 때
    "업비트 시장가": {a: 0.0005 + SLIP[a] for a in SYMS},
    "업비트 지정가(낙관)": {a: 0.0005 for a in SYMS},
    "바이낸스 선물": {"BTC": 0.0006, "ETH": 0.0007, "SOL": 0.0007},
    "바이낸스 선물 지정가(낙관)": {a: 0.0002 for a in SYMS},
    "비용 0 (참고)": {a: 0.0 for a in SYMS},
}
TFS = {"1분": "1min", "5분": "5min", "15분": "15min"}
BPD = {"1분": 1440, "5분": 288, "15분": 96}


# ---------------------------------------------------------------- 데이터

def load_1m(cache: Path, log=print) -> dict[str, pd.DataFrame]:
    from ..data import binance_public as bp
    from .futures_lab import clean_wicks
    d = cache / "binance" / "spot_1m"
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for a, s in SYMS.items():
        p = d / f"{s}.parquet" if _has_parquet() else d / f"{s}.csv.gz"
        if not p.exists():
            log(f"  {s} 1분봉 받는 중 ({SINCE}~)")
            df = bp.fetch_um_klines(s, "1m", since_month=SINCE, workers=12, market="spot")
            if df.empty:
                raise RuntimeError(f"{s} 1분봉을 못 받음")
            df.to_parquet(p, index=False) if p.suffix == ".parquet" else df.to_csv(p, index=False)
        df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, parse_dates=["time"])
        df = df.set_index("time").sort_index()
        df = df[~df.index.duplicated()]
        out[a] = clean_wicks(df[["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_quote"]])
        log(f"  {a}: {len(df):,}개 1분봉 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d %H:%M}")
    return out


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "1min":
        return df
    g = df.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                        "volume": g["volume"].sum(), "quote_volume": g["quote_volume"].sum(),
                        "taker_buy_quote": g["taker_buy_quote"].sum()})
    return out.dropna(subset=["open", "close"])


# ---------------------------------------------------------------- 신호 (모두 봉 종가까지의 값만)

def _rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def signals(df: pd.DataFrame, fam: str, p: dict) -> tuple:
    """(롱 진입, 숏 진입, 롱 청산, 숏 청산, 최대 보유 봉 수). 숏은 롱 규칙을 뒤집은 것."""
    c, h, l_, v = df["close"], df["high"], df["low"], df["volume"]
    F = np.zeros(len(df), dtype=bool)
    if fam == "A":
        n, k = p["n"], p["k"]
        m, s = c.rolling(n).mean(), c.rolling(n).std()
        z = (c - m) / s.replace(0, np.nan)
        return (z < -k).to_numpy(), (z > k).to_numpy(), (z >= 0).to_numpy(), (z <= 0).to_numpy(), n
    if fam == "B":
        r = _rsi(c, 2)
        m5 = c.rolling(5).mean()
        if p["trend"]:
            m200 = c.rolling(200).mean()
            el, es = (r < p["thr"]) & (c > m200), (r > 100 - p["thr"]) & (c < m200)
        else:
            el, es = r < p["thr"], r > 100 - p["thr"]
        return el.to_numpy(), es.to_numpy(), (c > m5).to_numpy(), (c < m5).to_numpy(), 20
    if fam == "C":
        n = p["n"]
        hi, lo = h.rolling(n).max().shift(1), l_.rolling(n).min().shift(1)
        hi2, lo2 = h.rolling(n // 2).max().shift(1), l_.rolling(n // 2).min().shift(1)
        vok = (v > p["vm"] * v.rolling(n).mean().shift(1)) if p["vm"] else pd.Series(True, index=c.index)
        return ((c > hi) & vok).to_numpy(), ((c < lo) & vok).to_numpy(), (c < lo2).to_numpy(), (c > hi2).to_numpy(), n
    if fam == "D":
        n = p["n"]
        ratio = df["taker_buy_quote"].rolling(n).sum() / df["quote_volume"].rolling(n).sum().replace(0, np.nan)
        return (ratio > p["thr"]).to_numpy(), (ratio < 1 - p["thr"]).to_numpy(), (ratio < 0.5).to_numpy(), (ratio > 0.5).to_numpy(), n
    if fam == "G":
        r5 = np.log(c).diff(5)
        sd = r5.rolling(1000, min_periods=500).std()
        return (r5 < -p["k"] * sd).to_numpy(), (r5 > p["k"] * sd).to_numpy(), F, F, p["T"]
    raise ValueError(fam)


GRID = {
    "A": ("평균회귀 (평균에서 k표준편차 아래)", [{"n": n, "k": k} for n in (20, 60) for k in (2, 3)]),
    "B": ("코너스 RSI(2)", [{"thr": t, "trend": tr} for t in (5, 10) for tr in (False, True)]),
    "C": ("거래량 돌파", [{"n": n, "vm": vm} for n in (20, 60) for vm in (0, 3)]),
    "D": ("주문 흐름 (테이커 매수 비율)", [{"n": n, "thr": t} for n in (5, 20) for t in (0.60, 0.65)]),
    "G": ("급락 반등 / 급등 되돌림", [{"k": k, "T": T} for k in (3, 4) for T in (5, 20)]),
}


def _pname(fam: str, p: dict) -> str:
    if fam == "A":
        return f"N={p['n']} k={p['k']}"
    if fam == "B":
        return f"RSI2<{p['thr']}" + (" +200봉 추세" if p["trend"] else "")
    if fam == "C":
        return f"N={p['n']}" + (f" 거래량×{p['vm']}" if p["vm"] else "")
    if fam == "D":
        return f"N={p['n']} 비율>{p['thr']:.2f}"
    if fam == "G":
        return f"k={p['k']} {p['T']}봉 보유"
    return str(p)


# ---------------------------------------------------------------- 시뮬레이터

def run_pos(el, es, xl, xs, T: int, long_only: bool) -> np.ndarray:
    """봉 t 종가에 판단 → pos[t+1] = 봉 t+1 시가부터 t+2 시가까지 들고 있는 방향 (+1/−1/0)."""
    n = len(el)
    el, xl = np.nan_to_num(el).astype(bool).tolist(), np.nan_to_num(xl).astype(bool).tolist()
    es = [False] * n if long_only else np.nan_to_num(es).astype(bool).tolist()
    xs = np.nan_to_num(xs).astype(bool).tolist()
    pos = [0] * n
    cur, held = 0, 0
    for t in range(n - 1):
        if cur == 0:
            if el[t]:
                cur, held = 1, 0
            elif es[t]:
                cur, held = -1, 0
        else:
            held += 1
            if held >= T or (xl[t] if cur == 1 else xs[t]):
                cur = 0
        pos[t + 1] = cur
    return np.asarray(pos, dtype=np.int8)


def bar_returns(o: np.ndarray, pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(비용 전 봉 수익, 매매량). 봉 i 수익 = pos[i] × (시가[i+1]/시가[i] − 1)."""
    oo = np.empty(len(o))
    oo[:-1] = o[1:] / o[:-1] - 1
    oo[-1] = 0.0
    turn = np.abs(np.diff(pos.astype(np.int16), prepend=0)).astype(float)
    return pos * oo, turn


def trades_of(pos: np.ndarray, o: np.ndarray) -> pd.DataFrame:
    """거래 목록: 진입 봉, 보유 봉 수, 방향, 비용 전 수익."""
    p = pos.astype(np.int16)
    ch = np.flatnonzero(np.diff(p, prepend=0) != 0)
    rows = []
    for i, s in enumerate(ch):
        if p[s] == 0:
            continue
        e = ch[i + 1] if i + 1 < len(ch) else len(p) - 1
        rows.append((s, e - s, int(p[s]), p[s] * (o[e] / o[s] - 1)))
    return pd.DataFrame(rows, columns=["i", "bars", "side", "gross"])


def daily(idx: pd.DatetimeIndex, r: np.ndarray) -> pd.Series:
    s = pd.Series(np.log1p(np.clip(r, -0.99, None)), index=idx)
    return np.expm1(s.groupby(idx.normalize()).sum())


def perf(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {"cagr": np.nan, "sharpe": np.nan, "mdd": np.nan}
    eq = (1 + r).cumprod()
    yrs = len(r) / 365
    sd = r.std()
    return {"cagr": float(eq.iloc[-1] ** (1 / yrs) - 1) if eq.iloc[-1] > 0 else -1.0,
            "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else np.nan,
            "mdd": float((eq / eq.cummax() - 1).min()), "total": float(eq.iloc[-1] - 1)}


# ---------------------------------------------------------------- 한 설정 돌리기 (코인 3개 묶음)

def evaluate(bars: dict[str, pd.DataFrame], fam: str, p: dict, side: str) -> dict:
    """side: 'long' (업비트 현물) / 'ls' (선물 롱·숏). 돌려줌: 코인별 비용 전 일간 수익·매매량·거래 목록."""
    out = {}
    for a, df in bars.items():
        el, es, xl, xs, T = signals(df, fam, p)
        pos = run_pos(el, es, xl, xs, T, long_only=(side == "long"))
        o = df["open"].to_numpy()
        g, turn = bar_returns(o, pos)
        out[a] = {"idx": df.index, "g": g, "turn": turn, "trades": trades_of(pos, o), "pos": pos}
    return out


def net_daily(ev: dict, cost: dict) -> pd.Series:
    D = pd.DataFrame({a: daily(v["idx"], v["g"] - v["turn"] * cost[a]) for a, v in ev.items()}).fillna(0.0)
    return D.mean(axis=1)


def summarize(ev: dict, cost: dict, lo, hi) -> dict:
    r = net_daily(ev, cost).loc[lo:hi]
    st = perf(r)
    tr = pd.concat([v["trades"].assign(t=v["idx"][v["trades"]["i"].to_numpy()], a=a) for a, v in ev.items() if len(v["trades"])]
                   or [pd.DataFrame(columns=["i", "bars", "side", "gross", "t", "a"])], ignore_index=True)
    tr = tr[(tr["t"] >= lo) & (tr["t"] <= hi)]
    days = max(len(r), 1)
    expo = np.mean([np.mean(np.abs(v["pos"][(v["idx"] >= lo) & (v["idx"] <= hi)]) > 0) for v in ev.values()])
    st.update({"n": len(tr), "per_day": len(tr) / days / len(ev), "gross_bp": tr["gross"].mean() * 1e4 if len(tr) else np.nan,
               "win": float((tr["gross"] > 0).mean()) if len(tr) else np.nan, "expo": float(expo),
               "hold": float(tr["bars"].median()) if len(tr) else np.nan})
    return st


def placebo(ev: dict, bars: dict, lo, hi, draws: int = 200, seed: int = 7) -> float:
    """같은 횟수·같은 보유 봉 수·같은 방향으로 무작위 시점에 진입했을 때 비용 전 거래당 평균 수익 중 실제보다 낮은 비율."""
    rng = np.random.default_rng(seed)
    real, rand = [], np.zeros(draws)
    for a, v in ev.items():
        tr = v["trades"]
        idx = v["idx"]
        m = (idx >= lo) & (idx <= hi)
        if not len(tr):
            continue
        tr = tr[m[tr["i"].to_numpy()]]
        if not len(tr):
            continue
        L = np.log(bars[a]["open"].to_numpy())
        ok = np.flatnonzero(m)
        real.append(tr["gross"].to_numpy())
        b, s = tr["bars"].to_numpy(), tr["side"].to_numpy()
        for k in range(draws):
            st = rng.choice(ok[ok < len(L) - b.max() - 1], size=len(b))
            rand[k] += np.sum(s * np.expm1(L[st + b] - L[st]))
    if not real:
        return np.nan
    real = np.concatenate(real)
    return float(np.mean(rand / len(real) < real.mean()))


# ---------------------------------------------------------------- 변동성 돌파 (하루 한 번, 분봉으로 진입 시각 확인)

def vol_breakout(m1: dict[str, pd.DataFrame], k: float, ma_filter: bool) -> dict:
    """업비트 날짜(UTC 00:00 = KST 09:00) 기준. 목표가 = 오늘 시가 + k×어제(고가−저가). 1분봉 고가가 닿으면
    목표가(또는 그 분 시가가 더 높으면 시가)에 매수, 다음 날 첫 분 시가에 매도. 필터: 어제 종가 > 5일 평균."""
    out = {}
    for a, df in m1.items():
        day = df.index.normalize()
        D = df.groupby(day).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        tgt = D["open"] + k * (D["high"] - D["low"]).shift(1)
        ok = D["close"].shift(1) > D["close"].rolling(5).mean().shift(1) if ma_filter else pd.Series(True, index=D.index)
        t_m = tgt.reindex(day).to_numpy()
        hit = df["high"].to_numpy() >= t_m
        first = pd.Series(hit, index=df.index).groupby(day).idxmax()
        any_hit = pd.Series(hit, index=df.index).groupby(day).any()
        rows = []
        nxt_open = D["open"].shift(-1)
        for d0, ts in first.items():
            if not any_hit.get(d0, False) or not ok.get(d0, False) or pd.isna(nxt_open.get(d0)) or pd.isna(tgt.get(d0)):
                continue
            px = max(float(tgt[d0]), float(df.at[ts, "open"]))
            rows.append((d0, ts, nxt_open[d0] / px - 1))
        out[a] = pd.DataFrame(rows, columns=["day", "t", "gross"]).set_index("day")
    return out


def vb_daily(vb: dict, cost: dict) -> pd.Series:
    D = pd.DataFrame({a: t["gross"] - 2 * cost[a] for a, t in vb.items()})
    full = pd.date_range(min(t.index.min() for t in vb.values()), max(t.index.max() for t in vb.values()), freq="D")
    return D.reindex(full).fillna(0.0).mean(axis=1)


# ---------------------------------------------------------------- 실행

def run(cfg: dict | None = None, log=print) -> Path:
    from .. import config as config_mod
    from ..config import ROOT
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    m1 = load_1m(cache, log=log)
    end = min(d.index[-1] for d in m1.values())
    start = max(d.index[0] for d in m1.values())
    m1 = {a: d.loc[start:end] for a, d in m1.items()}
    tf_bars = {tf: {a: resample(d, rule) for a, d in m1.items()} for tf, rule in TFS.items()}
    lo_d, hi_d, lo_h, hi_h = start, DEV_END, HOLD_START, end

    # 그냥 보유 (참고)
    bh = pd.DataFrame({a: d["close"].resample("1D").last().pct_change() for a, d in m1.items()}).mean(axis=1)
    rows, best = [], {}
    for side, ckey in (("long", "업비트 시장가"), ("ls", "바이낸스 선물")):
        cost = COSTS[ckey]
        for fam, (fname, grid) in GRID.items():
            for tf in TFS:
                for p in grid:
                    ev = evaluate(tf_bars[tf], fam, p, side)
                    sd = summarize(ev, cost, lo_d, hi_d)
                    s0 = summarize(ev, COSTS["비용 0 (참고)"], lo_d, hi_d)
                    rows.append({"side": side, "fam": fam, "name": fname, "tf": tf, "param": _pname(fam, p), **{f"dev_{k}": v for k, v in sd.items()},
                                 "dev_gross_cagr": s0["cagr"]})
                    key = (side, fam)
                    sc = sd["sharpe"] if not np.isnan(sd["sharpe"]) else -9
                    if key not in best or sc > best[key][0]:
                        best[key] = (sc, tf, p, ev)
                    log(f"  {side} {fam} {tf} {_pname(fam, p)}: 개발 샤프 {sd['sharpe']:.2f} 하루 {sd['per_day']:.1f}번 거래당 {sd['gross_bp']:.1f}bp")
    # 변동성 돌파
    for k in (0.3, 0.5, 0.7):
        for maf in (False, True):
            vb = vol_breakout(m1, k, maf)
            r = vb_daily(vb, COSTS["업비트 시장가"])
            sd = perf(r.loc[lo_d:hi_d])
            tr = pd.concat([t.loc[lo_d:hi_d] for t in vb.values()])
            ndays = len(r.loc[lo_d:hi_d])
            sd.update({"n": len(tr), "per_day": len(tr) / max(ndays, 1) / 3, "gross_bp": tr["gross"].mean() * 1e4, "win": (tr["gross"] > 0).mean(),
                       "expo": np.nan, "hold": np.nan})
            pn = f"k={k}" + (" +5일 평균 필터" if maf else "")
            rows.append({"side": "long", "fam": "E", "name": "변동성 돌파 (래리 윌리엄스)", "tf": "하루", "param": pn,
                         **{f"dev_{x}": v for x, v in sd.items()}, "dev_gross_cagr": perf(vb_daily(vb, COSTS["비용 0 (참고)"]).loc[lo_d:hi_d])["cagr"]})
            sc = sd["sharpe"] if not np.isnan(sd["sharpe"]) else -9
            if ("long", "E") not in best or sc > best[("long", "E")][0]:
                best[("long", "E")] = (sc, "하루", {"k": k, "ma": maf}, vb)
            log(f"  변동성 돌파 {pn}: 개발 샤프 {sd['sharpe']:.2f} 거래당 {sd['gross_bp']:.1f}bp")
    allv = pd.DataFrame(rows)

    # 고른 설정 → 최종 확인 구간 + 비용별 + 우연 점검
    fin = []
    for (side, fam), (sc, tf, p, ev) in best.items():
        ckey = "업비트 시장가" if side == "long" else "바이낸스 선물"
        if fam == "E":
            name = "변동성 돌파 (래리 윌리엄스)"
            pn = f"k={p['k']}" + (" +5일 평균 필터" if p["ma"] else "")
            res = {}
            for cn in COSTS:
                r = vb_daily(ev, COSTS[cn])
                res[cn] = (perf(r.loc[lo_d:hi_d]), perf(r.loc[lo_h:hi_h]))
            trh = pd.concat([t.loc[lo_h:hi_h] for t in ev.values()])
            trd = pd.concat([t.loc[lo_d:hi_d] for t in ev.values()])
            info = {"per_day": len(trh) / max((hi_h - lo_h).days, 1) / 3, "gross_bp_dev": trd["gross"].mean() * 1e4,
                    "gross_bp_hold": trh["gross"].mean() * 1e4, "win_hold": (trh["gross"] > 0).mean(), "pl": np.nan}
        else:
            name = GRID[fam][0]
            pn = _pname(fam, p)
            res = {cn: (summarize(ev, COSTS[cn], lo_d, hi_d), summarize(ev, COSTS[cn], lo_h, hi_h)) for cn in COSTS}
            h0 = res["비용 0 (참고)"][1]
            info = {"per_day": h0["per_day"], "gross_bp_dev": res["비용 0 (참고)"][0]["gross_bp"], "gross_bp_hold": h0["gross_bp"],
                    "win_hold": h0["win"], "pl": placebo(ev, tf_bars[tf], lo_d, hi_d)}
        fin.append({"side": side, "fam": fam, "name": name, "tf": tf, "param": pn, "ckey": ckey, "res": res, **info})
        log(f"  [고름] {side} {name} {tf} {pn}: 확인 구간 {ckey} 샤프 {res[ckey][1]['sharpe']:.2f}")

    rep = ROOT / "reports" / "research"
    rep.mkdir(parents=True, exist_ok=True)
    allv.to_csv(rep / "hft_lab_all.csv", index=False)
    return write_doc(allv, fin, bh, (lo_d, hi_d, lo_h, hi_h), m1)


def _f(v, fmt="{:+.1%}"):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)


def write_doc(allv: pd.DataFrame, fin: list, bh: pd.Series, per: tuple, m1: dict) -> Path:
    from ..config import ROOT
    lo_d, hi_d, lo_h, hi_h = per
    today = pd.Timestamp.now(tz="Asia/Seoul")
    bd, bhh = perf(bh.loc[lo_d:hi_d]), perf(bh.loc[lo_h:hi_h])
    L = [f"# 고빈도(분봉 단타) 실험 ({today:%Y-%m-%d})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         f"- 데이터: 바이낸스 현물 1분봉 BTC·ETH·SOL {lo_d:%Y-%m-%d} ~ {hi_h:%Y-%m-%d %H:%M} (UTC), 5·15분봉은 1분봉을 묶음.",
         f"- 개발 구간 {lo_d:%Y-%m} ~ {hi_d:%Y-%m} 에서 방식마다 설정 1개를 고르고, 최종 확인 {lo_h:%Y-%m} ~ 은 그 1개만.",
         "- 체결: 봉 종가에 판단 → 다음 봉 시가. 코인 3개에 똑같이 나눔. 레버리지 없음.",
         "- 비용(한 번 사거나 팔 때): 업비트 시장가 BTC 0.07% · ETH/SOL 0.09%, 업비트 지정가(다 체결된다고 친 낙관) 0.05%, 바이낸스 선물 시장가 0.06~0.07% · 지정가(낙관) 0.02%.",
         "- '거래당 bp' = 비용 빼기 전 한 번 거래의 평균 수익 (1bp = 0.01%). **왕복 비용이 업비트 시장가 14~18bp, 지정가 10bp** 라서 이보다 커야 남음.",
         "- '우연 점검' = 같은 횟수·보유 시간으로 무작위 시점에 산 200번 중 실제보다 거래당 수익이 낮았던 비율 (95% 이상이어야 우연이 아니라고 봄).",
         "", f"참고 · 그냥 보유(코인 3개 똑같이): 개발 구간 연 {_f(bd['cagr'])} / MDD {_f(bd['mdd'], '{:.0%}')} / 샤프 {_f(bd['sharpe'], '{:.2f}')}, "
         f"최종 확인 구간 연 {_f(bhh['cagr'])} / MDD {_f(bhh['mdd'], '{:.0%}')} / 샤프 {_f(bhh['sharpe'], '{:.2f}')}", ""]

    for side, title, ck, lk in (("long", "## 1. 업비트 현물 (롱만, 시장가 비용)", "업비트 시장가", "업비트 지정가(낙관)"),
                                ("ls", "## 2. 선물 롱·숏 (바이낸스 선물 비용, 레버리지 1배)", "바이낸스 선물", "바이낸스 선물 지정가(낙관)")):
        L += [title, "", "방식마다 개발 구간에서 가장 나은 설정 1개 → 최종 확인 구간.", "",
              "| 방식 | 봉 · 설정 | 하루 거래(코인당) | 거래당 bp (개발 → 확인) | 우연 점검 | 개발: 연 / MDD / 샤프 | **확인: 연 / MDD / 샤프** | 확인 · 지정가(낙관) 연 | 확인 · 비용 0 연 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for x in [f for f in fin if f["side"] == side]:
            d, h = x["res"][ck]
            lim = x["res"][lk][1]
            z = x["res"]["비용 0 (참고)"][1]
            L.append(f"| {x['name']} | {x['tf']} · {x['param']} | {_f(x['per_day'], '{:.1f}')} | {_f(x['gross_bp_dev'], '{:+.1f}')} → {_f(x['gross_bp_hold'], '{:+.1f}')} | "
                     f"{_f(x['pl'], '{:.0%}')} | {_f(d['cagr'])} / {_f(d['mdd'], '{:.0%}')} / {_f(d['sharpe'], '{:.2f}')} | "
                     f"**{_f(h['cagr'])} / {_f(h['mdd'], '{:.0%}')} / {_f(h['sharpe'], '{:.2f}')}** | {_f(lim['cagr'])} | {_f(z['cagr'])} |")
        L.append("")

    L += ["## 3. 모든 설정 (개발 구간)", ""]
    for side, nm in (("long", "업비트 현물 롱만"), ("ls", "선물 롱·숏")):
        a = allv[allv["side"] == side]
        pos_net = (a["dev_cagr"] > 0).mean()
        pos_g = (a["dev_gross_cagr"] > 0).mean()
        L.append(f"- {nm}: 설정 {len(a)}개 중 비용 뒤 플러스 **{pos_net:.0%}**, 비용 빼기 전 플러스 {pos_g:.0%}. "
                 f"거래당 bp 중앙 {a['dev_gross_bp'].median():+.1f}, 하루 거래 중앙 {a['dev_per_day'].median():.1f}번.")
    L += ["", "| 쪽 | 방식 | 봉 | 설정 | 하루 거래 | 거래당 bp | 승률 | 비용 전 연 | 비용 뒤 연 | MDD | 샤프 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in allv.sort_values(["side", "fam", "tf"]).iterrows():
        L.append(f"| {'롱' if r['side'] == 'long' else '롱숏'} | {r['name']} | {r['tf']} | {r['param']} | {_f(r['dev_per_day'], '{:.1f}')} | {_f(r['dev_gross_bp'], '{:+.1f}')} | "
                 f"{_f(r['dev_win'], '{:.0%}')} | {_f(r['dev_gross_cagr'])} | {_f(r['dev_cagr'])} | {_f(r['dev_mdd'], '{:.0%}')} | {_f(r['dev_sharpe'], '{:.2f}')} |")
    L += ["", "## 4. 비용을 넘으려면", "",
          "하루 N번 거래하면 비용만 연 (N × 왕복 비용 × 365). 업비트 시장가 왕복 0.14~0.18% 기준:", "",
          "| 하루 거래 | 1년 비용 (자금 대비, 대략) |", "|---|---|"]
    for n in (0.1, 1, 5, 20):
        L.append(f"| {n}번 | {n * 0.0016 * 365:.0%} |")
    L.append("")
    p = ROOT / "docs" / f"hft-lab-{today:%Y-%m-%d}.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p
