"""결과 표·CSV·차트."""

import os

import matplotlib

matplotlib.use("Agg")  # 창 없이 PNG 로만 저장
import matplotlib.pyplot as plt
import pandas as pd

PCT = ["총수익률", "CAGR", "MDD", "승률", "평균투자비중", "B&H 총수익률", "B&H CAGR", "B&H MDD"]


def fmt_table(rows):
    df = pd.DataFrame(rows)
    shown = df.copy()
    for c in shown.columns:
        if c in PCT:
            shown[c] = shown[c].map(lambda v: "" if pd.isna(v) else f"{v * 100:,.1f}%")
        elif c in ("샤프지수", "B&H 샤프지수"):
            shown[c] = shown[c].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
        elif c == "거래횟수":
            shown[c] = shown[c].map(lambda v: "" if pd.isna(v) else f"{int(v)}")
    return df, shown.to_string(index=False)


def equity_chart(path, title, curves, currency):
    """curves: {이름: equity Series}. 위: 자산곡선, 아래: 낙폭."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for name, s in curves.items():
        ax1.plot(s.index, s.values, label=name, linewidth=1.4)
        ax2.plot(s.index, (s / s.cummax() - 1) * 100, linewidth=1.0)
    ax1.set_title(title)
    ax1.set_ylabel(f"Equity ({currency})")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def symbol_grid(path, per_symbol):
    """per_symbol: {symbol: (strategy_equity, bh_equity)} 종목별 작은 차트 모음."""
    n = len(per_symbol)
    if n == 0:
        return
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 3 * rows), squeeze=False)
    for ax, (sym, (st, bh)) in zip(axes.flat, per_symbol.items()):
        ax.plot(st.index, st / st.iloc[0], label="Strategy", linewidth=1.2)
        ax.plot(bh.index, bh / bh.iloc[0], label="Buy & Hold", linewidth=1.0, alpha=0.8)
        ax.set_title(sym)
        ax.grid(alpha=0.3)
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    axes.flat[0].legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def save(out_dir, summary_df, trades, equities):
    os.makedirs(out_dir, exist_ok=True)
    summary_df.to_csv(os.path.join(out_dir, "summary.csv"), index=False, encoding="utf-8-sig")
    trades.to_csv(os.path.join(out_dir, "trades.csv"), index=False, encoding="utf-8-sig")
    for market, eq in equities.items():
        eq.to_csv(os.path.join(out_dir, f"equity_{market}.csv"), encoding="utf-8-sig")
