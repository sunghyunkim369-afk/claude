"""주식 뉴스 모으기 (RSS). 제목·출처·시각·링크만 쓰고 본문은 가져오지 않음.

국내: 연합뉴스 경제, 한국경제 증권, 매일경제 증권, Google 뉴스(한국어 검색)
해외: CNBC Markets, MarketWatch, Google 뉴스(영어 검색)
구분(자동, 틀릴 수 있음): 공식 발표 / 보도 / 전망·추측
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def gnews(q: str, lang: str = "ko") -> str:
    loc = "hl=ko&gl=KR&ceid=KR:ko" if lang == "ko" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}+when:1d&{loc}"


FEEDS = {
    "국내": [("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"), ("한국경제", "https://www.hankyung.com/feed/finance"),
             ("매일경제", "https://www.mk.co.kr/rss/50200011/"), ("Google 뉴스", gnews("코스피 OR 코스닥 OR 증시"))],
    "해외": [("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20409666"),
             ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
             ("Google 뉴스", gnews("stock market OR Fed OR earnings OR Nasdaq OR S&P 500", "en"))],
}
# 큰 언론사 (Google 뉴스 출처 이름 기준) — 먼저 보여줌
MAJOR = {"연합뉴스", "한국경제", "매일경제", "매일경제 마켓", "서울경제", "머니투데이", "이데일리", "조선비즈", "조선일보", "중앙일보", "동아일보",
         "KBS 뉴스", "MBC 뉴스", "SBS 뉴스", "연합인포맥스", "뉴스1", "뉴시스", "헤럴드경제", "아시아경제", "파이낸셜뉴스",
         "CNBC", "MarketWatch", "Reuters", "Bloomberg", "Bloomberg.com", "The Wall Street Journal", "WSJ", "Financial Times",
         "Associated Press", "AP News", "Barron's", "Yahoo Finance", "CNN", "The New York Times", "Fox Business", "Investopedia"}
OFFICIAL = re.compile(r"(공시|발표했다|확정|의결|Fed (says|holds|raises|cuts)|announces|reports (first|second|third|fourth)|SEC (charges|approves))", re.I)
SPECUL = re.compile(r"(전망|가능성|관측|우려|예상|할 듯|설\b|루머|could|may|might|expected to|reportedly|rumor|sources say|eyes|weighs)", re.I)
TOPICS = [("금리·연준", r"(금리|연준|FOMC|Fed|Powell|파월|Warsh|워시|rate|Treasury|국채)"),
          ("실적", r"(실적|어닝|영업이익|매출|earnings|revenue|guidance|EPS|quarter)"),
          ("반도체·AI", r"(반도체|HBM|AI|엔비디아|Nvidia|삼성전자|하이닉스|TSMC|chip|semiconductor|Anthropic|OpenAI)"),
          ("환율·달러", r"(환율|원/달러|달러|dollar|yen|엔화|위안)"),
          ("유가·원자재", r"(유가|원유|OPEC|oil|crude|금값|gold|구리|copper)"),
          ("정책·규제", r"(규제|관세|tariff|정부|법안|세제|금융위|SEC|sanction|제재)"),
          ("지정학", r"(전쟁|공습|미사일|중동|이란|이스라엘|우크라이나|러시아|대만|북한|war|strike|missile|Iran|Israel|Ukraine|Taiwan)"),
          ("경제지표", r"(CPI|PCE|고용|실업|GDP|PMI|소비자물가|inflation|jobs|payroll|retail sales)")]


def _parse(xml: bytes, source: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = it.find("source")
        pub = it.findtext("pubDate") or ""
        try:
            t = pd.Timestamp(parsedate_to_datetime(pub)).tz_convert("UTC").tz_localize(None)
        except Exception:
            t = pd.NaT
        name = src.text.strip() if src is not None and src.text else source
        if source == "Google 뉴스" and " - " in title:
            title = title.rsplit(" - ", 1)[0]
        out.append({"title": title, "link": link, "source": name, "time": t})
    return out


def fetch(url: str, source: str, timeout: int = 20) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return _parse(r.content, source)


def kind(title: str, source: str) -> str:
    if SPECUL.search(title):
        return "전망·추측"
    if OFFICIAL.search(title):
        return "공식 발표"
    return "보도"


def topics(title: str) -> list[str]:
    return [n for n, p in TOPICS if re.search(p, title, re.I)]


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", "", t.lower())[:40]


def collect(hours: int = 24, per_group: int = 10, log=print) -> tuple[dict[str, list[dict]], list[str]]:
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    out, errors = {}, []
    for grp, feeds in FEEDS.items():
        items = []
        for name, url in feeds:
            try:
                items += fetch(url, name)
            except Exception as e:
                errors.append(f"{name}({grp}): {type(e).__name__}")
        seen, keep = set(), []
        for i in sorted(items, key=lambda x: x["time"] if pd.notna(x["time"]) else pd.Timestamp(0), reverse=True):
            k = _norm(i["title"])
            if not i["title"] or k in seen or (pd.notna(i["time"]) and now - i["time"] > pd.Timedelta(hours=hours)):
                continue
            seen.add(k)
            i["kind"], i["topics"] = kind(i["title"], i["source"]), topics(i["title"])
            keep.append(i)
        # 큰 언론사·주제가 잡힌 기사 먼저 (시각 순서는 유지), 같은 주제·같은 출처는 3개까지
        keep.sort(key=lambda i: -(2 * (i["source"] in MAJOR) + (len(i["topics"]) > 0)))
        pick, n_topic, n_src = [], {}, {}
        for i in keep:
            t0 = i["topics"][0] if i["topics"] else ""
            if (t0 and n_topic.get(t0, 0) >= 3) or n_src.get(i["source"], 0) >= 3:
                continue
            pick.append(i)
            n_topic[t0] = n_topic.get(t0, 0) + 1
            n_src[i["source"]] = n_src.get(i["source"], 0) + 1
            if len(pick) >= per_group:
                break
        out[grp] = pick
    return out, errors


def stock_news(name: str, lang: str = "ko", n: int = 2) -> list[dict]:
    """종목 관련 최근 뉴스 (Google 뉴스 검색, 최근 1~3일)."""
    try:
        q = f'"{name}"' + (" 주가 OR 실적 OR 공시" if lang == "ko" else " stock")
        items = fetch(gnews(q, lang).replace("when:1d", "when:3d"), "Google 뉴스")
    except Exception:
        return []
    junk = re.compile(r"(Stock Price, News|Stock Quote|Quote & History|Price and Forecast|주가 차트|시세 조회|stock price today)", re.I)
    items = [i for i in items if not junk.search(i["title"])]
    for i in items:
        i["kind"] = kind(i["title"], i["source"])
    return items[:n]
