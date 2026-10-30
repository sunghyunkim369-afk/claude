"""실적 발표·경제지표 일정 (나스닥 공개 캘린더). 접속이 막히면 빈 목록.

시각: 실적은 '장 전/장 후', 경제지표는 미국 동부시간(서머타임 반영) → 한국시간으로 바꿔 표시.
"""
from __future__ import annotations

import html
import re

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
TIME_KR = {"time-pre-market": "장 전 (한국 밤~새벽)", "time-after-hours": "장 후 (한국 새벽~아침)", "time-not-supplied": "시각 미정"}
# 시장 영향이 큰 지표 (이름에 이 말이 들어가면 굵게). 'Dallas Fed' 같은 지역 연은 지표·GDPNow 는 빼려고 정규식으로 좁힘
KEY_RE = re.compile(r"(\bCPI\b|\bPCE\b|Nonfarm|Unemployment Rate|^GDP|\bGDP \(|Fed Interest Rate|FOMC|Fed Chair|Retail Sales|"
                    r"ISM (Manufacturing|Non-Manufacturing|Services) PMI|\bPPI\b|Initial Jobless|JOLTS|CB Consumer Confidence|"
                    r"Michigan Consumer Sentiment|Core Durable|Beige Book)", re.I)


def _get(url: str) -> dict:
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    return r.json()


def earnings(day: pd.Timestamp, members: set[str] | None = None) -> list[dict]:
    try:
        js = _get(f"https://api.nasdaq.com/api/calendar/earnings?date={day:%Y-%m-%d}")
    except Exception:
        return []
    rows = (js.get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").replace(".", "-")
        if members is not None and sym not in members:
            continue
        cap = str(r.get("marketCap") or "").replace("$", "").replace(",", "")
        out.append({"date": day.date(), "symbol": sym, "name": r.get("name", ""), "when": TIME_KR.get(r.get("time", ""), r.get("time", "")),
                    "eps_fcst": r.get("epsForecast", ""), "eps_last": r.get("lastYearEPS", ""),
                    "mcap": float(cap) if cap.replace(".", "").isdigit() else 0.0})
    return sorted(out, key=lambda x: -x["mcap"])


def economic(day: pd.Timestamp, countries=("United States",)) -> list[dict]:
    """미국 동부 날짜 day 의 경제지표. 나스닥 캘린더는 date=D 로 물으면 D-1 일 발표분을 돌려줌
    (2026-10-28 06:35 ET 에 date=10/28 로 받은 내구재·CB 소비자신뢰가 이미 실제값을 담고 있었고(10/27 발표),
    10/28 FOMC 는 date=10/29 에 나옴) → 하루 뒤 날짜로 물어서 day 일정을 얻음."""
    try:
        js = _get(f"https://api.nasdaq.com/api/calendar/economicevents?date={day + pd.Timedelta(days=1):%Y-%m-%d}")
    except Exception:
        return []
    rows = (js.get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        if r.get("country") not in countries:
            continue
        name = r.get("eventName", "")
        t = r.get("gmt", "")
        try:   # 칸 이름은 'gmt' 지만 실제 값은 미국 동부시간 (CB 소비자신뢰 10:00 = 발표 시각과 같음) → 서머타임 반영해 한국시간으로
            ts = pd.Timestamp(f"{day:%Y-%m-%d} {t}").tz_localize("America/New_York").tz_convert("Asia/Seoul")
            kst = ts.strftime("%m/%d %H:%M")
        except Exception:
            ts, kst = pd.NaT, t
        clean = lambda v: html.unescape(str(v or "")).replace("\xa0", " ").strip()  # noqa: E731
        out.append({"time_kst": kst, "ts": ts, "country": r.get("country"), "event": name, "actual": clean(r.get("actual")),
                    "consensus": clean(r.get("consensus")), "previous": clean(r.get("previous")),
                    "key": bool(KEY_RE.search(name))})
    return out


# 주요 지표 한국어 이름 (앞에서부터 처음 맞는 것)
KO_NAMES = [("Fed Interest Rate Decision", "美 기준금리"), ("GDP Price Index", "GDP 물가지수"), ("GDP Sales", "GDP 최종판매"),
            ("Core PCE Prices", "근원 PCE 물가(GDP 집계)"), ("PCE Prices", "PCE 물가(GDP 집계)"), ("Core PCE", "근원 PCE 물가"), ("PCE", "PCE 물가"),
            ("Core CPI", "근원 소비자물가(CPI)"), ("CPI", "소비자물가(CPI)"), ("Core PPI", "근원 생산자물가(PPI)"), ("PPI", "생산자물가(PPI)"),
            ("Nonfarm", "비농업 고용"), ("Unemployment Rate", "실업률"), ("Initial Jobless", "신규 실업수당 청구"),
            ("JOLTS", "구인건수(JOLTS)"), ("GDP", "GDP 성장률"), ("Retail Sales", "소매판매"),
            ("ISM Manufacturing", "ISM 제조업 지수"), ("ISM Non-Manufacturing", "ISM 서비스업 지수"), ("ISM Services", "ISM 서비스업 지수"),
            ("CB Consumer Confidence", "CB 소비자신뢰"), ("Michigan", "미시간 소비심리"), ("Core Durable", "근원 내구재 주문"),
            ("Beige Book", "베이지북"), ("FOMC Press Conference", "FOMC 기자회견"), ("FOMC Statement", "FOMC 성명"),
            ("Fed Chair", "연준 의장 발언")]


PERIOD = [("(MoM)", "전월비"), ("(YoY)", "전년비"), ("(QoQ)", "전기비"), ("(Annualized)", "연율")]


def ko_name(name: str) -> str:
    """한국어 이름 + 기간 (같은 지표의 전월비·전년비가 같은 이름으로 보이지 않게)."""
    base = next((v for k, v in KO_NAMES if k.lower() in name.lower()), name)
    per = [v for k, v in PERIOD if k.lower() in name.lower()]
    return base + (f" ({'·'.join(per)})" if per and base != name else "")


def _num(v: str) -> float | None:
    m = re.match(r"^\(?(-?[\d.,]+)\)?\s*([%KMBT]?)", (v or "").replace(",", ""))
    if not m:
        return None
    try:
        x = float(m.group(1))
    except ValueError:
        return None
    return -x if (v or "").strip().startswith("(") else x


def verdict(e: dict) -> str:
    """금리 결정은 이전과 비교(인상/인하/동결), 나머지는 예상과 비교."""
    a, c, p = _num(e["actual"]), _num(e["consensus"]), _num(e["previous"])
    if a is None:
        return ""
    if "Interest Rate Decision" in e["event"]:
        if p is None:
            return ""
        return "동결" if abs(a - p) < 1e-9 else ("인상" if a > p else "인하")
    if c is None:
        return ""
    if abs(a - c) < 1e-9:
        return "예상과 같음"
    return "예상보다 높음" if a > c else "예상보다 낮음"


def recent_results(hours: int = 30, now: pd.Timestamp | None = None) -> list[dict]:
    """최근 hours 시간 안에 발표된 주요 지표 결과 (실제값이 나온 것만)."""
    now = now or pd.Timestamp.now(tz="Asia/Seoul")
    ny = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
    rows = economic(ny) + economic(ny - pd.Timedelta(days=1))
    out, seen = [], set()
    for e in rows:
        if not e["key"] or not e["actual"] or pd.isna(e["ts"]) or (e["event"], e["time_kst"]) in seen:
            continue
        if not (now - pd.Timedelta(hours=hours) <= e["ts"] <= now):
            continue
        seen.add((e["event"], e["time_kst"]))
        out.append({**e, "ko": ko_name(e["event"]), "verdict": verdict(e)})
    # 금리 결정을 맨 위로
    return sorted(out, key=lambda e: ("Interest Rate Decision" not in e["event"], e["ts"]))


def result_lines(results: list[dict]) -> list[str]:
    """텔레그램 HTML 줄."""
    from html import escape as esc
    out = []
    dup = {k for k in (e["ko"] for e in results) if sum(1 for x in results if x["ko"] == k) > 1}
    for e in results:
        v = e["verdict"]
        if e["ko"] in dup and e["ko"] != e["event"]:   # 한국어 이름이 겹치면 원래 이름을 붙임
            e = {**e, "ko": f"{e['ko']} · {e['event']}"}
        if "Interest Rate Decision" in e["event"]:
            out.append(f"🏦 <b>{esc(e['ko'])}: {esc(v or e['actual'])}</b> {esc(e['actual'])}"
                       + (f" (예상 {esc(e['consensus'])}, 이전 {esc(e['previous'])})" if e["consensus"] else "") + f" — {esc(e['time_kst'])}")
        else:
            out.append(f"• {esc(e['ko'])} <b>{esc(e['actual'])}</b>" + (f" (예상 {esc(e['consensus'])})" if e["consensus"] else "")
                       + (f" → <b>{esc(v)}</b>" if v else "") + f"  <i>{esc(e['time_kst'])}</i>")
    return out
