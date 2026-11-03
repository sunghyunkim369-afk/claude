import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/user/claude/backtest")
import pit, contest
from main import load_config
import multiprocessing as mp
folder = sys.argv[1]
cfg = load_config("/home/user/claude/backtest/config.yaml")
df = pit.load_marcap(folder, range(2019, 2027))
dates = sorted(df["Date"].unique())
by_date = dict(tuple(df.groupby("Date")))
inside = [d for d in dates if d >= pd.Timestamp("2020-01-01")]
wins = contest.windows(inside, 14, 3)
pits = [pit.universe_on(by_date[dates[dates.index(a) - 1]]) for a, _ in wins]
codes = set().union(*pits)
raw = {}
for c, g in df[df["Code"].isin(codes)].groupby("Code"):
    g = pit.adjust_splits(g).set_index("Date")
    if len(g) > 70:
        raw[c] = g[["Open", "High", "Low", "Close", "Volume"]]
frames = contest.prepare(raw, cfg, [20])
bars = contest.build_bars(frames)
pit._G.update(cfg=cfg, wins=wins, pit=pits, frames=frames, bars=bars, regime=contest.build_regime(bars))
V = [("지금 공식 3종목", {}), ("2종목", {"top_k": 2}), ("1종목 몰빵", {"top_k": 1})]
with mp.get_context("fork").Pool(3) as pool:
    res = pool.map(pit._run_one, [{**contest.VOL_BASE, **v} for _, v in V])
print(f"14거래일 구간 {len(wins)}개 (2020~2026, 그때 기준 종목, 비용 포함)")
for (n, _), (p, r, s) in zip(V, res):
    r = np.array(r)
    print(f"{n}: +35% 이상 {np.mean(r >= .354)*100:.1f}%, +20% 이상 {np.mean(r >= .2)*100:.1f}%, +10% 이상 {np.mean(r>=.1)*100:.1f}%, 중앙값 {np.median(r)*100:+.1f}%, 하위10% {np.percentile(r,10)*100:+.1f}%")
