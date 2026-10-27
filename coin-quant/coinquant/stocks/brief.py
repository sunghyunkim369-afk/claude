"""매일 주식 시황 페이지 + 텔레그램 — `python -m coinquant stocks-brief`

형식(오선 '오늘의 소식' 참고): 한눈에 보기 → 시장 지표 → 주요 뉴스(해외·국내) → 실적 발표 → 경제지표 일정
→ 🎯 나만의 매수 신호(공식 + 근거 + 관련 뉴스) → 신호 꺼짐 → 위험·안내.
매수 신호 규칙(코인과 같은 확정 공식, 주식 검증: docs/stocks-validation-*.md):
  중기 추세 상태 켜짐 & 장기 점수 −2 초과 & 중기 점수 +2 이상, 그리고 최근 5거래일 안에 새로 켜진 종목만 '새 신호'.
  순서는 거래대금(유동성) 순 — 예측력 순위가 아님.
지수 신호(5-1)는 검증 근거가 있고, 종목 목록(5-2)은 검증에서 고르기 효과가 확인되지 않아 '관심 목록'으로만 표시.
결과: out/briefing/daily/날짜.md, out/briefing/README.md(목차), 텔레그램 요약(.env/Secrets 의 TELEGRAM_*).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..trend import MultiTrend, num
from . import calendar as cal, data as sd, news as sn, universe as su
from .validate import COST, DEV_END, formula_position, trades

KST = "Asia/Seoul"
FRESH_DAYS = 5
TOP_N = 10
WEEKDAY = "월화수목금토일"


def complete_bars(F: dict, mkt: str) -> dict:
    """아직 장이 안 끝난 오늘 봉은 버림 (한국 15:30 KST, 미국 16:00 ET 마감 + 여유)."""
    if not F or "close" not in F:
        return F
    if mkt == "KR":
        now = pd.Timestamp.now(tz=KST)
        cut = now.normalize().tz_localize(None) if now.hour < 16 else None
    else:
        now = pd.Timestamp.now(tz="America/New_York")
        cut = now.normalize().tz_localize(None) if now.hour < 17 else None
    if cut is None:
        return F
    return {k: v[v.index < cut] for k, v in F.items()}


def _ret(s: pd.Series, n: int) -> float:
    s = s.dropna()
    return s.iloc[-1] / s.iloc[-1 - n] - 1 if len(s) > n else np.nan


def _single(M: dict, t: str):
    """지표 하나를 자기 거래일 달력으로만 계산 (한국·미국 휴장일이 섞이면 이동평균이 틀어짐)."""
    c = M["close"][[t]].dropna()
    h = M["high"][[t]].reindex(c.index)
    l_ = M["low"][[t]].reindex(c.index)
    pos, mt = formula_position(c, h.where(h > 0).fillna(c), l_.where(l_ > 0).fillna(c))
    return c[t], pos[t], mt


def market_table(M: dict) -> list[dict]:
    c = M["close"]
    rows = []
    for t, name, grp in su.MARKET:
        if t not in c.columns:
            continue
        s = c[t].dropna()
        if len(s) < 25:
            continue
        _, _, mt = _single(M, t)
        d = s.index[-1]
        sc = {k: mt.h[k].score[t].dropna() for k in ("단기", "중기", "장기")}
        st = mt.h["중기"].state[t]
        lab = (f"{int(sc['단기'].iloc[-1]):+d}/{int(sc['중기'].iloc[-1]):+d}/{int(sc['장기'].iloc[-1]):+d}"
               if all(len(v) for v in sc.values()) else "-")
        on = bool(st.iloc[-1]) if len(st) else False
        rows.append({"묶음": grp, "지표": name, "기호": t, "날짜": d.date(), "종가": s.iloc[-1], "1일": _ret(s, 1), "1주": _ret(s, 5),
                     "1달": _ret(s, 21), "추세 점수(단/중/장)": lab, "중기 추세": "켜짐" if on else "꺼짐",
                     "200일선 대비": s.iloc[-1] / s.rolling(200).mean().iloc[-1] - 1 if len(s) >= 200 else np.nan})
    return rows


# 지수 신호: 주식 검증(docs/stocks-validation-2026-10-27.md, ~2024)에서 지수에 공식을 쓴 결과 → 근거 등급
INDEX_PLAN = {
    "^IXIC": ("나스닥 종합", "QQQ · TIGER 미국나스닥100(133690)", "근거 있음",
              "1972~2024 공식 연 8.9%·MDD −36% vs 보유 10.2%·−78%, 샤프 0.79 vs 0.58, 무작위 타이밍 200번보다 모두 나음"),
    "^KS11": ("코스피", "KODEX 200(069500) · TIGER 200(102110)", "근거 있음",
              "1997~2024 공식 연 5.1%·MDD −45% vs 보유 4.9%·−64%, 샤프 0.42 vs 0.31, 무작위 타이밍 상위 2.5%"),
    "^GSPC": ("S&P 500", "SPY · TIGER 미국S&P500(360750)", "손실 방어만",
              "1950~2024 공식 연 4.1%·MDD −36% vs 보유 8.1%·−57%, 샤프 0.49 vs 0.59 — 큰 하락은 줄였지만 수익은 절반"),
    "^KQ11": ("코스닥", "KODEX 코스닥150(229200)", "근거 약함",
              "1997~2024 공식 연 −0.9%·MDD −55% vs 보유 −1.0%·−74%, 무작위 타이밍과 비슷"),
}


def index_signals(M: dict) -> list[dict]:
    out = []
    for t in INDEX_PLAN:
        if t not in M["close"].columns or M["close"][t].notna().sum() < 300:
            continue
        cs, p_, mt = _single(M, t)
        s = p_.astype(bool)
        d = s.index[-1]
        if pd.isna(mt.h["중기"].score.at[d, t]):
            continue
        rev = s.to_numpy()[::-1]
        run = int(len(rev) if rev.all() else np.argmin(rev)) if rev[0] else 0
        offrun = int(len(rev) if (~rev).all() else np.argmin(~rev)) if not rev[0] else 0
        msc = int(mt.h["중기"].score.at[d, t])
        state = ("매수 가능" if s.iloc[-1] and msc >= 2 else "보유만 (신규 진입 대기)" if s.iloc[-1] else "관망 (현금)")
        hi, lo = mt.h["중기"].chan_hi.at[d, t], mt.h["중기"].chan_lo.at[d, t]
        name, etf, grade, stat = INDEX_PLAN[t]
        out.append({"ticker": t, "name": name, "etf": etf, "grade": grade, "stat": stat, "date": d.date(), "state": state,
                    "score": {k: int(mt.h[k].score.at[d, t]) for k in ("단기", "중기", "장기")},
                    "on_days": run, "off_days": offrun, "chan60": lo + 0.6 * (hi - lo), "chan40": lo + 0.4 * (hi - lo),
                    "close": cs.at[d]})
    return out


def signals(F: dict, uni: pd.DataFrame, mkt: str, news_titles: list[str]) -> tuple[list[dict], list[dict], dict]:
    c = F["close"]
    c = c.loc[:, c.notna().sum() > 260]
    o, h, l_, v = (F[k].reindex_like(c) for k in ("open", "high", "low", "volume"))
    h, l_ = h.where(h > 0).fillna(c), l_.where(l_ > 0).fillna(c)
    pos, mt = formula_position(c, h, l_)
    d = c.index[-1]
    alive = c.loc[d].notna()
    S, Mm, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    base = pos.astype(bool)
    rev = base.to_numpy()[::-1]
    run_d = pd.Series(np.where(rev.all(0), len(rev), np.argmin(rev, axis=0)), index=base.columns)   # 오늘까지 연속으로 켜진 날 수
    ok_now = base.loc[d] & (Mm.score.loc[d] >= 2) & alive
    fresh = ok_now & (run_d <= FRESH_DAYS)
    off = (~base.loc[d]) & base.shift(1).fillna(False).iloc[-3:].any() & alive     # 최근 3일 안에 꺼짐
    dv = (c * v).iloc[-60:].mean()
    info = uni.set_index("ticker")
    cost = COST[mkt]

    def card(a: str) -> dict:
        nm = info.at[a, "name"] if a in info.index else a
        tr = trades(pos[a].loc[:DEV_END], o[a].loc[:DEV_END], c[a].loc[:DEV_END], cost)
        tr = tr[tr["entry"] >= pd.Timestamp("2003-01-01")] if len(tr) else tr
        snap = mt.snapshot(d, a)
        hi, lo = Mm.chan_hi.at[d, a], Mm.chan_lo.at[d, a]
        mention = any(str(nm) and str(nm) in t for t in news_titles) or any(f"({a.split('.')[0]})" in t for t in news_titles)
        return {"ticker": a, "name": nm, "sector": info.at[a, "sector"] if a in info.index else "", "close": c.at[d, a],
                "r1": _ret(c[a], 1), "r20": _ret(c[a], 20), "on_days": int(run_d[a]),
                "score": {k: int(mt.h[k].score.at[d, a]) for k in ("단기", "중기", "장기")}, "snap": snap,
                "exit_chan": float(lo + 0.4 * (hi - lo)), "exit_ema": float(Mm.slow.at[d, a]),
                "hist": {"n": len(tr), "win": (tr["ret"] > 0).mean() if len(tr) else np.nan,
                         "median": tr["ret"].median() if len(tr) else np.nan, "worst": tr["ret"].min() if len(tr) else np.nan},
                "dv": dv.get(a, np.nan), "mention": mention}

    fr = sorted(fresh[fresh].index, key=lambda a: -np.nan_to_num(dv.get(a, 0)))
    of = sorted(off[off].index, key=lambda a: -np.nan_to_num(dv.get(a, 0)))
    # 뉴스에 이름이 나온 새 신호 종목은 순서와 상관없이 포함
    ment = [a for a in fr if (info.at[a, "name"] if a in info.index else a) and any(str(info.at[a, "name"]) in t for t in news_titles if a in info.index)]
    pick = list(dict.fromkeys(fr[:TOP_N] + ment))
    summary = {"date": d.date(), "n_fresh": int(fresh.sum()), "n_on": int(ok_now.sum()), "n_trend": int(base.loc[d][alive].sum()),
               "n_alive": int(alive.sum())}
    return [card(a) for a in pick], [card(a) for a in of[:TOP_N]], summary


def _ratio(v) -> str:
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.0%}"


def _p(v, d=1):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.{d}f}%"


def _md_table(cols: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]
    return out


def render(today: pd.Timestamp, mk: list[dict], news: dict, news_err: list[str], earn: list[dict], econ: list[dict],
           sig: dict, val_line: str, idx_sig: list[dict] | None = None) -> str:
    o = [f"# {today:%Y년 %m월 %d일} ({WEEKDAY[today.weekday()]}) 오늘의 소식\n",
         "> ⚠️ 투자 판단을 돕는 정보일 뿐 투자 권유가 아닙니다. 과거 성과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다. "
         "뉴스 구분(공식 발표/보도/전망·추측)은 제목으로 자동 분류한 것이라 틀릴 수 있으니 원문을 확인하세요.\n"]
    get = {r["기호"]: r for r in mk}
    line = " · ".join(f"{get[t]['지표']} {_p(get[t]['1일'])}" for t in ("^GSPC", "^IXIC", "^KS11", "^KQ11") if t in get)
    extra = " · ".join(f"{get[t]['지표']} {num(get[t]['종가'])}" for t in ("KRW=X", "^TNX", "^VIX") if t in get)
    o += ["## 📌 한눈에 보기\n", f"- 지수: {line}", f"- 금리·환율·변동성: {extra}"]
    for x in idx_sig or []:
        o.append(f"- {x['name']} 신호: **{x['state']}** ({x['grade']})")
    for mkt, s in sig.items():
        sm = s[2]
        o.append(f"- {mkt} 추세 시작 종목 {sm['n_fresh']}개 (관심 목록) · 중기 추세 켜진 종목 {sm['n_trend']}/{sm['n_alive']}")
    o += ["", "## 1. 시장 지표\n"]
    o += _md_table(["묶음", "지표", "종가", "1일", "1주", "1달", "200일선 대비", "추세(단/중/장)", "중기 추세", "기준일"],
                   [[r["묶음"], r["지표"], num(r["종가"]), _p(r["1일"]), _p(r["1주"]), _p(r["1달"]), _p(r["200일선 대비"]),
                     r["추세 점수(단/중/장)"], r["중기 추세"], r["날짜"]] for r in mk])
    o += ["", "추세 점수: 기간마다 이동평균·채널 위치·회귀 추세선 근거를 −1/0/+1로 매겨 합한 값(−3~+3). 중기 추세는 +2 이상에서 켜지고 0 이하에서 꺼짐.\n"]
    o.append("## 2. 주요 뉴스\n")
    for grp in ("해외", "국내"):
        o.append(f"### {grp}\n")
        items = news.get(grp, [])
        if not items:
            o.append("- 가져온 기사 없음\n")
            continue
        for i in items:
            t = (i["time"] + pd.Timedelta(hours=9)).strftime("%m/%d %H:%M") if pd.notna(i["time"]) else ""
            tp = f" `{'·'.join(i['topics'])}`" if i["topics"] else ""
            o.append(f"- **[{i['kind']}]** [{i['title']}]({i['link']}) — {i['source']} {t}{tp}")
        o.append("")
    if news_err:
        o.append(f"접속 실패한 출처: {', '.join(news_err)}\n")
    o.append("## 3. 실적 발표 (S&P500 종목, 미국 날짜 기준)\n")
    if earn:
        o += _md_table(["날짜", "종목", "회사", "발표 시각", "EPS 예상", "작년 EPS"],
                       [[e["date"], e["symbol"], e["name"], e["when"], e["eps_fcst"] or "-", e["eps_last"] or "-"] for e in earn[:25]])
        o.append("")
    else:
        o.append("- 일정 없음 (또는 캘린더 접속 실패)\n")
    o.append("## 4. 경제지표 일정 (미국, 한국시간)\n")
    key = [e for e in econ if e["key"]]
    if econ:
        show = key or econ[:8]
        o += _md_table(["한국시간", "지표", "실제", "예상", "이전"],
                       [[e["time_kst"], f"**{e['event']}**" if e["key"] else e["event"], e["actual"] or "-",
                         e["consensus"] or "-", e["previous"] or "-"] for e in show])
        rest = len(econ) - len(show)
        o.append(f"\n{'시장 영향이 큰 지표(CPI·고용·FOMC·소비심리 등)만 표시. ' if key else ''}"
                 f"{f'그 밖의 지표 {rest}개는 생략.' if rest > 0 else ''}\n")
    else:
        o.append("- 일정 없음 (또는 캘린더 접속 실패)\n")
    o += ["## 5. 🎯 나만의 매수 신호\n",
          "### 5-1. 지수·대표 ETF (검증 근거가 있는 신호)\n",
          "규칙(코인과 같은 확정 공식): 중기 추세가 켜져 있고 장기 점수가 −2 초과면 보유. **새로 사는 건 중기 점수 +2 이상일 때만**, "
          "중기 점수가 0 이하로 떨어지면 다음 거래일에 팝니다.\n"]
    o += _md_table(["지수", "상태", "추세(단/중/장)", "기간", "다시 켜지는/꺼지는 기준(채널 60%/40%선)", "살 때 (ETF)", "근거 등급", "과거 검증"],
                   [[x["name"], f"**{x['state']}**", f"{x['score']['단기']:+d}/{x['score']['중기']:+d}/{x['score']['장기']:+d}",
                     f"켜진 지 {x['on_days']}일" if x["on_days"] else f"꺼진 지 {x['off_days']}일",
                     f"{num(x['chan60'])} / {num(x['chan40'])}", x["etf"], x["grade"], x["stat"]] for x in (idx_sig or [])])
    o += ["", f"검증: {val_line}", "",
          "### 5-2. 추세가 막 시작된 종목 (관심 목록)\n",
          f"같은 공식으로 최근 {FRESH_DAYS}거래일 안에 '매수 가능'이 된 종목입니다. ⚠ **주식 검증에서 개별 종목 고르기 효과는 확인되지 않았습니다** "
          "(신호가 켜진 뒤 20·60일 수익이 아무 날과 같았고, 무작위 타이밍보다 나은 종목이 미국 38%·한국 51%). "
          "매수 근거가 아니라 '추세가 바뀐 종목' 목록으로 보고, 뉴스·실적을 직접 확인하세요. 순서는 거래대금 순.\n"]
    for mkt, (cards, offs, sm) in sig.items():
        o.append(f"#### {mkt} — {sm['n_fresh']}개 중 {len(cards)}개 (기준일 {sm['date']})\n")
        if not cards:
            o.append("- 새 신호 없음\n")
        for k in cards:
            sc = k["score"]
            h = k["hist"]
            o.append(f"**{k['name']} ({k['ticker']}){' 📰' if k['mention'] else ''}**\n")
            o.append(f"- 종가 {num(k['close'])} · 1일 {_p(k['r1'])} · 20일 {_p(k['r20'])} · 켜진 지 {k['on_days']}거래일"
                     + (f" · {k['sector']}" if k["sector"] else ""))
            o.append(f"- 추세 점수 단기 {sc['단기']:+d} / 중기 {sc['중기']:+d} / 장기 {sc['장기']:+d}\n")
            o += _md_table(["기간", "판정", "이동평균", "채널", "회귀"],
                           [[r["기간"], f"{r['판정']} ({int(r['점수']):+d})" if not pd.isna(r["점수"]) else r["판정"], r["이동평균"], r["채널"], r["회귀"]]
                            for r in k["snap"]])
            o.append("")
            o.append(f"- 꺼지는 쪽 기준: 종가가 채널 40%선 {num(k['exit_chan'])} 또는 느린 EMA {num(k['exit_ema'])} 아래로 가면 약해짐")
            o.append(f"- 이 종목 과거 같은 신호(2003~2024, 비용 포함): {h['n']}회 · 승률 {_ratio(h['win'])} · "
                     f"중앙값 {_p(h['median'])} · 최악 {_p(h['worst'])}")
            for nw in k.get("news", []):
                o.append(f"- 관련 뉴스 [{nw['kind']}]: [{nw['title']}]({nw['link']}) — {nw['source']}")
            o.append("")
        if offs:
            o.append(f"**신호 꺼짐 (최근 3일, 갖고 있으면 확인)**: " + ", ".join(f"{k['name']}({k['ticker']})" for k in offs) + "\n")
    o += ["## 6. 위험·안내\n",
          "- 📰 = 오늘 주요 뉴스 제목에 이름이 나온 종목. 뉴스는 매수 근거가 아니라 확인할 거리입니다.",
          "- 종목 과거 성적은 지금 남아 있는 종목만으로 계산해 실제보다 좋게 보일 수 있습니다(생존 편향).",
          "- 실적 발표 전후·큰 지표 발표일에는 하루 변동이 커질 수 있습니다. 신호가 있어도 일정 뒤 진입을 고려하세요(검증한 규칙 아님).",
          "- 해외 주식은 환율 변동과 환전 비용, 양도소득세(연 250만 원 초과분 22%)가 따로 있습니다."]
    return "\n".join(o)


def telegram_text(today, mk, news, sig, url: str, idx_sig: list[dict] | None = None) -> str:
    """텔레그램 요약 (HTML 서식). 전체 내용은 페이지 링크로."""
    from html import escape as e
    get = {r["기호"]: r for r in mk}
    ic = {"매수 가능": "🟢", "보유만 (신규 진입 대기)": "🟡", "관망 (현금)": "⚪"}

    def idx(ts):
        return " · ".join(f"{e(get[t]['지표'])} <b>{_p(get[t]['1일'])}</b>" for t in ts if t in get)

    L = [f"<b>📈 주식 오늘의 소식</b>  {today:%m/%d}({WEEKDAY[today.weekday()]})", "", "<b>지수</b>",
         f"🇺🇸 {idx(('^GSPC', '^IXIC'))}", f"🇰🇷 {idx(('^KS11', '^KQ11'))}"]
    ex = []
    if "KRW=X" in get:
        ex.append(f"환율 {get['KRW=X']['종가']:,.0f}원")
    if "^TNX" in get:
        ex.append(f"미10년 {get['^TNX']['종가']:.2f}%")
    if "^VIX" in get:
        ex.append(f"VIX {get['^VIX']['종가']:.1f}")
    if ex:
        L.append(" · ".join(ex))
    if idx_sig:
        L += ["", "<b>🎯 지수 ETF 신호</b>"]
        for x in idx_sig:
            L.append(f"{ic.get(x['state'], '⚪')} <b>{e(x['name'])}</b> {e(x['state'])}  <i>{e(x['grade'])}</i>")
    for grp, flag in (("해외", "🌎"), ("국내", "🇰🇷")):
        items = news.get(grp, [])[:3]
        if items:
            L += ["", f"<b>{flag} {grp} 뉴스</b>"]
            for i in items:
                t = e(i["title"][:60] + ("…" if len(i["title"]) > 60 else ""))
                L.append(f"• <a href=\"{e(i['link'], quote=True)}\">{t}</a> <i>{e(i['kind'])}</i>" if i.get("link") else f"• {t}")
    L += ["", "<b>👀 추세 시작 종목</b>  <i>(관심 목록, 매수 근거 아님)</i>"]
    for mkt, (cards, offs, sm) in sig.items():
        flag = "🇺🇸" if mkt == "미국" else "🇰🇷"
        names = ", ".join(e(str(k["name"])) + ("📰" if k["mention"] else "") for k in cards[:5]) or "없음"
        more = f" 외 {sm['n_fresh'] - min(len(cards), 5)}개" if sm["n_fresh"] > 5 else ""
        L.append(f"{flag} {names}{more}")
    if url:
        L += ["", f"📄 <a href=\"{e(url, quote=True)}\">전체 페이지 보기</a> (실적·경제지표·종목 근거)"]
    L += ["", "<i>투자 권유 아님 · 원금 손실 가능</i>"]
    return "\n".join(L)[:4000]


def main(cfg: dict | None = None, send: bool = True, log=print) -> Path:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg) / "stocks"
    t0 = time.time()
    today = pd.Timestamp.now(tz=KST).tz_localize(None).normalize()
    mkt_tk = list(dict.fromkeys([t for t, _, _ in su.MARKET]))
    M = sd.update(cache, "brief_mkt", mkt_tk, start="2018-01-01", log=log)
    us_u = su.us_universe()
    kr_u = su.kr_universe(200)
    US = complete_bars(sd.update(cache, "us", list(us_u["ticker"]), start="2000-01-01", log=log), "US")
    KR = complete_bars(sd.update(cache, "kr", list(kr_u["ticker"]), start="2000-01-01", log=log), "KR")
    log(f"  데이터 {time.time() - t0:.0f}초")
    news, nerr = sn.collect(log=log)
    titles = [i["title"] for g in news.values() for i in g]
    sig = {"미국": signals(US, us_u, "US", titles), "한국": signals(KR, kr_u, "KR", titles)}
    for mkt, (cards, _, _) in sig.items():
        for k in cards:
            k["news"] = sn.stock_news(k["name"], "ko" if mkt == "한국" else "en")
    ny = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
    members = set(us_u["ticker"])
    earn = cal.earnings(ny, members) + cal.earnings(ny + pd.Timedelta(days=1), members)
    econ = cal.economic(ny) + cal.economic(ny + pd.Timedelta(days=1))
    val_line = cfg.get("stocks", {}).get("validation_summary", "docs/stocks-validation-2026-10-27.md (2024년까지, 2025~ 잠금)")
    isig = index_signals(M)
    md = render(today, market_table(M), news, nerr, earn, econ, sig, val_line, isig)
    out = config_mod.ROOT / "out" / "briefing"
    (out / "daily").mkdir(parents=True, exist_ok=True)
    page = out / "daily" / f"{today:%Y-%m-%d}.md"
    page.write_text(md, encoding="utf-8")
    pages = sorted((out / "daily").glob("*.md"), reverse=True)
    (out / "README.md").write_text("# 주식 오늘의 소식\n\n" + "\n".join(f"- [{p.stem}](daily/{p.name})" for p in pages[:120]) + "\n",
                                   encoding="utf-8")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    url = f"https://github.com/{repo}/blob/stock-briefing/daily/{page.name}" if repo else ""
    text = telegram_text(today, market_table(M), news, sig, url, isig)
    (out / "telegram.txt").write_text(text, encoding="utf-8")
    log(text)
    if send:
        from ..app.alert import send_telegram
        log(send_telegram(text, html_mode=True))
    log(f"완료: {page} ({time.time() - t0:.0f}초)")
    return page
