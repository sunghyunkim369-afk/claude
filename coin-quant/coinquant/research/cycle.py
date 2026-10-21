"""비트코인 4년 주기(반감기 사이클)가 실재하는지 검증: `python -m coinquant cycle`

질문 네 가지
1. 기술: 반감기마다 고점·저점이 언제, 얼마나 왔나
2. 우연 검정: '반감기 후 0~18개월 강세, 18~30개월 약세' 정렬이 무작위로 맞춘 정렬보다 뚜렷한가 (순열 검정)
3. 예측 성적: 그 전 사이클들만 보고 다음 사이클 고점·저점 시기를 예측했으면 맞았나 (표본 밖)
4. 변화: 사이클 진폭(상승 배수·낙폭)이 줄고 있나 → 지금 위치

원칙: 사이클을 '공식 규칙'으로 쓸지 판단하는 2·5절은 2024-12-31까지만 사용 (최종 검증 구간 잠금 유지).
지금 위치(6절)를 말하려면 2025년 이후 BTC 가격을 볼 수밖에 없어서 그 부분은 따로 표시합니다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..backtest import engine, metrics

HALVINGS = [pd.Timestamp(d) for d in ("2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20")]
NEXT_HALVING_EST = pd.Timestamp("2028-04-15")   # 블록 속도에 따라 몇 주 달라질 수 있음 (추정)
DEV_END = pd.Timestamp("2024-12-31")


def btc_series(cache: Path) -> pd.Series:
    """Coin Metrics(2010~) + 그 뒤는 바이낸스 BTCUSDT 종가. 겹치는 기간 두 값의 차이는 중앙값 0.01% 수준."""
    cm = pd.read_csv(cache / "coinmetrics" / "btc.csv", parse_dates=["date"]).set_index("date")["PriceUSD"].dropna()
    bn = pd.read_csv(cache / "binance" / "spot_1d" / "BTCUSDT.csv", parse_dates=["date"]).set_index("date")["close"]
    s = pd.concat([cm, bn[bn.index > cm.index.max()]]).sort_index()
    return s.asfreq("D").ffill()


def cycle_table(s: pd.Series) -> pd.DataFrame:
    rows = []
    for i, h in enumerate(HALVINGS):
        nxt = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else NEXT_HALVING_EST
        seg = s.loc[h:nxt]
        if len(seg) < 30:
            continue
        peak_d = seg.loc[: h + pd.Timedelta(days=1000)].idxmax()
        after = s.loc[peak_d:nxt]
        trough_d = after.idxmin()
        # 직전 저점: 직전 사이클 고점 이후 ~ 이번 반감기 사이 최저 (첫 사이클은 2011-06 고점 이후)
        prev_peak = (s.loc[HALVINGS[i - 1]:HALVINGS[i - 1] + pd.Timedelta(days=1000)].idxmax() if i
                     else s.loc["2011"].idxmax())
        prev_trough = s.loc[prev_peak:h].idxmin()
        complete = trough_d < after.index[-1] - pd.Timedelta(days=60) or nxt <= s.index[-1]
        rows.append({
            "반감기": h.date(), "반감기 가격": s.loc[h], "고점일": peak_d.date(), "반감기→고점(일)": (peak_d - h).days,
            "고점": s.loc[peak_d], "반감기 대비 배수": s.loc[peak_d] / s.loc[h],
            "직전 저점 대비 배수": s.loc[peak_d] / s.loc[prev_trough],
            "저점일(고점 이후 최저)": trough_d.date(), "고점→저점(일)": (trough_d - peak_d).days,
            "반감기→저점(일)": (trough_d - h).days, "저점": s.loc[trough_d], "고점 대비 낙폭": s.loc[trough_d] / s.loc[peak_d] - 1,
            "끝난 사이클": "예" if complete and nxt <= s.index[-1] else "진행 중",
        })
    return pd.DataFrame(rows)


def phase_of(idx: pd.DatetimeIndex) -> pd.Series:
    """각 날짜가 직전 반감기로부터 며칠째인지 (첫 반감기 이전은 NaN)."""
    out = pd.Series(np.nan, index=idx)
    for i, h in enumerate(HALVINGS):
        end = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else idx.max() + pd.Timedelta(days=1)
        m = (idx >= h) & (idx < end)
        out[m] = (idx[m] - h).days
    return out


PHASES = [("0~6개월", 0, 183), ("6~12개월", 183, 365), ("12~18개월", 365, 548), ("18~24개월", 548, 730),
          ("24~30개월", 730, 913), ("30~36개월", 913, 1095), ("36~48개월", 1095, 1500)]


def phase_returns(s: pd.Series, end=DEV_END) -> pd.DataFrame:
    """사이클 × 국면별 연환산 수익률(로그 수익 평균 × 365 → 배수)."""
    r = np.log(s).diff().loc[HALVINGS[0]:end]
    ph = phase_of(r.index)
    rows = []
    for i, h in enumerate(HALVINGS):
        row = {"사이클": f"{h.year} 반감기"}
        for name, a, b in PHASES:
            m = (r.index >= h) & (ph >= a) & (ph < b) & (r.index < (HALVINGS[i + 1] if i + 1 < len(HALVINGS) else end + pd.Timedelta(days=1)))
            x = r[m]
            row[name] = np.exp(x.mean() * 365) - 1 if len(x) > 60 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def permutation_test(s: pd.Series, end=DEV_END, n=5000, seed=0, start=None) -> dict:
    """통계량 = (반감기 후 0~18개월 하루 평균 로그수익) − (18~30개월 평균).

    귀무가설(사이클 없음): 수익률 흐름을 통째로 임의 일수만큼 돌려(원형 이동) 반감기 달력과의 정렬만 깨뜨림.
    변동성 뭉침·추세 같은 성질은 그대로 둬서, '아무 데나 맞춰도 이만큼은 나온다'와 비교합니다.
    """
    r = np.log(s).diff().loc[start or HALVINGS[0]:end].dropna()
    ph = phase_of(r.index).to_numpy()
    up = (ph >= 0) & (ph < 548)
    down = (ph >= 548) & (ph < 913)
    x = r.to_numpy()

    def stat(v):
        return v[up].mean() - v[down].mean()

    t0 = stat(x)
    rng = np.random.default_rng(seed)
    shifts = rng.integers(90, len(x) - 90, size=n)   # 너무 작은 이동은 제외
    null = np.array([stat(np.roll(x, k)) for k in shifts])
    return {"stat_annual": t0 * 365, "p_value": float((null >= t0).mean()), "null_p95_annual": float(np.quantile(null, 0.95) * 365),
            "n_days": len(x), "range": f"{r.index[0].date()} ~ {r.index[-1].date()}"}


def forecast_check(ct: pd.DataFrame) -> pd.DataFrame:
    """그 전 사이클들의 '반감기→고점', '반감기→저점' 평균으로 다음 사이클을 예측했다면."""
    rows = []
    for k in range(1, len(ct)):
        prev = ct.iloc[:k]
        prev_done = prev[prev["끝난 사이클"] == "예"]
        h = pd.Timestamp(ct.iloc[k]["반감기"])
        pk = prev["반감기→고점(일)"]
        tr = prev_done["반감기→저점(일)"] if len(prev_done) else prev["반감기→저점(일)"]
        act_pk = ct.iloc[k]["반감기→고점(일)"]
        row = {"예측 대상": f"{h.year} 반감기", "쓴 사이클 수": k,
               "예측 고점 시기": f"{(h + pd.Timedelta(days=int(pk.min()))).date()} ~ {(h + pd.Timedelta(days=int(pk.max()))).date()}",
               "실제 고점": ct.iloc[k]["고점일"], "고점 오차(일, 평균 대비)": int(act_pk - pk.mean()),
               "고점이 예측 범위 ±60일 안": "예" if pk.min() - 60 <= act_pk <= pk.max() + 60 else "아니오"}
        if ct.iloc[k]["끝난 사이클"] == "예":
            act_tr = ct.iloc[k]["반감기→저점(일)"]
            row["예측 저점 시기"] = f"{(h + pd.Timedelta(days=int(tr.min()))).date()} ~ {(h + pd.Timedelta(days=int(tr.max()))).date()}"
            row["실제 저점"] = ct.iloc[k]["저점일(고점 이후 최저)"]
            row["저점 오차(일, 평균 대비)"] = int(act_tr - tr.mean())
        else:
            row["예측 저점 시기"] = f"{(h + pd.Timedelta(days=int(tr.min()))).date()} ~ {(h + pd.Timedelta(days=int(tr.max()))).date()}"
            row["실제 저점"] = "아직 모름"
            row["저점 오차(일, 평균 대비)"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def cycle_rule_backtest(s: pd.Series, cfg: dict, end=DEV_END) -> pd.DataFrame:
    """'사이클 달력' 규칙: 반감기 후 0~18개월 보유, 18~30개월 현금, 30개월~다음 반감기 보유. 비용 포함, 2024년까지.

    비교: BTC 그냥 보유, 200일선 위에서만 보유(추세 규칙). 첫 반감기 직후 시작.
    규칙 자체가 과거 사이클을 보고 만든 것이라 2012·2016 사이클 성적은 '표본 안', 2020 사이클이 그나마 '표본 밖'에 가깝습니다.
    """
    s = s.loc[: end]
    idx = s.loc[HALVINGS[0]:].index
    ph = phase_of(idx)
    on_cycle = ~((ph >= 548) & (ph < 913))
    sma = s.rolling(200).mean().reindex(idx)
    on_trend = s.reindex(idx) > sma
    close = s.reindex(idx).to_frame("BTC")
    costs = engine.Costs(cfg["costs"]["fee"], cfg["costs"]["slippage_core"], cfg["costs"]["slippage_other"], ("BTC",))
    out = []
    for name, on in (("BTC 그냥 보유", pd.Series(True, index=idx)), ("사이클 달력 규칙", on_cycle), ("200일선 위에서만 보유", on_trend),
                     ("사이클 달력 + 200일선 둘 다", on_cycle & on_trend)):
        w = on.astype(float).to_frame("BTC")
        res = engine.run(w, close, close, costs, engine.Rules(rebalance_days=1))
        row = {"규칙": name, **{k: v for k, v in metrics.summarize(res.equity).items() if k in ("연복리(CAGR)", "MDD", "샤프", "월하위10%")}}
        for i, h in enumerate(HALVINGS):
            e = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else end
            seg = res.equity.loc[h:e]
            row[f"{h.year} 사이클 수익"] = seg.iloc[-1] / seg.iloc[0] - 1 if len(seg) > 30 else np.nan
        row["평균투자비중"] = float(on.mean())
        out.append(row)
    return pd.DataFrame(out)


def oos_cycle_rule(s: pd.Series, ct: pd.DataFrame, cfg: dict, end=DEV_END) -> pd.DataFrame:
    """표본 밖 사이클 규칙: 각 사이클의 매도일 = 반감기 + (그 전 사이클들의 반감기→고점 일수 최댓값),
    재매수일 = 반감기 + (그 전 사이클들의 반감기→저점 일수 평균). 그 사이클 숫자는 전혀 안 씀. 2020 사이클만 끝까지 평가 가능."""
    rows = []
    costs = cfg["costs"]["fee"] + cfg["costs"]["slippage_core"]
    for k in range(1, len(HALVINGS)):
        h = HALVINGS[k]
        e = HALVINGS[k + 1] if k + 1 < len(HALVINGS) else end
        if e > end:
            e = end
        prev = ct.iloc[:k]
        sell = h + pd.Timedelta(days=int(prev["반감기→고점(일)"].max()))
        buy = h + pd.Timedelta(days=int(prev["반감기→저점(일)"].mean()))
        hold = s.loc[e] / s.loc[h] - 1
        if sell >= e:
            rule = hold
        else:
            rule = (s.loc[sell] / s.loc[h]) * (1 - costs) * ((s.loc[e] / s.loc[buy]) * (1 - costs) if buy < e else 1) - 1
        rows.append({"사이클": f"{h.year} 반감기", "평가 기간": f"{h.date()} ~ {e.date()}", "매도일(예측)": sell.date(),
                     "재매수일(예측)": buy.date(), "규칙 수익": rule, "그냥 보유 수익": hold,
                     "매도일~재매수일 BTC 변화": s.loc[min(buy, e)] / s.loc[min(sell, e)] - 1 if sell < e else np.nan})
    return pd.DataFrame(rows)


def same_phase_compare(s: pd.Series, day: int) -> pd.DataFrame:
    """반감기 후 같은 날짜(day일째)에 각 사이클은 어디였나."""
    rows = []
    for i, h in enumerate(HALVINGS):
        d = h + pd.Timedelta(days=day)
        if d > s.index[-1]:
            continue
        seg = s.loc[h:d]
        peak = seg.max()
        rows.append({"사이클": f"{h.year} 반감기", "날짜": d.date(), "반감기 대비": s.loc[d] / s.loc[h] - 1,
                     "그때까지 고점 대비": s.loc[d] / peak - 1, "고점 이후 경과(일)": (d - seg.idxmax()).days,
                     "200일선 대비": s.loc[d] / s.rolling(200).mean().loc[d] - 1,
                     "이후 1년 수익": (s.loc[d + pd.Timedelta(days=365)] / s.loc[d] - 1) if d + pd.Timedelta(days=365) <= s.index[-1] else np.nan})
    return pd.DataFrame(rows)


def amplitude(ct: pd.DataFrame) -> dict:
    m = ct["직전 저점 대비 배수"].to_numpy(float)
    dd = ct.loc[ct["끝난 사이클"] == "예", "고점 대비 낙폭"].to_numpy(float)
    ratio = m[1:] / m[:-1]
    return {"배수": m, "배수 감소율": ratio, "낙폭": dd}


def svg_chart(s: pd.Series, path: Path) -> None:
    """반감기 기준으로 정렬한 사이클 비교 (가로: 반감기 후 경과, 세로: 반감기 가격 대비 배수, 로그 눈금). 정적 그림이라 숫자는 1·6절 표로."""
    W, H, L, R, T, B = 760, 440, 56, 150, 56, 44
    xs_max = 1460
    series = []
    for i, h in enumerate(HALVINGS):
        e = HALVINGS[i + 1] if i + 1 < len(HALVINGS) else s.index[-1]
        seg = s.loc[h:e]
        series.append((h.year, (seg.index - h).days.to_numpy(), (seg / seg.iloc[0]).to_numpy(), i + 1))
    ymin, ymax = 0.5, max(y.max() for _, _, y, _ in series) * 1.3
    lo, hi = np.log10(ymin), np.log10(ymax)

    def px(x):
        return L + (W - L - R) * x / xs_max

    def py(y):
        return T + (H - T - B) * (1 - (np.log10(max(y, ymin)) - lo) / (hi - lo))

    css = (".viz{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e6e5e0;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100}"
           "@media (prefers-color-scheme:dark){.viz{--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#34332f;"
           "--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}}"
           ".t{fill:var(--ink2)}.h{fill:var(--ink);font-weight:600}.gr{stroke:var(--grid);stroke-width:1}")
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Malgun Gothic,sans-serif" '
         f'font-size="11" class="viz" role="img" aria-label="반감기 기준으로 정렬한 비트코인 사이클 비교">',
         f"<style>{css}</style>", f'<rect width="{W}" height="{H}" fill="var(--surface)"/>',
         f'<text class="h" x="{L}" y="20" font-size="14">반감기 후 BTC 가격 (반감기 날 가격 = 1배, 로그 눈금)</text>']
    # 범례 (2개 이상 시리즈)
    lx = L
    for yr, _, _, k in series:
        g.append(f'<line x1="{lx}" x2="{lx + 16}" y1="38" y2="38" stroke="var(--s{k})" stroke-width="2"/>'
                 f'<text class="t" x="{lx + 21}" y="42">{yr} 반감기</text>')
        lx += 92
    for v in (0.5, 1, 2, 5, 10, 20, 50, 100):
        if ymin <= v <= ymax:
            g.append(f'<line class="gr" x1="{L}" x2="{W - R}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>'
                     f'<text class="t" x="{L - 6}" y="{py(v) + 4:.1f}" text-anchor="end">{v:g}배</text>')
    for d in range(0, xs_max + 1, 365):
        g.append(f'<line class="gr" x1="{px(d):.1f}" x2="{px(d):.1f}" y1="{T}" y2="{H - B}"/>'
                 f'<text class="t" x="{px(d):.1f}" y="{H - B + 16}" text-anchor="middle">{d // 365}년</text>')
    g.append(f'<text class="t" x="{(L + W - R) / 2}" y="{H - 8}" text-anchor="middle">반감기 후 경과</text>')
    labels = []
    for yr, x, y, k in series:
        step = max(len(x) // 500, 1)
        pts = " ".join(f"{px(a):.1f},{py(b):.1f}" for a, b in zip(x[::step], y[::step]))
        g.append(f'<polyline fill="none" stroke="var(--s{k})" stroke-width="2" stroke-linejoin="round" points="{pts}"/>')
        labels.append([py(y[-1]), px(x[-1]), f"{yr}: {y[-1]:.1f}배", k])
    labels.sort()
    for a, b in zip(labels, labels[1:]):  # 이름표 겹침 방지
        if b[0] - a[0] < 14:
            b[0] = a[0] + 14
    for yy, xx, text, k in labels:
        g.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4" fill="var(--s{k})" stroke="var(--surface)" stroke-width="2"/>'
                 f'<text class="t" x="{W - R + 8}" y="{yy + 4:.1f}">{text}</text>')
    g.append("</svg>")
    path.write_text("\n".join(g), encoding="utf-8")


def _md(df: pd.DataFrame, pct=(), num=(), money=()) -> str:
    lines = ["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if isinstance(v, (float, np.floating)) and np.isnan(v):
                cells.append("-")
            elif c in pct:
                cells.append(f"{v * 100:+.0f}%" if abs(v) >= 0.995 else f"{v * 100:+.1f}%")
            elif c in num:
                cells.append(f"{v:,.1f}")
            elif c in money:
                cells.append(f"${v:,.0f}" if v >= 10 else f"${v:,.2f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    s = btc_series(cache)
    today = s.index[-1]
    ct = cycle_table(s)
    pr = phase_returns(s)
    pt = permutation_test(s)
    pt_pre = permutation_test(s, end=HALVINGS[2] - pd.Timedelta(days=1))
    pt_post = permutation_test(s, start=HALVINGS[2])
    oos = oos_cycle_rule(s, ct, cfg)
    fc = forecast_check(ct)
    bt = cycle_rule_backtest(s, cfg)
    day_now = (today - HALVINGS[-1]).days
    sp = same_phase_compare(s, day_now)
    amp = amplitude(ct)
    ath = s.loc[:today].max()
    sma200 = s.rolling(200).mean().iloc[-1]
    docs = config_mod.ROOT / "docs"
    svg = docs / "cycle-aligned.svg"
    svg_chart(s, svg)

    o = []
    w = o.append
    w(f"# 비트코인 4년 주기(반감기 사이클) 검증 · {pd.Timestamp.now(tz='Asia/Seoul').date()}\n")
    w("> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 사이클이 과거에 있었더라도 앞으로 반복된다는 보장은 없고, 원금 손실 가능성이 있습니다.\n")
    w(f"데이터: BTC 달러 일별 종가 {s.index[0].date()} ~ {today.date()} (Coin Metrics 2010~ + 바이낸스, 겹치는 기간 차이 중앙값 0.01%). "
      f"반감기 {', '.join(str(h.date()) for h in HALVINGS)} · 다음 반감기는 {NEXT_HALVING_EST:%Y-%m} 무렵(추정).\n")
    w("**표본이 아주 작다는 점이 가장 큰 한계입니다.** 반감기 뒤 끝난 사이클은 3번(2012·2016·2020), 진행 중 1번(2024)뿐이라 "
      "어떤 검정도 '확실히 있다/없다'를 말하기 어렵습니다. 또 4년 주기는 2013·2017년을 보고 사람들이 만든 이야기라, 그 두 사이클로는 검증이 안 되고 "
      "그 뒤(2020·2024)가 진짜 시험대입니다.\n")
    w('![반감기 기준 정렬](cycle-aligned.svg)\n')
    w("## 1. 사이클마다 고점·저점\n")
    w(_md(ct.drop(columns=["끝난 사이클"]), pct=("고점 대비 낙폭",), num=("반감기 대비 배수", "직전 저점 대비 배수"),
          money=("반감기 가격", "고점", "저점")))
    w(f"\n'저점'은 고점 이후 다음 반감기 전까지(진행 중인 사이클은 오늘까지) 최저가입니다. 2024 사이클은 아직 끝나지 않아 저점이 더 낮아질 수 있습니다.\n")
    w("## 2. 반감기 후 국면별 연환산 수익률 (2024-12-31까지)\n")
    w(_md(pr, pct=[p[0] for p in PHASES]))
    w(f"\n**순열 검정**: 통계량 = (반감기 후 0~18개월 평균 수익) − (18~30개월 평균 수익), 연환산 로그 기준 {pt['stat_annual']:.2f}. "
      f"수익률 흐름을 임의 일수만큼 통째로 밀어서(변동성·추세 성질은 유지, 반감기 달력과의 정렬만 깸) 5,000번 계산한 값보다 큰 경우가 "
      f"**{'0.1% 미만' if pt['p_value'] < 0.001 else format(pt['p_value'], '.1%')}** (p값). 무작위 정렬의 상위 5% 경계는 {pt['null_p95_annual']:.2f}.\n")
    verdict_p = ("무작위 정렬로는 잘 안 나오는 정도(5% 미만)라 '반감기 달력과 강세·약세가 맞아떨어진 것'은 우연만으로 보기 어렵습니다."
                 if pt["p_value"] < 0.05 else
                 "무작위 정렬로도 꽤 나오는 정도라, 이 숫자만으로 사이클이 있다고 말하기 어렵습니다.")
    post_p = "0.1% 미만" if pt_post["p_value"] < 0.001 else f"{pt_post['p_value']:.1%}"
    w(f"- 이론이 퍼지기 전 구간({pt_pre['range']}): p값 {pt_pre['p_value']:.1%} · 이론이 알려진 뒤 구간({pt_post['range']}, "
      f"사실상 2020 사이클 하나): p값 {post_p}\n")
    w(f"→ {verdict_p} 다만 사이클 3번 남짓이라 p값 자체가 불안정하고, '반감기 때문'인지 같은 시기의 다른 원인(예: 2017·2021 세계 유동성 확대, 2020 대규모 돈 풀기) 때문인지는 이 데이터로 구분할 수 없습니다.\n")
    w("## 3. 그 전 사이클만 보고 다음을 예측했다면 (표본 밖 성적)\n")
    w(_md(fc))
    w("\n'예측 고점 시기'는 그 전 사이클들의 반감기→고점 일수 최솟값~최댓값입니다. ±60일 여유를 줬을 때 맞았는지 봤습니다.\n")
    w("### 표본 밖 매매 규칙: 매도·재매수 날짜를 그 전 사이클 숫자로만 정했다면\n")
    w(_md(oos, pct=("규칙 수익", "그냥 보유 수익", "매도일~재매수일 BTC 변화")))
    w("\n매도일 = 반감기 + 그 전 사이클들의 '반감기→고점' 최장 일수, 재매수일 = 반감기 + 그 전 '반감기→저점' 평균 일수. "
      "2024 사이클은 2024-12-31까지만(최종 검증 구간 잠금) 계산해서 아직 매도 전입니다.\n")
    w("## 4. 사이클이 약해지고 있나\n")
    mults = ", ".join(f"{h.year}: {m:,.1f}배" for h, m in zip(HALVINGS, amp["배수"]))
    dds = ", ".join(f"{d * 100:.0f}%" for d in amp["낙폭"])
    w(f"- 직전 저점 → 사이클 고점 상승 배수: {mults}. 다음 사이클 배수 / 이전 배수: "
      + ", ".join(f"{r:.2f}" for r in amp["배수 감소율"]) + " → **매 사이클 상승 폭이 크게 줄고 있습니다.**")
    w(f"- 끝난 사이클의 고점 대비 최대 낙폭: {dds} (2024 사이클은 지금까지 {ct.iloc[-1]['고점 대비 낙폭'] * 100:.0f}%).")
    w(f"- 반감기→고점 일수: {', '.join(str(x) for x in ct['반감기→고점(일)'])}일 → 고점 시기는 오히려 꽤 일정했습니다(1년 반 전후).\n")
    w("## 5. '사이클 달력'을 매매 규칙으로 쓰면 (BTC, 비용 포함, 2012-11 ~ 2024-12)\n")
    w(_md(bt, pct=("연복리(CAGR)", "MDD", "월하위10%", "평균투자비중") + tuple(c for c in bt.columns if c.endswith("사이클 수익")), num=("샤프",)))
    w("\n사이클 달력 규칙 = 반감기 후 18~30개월만 현금, 나머지 보유. 이 규칙은 과거 사이클을 보고 만든 것이라 2012·2016 사이클 성적은 낙관적입니다. "
      "최종 검증 구간(2025~)은 공식 검증용으로 잠가 두어 여기서 계산하지 않았습니다.\n")
    w(f"## 6. 지금은 어디인가 ({today.date()} 기준 · 2025년 이후 가격을 보는 부분)\n")
    w(f"- 2024-04-20 반감기 후 **{day_now}일째 (약 {day_now / 30.4:.0f}개월)**. 과거 국면 구분으로는 '{next(n for n, a, b in PHASES if a <= day_now < b)}' 구간.")
    w(f"- 이번 사이클 고점 {ct.iloc[-1]['고점일']} ${ct.iloc[-1]['고점']:,.0f} (반감기 {ct.iloc[-1]['반감기→고점(일)']}일 후) → 지금 ${s.iloc[-1]:,.0f}, "
      f"고점 대비 {s.iloc[-1] / ath - 1:+.0%}, 고점 이후 최저 ${ct.iloc[-1]['저점']:,.0f} ({ct.iloc[-1]['저점일(고점 이후 최저)']}, {ct.iloc[-1]['고점 대비 낙폭']:+.0%}).")
    w(f"- 200일선 대비 {s.iloc[-1] / sma200 - 1:+.1%} (추세 신호로는 '{'상승' if s.iloc[-1] > sma200 else '하락'}').\n")
    w("반감기 후 같은 날짜에 과거 사이클은 어디였나:\n")
    w(_md(sp, pct=("반감기 대비", "그때까지 고점 대비", "200일선 대비", "이후 1년 수익")))
    w("")
    return o, {"ct": ct, "pt": pt, "pt_pre": pt_pre, "pt_post": pt_post, "oos": oos, "fc": fc, "bt": bt, "sp": sp, "day_now": day_now, "s": s, "ath": ath, "sma200": sma200}


def verdict(r: dict) -> list[str]:
    ct, pt_pre, pt_post, fc, oos = r["ct"], r["pt_pre"], r["pt_post"], r["fc"], r["oos"]
    cur = ct.iloc[-1]
    last_tr_window_end = pd.Timestamp(HALVINGS[-1]) + pd.Timedelta(days=int(ct.iloc[:-1]["반감기→저점(일)"].max()))
    s = r["s"]
    hits = int((fc["고점이 예측 범위 ±60일 안"] == "예").sum())
    o = ["## 7. 종합 판단\n",
         "**질문 1. 4년 주기는 실재했나? → '시기'는 놀랄 만큼 맞아 왔지만, 근거는 사이클 3~4번이 전부입니다.**",
         f"- 반감기 후 18~30개월은 끝난 사이클 3번 모두 연환산 −58%~−83%로 약세였고, 0~18개월에 큰 상승이 몰렸습니다(2절). "
         f"무작위로 정렬했을 때 이런 차이가 나올 확률은 2020년 이전 구간 {pt_pre['p_value']:.1%}, 2020년 이후 구간 0.1% 미만입니다.",
         f"- 이론이 널리 알려진 뒤의 두 사이클(2020·2024)에서 고점 시기를 그 전 사이클만으로 예측했을 때 둘 다 ±60일 안에 들어왔습니다"
         f"(예측 {len(fc)}번 중 {hits}번). 2024 사이클 고점은 반감기 534일 뒤({cur['고점일']})로 과거 525·546일과 거의 같았습니다.",
         "- 하지만 사이클을 매매 규칙으로 바꾸면 표본 밖 성적은 1승 1패였습니다(3절 표): 2016 사이클은 2013년 숫자로 정한 매도일이 너무 일러 "
         "마지막 폭등을 놓쳤고, 2020 사이클은 크게 이겼습니다.",
         "- 반감기가 '원인'인지는 이 데이터로 가릴 수 없습니다. 2013·2017·2021 고점은 세계 유동성 확대기와도 겹치고, "
         "사람들이 4년 주기를 믿고 같은 시기에 사고팔아서 스스로 맞아떨어지는 면도 있을 수 있습니다.\n",
         "**질문 2. 사이클이 사라지고 있나? → 크기는 확실히 줄고 있고, 시기는 아직 유지되고 있습니다.**",
         "- 직전 저점→고점 상승 배수가 539배 → 112배 → 21배 → 7.9배, 반감기 날 가격 대비 고점은 92배 → 30배 → 7.9배 → 1.9배로 매번 크게 줄었습니다.",
         f"- 고점 이후 최대 낙폭도 −85% → −84% → −77% → (이번) −53%로 얕아지는 중입니다. 시장이 커지고, 현물 ETF·기관 자금이 들어오며 "
         "변동이 줄어드는 흐름과 맞습니다.",
         "- 정리하면 '사이클이 없다'고 하기엔 시기가 너무 잘 맞았고, '예전 같은 사이클'이라 하기엔 크기가 너무 작아졌습니다. "
         "**사이클이 주는 기대 수익은 줄고, 사이클을 믿고 크게 거는 위험은 그대로**라고 보는 게 데이터에 가장 가깝습니다.\n",
         f"**질문 3. 지금은 어느 국면인가? ({s.index[-1].date()} 기준)**",
         f"- 달력상으로는 반감기 {r['day_now']}일째(약 30개월), 과거 사이클에서 '바닥을 지나 회복이 시작되던' 구간입니다. 과거 바닥은 반감기 777·889·912일째였고, "
         f"이번 사이클의 고점 후 최저는 801일째({cur['저점일(고점 이후 최저)']}, ${cur['저점']:,.0f}, 고점 대비 {cur['고점 대비 낙폭']:.0%})로 예측 범위 안에 들어왔습니다.",
         f"- 가격으로도 지금은 그 저점보다 {s.iloc[-1] / cur['저점'] - 1:+.0%} 위, 200일선보다 {s.iloc[-1] / r['sma200'] - 1:+.0%} 위라 추세 신호도 '상승'입니다.",
         f"- 그래서 **'4년 주기가 이번에도 맞았다면 지금은 약세장 바닥(2026-06)을 지나 회복 초기'** 라는 해석이 가능합니다. 과거 같은 시점(반감기 30개월) 이후 1년 수익은 "
         "+127%, +102%, +113%였지만 사례가 3개뿐이고, 진폭이 매번 줄어 온 걸 보면 그 크기를 기대하긴 어렵습니다.",
         f"- 반대 시나리오: 이번 하락(−53%)이 과거(−77%~−85%)보다 훨씬 얕았기 때문에, 아직 바닥이 아니거나 사이클 자체가 흐려졌을 수 있습니다. "
         f"과거 기준 저점 예측 구간은 {last_tr_window_end.date()}에 끝났으므로, **앞으로 2026-06-30 저점(${cur['저점']:,.0f})을 깨면 "
         "이번 사이클은 '시기'마저 어긋난 것**으로 봐야 합니다. 이건 앞으로 확인할 수 있는 분명한 기준입니다.\n",
         "**공식에 넣을까? (제안)**",
         "- 사이클 '달력'을 매매 규칙으로 바로 쓰는 건 권하지 않습니다. 표본 밖 1승 1패이고, 사이클 3~4번에 맞춘 규칙은 우연에 기대는 정도가 큽니다.",
         "- 대신 리포트의 '시장 상태'에 **사이클 위치(반감기 후 경과일, 과거 같은 시점 비교, 저점 기준선)를 참고 정보로** 보여주고, "
         "매매 판단은 검증된 추세 신호(200일선 등)에 맡기는 쪽을 제안합니다. 추세 신호는 사이클이 맞으면 같이 따라가고, 어긋나면 알아서 빠져나옵니다.",
         ]
    return o


def write(cfg: dict | None = None, log=print) -> Path:
    o, r = main(cfg)
    o += verdict(r)
    path = config_mod.ROOT / "docs" / f"cycle-analysis-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text("\n".join(o) + "\n", encoding="utf-8")
    log(f"완료: {path}")
    return path
