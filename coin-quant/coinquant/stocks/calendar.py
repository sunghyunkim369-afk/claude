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
    try:
        js = _get(f"https://api.nasdaq.com/api/calendar/economicevents?date={day:%Y-%m-%d}")
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
            kst = (pd.Timestamp(f"{day:%Y-%m-%d} {t}").tz_localize("America/New_York").tz_convert("Asia/Seoul")
                   .strftime("%m/%d %H:%M"))
        except Exception:
            kst = t
        clean = lambda v: html.unescape(str(v or "")).replace("\xa0", " ").strip()  # noqa: E731
        out.append({"time_kst": kst, "country": r.get("country"), "event": name, "actual": clean(r.get("actual")),
                    "consensus": clean(r.get("consensus")), "previous": clean(r.get("previous")),
                    "key": bool(KEY_RE.search(name))})
    return out
