"""오늘의 이슈: RSS 로 모아 3~5개 고르기.

- 출처 링크는 항상 붙입니다.
- '확인된 사실 / 보도 / 추측·루머' 구분은 **자동 분류(표현·출처 기준)** 입니다. 틀릴 수 있으니 링크로 확인하세요.
  · 확인된 사실: 공식 기관·거래소 발표 (SEC, 연준, 업비트 공지 등)
  · 추측·루머: 제목에 '루머, 소식통, 가능성, 전망, reportedly, could, rumor' 같은 표현
  · 보도: 그 밖의 언론 기사 (사실 보도지만 1차 출처 확인 필요)
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

FEEDS = [
    # (이름, 주소, 공식 발표 여부)
    ("SEC 보도자료", "https://www.sec.gov/news/pressreleases.rss", True),
    ("연준(Fed) 발표", "https://www.federalreserve.gov/feeds/press_all.xml", True),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", False),
    ("Cointelegraph", "https://cointelegraph.com/rss", False),
    ("Decrypt", "https://decrypt.co/feed", False),
    ("The Block", "https://www.theblock.co/rss.xml", False),
    ("블록미디어", "https://www.blockmedia.co.kr/feed", False),
]
UPBIT_NOTICE = "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=all"

CATEGORIES = {
    "규제": ["sec", "regulat", "lawsuit", "court", "ban", "license", "cftc", "법안", "규제", "금융위", "금감원", "제재", "소송", "mica"],
    "ETF 자금": ["etf", "inflow", "outflow", "blackrock", "ibit", "fidelity", "자금 유입", "자금 유출", "현물 etf"],
    "거래소 사고·상장폐지": ["delist", "suspend", "halt", "outage", "insolv", "bankrupt", "withdrawal", "상장폐지", "유의종목",
                     "거래 지원 종료", "입출금 중단", "파산", "출금"],
    "금리·물가": ["fomc", "fed", "rate cut", "rate hike", "cpi", "inflation", "powell", "pce", "jobs report", "금리", "물가", "연준", "고용"],
    "해킹": ["hack", "exploit", "drain", "stolen", "breach", "attack", "해킹", "탈취", "유출", "공격"],
    "상장": ["listing", "lists", "상장", "거래 지원"],
}
RUMOR = ["rumor", "rumour", "reportedly", "sources say", "source says", "could ", "may ", "might ", "speculat", "unconfirmed",
         "expected to", "considering", "mulls", "eyes ", "루머", "소식통", "가능성", "전망", "관측", "알려졌", "할 듯", "검토", "예상"]

COIN_NAMES = {
    "BTC": ["bitcoin", "btc", "비트코인"], "ETH": ["ethereum", "ether ", "eth", "이더리움"], "SOL": ["solana", "sol ", "솔라나"],
    "XRP": ["xrp", "ripple", "리플"], "ADA": ["cardano", "카르다노", "에이다"], "DOGE": ["dogecoin", "doge", "도지"],
    "LINK": ["chainlink", "체인링크"], "AVAX": ["avalanche", "아발란체"], "DOT": ["polkadot", "폴카닷"], "TRX": ["tron", "트론"],
    "SUI": ["sui ", "수이"], "TON": ["toncoin", "ton ", "톤코인"], "USDT": ["tether", "테더"], "USDC": ["usdc", "circle"],
}


def _text(el, tag):
    x = el.find(tag)
    return html.unescape(x.text or "").strip() if x is not None and x.text else ""


def fetch_feed(name: str, url: str, official: bool, timeout=20) -> list[dict]:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "coin-quant/1.0 (personal research)"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = root.iter("item")
    out = []
    for it in items:
        title = _text(it, "title")
        link = _text(it, "link")
        pub = _text(it, "pubDate")
        desc = re.sub("<[^>]+>", "", _text(it, "description"))[:300]
        try:
            t = parsedate_to_datetime(pub).astimezone(tz=None)
            t = pd.Timestamp(t).tz_convert("UTC").tz_localize(None)
        except Exception:
            t = pd.NaT
        out.append({"source": name, "title": title, "link": link, "time": t, "summary": desc, "official": official})
    return out


def fetch_upbit_notices() -> list[dict]:
    """업비트 공지 (공식 API 문서에 없는 주소라 바뀌면 조용히 빠짐)."""
    r = requests.get(UPBIT_NOTICE, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json().get("data", {})
    rows = data.get("notices") or data.get("list") or []
    out = []
    for n in rows:
        nid = n.get("id")
        out.append({"source": "업비트 공지", "title": n.get("title", ""),
                    "link": f"https://upbit.com/service_center/notice?id={nid}",
                    "time": pd.Timestamp(n.get("listed_at") or n.get("created_at")).tz_convert("UTC").tz_localize(None)
                    if (n.get("listed_at") or n.get("created_at")) else pd.NaT,
                    "summary": "", "official": True})
    return out


def classify(item: dict) -> dict:
    t = (item["title"] + " " + item.get("summary", "")).lower()
    title = item["title"].lower() + " "
    cats = [c for c, kws in CATEGORIES.items() if any(k in t for k in kws)]
    coins = [c for c, kws in COIN_NAMES.items() if any(k in f" {t} " for k in kws)]
    if item.get("official"):
        kind = "확인된 사실(공식 발표)"
    elif any(k in title for k in RUMOR):
        kind = "추측·루머(표현 기준 자동 분류)"
    else:
        kind = "보도(1차 출처 확인 필요)"
    return {**item, "categories": cats, "coins": coins, "kind": kind}


def collect(max_items: int = 5, hours: int = 36, log=print) -> tuple[list[dict], list[str]]:
    items, errors = [], []
    for name, url, official in FEEDS:
        try:
            items += fetch_feed(name, url, official)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
    try:
        items += fetch_upbit_notices()
    except Exception as e:
        errors.append(f"업비트 공지: {type(e).__name__}")
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    items = [classify(i) for i in items if i["title"]]
    recent = [i for i in items if pd.isna(i["time"]) or (now - i["time"]) <= pd.Timedelta(hours=hours)]
    # 공식 기관 발표는 코인 관련일 때만
    recent = [i for i in recent if not (i["source"] in ("SEC 보도자료", "연준(Fed) 발표") and not i["categories"])
              and (i["categories"] or i["coins"])]

    def score(i):
        s = 2 * len(i["categories"]) + len(i["coins"]) + (1 if any(c in ("BTC", "ETH", "SOL") for c in i["coins"]) else 0)
        s += 2 if i["official"] else 0
        s += 1 if any(c in i["categories"] for c in ("해킹", "거래소 사고·상장폐지", "규제")) else 0
        return s

    recent.sort(key=lambda i: (score(i), i["time"] if not pd.isna(i["time"]) else pd.Timestamp(0)), reverse=True)
    picked, seen_titles, cat_count = [], set(), {}
    for i in recent:
        key = re.sub(r"\W+", "", i["title"].lower())[:40]
        if key in seen_titles:
            continue
        main = i["categories"][0] if i["categories"] else "기타"
        if cat_count.get(main, 0) >= 2:      # 한 주제만 몰리지 않게
            continue
        picked.append(i)
        seen_titles.add(key)
        cat_count[main] = cat_count.get(main, 0) + 1
        if len(picked) >= max_items:
            break
    if errors:
        log("  뉴스 일부 출처 실패: " + ", ".join(errors))
    return picked, errors
