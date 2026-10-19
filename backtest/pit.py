"""생존 편향·위약 테스트 (입상형 공식이 진짜 효과가 있는지).

1) 생존 편향: 지금까지는 '오늘' 시가총액 상위 250종목으로 5년 전부터 시험했어요.
   오늘 큰 회사는 대개 지난 몇 년간 많이 오른 회사라 모멘텀 결과가 부풀려질 수 있어요.
   → 구간마다 '그 구간 시작 전날' 기준 코스피 시총 상위 150 + 코스닥 상위 100 으로 다시 시험해요.
     자료: FinanceData/marcap (상장폐지 종목까지 날짜별 시가총액·가격이 있어요)
2) 위약 테스트: 같은 매매 규칙(3종목, 2일마다 교체, 비용 포함)으로
   - 무작위 3종목 (구간마다 다른 무작위, 5번 반복해서 합침)
   - 상승 추세 종목 중 무작위 3종목 (5번)
   - 20일 변동성 상위 3종목
   과 비교해서, +30% 확률이 '모멘텀' 덕분인지 '3종목 집중·변동성' 덕분인지 봐요.

가격: marcap 은 수정주가가 아니라서, 상장주식수가 1.5배 넘게 늘거나 2/3 아래로 줄면서
      가격이 그 반대로 움직인 날을 액면분할·병합으로 보고 앞쪽 가격을 맞춰요.
상장폐지: 마지막 거래 가격으로 평가해요 (실제로는 정리매매에서 더 잃을 수 있어요 → 공식에 유리한 쪽 오차).

실행: python pit.py [marcap 폴더] → results/pit.txt
"""

import os
import sys
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contest  # noqa: E402
from main import load_config  # noqa: E402

YEARS = range(2020, 2027)
URL = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{}.parquet"
N_KOSPI, N_KOSDAQ = 150, 100
SEEDS = 5


def load_marcap(folder, years=YEARS):
    os.makedirs(folder, exist_ok=True)
    frames = []
    for y in years:
        path = os.path.join(folder, f"marcap-{y}.parquet")
        if not os.path.exists(path):
            urllib.request.urlretrieve(URL.format(y), path)
        frames.append(pd.read_parquet(path, columns=["Code", "Name", "Market", "Date", "Open", "High", "Low",
                                                     "Close", "Volume", "Marcap", "Stocks"]))
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
    df["Market"] = df["Market"].astype(str).str.replace("KOSDAQ GLOBAL", "KOSDAQ")
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])]
    code, name = df["Code"].astype(str), df["Name"].astype(str)
    common = code.str.fullmatch(r"\d{5}0") & ~name.str.contains(r"우$|우B$|우\(|스팩|리츠|REIT", regex=True)
    df = df[common & (df["Close"] > 0)].copy()
    df["Code"] = df["Code"].astype(str)
    return df.sort_values(["Code", "Date"])


def adjust_splits(g):
    """액면분할·병합으로 보이는 날 앞쪽 가격을 나눠서 수익률이 끊기지 않게 해요."""
    g = g.copy()
    k = g["Stocks"] / g["Stocks"].shift(1)
    pr = g["Close"] / g["Close"].shift(1)
    split = ((k >= 1.5) | (k <= 0.67)) & ((pr * k - 1).abs() < 0.35)
    factor = pd.Series(np.where(split, 1 / k, 1.0), index=g.index)
    # 각 날짜보다 '뒤에' 있는 분할 비율을 곱해요 (뒤에서부터 누적)
    after = factor[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    for c in ("Open", "High", "Low", "Close"):
        g[c] = g[c] * after
    halted = g["Open"] <= 0
    for c in ("Open", "High", "Low"):
        g.loc[halted, c] = g.loc[halted, "Close"]
    return g


def universe_on(df_day):
    out = []
    for market, n in (("KOSPI", N_KOSPI), ("KOSDAQ", N_KOSDAQ)):
        m = df_day[df_day["Market"] == market].nlargest(n, "Marcap")
        out += list(m["Code"])
    return set(out)


def pooled(rows):
    """여러 번 돌린 무작위 결과를 구간 단위로 이어 붙여요."""
    rets, bench, stats = [], [], []
    for r, b, s in rows:
        rets += r
        bench += b
        stats += s
    return rets, bench, stats


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--search" in sys.argv:
        return search(args[0] if args else os.path.join(HERE, ".cache", "marcap"))
    if "--eras" in sys.argv:
        return eras(args[0] if args else os.path.join(HERE, ".cache", "marcap"))
    if "--weak" in sys.argv:
        return eras(args[0] if args else os.path.join(HERE, ".cache", "marcap"), WEAK_VARIANTS, "pit_weak",
                    with_refs=False)
    folder = args[0] if args else os.path.join(HERE, ".cache", "marcap")
    cfg = load_config(os.path.join(HERE, "config.yaml"))
    df = load_marcap(folder)
    dates = sorted(df["Date"].unique())
    by_date = dict(tuple(df.groupby("Date")))
    days = 26
    first = 130  # 지표 계산용 앞 기간
    wins = contest.windows(dates[first:], days, 5)
    # 구간 시작 전날 기준 종목 (그때의 시총 상위)
    pit = []
    for a, _ in wins:
        prev = dates[dates.index(a) - 1]
        pit.append(universe_on(by_date[prev]))
    today = universe_on(by_date[dates[-1]])
    codes = set().union(*pit) | today
    print(f"구간 {len(wins)}개 ({wins[0][0]:%Y-%m-%d} ~ {wins[-1][1]:%Y-%m-%d}), 한 번이라도 대상이 된 종목 {len(codes)}개, "
          f"오늘 기준 250종목 중 처음 구간에도 있던 종목 {len(today & pit[0])}개")
    raw = {}
    for c, g in df[df["Code"].isin(codes)].groupby("Code"):
        g = adjust_splits(g).set_index("Date")
        if len(g) > 70:
            raw[c] = g[["Open", "High", "Low", "Close", "Volume"]]
    frames = contest.prepare(raw, cfg, [20])
    bars = contest.build_bars(frames)
    regime = contest.build_regime(bars)
    cap, rule, scale = cfg["capital"]["KR"], contest.RULES["KR"], cfg["capital"]["KR"] / 1e8
    base = dict(contest.VOL_BASE)

    def run(params, universes):
        rot = contest.Rotation(frames, "KR", cfg, params, bars, regime)
        rets, stats, bench = [], [], []
        for (a, b), allowed in zip(wins, universes):
            curve, st = rot.run(a, b, cap, allowed=allowed)
            rets.append(curve.iloc[-1] / cap - 1)
            stats.append(st)
            bench.append(contest.equal_weight_return({s: frames[s] for s in allowed if s in frames}, a, b))
        return rets, bench, stats

    cut = int(len(wins) * 0.6)
    cases = [
        ("모멘텀 (지금 공식) — 오늘 기준 종목 [생존 편향 있음]", [run(base, [today] * len(wins))]),
        ("모멘텀 (지금 공식) — 그때 기준 종목", [run(base, pit)]),
        ("모멘텀 + 약세장 금액 절반 — 그때 기준 종목",
         [run({**base, "regime": "breadth40", "regime_off": "scale50"}, pit)]),
        ("무작위 3종목 (5번) — 그때 기준 종목",
         [run({**base, "rank": f"random{i}", "trend_filter": False}, pit) for i in range(SEEDS)]),
        ("상승 추세 중 무작위 3종목 (5번) — 그때 기준 종목",
         [run({**base, "rank": f"random{i}"}, pit) for i in range(SEEDS)]),
        ("20일 변동성 상위 3종목 — 그때 기준 종목", [run({**base, "rank": "vol", "trend_filter": False}, pit)]),
    ]
    rows = []
    for name, runs in cases:
        parts = {"학습": ([], [], []), "검증": ([], [], [])}
        for r, b, s in runs:
            for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
                parts[part][0].extend(r[sl])
                parts[part][1].extend(b[sl])
                parts[part][2].extend(s[sl])
        row = {"방식": name}
        for part in ("학습", "검증"):
            sm = contest.summarize(*parts[part], rule, scale)
            for k in ("중앙값", "평균", "상위10%", "+30%확률", "하위10%", "최악", "기준평균"):
                row[f"{part} {k}"] = sm[k]
        rows.append(row)
        print(f"  {name} 끝")
    text = "\n".join([
        f"생존 편향·위약 테스트: 26거래일 구간 {len(wins)}개 (학습 {cut}개 ~{wins[cut][0]:%Y-%m-%d} 전 / 검증 {len(wins) - cut}개)",
        f"'그때 기준 종목' = 구간 시작 전날 코스피 시총 상위 {N_KOSPI} + 코스닥 상위 {N_KOSDAQ} (상장폐지 종목 포함)",
        f"'기준평균' = 그 구간 대상 종목 전체를 똑같이 나눠 산 평균 (비용 제외)",
        "", contest.fmt(pd.DataFrame(rows)), ""])
    print(text)
    out = os.path.join(HERE, "results")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "pit.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


# ---------- 변수 탐색 (그때 기준 종목으로) ----------
# 미리 정한 고르는 법: 학습 구간에서 수상 조건 충족률 95% 이상 중 +30% 확률 1위 (같으면 중앙값).
# 검증 구간은 고른 뒤에 한 번만 봐요. 지금 공식·무작위·변동성 상위와 같이 보여 줘요.
SEARCH_GRID = {"lookback": [5, 10, 20, 60], "top_k": [2, 3, 5], "rebalance_days": [1, 2, 5],
               "trend_filter": [True, False], "regime": ["none", "breadth40"]}
_G = {}


def _prepare(folder):
    cfg = load_config(os.path.join(HERE, "config.yaml"))
    df = load_marcap(folder)
    dates = sorted(df["Date"].unique())
    by_date = dict(tuple(df.groupby("Date")))
    wins = contest.windows(dates[130:], 26, 5)
    pit = [universe_on(by_date[dates[dates.index(a) - 1]]) for a, _ in wins]
    codes = set().union(*pit)
    raw = {}
    for c, g in df[df["Code"].isin(codes)].groupby("Code"):
        g = adjust_splits(g).set_index("Date")
        if len(g) > 70:
            raw[c] = g[["Open", "High", "Low", "Close", "Volume"]]
    frames = contest.prepare(raw, cfg, [5, 10, 20, 60])
    bars = contest.build_bars(frames)
    _G.update(cfg=cfg, wins=wins, pit=pit, frames=frames, bars=bars, regime=contest.build_regime(bars))


def _run_one(params):
    g = _G
    cap = g["cfg"]["capital"]["KR"]
    rot = contest.Rotation(g["frames"], "KR", g["cfg"], params, g["bars"], g["regime"])
    rets, stats = [], []
    for (a, b), allowed in zip(g["wins"], g["pit"]):
        curve, st = rot.run(a, b, cap, allowed=allowed)
        rets.append(curve.iloc[-1] / cap - 1)
        stats.append(st)
    return params, rets, stats


def search(folder):
    import itertools
    import multiprocessing as mp
    _prepare(folder)
    g = _G
    rule, scale = contest.RULES["KR"], g["cfg"]["capital"]["KR"] / 1e8
    cut = int(len(g["wins"]) * 0.6)
    bench = [0.0] * len(g["wins"])
    combos = []
    for values in itertools.product(*SEARCH_GRID.values()):
        p = {**contest.VOL_BASE, **dict(zip(SEARCH_GRID, values))}
        if p["regime"] != "none":
            p["regime_off"] = "scale50"
        combos.append(p)
    refs = [{**contest.VOL_BASE, "rank": f"random{i}", "trend_filter": False} for i in range(SEEDS)] + \
           [{**contest.VOL_BASE, "rank": "vol", "trend_filter": False}]
    with mp.get_context("fork").Pool(4) as pool:
        results = pool.map(_run_one, combos + refs, chunksize=2)
    rows = []
    for p, rets, stats in results[:len(combos)]:
        row = {k: p[k] for k in SEARCH_GRID}
        for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
            sm = contest.summarize(rets[sl], bench[sl], stats[sl], rule, scale)
            for k in ("중앙값", "평균", "상위10%", "+30%확률", "하위10%", "최악", "조건충족률"):
                row[f"{part} {k}"] = sm[k]
        rows.append(row)
    df = pd.DataFrame(rows)
    ok = df[df["학습 조건충족률"] >= 0.95].sort_values(["학습 +30%확률", "학습 중앙값"], ascending=False)
    best = ok.iloc[0]
    cur = df[(df.lookback == 20) & (df.top_k == 3) & (df.rebalance_days == 2) & (df.trend_filter)
             & (df.regime == "breadth40")]
    cur0 = df[(df.lookback == 20) & (df.top_k == 3) & (df.rebalance_days == 2) & (df.trend_filter)
              & (df.regime == "none")]
    ref_rows = []
    for name, part_runs in (("무작위 3종목 (5번)", results[len(combos):len(combos) + SEEDS]),
                            ("변동성 상위 3종목", results[len(combos) + SEEDS:])):
        row = {"lookback": name}
        for part, sl in (("학습", slice(0, cut)), ("검증", slice(cut, None))):
            r = sum((x[1][sl] for x in part_runs), [])
            s = sum((x[2][sl] for x in part_runs), [])
            sm = contest.summarize(r, [0.0] * len(r), s, rule, scale)
            for k in ("중앙값", "평균", "상위10%", "+30%확률", "하위10%", "최악", "조건충족률"):
                row[f"{part} {k}"] = sm[k]
        ref_rows.append(row)
    show = lambda d: contest.fmt(d.reset_index(drop=True))
    text = "\n".join([
        f"변수 탐색 (그때 기준 종목, 생존 편향 제거): 조합 {len(combos)}개, 구간 {len(g['wins'])}개 "
        f"(학습 {cut}개 / 검증 {len(g['wins']) - cut}개)",
        "고르는 법(미리 정함): 학습 구간 조건충족률 95% 이상 중 +30% 확률 1위",
        "", "[학습 기준 상위 10개 — 검증 값은 참고]", show(ok.head(10)),
        "", "[고른 조합]", show(ok.head(1)),
        "", "[지금 공식: 20일·3종목·2일·추세조건, 약세장 절반 / 약세장 대응 없음]", show(pd.concat([cur, cur0])),
        "", "[비교 기준]", show(pd.DataFrame(ref_rows)), ""])
    print(text)
    df.to_csv(os.path.join(HERE, "results", "pit_search.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(HERE, "results", "pit_search.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


# ---------- 시대별 비교 (더 긴 기간) ----------
# 2015-06-15 전에는 하루 가격제한폭이 ±15% (지금 ±30%) 라 급등주 모양이 달라요 → 시대를 나눠서 봐요.
ERAS = [("2005-01-01", "2015-06-12", "2005~2015.6 (가격제한 ±15%)"),
        ("2015-06-15", "2019-12-31", "2015.6~2019"),
        ("2020-01-01", "2026-12-31", "2020~2026")]
ERA_VARIANTS = [
    ("지금 공식: 20일·3종목·2일·추세", {}),
    ("20일·2종목·2일·추세 없음", {"top_k": 2, "trend_filter": False}),
    ("20일·3종목·매일", {"rebalance_days": 1}),
    ("10일·3종목·2일", {"lookback": 10}),
    ("60일·3종목·2일", {"lookback": 60}),
    ("20일·5종목·2일", {"top_k": 5}),
]


# 시기별 큰 사건 (이 구간과 겹치는 26일 구간들만 따로 모아서 봐요)
EVENTS = [
    ("2008 글로벌 금융위기", "2008-06-01", "2009-03-06"),
    ("2009~2010 위기 후 반등", "2009-03-09", "2010-12-31"),
    ("2011 미국 신용강등·유럽 재정위기", "2011-08-01", "2011-12-30"),
    ("2015 가격제한 ±30% 확대·중국 증시 폭락", "2015-06-15", "2015-12-30"),
    ("2017 반도체 대형주 랠리", "2017-01-02", "2017-12-28"),
    ("2018 미중 무역분쟁 급락", "2018-10-01", "2019-01-31"),
    ("2020 코로나 폭락", "2020-02-17", "2020-03-31"),
    ("2020~2021 코로나 후 유동성 랠리", "2020-04-01", "2021-06-30"),
    ("2022 금리 인상 약세장", "2022-01-03", "2022-10-31"),
    ("2023 2차전지 테마 과열", "2023-03-02", "2023-08-31"),
    ("2024.8 블랙먼데이", "2024-07-25", "2024-08-30"),
    ("2024.12 비상계엄·탄핵 정국", "2024-12-02", "2025-01-31"),
    ("2025.4 미국 관세 충격", "2025-03-25", "2025-05-15"),
    ("2025 하반기~2026 AI 반도체 랠리", "2025-06-02", "2026-06-30"),
    ("2026.6~10 급등락 장세", "2026-06-15", "2026-10-16"),
]


def event_table(windows_df):
    """사건 구간과 겹치는 26일 구간들의 성적 (지금 공식 / 무작위 / 전체 동일비중)."""
    rows = []
    w = windows_df
    for name, a, b in EVENTS:
        a, b = pd.Timestamp(a), pd.Timestamp(b)
        sel = w[(w["start"] <= b) & (w["end"] >= a)]
        if sel.empty:
            continue
        cur = sel[sel["방식"] == ERA_VARIANTS[0][0]]
        rnd = sel[sel["방식"] == "무작위 3종목 (5번)"]
        rows.append({"사건": name, "구간수": len(cur),
                     "공식 중앙값": cur["ret"].median(), "공식 평균": cur["ret"].mean(),
                     "공식 +30%확률": (cur["ret"] >= contest.TAIL_GOAL).mean(), "공식 최악": cur["ret"].min(),
                     "무작위 중앙값": rnd["ret"].median(), "무작위 +30%확률": (rnd["ret"] >= contest.TAIL_GOAL).mean(),
                     "시장(동일비중) 평균": cur["bench"].mean()})
    return pd.DataFrame(rows)


# 약세장·변동장에 강한 공식 후보 (모두 그때 기준 시총 상위 250종목, 2일마다 교체, 비용 포함)
_PULLBACK = {"rank": "rev5", "trend_filter": "above60"}
_REVERSAL = {"rank": "rev5", "trend_filter": False}
WEAK_VARIANTS = [
    ("지금 공식(모멘텀)", {}),
    ("단기 반전: 5일 낙폭 상위 3", _REVERSAL),
    ("눌림목: 60일선 위 중 5일 낙폭 상위 3", _PULLBACK),
    ("과매도: RSI 최저 3 (60일선 위)", {"rank": "rsi_low", "trend_filter": "above60"}),
    ("저변동: 60일 변동성 최저 5, 5일 교체", {"rank": "lowvol", "trend_filter": False, "top_k": 5, "rebalance_days": 5}),
    ("국면전환(약세장=60일선 위 40% 미만): 모멘텀/눌림목", {"regime": "breadth40", "weak_params": _PULLBACK}),
    ("국면전환(약세장): 모멘텀/단기 반전", {"regime": "breadth40", "weak_params": _REVERSAL}),
    ("국면전환(변동장): 모멘텀/눌림목", {"regime": "chop", "weak_params": _PULLBACK}),
    ("국면전환(변동장): 모멘텀/단기 반전", {"regime": "chop", "weak_params": _REVERSAL}),
]


def event_median_table(windows_df, names):
    """사건별 중앙값 (방식마다 한 열) + 시장 평균."""
    rows = []
    for ev, a, b in EVENTS:
        a, b = pd.Timestamp(a), pd.Timestamp(b)
        sel = windows_df[(windows_df["start"] <= b) & (windows_df["end"] >= a)]
        if sel.empty:
            continue
        row = {"사건": ev}
        for i, n in enumerate(names):
            row[f"{i + 1} 중앙값"] = sel[sel["방식"] == n]["ret"].median()
        row["시장 평균"] = sel[sel["방식"] == names[0]]["bench"].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def eras(folder, variants=None, out_name="pit_eras", with_refs=True):
    import multiprocessing as mp
    variants = variants or ERA_VARIANTS
    window_rows = []
    out_lines = ["시대별 비교 (생존 편향 제거: 구간 시작 전날 코스피 시총 상위 150 + 코스닥 100, 상장폐지 포함)",
                 "26거래일 구간을 5거래일씩 밀며, 비용 포함. 무작위는 5번 합친 값", ""]
    for start, end, label in ERAS:
        y0, y1 = int(start[:4]) - 1, int(end[:4])
        cfg = load_config(os.path.join(HERE, "config.yaml"))
        df = load_marcap(folder, range(y0, min(y1, 2026) + 1))
        dates = sorted(df["Date"].unique())
        by_date = dict(tuple(df.groupby("Date")))
        s0 = pd.Timestamp(start)
        inside = [d for d in dates if s0 <= d <= pd.Timestamp(end)]
        wins = contest.windows(inside, 26, 5)
        pit = [universe_on(by_date[dates[dates.index(a) - 1]]) for a, _ in wins]
        codes = set().union(*pit)
        raw = {}
        for c, g in df[df["Code"].isin(codes)].groupby("Code"):
            g = adjust_splits(g).set_index("Date")
            if len(g) > 70:
                raw[c] = g[["Open", "High", "Low", "Close", "Volume"]]
        del df, by_date
        frames = contest.prepare(raw, cfg, [5, 10, 20, 60])
        bars = contest.build_bars(frames)
        _G.clear()
        _G.update(cfg=cfg, wins=wins, pit=pit, frames=frames, bars=bars, regime=contest.build_regime(bars))
        refs = ([{**contest.VOL_BASE, "rank": f"random{i}", "trend_filter": False} for i in range(SEEDS)] +
                [{**contest.VOL_BASE, "rank": "vol", "trend_filter": False}]) if with_refs else []
        params = [{**contest.VOL_BASE, **v} for _, v in variants] + refs
        with mp.get_context("fork").Pool(3) as pool:
            res = pool.map(_run_one, params, chunksize=1)
        rule, scale = contest.RULES["KR"], cfg["capital"]["KR"] / 1e8
        bench = [contest.equal_weight_return({s: frames[s] for s in u if s in frames}, a, b) for (a, b), u in zip(wins, pit)]
        groups = [(n, [res[i]]) for i, (n, _) in enumerate(variants)]
        if with_refs:
            groups += [("무작위 3종목 (5번)", res[len(variants):len(variants) + SEEDS]),
                       ("변동성 상위 3종목", res[len(variants) + SEEDS:])]
        rows = []
        for name, rs in groups:
            r = sum((x[1] for x in rs), [])
            st = sum((x[2] for x in rs), [])
            for k, ret in enumerate(r):
                (a, b) = wins[k % len(wins)]
                window_rows.append({"era": label, "방식": name, "start": a, "end": b, "ret": ret,
                                    "bench": bench[k % len(wins)]})
            sm = contest.summarize(r, bench * len(rs), st, rule, scale)
            rows.append({"방식": name, **{k: sm[k] for k in ("중앙값", "평균", "상위10%", "+30%확률", "하위10%", "최악",
                                                          "조건충족률", "기준평균")}})
        out_lines += [f"[{label}] 구간 {len(wins)}개 ({wins[0][0]:%Y-%m-%d} ~ {wins[-1][1]:%Y-%m-%d}), 대상이 된 종목 {len(codes)}개",
                      contest.fmt(pd.DataFrame(rows)), ""]
        print("\n".join(out_lines[-3:]), flush=True)
        del frames, bars, raw
        _G.clear()
    wdf = pd.DataFrame(window_rows)
    wdf.to_csv(os.path.join(HERE, "results", f"{out_name}_windows.csv"), index=False, encoding="utf-8-sig")
    if with_refs:
        ev = event_table(wdf)
        out_lines += ["[시기별 큰 사건] 사건 기간과 겹치는 26일 구간들 (지금 공식 vs 무작위 3종목 vs 시장 전체 동일비중)",
                      contest.fmt(ev), ""]
    else:
        names = [n for n, _ in variants]
        out_lines += ["[시기별 큰 사건] 사건 기간과 겹치는 26일 구간들의 중앙값 (번호는 아래 방식)",
                      "  " + " / ".join(f"{i + 1}={n}" for i, n in enumerate(names)),
                      contest.fmt(event_median_table(wdf, names)), ""]
    print("\n".join(out_lines[-3:]), flush=True)
    text = "\n".join(out_lines)
    with open(os.path.join(HERE, "results", f"{out_name}.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
