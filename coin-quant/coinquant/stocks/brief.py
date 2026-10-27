"""매일 주식 시황 페이지 + 텔레그램 — `python -m coinquant stocks-brief`

형식(오선 '오늘의 소식' 참고): 한눈에 보기 → 시장 지표 → 주요 뉴스(해외·국내) → 실적 발표 → 경제지표 일정
→ 🎯 나만의 매수 신호(공식 + 근거 + 관련 뉴스) → 신호 꺼짐 → 위험·안내.
매수 신호 규칙(코인과 같은 확정 공식, 주식 검증: docs/stocks-validation-*.md):
  중기 추세 상태 켜짐 & 장기 점수 −2 초과 & 중기 점수 +2 이상, 그리고 최근 5거래일 안에 새로 켜진 종목만 '새 신호'.
  순서는 거래대금(유동성) 순 — 예측력 순위가 아님.
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


def market_table(M: dict) -> list[dict]:
    c = M["close"]
    rows = []
    trend = {}
    keep = [t for t, _, _ in su.MARKET if t in c.columns]
    sub = c[keep].dropna(how="all")
    hl = {k: M[k][keep].reindex(sub.index) for k in ("high", "low")}
    mt = MultiTrend(sub, hl["high"].where(hl["high"] > 0).fillna(sub), hl["low"].where(hl["low"] > 0).fillna(sub))
    for t, name, grp in su.MARKET:
        if t not in c.columns:
            continue
        s = c[t].dropna()
        if len(s) < 25:
            continue
        d = s.index[-1]
        sc = {k: mt.h[k].score[t].loc[:d].dropna() for k in ("단기", "중기", "장기")}
        st = mt.h["중기"].state[t].loc[:d]
        lab = (f"{int(sc['단기'].iloc[-1]):+d}/{int(sc['중기'].iloc[-1]):+d}/{int(sc['장기'].iloc[-1]):+d}"
               if all(len(v) for v in sc.values()) else "-")
        trend[t] = bool(st.iloc[-1]) if len(st) else False
        rows.append({"묶음": grp, "지표": name, "기호": t, "날짜": d.date(), "종가": s.iloc[-1], "1일": _ret(s, 1), "1주": _ret(s, 5),
                     "1달": _ret(s, 21), "추세 점수(단/중/장)": lab, "중기 추세": "켜짐" if trend[t] else "꺼짐",
                     "200일선 대비": s.iloc[-1] / s.rolling(200).mean().iloc[-1] - 1 if len(s) >= 200 else np.nan})
    return rows


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


def _p(v, d=1):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.{d}f}%"


def _md_table(cols: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]
    return out


def render(today: pd.Timestamp, mk: list[dict], news: dict, news_err: list[str], earn: list[dict], econ: list[dict],
           sig: dict, val_line: str) -> str:
    o = [f"# {today:%Y년 %m월 %d일} ({WEEKDAY[today.weekday()]}) 오늘의 소식\n",
         "> ⚠️ 투자 판단을 돕는 정보일 뿐 투자 권유가 아닙니다. 과거 성과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다. "
         "뉴스 구분(공식 발표/보도/전망·추측)은 제목으로 자동 분류한 것이라 틀릴 수 있으니 원문을 확인하세요.\n"]
    get = {r["기호"]: r for r in mk}
    line = " · ".join(f"{get[t]['지표']} {_p(get[t]['1일'])}" for t in ("^GSPC", "^IXIC", "^KS11", "^KQ11") if t in get)
    extra = " · ".join(f"{get[t]['지표']} {num(get[t]['종가'])}" for t in ("KRW=X", "^TNX", "^VIX") if t in get)
    o += ["## 📌 한눈에 보기\n", f"- 지수: {line}", f"- 금리·환율·변동성: {extra}"]
    for mkt, s in sig.items():
        sm = s[2]
        o.append(f"- {mkt} 새 매수 신호 {sm['n_fresh']}개 (매수 가능 상태 {sm['n_on']}개, 중기 추세 켜진 종목 {sm['n_trend']}/{sm['n_alive']}) · "
                 f"신호 꺼짐(최근 3일) {len(s[1])}개 이상")
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
    if econ:
        o += _md_table(["한국시간", "지표", "실제", "예상", "이전"],
                       [[("**" if e["key"] else "") + e["time_kst"] + ("**" if e["key"] else ""), e["event"], e["actual"] or "-",
                         e["consensus"] or "-", e["previous"] or "-"] for e in econ])
        o.append("\n굵은 글씨 = 시장 영향이 큰 지표(CPI·고용·FOMC 등)\n")
    else:
        o.append("- 일정 없음 (또는 캘린더 접속 실패)\n")
    o += ["## 5. 🎯 나만의 매수 신호\n",
          f"규칙: 중기 추세 켜짐 & 중기 점수 +2 이상 & 장기 점수 −2 초과, 그리고 **최근 {FRESH_DAYS}거래일 안에 새로 켜진** 종목. "
          "다음 거래일에 사고, 중기 추세가 꺼지면(점수 0 이하) 팝니다. 순서는 거래대금 순(예측력 순위 아님).", "",
          f"검증 요약: {val_line}", ""]
    for mkt, (cards, offs, sm) in sig.items():
        o.append(f"### {mkt} — 새 신호 {sm['n_fresh']}개 중 {len(cards)}개 (기준일 {sm['date']})\n")
        if not cards:
            o.append("- 새 신호 없음\n")
        for k in cards:
            sc = k["score"]
            h = k["hist"]
            o.append(f"#### {k['name']} ({k['ticker']}){' 📰' if k['mention'] else ''}\n")
            o.append(f"- 종가 {num(k['close'])} · 1일 {_p(k['r1'])} · 20일 {_p(k['r20'])} · 켜진 지 {k['on_days']}거래일"
                     + (f" · {k['sector']}" if k["sector"] else ""))
            o.append(f"- 추세 점수 단기 {sc['단기']:+d} / 중기 {sc['중기']:+d} / 장기 {sc['장기']:+d}\n")
            o += _md_table(["기간", "판정", "이동평균", "채널", "회귀"],
                           [[r["기간"], f"{r['판정']} ({int(r['점수']):+d})" if not pd.isna(r["점수"]) else r["판정"], r["이동평균"], r["채널"], r["회귀"]]
                            for r in k["snap"]])
            o.append("")
            o.append(f"- 꺼지는 쪽 기준: 종가가 채널 40%선 {num(k['exit_chan'])} 또는 느린 EMA {num(k['exit_ema'])} 아래로 가면 약해짐")
            o.append(f"- 이 종목 과거 같은 신호(2003~2024, 비용 포함): {h['n']}회 · 승률 {f"{h['win']:.0%}" if not np.isnan(h['win']) else '-'} · "
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


def telegram_text(today, mk, news, sig, url: str) -> str:
    get = {r["기호"]: r for r in mk}
    lines = [f"📰 {today:%m/%d}({WEEKDAY[today.weekday()]}) 주식 오늘의 소식"]
    lines.append(" · ".join(f"{get[t]['지표']} {_p(get[t]['1일'])}" for t in ("^GSPC", "^IXIC", "^KS11", "^KQ11") if t in get))
    lines.append(" · ".join(f"{get[t]['지표']} {num(get[t]['종가'])}" for t in ("KRW=X", "^TNX", "^VIX") if t in get))
    for grp in ("해외", "국내"):
        for i in news.get(grp, [])[:3]:
            lines.append(f"• [{grp}·{i['kind']}] {i['title'][:70]}")
    for mkt, (cards, offs, sm) in sig.items():
        names = ", ".join(f"{k['name']}{'📰' if k['mention'] else ''}" for k in cards[:6]) or "없음"
        lines.append(f"🎯 {mkt} 새 매수 신호 {sm['n_fresh']}개: {names}")
        if offs:
            lines.append(f"   꺼짐: {', '.join(k['name'] for k in offs[:5])}")
    if url:
        lines.append(f"전체 보기: {url}")
    lines.append("※ 투자 권유 아님. 원금 손실 가능.")
    return "\n".join(lines)[:4000]


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
    val_line = cfg.get("stocks", {}).get("validation_summary", "주식 검증 문서: docs/stocks-validation-*.md")
    md = render(today, market_table(M), news, nerr, earn, econ, sig, val_line)
    out = config_mod.ROOT / "out" / "briefing"
    (out / "daily").mkdir(parents=True, exist_ok=True)
    page = out / "daily" / f"{today:%Y-%m-%d}.md"
    page.write_text(md, encoding="utf-8")
    pages = sorted((out / "daily").glob("*.md"), reverse=True)
    (out / "README.md").write_text("# 주식 오늘의 소식\n\n" + "\n".join(f"- [{p.stem}](daily/{p.name})" for p in pages[:120]) + "\n",
                                   encoding="utf-8")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    url = f"https://github.com/{repo}/blob/stock-briefing/daily/{page.name}" if repo else ""
    text = telegram_text(today, market_table(M), news, sig, url)
    (out / "telegram.txt").write_text(text, encoding="utf-8")
    log(text)
    if send:
        from ..app.alert import send_telegram
        log(send_telegram(text))
    log(f"완료: {page} ({time.time() - t0:.0f}초)")
    return page
