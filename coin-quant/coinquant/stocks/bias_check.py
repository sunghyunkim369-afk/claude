"""주식(지수) 공식 편향 점검 — `python -m coinquant stocks-bias` (주식 시세 사이트가 막힌 곳에선 GitHub Actions 에서)

질문: 지수 공식의 '근거 있음'(나스닥·코스피)이 고른 시장·빠진 배당·세금·시작 시점 같은 편향 때문은 아닌가?
공식·숫자는 그대로(코인에서 만든 확정 공식, 주식에 맞춰 바꾸지 않음). 판단 종가 → 다음 거래일 시가 체결, 비용 포함.

1. 선택 편향: 나스닥·코스피만이 아니라 yfinance 에 기록이 긴 세계 대표 지수 전부 (성과를 보고 고르지 않음, 잃어버린 30년 닛케이·상하이 포함)
2. 배당·현금 이자: 배당 포함 ETF(SPY·QQQ·DIA·KODEX 200)로 보유 쪽에 배당을 넣고, 공식이 현금일 땐 단기 금리(미 3개월 국채, 한국 3개월 금리)
3. 세금: 국내 상장 해외 ETF(TIGER 미국나스닥100 등)는 팔 때마다 차익의 15.4% → 공식은 거래마다, 보유는 마지막에 한 번
4. 시작 시점: 10년 창을 1년씩 옮겨 가며
5. 환율: 원화로 본 미국 지수 (원/달러 FRED DEXKOUS)
6. 하루 늦은 체결
7. 잠가 뒀던 2025-01 ~ 현재 (한 번 열기)
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .. import config as config_mod
from . import data as sd, validate as V

WORLD = [  # (기호, 이름, 비용 시장) — 규칙: 나라·지역 대표 지수 중 yfinance 일봉이 20년 이상인 것 전부 시도
    ("^GSPC", "미국 S&P 500", "US"), ("^IXIC", "미국 나스닥", "US"), ("^DJI", "미국 다우", "US"), ("^NYA", "미국 NYSE 종합", "US"),
    ("^RUT", "미국 러셀 2000", "US"), ("^GSPTSE", "캐나다 TSX", "US"), ("^BVSP", "브라질 보베스파", "US"), ("^MXX", "멕시코 IPC", "US"),
    ("^FTSE", "영국 FTSE 100", "US"), ("^GDAXI", "독일 DAX", "US"), ("^FCHI", "프랑스 CAC 40", "US"), ("^AEX", "네덜란드 AEX", "US"),
    ("^SSMI", "스위스 SMI", "US"), ("^IBEX", "스페인 IBEX", "US"), ("^STOXX50E", "유로 스톡스 50", "US"),
    ("^N225", "일본 닛케이", "US"), ("^HSI", "홍콩 항셍", "US"), ("000001.SS", "중국 상하이", "US"), ("^TWII", "대만 가권", "US"),
    ("^KS11", "한국 코스피", "KR"), ("^KQ11", "한국 코스닥", "KR"), ("^BSESN", "인도 센섹스", "US"), ("^JKSE", "인도네시아", "US"),
    ("^STI", "싱가포르 STI", "US"), ("^AXJO", "호주 ASX 200", "US"), ("^NZ50", "뉴질랜드 NZX 50", "US"),
]
SIGNAL = {"^IXIC": "나스닥", "^KS11": "코스피", "^GSPC": "S&P 500", "^KQ11": "코스닥"}
TR_ETF = [("SPY", "SPY (S&P 500, 배당 포함)", "US", "DTB3"), ("QQQ", "QQQ (나스닥 100, 배당 포함)", "US", "DTB3"),
          ("DIA", "DIA (다우, 배당 포함)", "US", "DTB3"), ("069500.KS", "KODEX 200 (코스피 200, 배당 포함)", "KR", "KR3M")]
FRED = {"DTB3": "DTB3", "KR3M": "IR3TIB01KRM156N", "KRW": "DEXKOUS"}
HOLD_START = "2025-01-01"
TAX = 0.154


def fred(series: str) -> pd.Series:
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "v"]
    df["date"] = pd.to_datetime(df["date"])
    return pd.to_numeric(df.set_index("date")["v"], errors="coerce").dropna()


def series(F: dict, tk: str, end: str | None = None):
    c, o, h, l_ = (F[k][[tk]].loc[:end].dropna(how="all") for k in ("close", "open", "high", "low"))
    c = c.dropna()
    o, h, l_ = o.reindex(c.index), h.reindex(c.index), l_.reindex(c.index)
    h, l_ = h.where(h > 0).fillna(c), l_.where(l_ > 0).fillna(c)
    return c, o, h, l_


def run_one(F: dict, tk: str, mkt: str, end: str | None = None, delay: int = 0, rf: pd.Series | None = None):
    """(공식 일수익, 보유 일수익, 비중) — 지표 계산은 처음부터, 성적은 데이터 처음 3년 뒤부터 (지표 준비 기간)."""
    c, o, h, l_ = series(F, tk, end)
    if len(c) < V.TD * 8:
        return None
    pos, _ = V.formula_position(c, h, l_)
    if delay:
        pos = pos.shift(delay).fillna(0.0)
    s, b = V.strat_returns(pos, o, c, V.COST[mkt])
    s, b, p = s[tk], b[tk], pos[tk]
    if rf is not None:   # 현금일 때 단기 금리 (연 % → 거래일)
        r_d = (rf.reindex(s.index, method="ffill") / 100 / V.TD).fillna(0)
        s = s + (1 - p.shift(1).fillna(0)) * r_d
    start = c.index[0] + pd.Timedelta(days=365 * 3)
    return s.loc[start:], b.loc[start:], p.loc[start:]


def after_tax(s: pd.Series, p: pd.Series, b: pd.Series) -> tuple[float, float]:
    """국내 상장 해외 ETF 세금 근사: 공식은 거래가 끝날 때 그 거래 차익(+)의 15.4%, 보유는 마지막에 한 번."""
    held = p.shift(1).fillna(0).to_numpy()
    r = s.fillna(0).to_numpy()
    eq, seg = 1.0, 1.0
    for i in range(len(r)):
        if held[i] > 0:
            seg *= 1 + r[i]
        else:
            eq *= 1 + r[i]
        ending = held[i] > 0 and (i == len(r) - 1 or held[i + 1] == 0)
        if ending:
            gain = seg - 1
            eq *= seg - (TAX * gain if gain > 0 else 0)
            seg = 1.0
    yrs = len(r) / V.TD
    vb = float((1 + b.fillna(0)).prod())
    vb_after = vb - TAX * max(vb - 1, 0)
    return eq ** (1 / yrs) - 1, vb_after ** (1 / yrs) - 1


def rolling(s: pd.Series, b: pd.Series, years: int = 10) -> dict:
    out = []
    y0, y1 = s.index[0].year + 1, s.index[-1].year - years + 1
    for y in range(y0, y1 + 1):
        a, z = f"{y}-01-01", f"{y + years - 1}-12-31"
        ss, bb = V.stats(s.loc[a:z]), V.stats(b.loc[a:z])
        if ss and bb:
            out.append((ss["샤프"] > bb["샤프"], ss["MDD"] > bb["MDD"], ss["연복리"] - bb["연복리"]))
    if not out:
        return {}
    arr = np.array(out, dtype=float)
    return {"n": len(out), "sharpe_win": arr[:, 0].mean(), "mdd_win": arr[:, 1].mean(), "cagr_diff_med": float(np.median(arr[:, 2])),
            "cagr_diff_min": float(arr[:, 2].min())}


def main(cfg: dict | None = None, log=print) -> Path:
    t0 = time.time()
    cfg = cfg or config_mod.load()
    d = config_mod.cache_dir(cfg) / "stocks"
    F = sd.update(d, "world", [t for t, _, _ in WORLD], start="1927-01-01", log=log)
    E = sd.update(d, "tr_etf", [t for t, _, _, _ in TR_ETF], start="1990-01-01", log=log)
    rates = {}
    for k, sid in FRED.items():
        try:
            rates[k] = fred(sid)
            log(f"  FRED {sid}: {len(rates[k])}개 {rates[k].index[0]:%Y}~{rates[k].index[-1]:%Y-%m}")
        except Exception as e:
            log(f"  FRED {sid} 실패: {type(e).__name__}")
    log(f"  데이터 {time.time() - t0:.0f}초")

    # 1. 세계 지수 (개발 구간 ~2024) + 6. 하루 늦게 + 4. 10년 창 + 7. 2025~
    world = []
    for tk, nm, mkt in WORLD:
        if tk not in F["close"].columns:
            log(f"  {nm}: 데이터 없음")
            continue
        res = run_one(F, tk, mkt, end=V.DEV_END)
        if res is None:
            log(f"  {nm}: 기록 짧음")
            continue
        s, b, p = res
        dl = run_one(F, tk, mkt, end=V.DEV_END, delay=1)
        full = run_one(F, tk, mkt)
        a, hh = V.stats(s), V.stats(b)
        ho = (V.stats(full[0].loc[HOLD_START:]), V.stats(full[1].loc[HOLD_START:])) if full else ({}, {})
        world.append({"tk": tk, "name": nm, "from": s.index[0].year, "a": a, "h": hh, "dl": V.stats(dl[0]) if dl else {},
                      "expo": float(p.mean()), "roll": rolling(s, b), "ho": ho})
        log(f"  {nm} ({time.time() - t0:.0f}초)")

    # 2·3. 배당 포함 ETF + 현금 금리 + 세금
    tr = []
    for tk, nm, mkt, rk in TR_ETF:
        if tk not in E["close"].columns:
            continue
        rf = rates.get(rk)
        r0 = run_one(E, tk, mkt, end=V.DEV_END)
        r1 = run_one(E, tk, mkt, end=V.DEV_END, rf=rf) if rf is not None else None
        if r0 is None:
            continue
        s0, b, p = r0
        row = {"name": nm, "from": s0.index[0].year, "no_cash": V.stats(s0), "h": V.stats(b), "expo": float(p.mean())}
        if r1 is not None:
            row["cash"] = V.stats(r1[0])
            if mkt == "US":
                row["tax"] = after_tax(r1[0], p, b)
        full = run_one(E, tk, mkt, rf=rf)
        if full:
            row["ho"] = (V.stats(full[0].loc[HOLD_START:]), V.stats(full[1].loc[HOLD_START:]))
        tr.append(row)
        log(f"  {nm} ({time.time() - t0:.0f}초)")

    # 5. 환율: 원화로 본 미국 지수 (공식 현금 = 원화 현금, 이자 0)
    fx_rows = []
    if "KRW" in rates:
        fx = rates["KRW"]
        for tk in ("^IXIC", "^GSPC"):
            res = run_one(F, tk, "US", end=V.DEV_END)
            if res is None:
                continue
            s, b, p = res
            f = fx.reindex(s.index, method="ffill")
            fr = (f / f.shift(1) - 1).fillna(0)
            held = p.shift(1).fillna(0)
            s_k = (1 + s) * (1 + held * fr) - 1
            b_k = (1 + b) * (1 + fr) - 1
            st = max(s.index[0], fx.index[0] + pd.Timedelta(days=30))
            fx_rows.append({"name": SIGNAL[tk], "from": st.year, "usd": (V.stats(s.loc[st:]), V.stats(b.loc[st:])),
                            "krw": (V.stats(s_k.loc[st:]), V.stats(b_k.loc[st:]))})
    path = render(world, tr, fx_rows, time.time() - t0, F["close"].index[-1])
    log(f"  → {path}")
    return path


def render(world, tr, fx_rows, secs, last) -> Path:
    P = lambda x, d=1: "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{d}f}%"  # noqa: E731
    F2 = lambda x: "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.2f}"  # noqa: E731
    today = pd.Timestamp.now(tz="Asia/Seoul").date()
    L = [f"# 주식 지수 공식 — 편향 점검 ({today})", "",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.", "",
         "## 0. 조건", "",
         "- 공식: 코인에서 만든 확정 공식 그대로(중기 추세 보유 + 장기 하락이면 중단). 주식에 맞춰 숫자를 바꾼 적 없음.",
         "- 판단은 그날 종가, 체결은 다음 거래일 시가. 비용 미국·기타 0.12%, 한국 0.215% (매매마다). 성적은 데이터 시작 3년 뒤부터(지표 준비).",
         f"- 개발 구간 ~2024-12. 7절만 잠가 뒀던 2025-01 ~ {last:%Y-%m-%d}.",
         f"- 계산 {secs / 60:.1f}분.", "",
         "## 1. 선택 편향 — 세계 대표 지수 전부 (개발 구간)", "",
         "| 지수 | 시작 | 공식 연복리 | 공식 MDD | 공식 샤프 | 보유 연복리 | 보유 MDD | 보유 샤프 | 샤프 나음 | MDD 얕음 | 투자 비중 | 하루 늦게 샤프 | 10년 창: 샤프 이긴 비율 | 10년 창: MDD 얕은 비율 | 10년 창: 연복리 차 중앙 (최악) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    sw, mw, cd, n = 0, 0, [], 0
    for w in world:
        a, h, r = w["a"], w["h"], w["roll"]
        if not a or not h:
            continue
        n += 1
        s_ok, m_ok = a["샤프"] > h["샤프"], a["MDD"] > h["MDD"]
        sw += s_ok
        mw += m_ok
        cd.append(a["연복리"] - h["연복리"])
        L.append(f"| {w['name']} | {w['from']} | {P(a['연복리'])} | {P(a['MDD'], 0)} | {F2(a['샤프'])} | {P(h['연복리'])} | {P(h['MDD'], 0)} | {F2(h['샤프'])} | "
                 f"{'○' if s_ok else '×'} | {'○' if m_ok else '×'} | {w['expo']:.0%} | {F2(w['dl'].get('샤프'))} | "
                 + (f"{r['sharpe_win']:.0%} | {r['mdd_win']:.0%} | {P(r['cagr_diff_med'])} ({P(r['cagr_diff_min'])})" if r else "- | - | -") + " |")
    L += ["", f"**요약: {n}개 지수 중 공식 샤프가 보유보다 높은 곳 {sw}개 ({sw / max(n, 1):.0%}), MDD가 얕은 곳 {mw}개 ({mw / max(n, 1):.0%}), "
          f"연복리 차(공식 − 보유) 중앙값 {P(float(np.median(cd)) if cd else np.nan)}.**", "",
          "## 2·3. 배당·현금 이자·세금 (배당 포함 ETF, 개발 구간)", "",
          "- 지수 가격엔 배당이 빠져 '보유'가 불리하고, 공식이 현금일 때 이자가 빠져 '공식'이 불리했습니다. 둘 다 넣은 비교입니다.",
          "- 세금: 국내 상장 해외 ETF 기준(팔 때 차익의 15.4%, 손실 상계 없음으로 근사). 공식은 거래마다 내고, 보유는 마지막에 한 번 냅니다. "
          "국내 주식형 ETF(KODEX 200)는 매매 차익 비과세라 세금 열이 없습니다. 해외 직접 투자(QQQ 직접)는 연 250만원 공제 후 22%로 다릅니다.", "",
          "| ETF | 시작 | 공식(현금 이자 없음) 연복리 / 샤프 | 공식(현금 이자 포함) 연복리 / MDD / 샤프 | 보유(배당 포함) 연복리 / MDD / 샤프 | 투자 비중 | 세후 연복리 공식 / 보유 | 2025~ 공식 / 보유 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in tr:
        nc, h = r["no_cash"], r["h"]
        ca = r.get("cash", {})
        tx = r.get("tax")
        ho = r.get("ho", ({}, {}))
        L.append(f"| {r['name']} | {r['from']} | {P(nc.get('연복리'))} / {F2(nc.get('샤프'))} | {P(ca.get('연복리'))} / {P(ca.get('MDD'), 0)} / {F2(ca.get('샤프'))} | "
                 f"{P(h.get('연복리'))} / {P(h.get('MDD'), 0)} / {F2(h.get('샤프'))} | {r['expo']:.0%} | "
                 + (f"{P(tx[0])} / {P(tx[1])}" if tx else "비과세") + f" | {P(ho[0].get('총수익'))} / {P(ho[1].get('총수익'))} |")
    L += ["", "## 5. 환율 — 원화로 본 미국 지수 (개발 구간, 현금은 원화·이자 없음)", "",
          "| 지수 | 시작 | 달러: 공식 연복리 / 샤프 | 달러: 보유 연복리 / 샤프 | 원화: 공식 연복리 / MDD / 샤프 | 원화: 보유 연복리 / MDD / 샤프 |", "|---|---|---|---|---|---|"]
    for r in fx_rows:
        (ua, ub), (ka, kb) = r["usd"], r["krw"]
        L.append(f"| {r['name']} | {r['from']} | {P(ua.get('연복리'))} / {F2(ua.get('샤프'))} | {P(ub.get('연복리'))} / {F2(ub.get('샤프'))} | "
                 f"{P(ka.get('연복리'))} / {P(ka.get('MDD'), 0)} / {F2(ka.get('샤프'))} | {P(kb.get('연복리'))} / {P(kb.get('MDD'), 0)} / {F2(kb.get('샤프'))} |")
    L += ["", f"## 7. 잠가 둔 구간 2025-01 ~ {last:%Y-%m} (한 번 열기)", "",
          "| 지수 | 공식 총수익 | 공식 MDD | 보유 총수익 | 보유 MDD |", "|---|---|---|---|---|"]
    for w in world:
        a, h = w["ho"]
        if a and h:
            mark = " ←신호 지수" if w["tk"] in SIGNAL else ""
            L.append(f"| {w['name']}{mark} | {P(a['총수익'])} | {P(a['MDD'], 0)} | {P(h['총수익'])} | {P(h['MDD'], 0)} |")
    L += ["", "## 8. 결론", "", "(실행 결과를 보고 사람이 씀)", ""]
    p = Path(config_mod.ROOT) / "docs" / f"stocks-bias-{today}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    return p
