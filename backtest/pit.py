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


def load_marcap(folder):
    os.makedirs(folder, exist_ok=True)
    frames = []
    for y in YEARS:
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
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, ".cache", "marcap")
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


if __name__ == "__main__":
    main()
