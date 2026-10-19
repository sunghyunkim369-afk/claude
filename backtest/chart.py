"""종목 차트·지표 뽑기 (Claude 가 차트를 보고 판단할 수 있게).

chart_list.txt 와 holdings.csv(보유 종목: code,name,qty,avg_price)의 종목코드마다:
  results/charts/{코드}_daily.png  일봉 6개월: 캔들, EMA5/20/60, 거래량, RSI14
  results/charts/{코드}_5m.png     5분봉 최근 5거래일 (yfinance, 못 받으면 건너뜀)
  results/charts/summary.txt       숫자 요약 (종가, 이평선 대비 위치, RSI, 20일 수익률, 고점 대비, 거래량 배수 등)

실행: python chart.py  (GitHub Actions 에서 커밋 메시지에 [chart-only] 가 있으면 돌아요)
"""

import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(HERE, "results", "charts")


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def daily(code, days=200):
    import FinanceDataReader as fdr
    df = fdr.DataReader(code, (date.today() - timedelta(days=days * 2)).isoformat())
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna().tail(days)


def intraday(code):
    import yfinance as yf
    for suffix in (".KQ", ".KS"):
        df = yf.Ticker(code + suffix).history(period="5d", interval="5m")
        if len(df) > 20:
            df.index = df.index.tz_convert("Asia/Seoul").tz_localize(None)
            return df[["Open", "High", "Low", "Close", "Volume"]]
    return None


def candles(ax, df):
    x = np.arange(len(df))
    up = df["Close"] >= df["Open"]
    for color, m in (("#d62728", up), ("#1f77b4", ~up)):
        ax.vlines(x[m], df["Low"][m], df["High"][m], color=color, linewidth=0.8)
        ax.bar(x[m], (df["Close"] - df["Open"])[m].abs().clip(lower=df["Close"][m] * 0.0005),
               bottom=np.minimum(df["Open"], df["Close"])[m], color=color, width=0.7)
    return x


def ticks(ax, df, fmt, n=8):
    step = max(1, len(df) // n)
    ax.set_xticks(range(0, len(df), step))
    ax.set_xticklabels([d.strftime(fmt) for d in df.index[::step]], fontsize=7)


def plot_daily(code, df, path):
    c = df["Close"]
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(11, 8), sharex=True, gridspec_kw={"height_ratios": [4, 1.2, 1.2]})
    x = candles(a1, df)
    for n, col in ((5, "#ff7f0e"), (20, "#9467bd"), (60, "#2ca02c")):
        a1.plot(x, ema(c, n), color=col, linewidth=1, label=f"EMA{n}")
    a1.legend(loc="upper left", fontsize=8)
    a1.set_title(f"{code} daily (last close {c.iloc[-1]:,.0f}, {df.index[-1]:%Y-%m-%d})")
    a1.grid(alpha=0.3)
    vol_up = df["Close"] >= df["Open"]
    a2.bar(x, df["Volume"], color=np.where(vol_up, "#d62728", "#1f77b4"), width=0.7)
    a2.plot(x, df["Volume"].rolling(20).mean(), color="black", linewidth=0.8, label="vol MA20")
    a2.legend(loc="upper left", fontsize=7)
    a3.plot(x, rsi(c), color="#8c564b", linewidth=1)
    a3.axhline(70, color="gray", linestyle="--", linewidth=0.6)
    a3.axhline(30, color="gray", linestyle="--", linewidth=0.6)
    a3.set_ylim(0, 100)
    a3.set_ylabel("RSI14", fontsize=8)
    ticks(a3, df, "%m-%d")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_5m(code, df, path):
    c = df["Close"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True, gridspec_kw={"height_ratios": [4, 1.2]})
    x = candles(a1, df)
    for n, col in ((20, "#9467bd"), (60, "#1f77b4"), (120, "#2ca02c")):
        a1.plot(x, c.rolling(n).mean(), color=col, linewidth=1, label=f"MA{n}")
    a1.legend(loc="upper left", fontsize=8)
    a1.set_title(f"{code} 5-min, last 5 sessions (last {c.iloc[-1]:,.0f})")
    a1.grid(alpha=0.3)
    a2.bar(x, df["Volume"], color="gray", width=0.7)
    ticks(a2, df, "%m-%d %H:%M", 10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def summary(code, df):
    c, v = df["Close"], df["Volume"]
    e5, e20, e60 = (ema(c, n).iloc[-1] for n in (5, 20, 60))
    last = c.iloc[-1]
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    hi20, lo20 = df["High"].tail(20).max(), df["Low"].tail(20).min()
    rows = [
        f"[{code}] {df.index[-1]:%Y-%m-%d} 종가 {last:,.0f} (전일 대비 {last / c.iloc[-2] - 1:+.2%})",
        f"  이평선: EMA5 {e5:,.0f} / EMA20 {e20:,.0f} / EMA60 {e60:,.0f} → 종가가 EMA20 {last / e20 - 1:+.1%}, "
        f"EMA60 {last / e60 - 1:+.1%}, 정배열(종가>20>60) {'예' if last > e20 > e60 else '아니오'}",
        f"  수익률: 5일 {last / c.iloc[-6] - 1:+.1%}, 20일 {last / c.iloc[-21] - 1:+.1%}, 60일 {last / c.iloc[-61] - 1:+.1%}",
        f"  20일 범위: 고가 {hi20:,.0f} ({last / hi20 - 1:+.1%}) / 저가 {lo20:,.0f} ({last / lo20 - 1:+.1%})",
        f"  RSI14 {rsi(c).iloc[-1]:.0f}, ATR14 {atr:,.0f} (종가의 {atr / last:.1%}, 하루 변동 폭)",
        f"  거래량: 오늘 {v.iloc[-1]:,.0f} = 20일 평균의 {v.iloc[-1] / v.tail(20).mean():.1f}배, "
        f"최근 5일 평균 = 60일 평균의 {v.tail(5).mean() / v.tail(60).mean():.1f}배",
    ]
    return rows


def load_holdings():
    """holdings.csv: code,name,qty,avg_price (매입가 기록. 계좌번호·키는 넣지 않아요)."""
    path = os.path.join(HERE, "holdings.csv")
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype={"code": str}, comment="#")
    return {r.code.zfill(6): {"name": r.name, "qty": float(r.qty), "avg": float(r.avg_price)} for r in df.itertuples()}


def holdings_lines(hold, last):
    out = ["[보유 종목 손익] (가격은 일봉 종가 또는 5분봉 마지막 값, 수수료·세금 제외)"]
    cost = value = 0.0
    for code, h in hold.items():
        p = last.get(code)
        if p is None:
            out.append(f"  {h['name']}({code}) 가격 못 받음")
            continue
        c, v = h["qty"] * h["avg"], h["qty"] * p
        cost, value = cost + c, value + v
        out.append(f"  {h['name']}({code}) {h['qty']:,.0f}주, 매입가 {h['avg']:,.0f} → {p:,.0f} ({p / h['avg'] - 1:+.2%}), "
                   f"평가 {v:,.0f}원 (손익 {v - c:+,.0f}원)")
    if cost:
        out.append(f"  합계: 매입 {cost:,.0f}원 → 평가 {value:,.0f}원 ({value / cost - 1:+.2%}, {value - cost:+,.0f}원)")
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(HERE, "chart_list.txt")
    codes = [l.split("#")[0].strip() for l in open(path, encoding="utf-8")] if os.path.exists(path) else []
    codes = [c for c in codes if c]
    hold = load_holdings()
    codes = list(dict.fromkeys(list(hold) + codes))
    lines = [f"만든 날: {date.today()}"]
    last_close = {}
    for code in codes:
        try:
            df = daily(code)
            plot_daily(code, df, os.path.join(OUT, f"{code}_daily.png"))
            lines += summary(code, df)
            last_close[code] = float(df["Close"].iloc[-1])
        except Exception as e:
            lines.append(f"[{code}] 일봉 실패: {e}")
            continue
        try:
            m = intraday(code)
            if m is not None:
                last_close[code] = float(m["Close"].iloc[-1])  # 5분봉이 더 최근 (보통 15~20분 늦음)
                plot_5m(code, m, os.path.join(OUT, f"{code}_5m.png"))
                lines.append(f"  5분봉: {m.index[0]:%m-%d %H:%M} ~ {m.index[-1]:%m-%d %H:%M}, 마지막 {m['Close'].iloc[-1]:,.0f}")
            else:
                lines.append("  5분봉: 못 받음")
        except Exception as e:
            lines.append(f"  5분봉 실패: {e}")
    if hold:
        lines = lines[:1] + holdings_lines(hold, last_close) + [""] + lines[1:]
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
