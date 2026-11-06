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


LAST_RAW: dict[str, list[dict]] = {}   # collect 가 고르기 전 전체 기사 (큰 이슈 찾기용)


def collect(hours: int = 24, per_group: int = 10, log=print) -> tuple[dict[str, list[dict]], list[str]]:
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    out, errors = {}, []
    LAST_RAW.clear()
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
        LAST_RAW[grp] = list(keep)
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


OSUN_CHANNEL = "UC_JJ_NhRqPKcIOj5Ko3W_3w"   # 오선의 미국 증시 라이브 (youtube.com/@futuresnow)


def youtube_latest(channel_id: str = OSUN_CHANNEL, hours: int = 30, n: int = 3) -> list[dict]:
    """유튜브 채널 공개 RSS 의 최근 영상 제목·링크·설명 (자막·영상 내용은 가져오지 않음)."""
    ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}
    try:
        r = requests.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", headers=UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    out = []
    for e in root.findall("a:entry", ns):
        t = pd.Timestamp(e.findtext("a:published", "", ns)).tz_convert("UTC").tz_localize(None)
        if now - t > pd.Timedelta(hours=hours):
            continue
        link = e.find("a:link", ns)
        out.append({"title": (e.findtext("a:title", "", ns) or "").strip(), "link": link.get("href") if link is not None else "",
                    "time": t, "source": "오선 라이브", "kind": "영상",
                    "desc": (e.findtext("m:group/m:description", "", ns) or "").strip()})
    return out[:n]


def fed_headlines(n: int = 2) -> list[dict]:
    """금리 결정 관련 최근 기사 (Google 뉴스, 하루 안, 큰 언론사 먼저)."""
    items = []
    for q, lang in (("FOMC 금리 결정", "ko"), ("Fed rate decision", "en")):
        try:
            items += fetch(gnews(q, lang), "Google 뉴스")
        except Exception:
            pass
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    items = [i for i in items if pd.isna(i["time"]) or now - i["time"] <= pd.Timedelta(hours=30)]
    items.sort(key=lambda i: (i["source"] not in MAJOR, -(i["time"].value if pd.notna(i["time"]) else 0)))
    seen, out = set(), []
    for i in items:
        k = _norm(i["title"])
        if k in seen:
            continue
        seen.add(k)
        i["kind"] = kind(i["title"], i["source"])
        out.append(i)
    return out[:n]


def results_block(hours: int = 30) -> list[str]:
    """'📢 어젯밤 발표 결과' 텔레그램 HTML 줄 (주요 지표 실제값 + 금리 결정이면 관련 기사). 없으면 []."""
    from html import escape as esc
    from . import calendar as cal
    try:
        res = cal.recent_results(hours)
    except Exception:
        return []
    if not res:
        return []
    lines = ["<b>📢 어젯밤 발표 결과</b>"] + cal.result_lines(res)
    if any("Interest Rate Decision" in r["event"] for r in res):
        for i in fed_headlines():
            t = esc(i["title"][:60] + ("…" if len(i["title"]) > 60 else ""))
            lines.append(f"📰 <a href=\"{esc(i['link'], quote=True)}\">{t}</a> <i>{esc(i['source'])}</i>")
    return lines


# ---------------------------------------------------------------- 오늘의 큰 이슈: 여러 언론사가 같은 이야기를 한 것
SYN = [(r"data[ -]?cent(er|re)s?", " datacenter "), (r"blue[ -]?wave", " 블루웨이브 "), (r"circuit[ -]?breakers?", " 서킷브레이커 "),
       (r"side[ -]?cars?", " 사이드카 "), (r"margin calls?", " 반대매매 "), (r"rate cuts?", " 금리인하 "), (r"금리 ?인하", " 금리인하 "),
       (r"rate hikes?", " 금리인상 "), (r"금리 ?인상", " 금리인상 "), (r"shut ?down", " 셧다운 "), (r"stablecoins?", " 스테이블코인 "), (r"데이터 ?센터", " 데이터센터 "), (r"\bA\.?I\.?\b", " ai "),
       (r"interest rates?", " rate "), (r"tariffs?", " tariff "), (r"\bFed\b|Federal Reserve", " fed ")]
STOP_EN = set("""stock stocks market markets share shares wall street today new says said after amid with from that this what
will more than over into week year years price prices investor investors trading trade nasdaq dow close closes rise rises fall falls
gain gains drop drops higher lower record earnings report reports quarter could may might its their they have has why how here
about top best buy sell index futures rally slump slide slides jump jumps soar tumble tumbles sink sinks set sets day ahead
first last big biggest high low points point percent amp news live update updates morning evening analyst analysts expected
company companies inc corp group shares the and for are but not you your all any can its was were has had his her how why who
whom when where which while than then them they these those this that there their our out off one two three four five six seven
ten per via vs just still also even ever much many most some such only own same each other again against before during until
between under above below down up into onto upon been being did does doing done get gets got make makes made take takes took
see sees seen say says said look looks know knows think thinks want wants need needs use uses used come comes came keep keeps
here now next back way ways time times people world global us u.s america american could should would might must shall plan
plans deal deals move moves amid ahead near nearly almost still yet worst best since biggest largest latest lowest highest
month months day days week weeks hour hours decade ever history session sessions straight streak consecutive late early
morning afternoon tonight tomorrow yesterday open opens opening close closing level levels mark marks""".split())
STOP_KO = set("""증시 코스피 코스닥 주가 상승 하락 시장 투자 오늘 마감 전망 종목 관련 기업 발표 급등 급락 강세 약세 국내 미국 투자자
속보 이번 지난 올해 내년 하루 연속 최대 최고 최저 장중 개장 출발 기록 이후 우려 기대 가능성 영향 분석 의견 주식 증권 지수
개월 이틀간 사흘간 종합 단독 오전 오후 최근 대비 이상 이하 만에 처음 사상 역대 붕괴 폭락 쇼크 추락 패닉 반등 급반등 회복 하락세 상승세
공방 마감시황 시황 뉴욕 서울 한국 정부 대통령 발언 계획 추진 예정 확대 축소 강화 완화 결정 검토 도입 시작 진행 결과 상황 문제 이유
가운데 때문 위해 통해 대한 관련해 따르면 밝혀 밝혔다 있다 없다 했다 된다 한다 등 것 수 및""".split())
KO_LABEL = {"datacenter": "데이터센터", "fed": "연준", "tariff": "관세", "rate": "금리", "ai": "AI", "oil": "유가", "china": "중국",
            "nvidia": "엔비디아", "apple": "애플", "tesla": "테슬라", "bitcoin": "비트코인", "inflation": "물가", "jobs": "고용",
            "election": "선거", "midterm": "중간선거", "midterms": "중간선거", "democrats": "민주당", "congress": "의회", "iran": "이란",
            "chip": "반도체", "chips": "반도체", "semiconductor": "반도체", "treasury": "미 국채", "yields": "국채금리", "yield": "국채금리"}
_KO_TAIL = re.compile(r"(으로|에서|에게|까지|부터|이며|이고|은|는|이|가|을|를|에|의|로|도|과|와|만)$")


def _tokens(title: str) -> set[str]:
    t = title
    for pat, rep in SYN:
        t = re.sub(pat, rep, t, flags=re.I)
    toks = set()
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", t):
        w = w.lower().strip("-")
        if len(w) > 4 and w.endswith("s") and w not in ("congress", "midterms", "yields", "chips", "jobs", "democrats"):
            w = w[:-1]
        if w not in STOP_EN and len(w) >= 3:
            toks.add(KO_LABEL.get(w, w))          # 영어·한국어 같은 이야기를 한 단어로
    for w in re.findall(r"[가-힣]{2,}", t):
        w = _KO_TAIL.sub("", w)
        if len(w) >= 2 and w not in STOP_KO:
            toks.add(w)
    return toks


def coherent_terms(w: str, termsets: list[set]) -> bool:
    """같은 이야기인지: 긴 고유한 말(블루웨이브·데이터센터 등)이거나, 그 기사들 40% 이상이 다른 단어 하나를 더 같이 씀."""
    if (re.fullmatch(r"[가-힣]+", w) and len(w) >= 4) or (re.fullmatch(r"[a-z]+", w) and len(w) >= 8):
        return True
    co: dict[str, int] = {}
    for ts in termsets:
        for x in ts - {w}:
            co[x] = co.get(x, 0) + 1
    return bool(co) and max(co.values()) >= max(2, 0.4 * len(termsets))


def big_issues(groups: dict[str, list[dict]] | None = None, n: int = 3, min_sources: int = 3) -> list[dict]:
    """같은 단어를 서로 다른 언론사 min_sources 곳 이상이 제목에 쓴 이야기 = 큰 이슈. [{"label","sources","item","count"}]"""
    groups = groups if groups is not None else LAST_RAW
    items = [i for g in groups.values() for i in g if i.get("title")]
    for i in items:
        i["_tok"] = _tokens(i["title"])
    used, out = set(), []
    for _ in range(n):
        df: dict[str, set] = {}
        for k, i in enumerate(items):
            if k in used:
                continue
            for w in i["_tok"]:
                df.setdefault(w, set()).add(i.get("source", ""))
        def coherent(w):   # 같은 이야기인지: 긴 고유한 말(블루웨이브·데이터센터 등)이거나, 그 기사들 40% 이상이 다른 단어 하나를 더 같이 씀
            if (re.fullmatch(r"[가-힣]+", w) and len(w) >= 4) or (re.fullmatch(r"[a-z]+", w) and len(w) >= 8):
                return True
            ix = [k for k, i in enumerate(items) if k not in used and w in i["_tok"]]
            co: dict[str, int] = {}
            for k in ix:
                for x in items[k]["_tok"] - {w}:
                    co[x] = co.get(x, 0) + 1
            return bool(co) and max(co.values()) >= max(2, 0.4 * len(ix))
        cand = sorted(((len(v), sum(1 for s_ in v if s_ in MAJOR), w) for w, v in df.items() if len(v) >= min_sources), reverse=True)
        pick = next((c for c in cand if coherent(c[2])), None)
        if pick is None:
            break
        cnt, maj, w = pick
        idx = [k for k, i in enumerate(items) if k not in used and w in i["_tok"]]
        best = sorted(idx, key=lambda k: (items[k].get("source") not in MAJOR,
                                          -(items[k]["time"].value if pd.notna(items[k].get("time")) else 0)))[0]
        used.update(idx)
        co: dict[str, int] = {}
        for k in idx:                             # 같이 많이 나온 단어 하나를 붙여 맥락 (예: 데이터센터 · 민주당)
            for x in items[k]["_tok"] - {w}:
                co[x] = co.get(x, 0) + 1
        second = max(co, key=co.get) if co and max(co.values()) >= 2 else None
        out.append({"label": w + (f" · {second}" if second else ""), "sources": cnt, "count": len(idx), "item": items[best]})
    return out


def issue_lines(issues: list[dict], title: str = "🔥 오늘의 큰 이슈") -> list[str]:
    """텔레그램 HTML 줄."""
    from html import escape as esc
    if not issues:
        return []
    L = [f"<b>{title}</b>  <i>여러 언론사가 같이 다룬 이야기</i>"]
    for x in issues:
        i = x["item"]
        t = esc(i["title"][:70] + ("…" if len(i["title"]) > 70 else ""))
        link = f"<a href=\"{esc(i['link'], quote=True)}\">{t}</a>" if i.get("link") else t
        L.append(f"• <b>{esc(x['label'])}</b> (언론 {x['sources']}곳) — {link} <i>{esc(i.get('source', ''))}</i>")
    return L
