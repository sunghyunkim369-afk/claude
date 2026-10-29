"""4시간봉 거래량 급증 돌파(롱만) 확인 검증: `python -m coinquant breakout4h`

futures-lab 에서 확인 구간까지 남은 '4시간봉 거래량 급증 돌파 롱만'을 실제로 쓰기 전에 두 가지를 더 봅니다.

1. 생존 편향: BTC·ETH·SOL 은 '오늘' 고른 코인이라 살아남은 코인만 본 셈입니다. 그래서 매달 그때 기준 거래대금 상위 20개
   (바이낸스 현물 30일 거래대금, 무기한 선물이 있는 것만, 나중에 상장폐지된 LUNA·FTT·SRM 등 포함)로 같은 규칙을 다시 돌립니다.
   비교로 '지금(2026-10)까지 살아남은 코인만'으로도 돌려 편향 크기를 보여줍니다.
2. 하루 한 번만 판단: 매일 09:20 KST 메시지에 맞춰 09:00 KST(UTC 00:00)에 끝나는 4시간봉에서만 사고팔 때 성적.
   손절을 거래소 예약 주문으로 걸 때 / 걸지 않고 하루 한 번 확인할 때, 예약 실행이 4시간 늦을 때.

규칙(바꾸지 않음): 종가가 직전 20봉 최고가 위 + 거래량이 직전 20봉 평균의 2배 → 매수.
10봉 최저가 아래로 마감하면 매도, 진입가 − 2×ATR(20) 손절.
비용: BTC·ETH 0.085% / 그 밖 0.205% (한 번 매매마다), 선물은 펀딩비(BTC 기록으로 근사) 포함.
업비트 현물 가정: 0.15%, 펀딩 없음.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod, indicators as ind
from . import futures_lab as fl

N_TOP = 20
SPOT_COST = 0.0015
ALT_COST = fl.FEE + 0.0015
KST9 = 20   # UTC 20:00 에 시작하는 4시간봉 = UTC 00:00(KST 09:00)에 끝남


def signals(B: dict) -> dict:
    c, h, l_, v = B["close"], B["high"], B["low"], B["volume"]
    vr = v / v.shift(1).rolling(20, min_periods=20).mean()
    level = ind.donchian_high(h, 20)
    brk = (c > level) & (vr > 2)
    atr = ind.atr(h, l_, c, 20)
    lvl = level.where(brk).ffill(limit=5)
    recent = brk.astype(float).rolling(6, min_periods=1).max().astype(bool) & (c > lvl)
    return {"entry": brk.fillna(False), "recent": recent.fillna(False), "exit": (c < ind.donchian_low(l_, 10)).fillna(False),
            "stop": 2 * atr, "level": level, "atr": atr}


VARIANTS = [
    ("A. 4시간마다 판단 · 손절 예약 주문 (원래)", {}),
    ("B. 하루 한 번(09:00 KST 봉) 판단 · 손절 예약 주문", {"daily": True}),
    ("C. 하루 한 번 판단 · 손절도 하루 한 번 종가로 확인", {"daily": True, "stop_close": True}),
    ("D. 하루 한 번 · 지난 24시간 돌파가 아직 유효하면 진입", {"daily": True, "recent": True}),
    ("E. B + 예약이 4시간 늦게 실행 (13:00 KST 체결)", {"daily": True, "delay": 1}),
    ("F. D + 예약이 4시간 늦게 실행", {"daily": True, "recent": True, "delay": 1}),
]


def run_variant(B: dict, S: dict, opts: dict, costs: dict, member: pd.DataFrame | None = None, cost_mult=1.0,
                funding=True) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cols = list(B["close"].columns)
    idx = B["close"].index
    check = np.ones(len(idx), bool) if not opts.get("daily") else (idx.hour == KST9)
    ent = S["recent"] if opts.get("recent") else S["entry"]
    if member is not None:
        ent = ent & member.reindex(index=idx, columns=cols).fillna(False).astype(bool)
    O, H, L, C = (B[k][cols].to_numpy(float) for k in ("open", "high", "low", "close"))
    FU = B["funding"][cols].to_numpy(float) if funding else np.zeros_like(C)
    E, X, ST = ent[cols].to_numpy(bool), S["exit"][cols].to_numpy(bool), S["stop"][cols].to_numpy(float)
    z = np.zeros(len(idx), bool)
    R, P, T = np.zeros_like(C), np.zeros_like(C), {}
    for j, a in enumerate(cols):
        r, p, tr = fl.sim(O[:, j], H[:, j], L[:, j], C[:, j], FU[:, j], E[:, j], z, X[:, j], z, stop=ST[:, j],
                          cost=costs.get(a, ALT_COST) * cost_mult, delay=opts.get("delay", 0), check=check,
                          stop_close=opts.get("stop_close", False))
        R[:, j], P[:, j], T[a] = r, p, tr
    return pd.DataFrame(R, index=idx, columns=cols), pd.DataFrame(P, index=idx, columns=cols), T


def load_universe(cache: Path, log=print) -> tuple[dict, pd.DataFrame, list[str]]:
    mem = json.loads((cache / "binance" / "top30_spotvol_monthly.json").read_text())
    h1 = {}
    for f in sorted((cache / "binance" / "um_1h").glob("*.csv.gz")):
        s = f.name[:-7]
        df = pd.read_csv(f, parse_dates=["time"])
        if len(df) < 200:
            continue
        h1[s] = fl.clean_wicks(df.set_index("time"))
    B = fl.bars({s: d for s, d in h1.items()}, "4h")
    idx = B["close"].index
    # 펀딩: 알트는 기록이 없어 BTC 펀딩으로 근사 (알트 펀딩은 보통 비슷하거나 더 높음)
    fb = fl.funding_bars(cache, idx, "4h")["BTC"]
    B["funding"] = pd.DataFrame({s: fb for s in B["close"].columns})
    member = pd.DataFrame(False, index=idx, columns=B["close"].columns)
    for m, syms in mem.items():
        st, en = pd.Timestamp(m), pd.Timestamp(m) + pd.offsets.MonthBegin(1)
        rows = (idx >= st) & (idx < en)
        if not rows.any():
            continue
        first = B["close"].loc[rows].iloc[: 6 * 3]          # 그 달 첫 3일 안에 선물 데이터가 있는 것만
        ok = [s for s in syms if s in first.columns and first[s].notna().any()][:N_TOP]
        member.loc[rows, ok] = True
    alive = []   # 지금도 거래되는 코인: 바이낸스 현물 일봉이 최근 10일 안에 있음 (선물 1시간봉은 상위였던 달만 받아서 못 씀)
    cut = pd.Timestamp.now().normalize() - pd.Timedelta(days=10)
    for s in B["close"].columns:
        f = cache / "binance" / "spot_1d" / f"{s[4:] if s.startswith('1000') and not (cache / 'binance' / 'spot_1d' / f'{s}.csv').exists() else s}.csv"
        if f.exists() and pd.read_csv(f, usecols=["date"])["date"].iloc[-1] >= str(cut.date()):
            alive.append(s)
    log(f"  유니버스: 1시간봉 있는 코인 {len(h1)}개, 상위 {N_TOP}에 한 번이라도 든 코인 {int(member.any().sum())}개, 지금 살아있는 것 {len(alive)}개")
    return B, member, alive


def port_slots(R: pd.DataFrame) -> pd.Series:
    """자금을 20칸으로 나눠 한 칸씩 (신호가 없는 칸은 현금)."""
    return R.fillna(0).sum(axis=1) / N_TOP


def bh_members(B: dict, member: pd.DataFrame) -> pd.Series:
    ret = B["close"].pct_change()
    return ret.where(member.shift(1).fillna(False).astype(bool)).mean(axis=1).fillna(0)


def run(cfg: dict | None = None, log=print) -> Path:
    t0 = time.time()
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    bpy = fl.BPY["4h"]
    out = {}

    # ---- 1. 코어 3개 (선물 / 업비트 현물 가정)
    h1 = fl.load_1h(cache, log=lambda *a: None)
    B3 = fl.bars(h1, "4h")
    B3["funding"] = fl.funding_bars(cache, B3["close"].index, "4h")
    S3 = signals(B3)
    C3 = B3["close"]
    dev3, hold3 = C3.index <= fl.DEV_END, C3.index >= fl.HOLD_START
    core_cost = {a: fl.COST for a in C3.columns}
    spot_cost = {a: SPOT_COST for a in C3.columns}
    bh3 = fl.port(C3.pct_change(), C3)
    rows3 = []
    for nm, opts in VARIANTS:
        for venue, costs, fund in (("선물", core_cost, True), ("업비트 현물 가정", spot_cost, False)):
            R, P, T = run_variant(B3, S3, opts, costs, funding=fund)
            R2, _, _ = run_variant(B3, S3, opts, costs, cost_mult=2, funding=fund)
            pr = fl.port(R, C3)
            ts = fl.trade_stats(T, C3.index, C3.index[0], fl.DEV_END, "4h")
            pl = fl.placebo(P, C3.pct_change(), B3["funding"] if fund else B3["funding"] * 0, C3, bpy, dev3, n=60)
            coins = {a: fl.perf(R[a].where(C3[a].notna())[dev3], bpy) for a in C3.columns}
            hcoins = {a: fl.perf(R[a].where(C3[a].notna())[hold3], bpy) for a in C3.columns}
            rows3.append({"name": nm, "venue": venue, "dev": fl.perf(pr[dev3], bpy), "hold": fl.perf(pr[hold3], bpy),
                          "dev2": fl.perf(fl.port(R2, C3)[dev3], bpy), "pl": pl, "ts": ts, "coins": coins, "hcoins": hcoins,
                          "hts": fl.trade_stats(T, C3.index, fl.HOLD_START, C3.index[-1], "4h"),
                          "expo": float(P.abs().where(C3.notna()).mean(axis=1)[dev3].mean())})
            log(f"  코어3 {nm} [{venue}] ({time.time() - t0:.0f}초)")
    out["core"] = rows3
    out["bh3"] = (fl.perf(bh3[dev3], bpy), fl.perf(bh3[hold3], bpy))

    # ---- 2. 생존 편향: 그때 상위 20 (상폐 포함) vs 지금 살아있는 코인만
    B, member, alive = load_universe(cache, log)
    S = signals(B)
    idx = B["close"].index
    dev, hold = idx <= fl.DEV_END, idx >= fl.HOLD_START
    costs = {s: (fl.COST if s in ("BTCUSDT", "ETHUSDT") else ALT_COST) for s in B["close"].columns}
    alive_mem = member.copy()
    alive_mem.loc[:, [c for c in member.columns if c not in alive]] = False
    rowsU = []
    for nm, opts in [VARIANTS[0], VARIANTS[1], VARIANTS[3], VARIANTS[4]]:
        for uni, mm in (("그때 상위 20 (상폐 포함)", member), ("지금 살아있는 코인만 (편향 있음)", alive_mem)):
            R, P, T = run_variant(B, S, opts, costs, member=mm)
            R2, _, _ = run_variant(B, S, opts, costs, member=mm, cost_mult=2)
            pr, pr2 = port_slots(R), port_slots(R2)
            bh = bh_members(B, mm)
            ts = fl.trade_stats(T, idx, idx[0], fl.DEV_END, "4h")
            hts = fl.trade_stats(T, idx, fl.HOLD_START, idx[-1], "4h")
            sym_pnl = pd.Series({s: sum(t[3] for t in T[s] if idx[t[1]] <= fl.DEV_END) for s in T if T[s]})
            dead = [s for s in T if s not in alive and T[s]]
            dead_pnl = float(sum(t[3] for s in dead for t in T[s]))
            exclude_core = (R.drop(columns=[c for c in ("BTCUSDT", "ETHUSDT", "SOLUSDT") if c in R.columns]).fillna(0).sum(axis=1) / N_TOP)
            rowsU.append({"name": nm, "uni": uni, "dev": fl.perf(pr[dev], bpy), "hold": fl.perf(pr[hold], bpy),
                          "dev2": fl.perf(pr2[dev], bpy), "bh_dev": fl.perf(bh[dev], bpy), "bh_hold": fl.perf(bh[hold], bpy),
                          "ts": ts, "hts": hts, "n_sym": int((sym_pnl != 0).sum()), "pos_sym": float((sym_pnl > 0).mean()) if len(sym_pnl) else 0,
                          "dead_n": len(dead), "dead_pnl": dead_pnl, "excore_dev": fl.perf(exclude_core[dev], bpy),
                          "excore_hold": fl.perf(exclude_core[hold], bpy),
                          "expo": float((P.abs().sum(axis=1) / N_TOP)[dev].mean())})
            log(f"  유니버스 {nm} [{uni}] ({time.time() - t0:.0f}초)")
    out["uni"] = rowsU
    path = render(out, time.time() - t0, C3.index[-1])
    log(f"  → {path}")
    return path


def render(out: dict, secs: float, last: pd.Timestamp) -> Path:
    pct, f2 = fl.pct, fl.f2
    today = pd.Timestamp.now(tz="Asia/Seoul").date()
    L = [f"# 4시간봉 거래량 급증 돌파(롱만) — 생존 편향·하루 한 번 판단 검증 ({today})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         "## 0. 조건", "",
         "- 규칙(futures-lab 그대로): 4시간봉 종가가 직전 20봉 최고가 위 + 거래량이 직전 20봉 평균의 2배 → 매수. "
         "10봉 최저가 아래 마감 → 매도, 진입가 − 2×ATR(20) 손절. 롱만.",
         f"- 개발 2020-01 ~ 2024-12, 확인 2025-01 ~ {last:%Y-%m-%d}. 비용: 선물 BTC·ETH 0.085%, 그 밖 0.205% + 펀딩비 / "
         "업비트 현물 가정 0.15%, 펀딩 없음.",
         "- 유니버스: 매달 그 전달까지의 바이낸스 현물 30일 평균 거래대금 상위(스테이블·레버리지 토큰·금 토큰 제외) 중 무기한 선물이 있는 20개. "
         "나중에 상장폐지된 코인도 그때 상위였으면 포함. 자금을 20칸으로 나눠 신호마다 한 칸(비어 있는 칸은 현금).",
         "- 알트 펀딩비는 기록이 없어 BTC 펀딩비로 근사. 이상 체결 꼬리(1시간 안 25% 초과)는 몸통까지 자름.",
         f"- 계산 {secs / 60:.1f}분.", "",
         "## 1. 코어 3개 (BTC·ETH·SOL) — 판단 방식별", "",
         f"(기준) 3개 균등 계속 보유: 개발 연복리 {pct(out['bh3'][0]['cagr'])} · MDD {pct(out['bh3'][0]['mdd'], 0)} · 샤프 {f2(out['bh3'][0]['sharpe'])} / "
         f"확인 {pct(out['bh3'][1]['cagr'])} · 샤프 {f2(out['bh3'][1]['sharpe'])}", "",
         "| 방식 | 체결 | 개발 연복리 | MDD | 샤프 | 비용 2배 | 무작위 대비 | 코인별 (BTC/ETH/SOL) | 연 거래(코인당) | 승률 | 보유(중앙) | 투자 비중 | 확인 연복리 | 확인 MDD | 확인 샤프 | 확인 코인별 | 확인 거래 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["core"]:
        d, h, ts = r["dev"], r["hold"], r["ts"]
        hold = ts.get("hold_h", np.nan)
        L.append(f"| {r['name']} | {r['venue']} | {pct(d['cagr'])} | {pct(d['mdd'], 0)} | {f2(d['sharpe'])} | {pct(r['dev2']['cagr'])} | {r['pl'][0]:.0%} | "
                 + " / ".join(pct(r["coins"][a]["cagr"], 0) for a in r["coins"]) +
                 f" | {ts.get('per_yr', 0):.0f} | {ts.get('win', 0):.0%} | {'-' if np.isnan(hold) else f'{hold / 24:.1f}일'} | {r['expo']:.0%} | "
                 f"{pct(h['cagr'])} | {pct(h['mdd'], 0)} | {f2(h['sharpe'])} | " + " / ".join(pct(r["hcoins"][a]["cagr"], 0) for a in r["hcoins"]) +
                 f" | {r['hts'].get('n', 0)} |")
    L += ["", "## 2. 생존 편향 — 그때 상위 20 (상폐 포함) vs 지금 살아있는 코인만", "",
          "| 방식 | 유니버스 | 개발 연복리 | MDD | 샤프 | 비용 2배 | 같은 종목 균등 보유 (개발) | BTC·ETH·SOL 빼고 (개발) | 거래한 코인 | 손익 + 코인 비율 | 상폐 코인 거래 손익합 | 연 거래(전체) | 승률 | 투자 비중 | 확인 연복리 | 확인 MDD | 확인 샤프 | 같은 종목 보유 (확인) | BTC·ETH·SOL 빼고 (확인) |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["uni"]:
        d, h = r["dev"], r["hold"]
        L.append(f"| {r['name']} | {r['uni']} | {pct(d['cagr'])} | {pct(d['mdd'], 0)} | {f2(d['sharpe'])} | {pct(r['dev2']['cagr'])} | "
                 f"{pct(r['bh_dev']['cagr'])} (샤프 {f2(r['bh_dev']['sharpe'])}, MDD {pct(r['bh_dev']['mdd'], 0)}) | {pct(r['excore_dev']['cagr'])} (샤프 {f2(r['excore_dev']['sharpe'])}) | "
                 f"{r['n_sym']} | {r['pos_sym']:.0%} | {r['dead_n']}개 {pct(r['dead_pnl'], 0)} | {r['ts'].get('per_yr', 0) * 3:.0f} | {r['ts'].get('win', 0):.0%} | {r['expo']:.0%} | "
                 f"{pct(h['cagr'])} | {pct(h['mdd'], 0)} | {f2(h['sharpe'])} | {pct(r['bh_hold']['cagr'])} (샤프 {f2(r['bh_hold']['sharpe'])}) | "
                 f"{pct(r['excore_hold']['cagr'])} |")
    L += ["", "- '연 거래(전체)'는 20칸 전체의 연간 거래 수(대략). '상폐 코인 거래 손익합'은 지금은 거래되지 않는 코인에서 낸 거래 수익률 합.", "",
          "## 3. 결론", "", "(실행 결과를 보고 사람이 씀)", ""]
    p = Path(config_mod.ROOT) / "docs" / f"breakout4h-{today}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p
