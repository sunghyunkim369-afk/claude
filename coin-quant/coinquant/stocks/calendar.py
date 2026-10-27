"""실적 발표·경제지표 일정 (나스닥 공개 캘린더). 접속이 막히면 빈 목록.

시각: 실적은 '장 전/장 후', 경제지표는 미국 동부시간 → 한국시간으로 바꿔 표시.
"""
from __future__ import annotations

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
TIME_KR = {"time-pre-market": "장 전 (한국 밤~새벽)", "time-after-hours": "장 후 (한국 새벽~아침)", "time-not-supplied": "시각 미정"}
KEY_EVENTS = ("CPI", "PCE", "Nonfarm", "Unemployment", "GDP", "FOMC", "Fed", "Retail Sales", "ISM", "PPI", "Jobless",
              "JOLTS", "Consumer Confidence", "Michigan", "Durable", "Housing Starts", "Beige", "Powell", "Interest Rate")


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
        try:
            kst = (pd.Timestamp(f"{day:%Y-%m-%d} {t}") + pd.Timedelta(hours=9)).strftime("%m/%d %H:%M")   # GMT → KST
        except Exception:
            kst = t
        out.append({"time_kst": kst, "country": r.get("country"), "event": name, "actual": r.get("actual", ""),
                    "consensus": r.get("consensus", ""), "previous": r.get("previous", ""),
                    "key": any(k.lower() in name.lower() for k in KEY_EVENTS)})
    return out
