"""주식에서 확정 공식(추세: 중기 보유 + 장기 하락이면 중단) 검증 — `python -m coinquant stocks-validate`

원칙 (코인과 같음): 그날 종가로 판단 → 다음 거래일 시가 체결, 비용 포함, 2025-01-01 이후는 잠금(보지 않음).
공식·파라미터는 코인 그대로 (기간은 '거래일 봉' 개수). 주식에 맞춰 바꾸지 않음.
대상
  1) 지수: S&P500(1950~), 나스닥(1972~), 다우, 코스피(1997~), 코스닥, 닛케이(참고: 공식을 만든 적 없는 시장)
  2) 종목: 미국 S&P500 현재 구성, 한국 시가총액 상위 200 (현재 기준) — ⚠ 생존 편향: 지금 남아 있는 '살아남은' 종목만이라
     절대 수익은 부풀려짐. 같은 종목의 '그냥 보유'와 비교하는 상대 비교 위주로 봄.
비용(매매마다): 미국 0.12% (수수료 0.07% + 슬리피지 0.05%, 환전 비용 제외), 한국 0.215% (왕복 0.43% = 수수료·거래세 0.20%·슬리피지).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..backtest import signals as sg
from ..trend import MultiTrend
from . import data as sd, universe as su

DEV_END = "2024-12-31"
TD = 252
COST = {"US": 0.0012, "KR": 0.00215}
INDEX_START = {"^GSPC": "1950-01-01", "^IXIC": "1972-01-01", "^DJI": "1993-01-01", "^KS11": "1997-01-01",
               "^KQ11": "1997-01-01", "^N225": "1966-01-01"}
INDEX_NAME = {"^GSPC": "S&P 500", "^IXIC": "나스닥 종합", "^DJI": "다우", "^KS11": "코스피", "^KQ11": "코스닥", "^N225": "닛케이(참고)"}
INDEX_MKT = {"^GSPC": "US", "^IXIC": "US", "^DJI": "US", "^KS11": "KR", "^KQ11": "KR", "^N225": "US"}
ERAS = {
    "US": [("1973~74 오일쇼크 약세", "1973-01-01", "1974-12-31"), ("1987 블랙먼데이 전후", "1987-01-01", "1988-12-31"),
           ("1990년대 강세", "1990-01-01", "1999-12-31"), ("2000~02 닷컴 붕괴", "2000-01-01", "2002-12-31"),
           ("2003~07 회복", "2003-01-01", "2007-10-09"), ("2007-10~09-03 금융위기", "2007-10-09", "2009-03-09"),
           ("2009~19 장기 강세", "2009-03-10", "2019-12-31"), ("2020 코로나", "2020-01-01", "2020-12-31"),
           ("2022 금리인상 약세", "2022-01-01", "2022-12-31"), ("2023~24", "2023-01-01", "2024-12-31")],
    "KR": [("1997~98 외환위기", "1997-01-01", "1998-12-31"), ("1999~2001 닷컴", "1999-01-01", "2001-12-31"),
           ("2003~07 강세", "2003-01-01", "2007-10-31"), ("2008 금융위기", "2007-11-01", "2009-03-31"),
           ("2011~16 박스피", "2011-05-01", "2016-12-31"), ("2018 미중 무역분쟁", "2018-01-01", "2018-12-31"),
           ("2020 코로나", "2020-01-01", "2020-12-31"), ("2021-07~22 하락", "2021-07-01", "2022-12-31"),
           ("2023~24", "2023-01-01", "2024-12-31")],
}


# ---------------------------------------------------------------- 신호·수익 (벡터 계산)
def formula_position(c: pd.DataFrame, h: pd.DataFrame, l_: pd.DataFrame) -> tuple[pd.DataFrame, MultiTrend]:
    mt = MultiTrend(c, h, l_)
    M, L = mt.h["중기"], mt.h["장기"]
    return (M.state & ~(L.score <= -2)).astype(float), mt


def strat_returns(pos: pd.DataFrame, o: pd.DataFrame, c: pd.DataFrame, cost: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """t일 종가 판단 → t+1 시가 체결. 하루 수익 = (전날 정한 비중) × 시가→다음 시가 수익 − 비용 × 비중 변화.
    마지막 날은 시가→종가. 반환: (공식 일수익, 그냥 보유 일수익) — 날짜는 체결일 기준."""
    o = o.where(o > 0).fillna(c.shift(1))          # 옛 지수는 시가가 없거나 0 → 전날 종가
    nxt = o.shift(-1).fillna(c)                    # 다음 시가 (마지막 날은 그날 종가)
    r = nxt / o - 1
    held = pos.shift(1).fillna(0.0)                # 어제 종가 신호 → 오늘 시가부터 보유
    turn = held.diff().abs().fillna(held.abs())
    alive = c.notna() & o.notna()
    strat = (held * r - cost * turn).where(alive)
    hold = r.where(alive)
    return strat, hold


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 60:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / TD
    return {"연복리": eq.iloc[-1] ** (1 / yrs) - 1, "MDD": (eq / eq.cummax() - 1).min(),
            "샤프": r.mean() / r.std() * np.sqrt(TD) if r.std() > 0 else np.nan, "총수익": eq.iloc[-1] - 1}


def trades(pos: pd.Series, o: pd.Series, c: pd.Series, cost: float) -> pd.DataFrame:
    """거래 단위: 신호 켜진 다음 시가 매수 → 꺼진 다음 시가 매도 (비용 포함)."""
    p = pos.fillna(0).astype(bool)
    o = o.where(o > 0).fillna(c.shift(1))
    on = p & ~p.shift(1, fill_value=False)
    off = ~p & p.shift(1, fill_value=False)
    ent = [o.index.get_loc(t) + 1 for t in p.index[on]]
    ex = [o.index.get_loc(t) + 1 for t in p.index[off]]
    rows = []
    for e in ent:
        if e >= len(o):
            continue
        x = next((k for k in ex if k > e), None)
        px_in = o.iloc[e]
        px_out = o.iloc[x] if x is not None and x < len(o) else c.iloc[-1]
        if np.isnan(px_in) or np.isnan(px_out):
            continue
        rows.append({"entry": o.index[e], "exit": o.index[min(x, len(o) - 1)] if x is not None else c.index[-1],
                     "ret": px_out / px_in * (1 - cost) ** 2 - 1, "days": (x if x is not None else len(o)) - e, "open": x is None})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 지수
def index_study(F: dict, log=print) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows, eras, plc = [], [], []
    for tk, start in INDEX_START.items():
        if tk not in F["close"].columns:
            continue
        sl = slice(None, DEV_END)
        c, o, h, l_ = (F[k][[tk]].loc[sl].dropna(how="all") for k in ("close", "open", "high", "low"))
        o, h, l_ = o.reindex(c.index), h.reindex(c.index), l_.reindex(c.index)
        h, l_ = h.where(h > 0).fillna(c), l_.where(l_ > 0).fillna(c)
        pos, _ = formula_position(c, h, l_)
        cost = COST[INDEX_MKT[tk]]
        s, b = strat_returns(pos, o, c, cost)
        s, b, pos = s[tk].loc[start:], b[tk].loc[start:], pos[tk].loc[start:]
        tr = trades(pos, o[tk].loc[start:], c[tk].loc[start:], cost)
        a, hh = stats(s), stats(b)
        # 위약: 같은 보유 비율·보유 기간의 무작위 타이밍 200번
        rs = []
        for seed in range(200):
            rp = sg.random_timing(pos.astype(bool), seed).astype(float).to_frame(tk)
            rr, _ = strat_returns(rp, o[[tk]].loc[start:], c[[tk]].loc[start:], cost)
            rs.append(stats(rr[tk]).get("샤프", np.nan))
        rs = np.array(rs)
        rows.append({"지수": INDEX_NAME[tk], "시작": start[:4], "공식 연복리": a["연복리"], "공식 MDD": a["MDD"], "공식 샤프": a["샤프"],
                     "보유 연복리": hh["연복리"], "보유 MDD": hh["MDD"], "보유 샤프": hh["샤프"], "투자 비중": pos.mean(),
                     "거래수": len(tr), "거래 승률": (tr["ret"] > 0).mean() if len(tr) else np.nan,
                     "거래 중앙값": tr["ret"].median() if len(tr) else np.nan,
                     "무작위 타이밍 대비 샤프 백분위": (rs < a["샤프"]).mean()})
        for en, x, z in ERAS[INDEX_MKT[tk]] if tk != "^N225" else []:
            ss, bb = stats(s.loc[x:z]), stats(b.loc[x:z])
            if ss:
                eras.append({"지수": INDEX_NAME[tk], "시대": en, "공식 총수익": ss["총수익"], "공식 MDD": ss["MDD"],
                             "보유 총수익": bb["총수익"], "보유 MDD": bb["MDD"]})
        log(f"  지수 {INDEX_NAME[tk]} 완료")
    return pd.DataFrame(rows), pd.DataFrame(eras), pd.DataFrame(plc)


# ---------------------------------------------------------------- 종목
def stock_study(F: dict, mkt: str, idx_close: pd.Series, start: str = "2003-01-01", log=print) -> dict:
    sl = slice(None, DEV_END)
    c = F["close"].loc[sl]
    o, h, l_ = (F[k].loc[sl].reindex_like(c) for k in ("open", "high", "low"))
    h, l_ = h.where(h > 0).fillna(c), l_.where(l_ > 0).fillna(c)
    c = c.dropna(axis=1, thresh=400)
    o, h, l_ = o[c.columns], h[c.columns], l_[c.columns]
    pos, mt = formula_position(c, h, l_)
    cost = COST[mkt]
    s, b = strat_returns(pos, o, c, cost)
    s, b, pos = s.loc[start:], b.loc[start:], pos.loc[start:].where(c.loc[start:].notna())
    # 종목별 비교
    per = []
    for a in c.columns:
        x, y = stats(s[a]), stats(b[a])
        if not x or not y:
            continue
        per.append({"종목": a, **{f"공식 {k}": v for k, v in x.items()}, **{f"보유 {k}": v for k, v in y.items()},
                    "투자 비중": pos[a].mean()})
    per = pd.DataFrame(per)
    # 무작위 타이밍 (종목마다 10번) → 공식 샤프가 무작위 중앙값보다 높은 종목 비율
    beat = []
    for a in per["종목"]:
        p_ = pos[a].dropna()
        rs = []
        for seed in range(10):
            rp = sg.random_timing(p_.astype(bool), seed).astype(float).reindex(c.index).fillna(0).to_frame(a)
            rr, _ = strat_returns(rp, o[[a]], c[[a]], cost)
            rs.append(stats(rr[a].loc[start:]).get("샤프", np.nan))
        beat.append(per.loc[per["종목"] == a, "공식 샤프"].iloc[0] > np.nanmedian(rs))
    per["무작위보다 샤프 높음"] = beat
    # 균등 포트폴리오: 살아 있는 종목 1/N 씩 (공식은 꺼진 몫 현금)
    n = b.notna().sum(axis=1).replace(0, np.nan)
    port = pd.DataFrame({"공식": s.fillna(0).sum(axis=1) / n, "그냥 보유": b.fillna(0).sum(axis=1) / n}).dropna()
    # 진입 직후 수익: 신호가 새로 켜진 날 → 다음 시가 매수, 20·60거래일 뒤 종가
    base = pos.fillna(0).astype(bool)
    fresh = base & ~base.shift(1, fill_value=False)
    ix = idx_close.reindex(c.index).ffill()
    regime_up = (ix > ix.rolling(200).mean())
    fwd = []
    for hzn in (20, 60):
        f = (c.shift(-hzn) / o.shift(-1) - 1).loc[start:]
        fi = (ix.shift(-hzn) / ix - 1).loc[start:]
        ex = f.sub(fi, axis=0)
        allr = f.where(c.loc[start:].notna())
        fr = fresh.loc[start:]
        up = pd.DataFrame(np.repeat(regime_up.loc[start:].to_numpy()[:, None], fr.shape[1], axis=1), index=fr.index, columns=fr.columns)
        for nm, m in (("모든 날 (조건 없음)", c.loc[start:].notna()), ("신호가 새로 켜진 날", fr),
                      ("새로 켜짐 & 지수 200일선 위", fr & up), ("새로 켜짐 & 지수 200일선 아래", fr & ~up)):
            v = allr.where(m).stack().dropna()
            e = ex.where(m).stack().dropna()
            fwd.append({"기간(거래일)": hzn, "조건": nm, "사례": len(v), "중앙값": v.median(), "하위10%": v.quantile(0.1),
                        "오른 비율": (v > 0).mean(), "지수 대비 초과 중앙값": e.median(), "지수보다 나은 비율": (e > 0).mean()})
    # 시대별 (균등 포트폴리오)
    era = []
    for en, x, z in ERAS[mkt]:
        if pd.Timestamp(z) < pd.Timestamp(start):
            continue
        p1, p2 = stats(port["공식"].loc[x:z]), stats(port["그냥 보유"].loc[x:z])
        if p1:
            era.append({"시대": en, "공식 총수익": p1["총수익"], "공식 MDD": p1["MDD"], "보유 총수익": p2["총수익"], "보유 MDD": p2["MDD"]})
    tr_all = []
    for a in c.columns:
        t = trades(pos[a].fillna(0), o[a].loc[start:], c[a].loc[start:], cost)
        if len(t):
            tr_all.append(t.assign(종목=a))
    tr_all = pd.concat(tr_all) if tr_all else pd.DataFrame()
    log(f"  {mkt} 종목 {len(per)}개 완료")
    return {"per": per, "port": port, "fwd": pd.DataFrame(fwd), "era": pd.DataFrame(era), "trades": tr_all}


# ---------------------------------------------------------------- 실행·보고
def fetch_all(cache: Path, log=print) -> dict:
    d = cache / "stocks"
    idx = sd.update(d, "index", list(INDEX_START), start="1927-01-01", log=log)
    us_u = su.us_universe()
    us = sd.update(d, "us", list(us_u["ticker"]), start="2000-01-01", log=log)
    kr_u = su.kr_universe(200)
    kr = sd.update(d, "kr", list(kr_u["ticker"]), start="2000-01-01", log=log)
    return {"index": idx, "us": us, "kr": kr, "us_u": us_u, "kr_u": kr_u}


def fmt(df: pd.DataFrame, pct: set) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, (bool, np.bool_)):
                cells.append("예" if v else "아니오")
            elif c in pct and isinstance(v, (float, np.floating)):
                cells.append("-" if pd.isna(v) else f"{v * 100:+.1f}%")
            elif isinstance(v, (float, np.floating)):
                cells.append("-" if pd.isna(v) else (f"{v:.2f}" if abs(v) < 100 else f"{v:,.0f}"))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


PCT = {"공식 연복리", "공식 MDD", "보유 연복리", "보유 MDD", "투자 비중", "거래 승률", "거래 중앙값", "무작위 타이밍 대비 샤프 백분위",
       "공식 총수익", "보유 총수익", "중앙값", "하위10%", "오른 비율", "지수 대비 초과 중앙값", "지수보다 나은 비율", "비율"}


def summary_rows(res: dict, mkt: str) -> pd.DataFrame:
    per, tr = res["per"], res["trades"]
    p1, p2 = stats(res["port"]["공식"]), stats(res["port"]["그냥 보유"])
    rows = [("종목 수", len(per)),
            ("종목별 연복리 중앙값: 공식 / 보유", f"{per['공식 연복리'].median():+.1%} / {per['보유 연복리'].median():+.1%}"),
            ("종목별 MDD 중앙값: 공식 / 보유", f"{per['공식 MDD'].median():+.1%} / {per['보유 MDD'].median():+.1%}"),
            ("종목별 샤프 중앙값: 공식 / 보유", f"{per['공식 샤프'].median():.2f} / {per['보유 샤프'].median():.2f}"),
            ("공식 샤프가 보유보다 높은 종목 비율", f"{(per['공식 샤프'] > per['보유 샤프']).mean():.0%}"),
            ("공식 MDD가 보유보다 얕은 종목 비율", f"{(per['공식 MDD'] > per['보유 MDD']).mean():.0%}"),
            ("공식 샤프가 무작위 타이밍(같은 보유 비율) 중앙값보다 높은 종목 비율", f"{per['무작위보다 샤프 높음'].mean():.0%}"),
            ("평균 투자 비중", f"{per['투자 비중'].mean():.0%}"),
            ("거래 수 / 승률 / 중앙값 / 하위10%", f"{len(tr):,} / {(tr['ret'] > 0).mean():.0%} / {tr['ret'].median():+.1%} / {tr['ret'].quantile(0.1):+.1%}"
             if len(tr) else "-"),
            ("거래 평균 보유일 (거래일)", f"{tr['days'].mean():.0f}" if len(tr) else "-"),
            ("균등 포트폴리오: 공식 연복리·MDD·샤프", f"{p1['연복리']:+.1%} · {p1['MDD']:+.1%} · {p1['샤프']:.2f}"),
            ("균등 포트폴리오: 보유 연복리·MDD·샤프", f"{p2['연복리']:+.1%} · {p2['MDD']:+.1%} · {p2['샤프']:.2f}")]
    return pd.DataFrame(rows, columns=["항목", "값"])


def main(cfg: dict | None = None, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    t0 = time.time()
    D = fetch_all(cache, log=log)
    log(f"데이터 {time.time() - t0:.0f}초: 지수 {D['index']['close'].shape[1]}, 미국 {D['us']['close'].shape[1]}, 한국 {D['kr']['close'].shape[1]}")
    it, ie, _ = index_study(D["index"], log=log)
    us = stock_study(D["us"], "US", D["index"]["close"]["^GSPC"], log=log)
    kr = stock_study(D["kr"], "KR", D["index"]["close"]["^KS11"], log=log)
    out = config_mod.ROOT / "reports" / "research"
    out.mkdir(parents=True, exist_ok=True)
    it.to_csv(out / "stocks_index.csv", index=False)
    for m, r in (("us", us), ("kr", kr)):
        r["per"].to_csv(out / f"stocks_{m}_per.csv", index=False)
        r["fwd"].to_csv(out / f"stocks_{m}_fwd.csv", index=False)
    md = render(it, ie, us, kr, D, time.time() - t0)
    path = config_mod.ROOT / "docs" / f"stocks-validation-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text(md, encoding="utf-8")
    log(f"완료: {path}")
    return path


def render(it, ie, us, kr, D, secs) -> str:
    o = ["# 주식에서 확정 공식 검증 (추세: 중기 보유 + 장기 하락이면 중단)\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         "## 0. 조건\n",
         f"- 기간: 각 지수 시작 ~ {DEV_END}. 2025-01-01 이후는 잠가 두고 보지 않았습니다 (나중에 한 번만 여는 최종 검증용).",
         "- 공식·파라미터는 코인과 똑같이 (기간은 거래일 봉 수). 주식에 맞춰 바꾼 것 없음. 그날 종가 판단 → 다음 거래일 시가 체결.",
         "- 비용(매매마다): 미국 0.12%, 한국 0.215% (거래세 포함 왕복 0.43%). 환전 비용·세금(양도세 등)은 빠져 있음.",
         "- 가격: yfinance 수정주가(배당·분할 반영). 지수는 배당 미포함 가격지수.",
         "- ⚠ 종목 검증은 **지금 남아 있는 종목**(S&P500 현재 구성, 한국 시총 상위 200 현재)만이라 생존 편향이 있습니다. "
         "절대 수익은 부풀려졌고, 같은 종목의 '그냥 보유'와 비교하는 상대 비교로 보세요.",
         f"- 종목 데이터: 미국 {D['us']['close'].shape[1]}개, 한국 {D['kr']['close'].shape[1]}개, 2003~{DEV_END[:4]}. 계산 {secs / 60:.1f}분\n",
         "## 1. 지수\n", fmt(it, PCT), "",
         "'무작위 타이밍 대비 샤프 백분위' = 같은 보유 비율·보유 기간으로 무작위로 사고판 200번 중 공식보다 샤프가 낮았던 비율 (95% 이상이면 우연으로 보기 어려움).\n",
         "### 시대별\n", fmt(ie, PCT), ""]
    for nm, r in (("미국 S&P500 현재 구성 종목", us), ("한국 시가총액 상위 200 (현재)", kr)):
        o += [f"## 2. {nm}\n", fmt(summary_rows(r, nm), set()), "",
              "### 신호가 새로 켜진 뒤 수익 (다음 시가 매수 → N거래일 뒤 종가)\n", fmt(r["fwd"], PCT), "",
              "### 시대별 (균등 포트폴리오)\n", fmt(r["era"], PCT), ""]
    return "\n".join(o)
