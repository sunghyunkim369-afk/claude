"""오늘의 큰 이슈 찾기 — 기사 제목의 '평소 대비 급증' + 실제 시장 움직임. 날마다 중요한 게 다르고, 개수도 다름(0~5개).

1. 평소와 비교: 최근 30일 동안 각 말이 하루에 몇 곳 언론사 제목에 나왔는지 기록해 두고, 오늘 평소의 몇 배인지 봄.
   매일 나오는 말(the·증시·금리 등)은 배수가 1 근처라 저절로 걸러짐. 기록이 3일 미만이면 흔한 말 목록 + '같은 이야기 확인'으로 대신함.
2. 같은 이야기 묶기: 가장 점수가 높은 말을 쓴 기사들 + 같은 회사를 언급한 기사들을 한 묶음으로. 영어·한국어는 같은 뜻 말(데이터센터 등)과
   같은 회사(엔티티)로 이어짐.
3. 시장이 실제로 움직였나: 묶음이 언급한 회사·지표(유가·금리·환율·코스피·나스닥·반도체·비트코인·금·VIX)의 오늘 움직임이
   최근 60일 하루 변동의 몇 배(z)인지.
4. 점수 = log2(1 + 언론사 수(큰 언론사는 2배)) × (1 + 0.5·log2(평소 대비 배수)) × (1 + 움직임 z/3).
   언론사 3곳 이상 + 점수 3 이상만, 높은 순 최대 5개. 조용한 날은 0개.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .news import MAJOR, _norm, _tokens, coherent_terms

HIST_DAYS = 30
MIN_SOURCES = 3
MIN_SCORE = 3.0
MAX_ISSUES = 5
THEMES = [  # (제목 정규식, 지표 기호, 표시 이름)
    (r"\boil\b|crude|opec|유가|원유", "CL=F", "유가"),
    (r"\bfed\b|연준|\brates?\b|금리|yields?|국채|treasur", "^TNX", "미 10년 금리"),
    (r"dollar|\bwon\b|환율|원/달러|원화|달러", "KRW=X", "원/달러"),
    (r"bitcoin|crypto|비트코인|가상자산|코인", "BTC-USD", "비트코인"),
    (r"kospi|kosdaq|코스피|코스닥|서킷브레이커|사이드카|반대매매", "^KS11", "코스피"),
    (r"nasdaq|나스닥|tech stocks|기술주", "^IXIC", "나스닥"),
    (r"chip|semiconductor|반도체|엔비디아|nvidia|hbm", "^SOX", "반도체 지수"),
    (r"\bgold\b|금값|금 가격", "GC=F", "금"),
    (r"\bvix\b|panic|selloff|sell-off|패닉|공포", "^VIX", "VIX"),
    (r"s&p ?500", "^GSPC", "S&P 500"),
]
LEVEL = {"^TNX", "^VIX"}   # 수준 지표는 % 대신 변화폭으로
ALIASES = {"GOOGL": ["google", "alphabet", "구글"], "META": ["meta platforms", "facebook", "메타"], "NVDA": ["nvidia", "엔비디아"],
           "AAPL": ["apple", "애플"], "MSFT": ["microsoft", "마이크로소프트"], "AMZN": ["amazon", "아마존"], "TSLA": ["tesla", "테슬라"],
           "AMD": ["amd"], "INTC": ["intel", "인텔"], "AVGO": ["broadcom", "브로드컴"], "ORCL": ["oracle", "오라클"], "NFLX": ["netflix", "넷플릭스"],
           "PLTR": ["palantir", "팔란티어"], "NOW": ["servicenow"], "MU": ["micron", "마이크론"], "TSM": ["tsmc"], "JPM": ["jpmorgan", "jp모건"]}
_SUFFIX = re.compile(r",?\s+(inc\.?|corp\.?|corporation|company|co\.?|holdings?|group|ltd\.?|plc|n\.v\.|s\.a\.|incorporated|class [abc]|\(the\)|& co\.?)$", re.I)
_GENERIC = set("""american general united first international global digital national new energy financial capital health
               data public service services systems technologies technology the bank southern western eastern northern""".split())


def entities(names: list[tuple[str, str]]) -> list[tuple[re.Pattern, str, str]]:
    """[(정규식, 기호, 표시 이름)] — 회사 이름이 제목에 나오면 그 회사의 오늘 움직임을 볼 수 있게."""
    out, seen = [], set()
    for tk, name in names:
        n = str(name).strip()
        for _ in range(3):
            n = _SUFFIX.sub("", n).strip()
        keys = set(ALIASES.get(tk, []))
        if re.search(r"[가-힣]", n):
            if len(n) >= 3:
                keys.add(n)
        elif n:
            keys.add(n.lower())
            first = n.split()[0].lower()
            if len(n.split()) > 1 and len(first) >= 5 and first not in _GENERIC:
                keys.add(first)
        for k in keys:
            if (k, tk) in seen or len(k) < 3:
                continue
            seen.add((k, tk))
            pat = re.compile(re.escape(k) if re.search(r"[가-힣]", k) else r"\b" + re.escape(k) + r"\b", re.I)
            out.append((pat, tk, name if not re.search(r"[가-힣]", k) else k))
    return out


def moves_from(close: pd.DataFrame, level: set[str] = LEVEL) -> dict[str, tuple[float, float]]:
    """{기호: (오늘 변화, z)} — z = 오늘 변화 ÷ 최근 60일 하루 변화의 표준편차."""
    out = {}
    for t in close.columns:
        s = close[t].dropna()
        if len(s) < 30:
            continue
        r = s.diff() if t in level else s.pct_change()
        r = r.dropna()
        sd = r.iloc[-61:-1].std()
        if sd and sd > 0 and not np.isnan(r.iloc[-1]):
            out[t] = (float(r.iloc[-1]), float(r.iloc[-1] / sd))
    return out


def load_hist(path: Path) -> dict[str, dict[str, int]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_hist(path: Path, hist: dict, day: str, counts: dict[str, int]) -> None:
    hist = {d: v for d, v in hist.items() if d != day}
    hist[day] = {k: v for k, v in counts.items() if v >= 1}
    keep = sorted(hist)[-HIST_DAYS:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({d: hist[d] for d in keep}, ensure_ascii=False), encoding="utf-8")


def find_issues(items: list[dict], hist: dict[str, dict[str, int]] | None = None, moves: dict | None = None,
                ents: list | None = None, day: str | None = None, max_n: int = MAX_ISSUES) -> tuple[list[dict], dict[str, int]]:
    """(이슈 목록, 오늘 말별 언론사 수). hist 에 오늘 날짜는 빼고 평소를 계산."""
    hist = {d: v for d, v in (hist or {}).items() if d != day}
    moves, ents = moves or {}, ents or []
    seen, X = set(), []
    for i in items:
        k = _norm(i.get("title", ""))
        if not k or k in seen:
            continue
        seen.add(k)
        t = i["title"]
        terms = set(_tokens(t))
        ent = {tk for pat, tk, _ in ents if pat.search(t)}
        terms |= {"@" + e for e in ent}
        themes = {code for pat, code, _ in THEMES if re.search(pat, t, re.I)}
        X.append({**i, "_terms": terms, "_ent": ent, "_themes": themes})
    src: dict[str, set] = {}
    for x in X:
        for w in x["_terms"]:
            src.setdefault(w, set()).add(x.get("source", ""))
    counts = {w: len(v) for w, v in src.items()}
    hist_ok = len(hist) >= 3
    base = {w: np.mean([h.get(w, 0) for h in hist.values()]) for w in counts} if hist_ok else {}
    burst = lambda w: (counts[w] + 0.5) / (base.get(w, 0) + 0.5) if hist_ok else 1.0  # noqa: E731
    name_of = {tk: nm for _, tk, nm in ents}
    used, out = set(), []
    while len(out) < max_n:
        left = [k for k in range(len(X)) if k not in used]
        s2: dict[str, set] = {}
        for k in left:
            for w in X[k]["_terms"]:
                s2.setdefault(w, set()).add(X[k].get("source", ""))
        cand = []
        for w, v in s2.items():
            if len(v) < MIN_SOURCES:
                continue
            b = burst(w)
            if hist_ok and b < 1.8 and not w.startswith("@"):
                continue                                   # 평소에도 이만큼 나오는 말
            cov = len(v) + sum(1 for s_ in v if s_ in MAJOR)
            cand.append((cov * max(b, 1.0), cov, b, w))
        cand.sort(reverse=True)
        pick = None
        for _, cov, b, w in cand:
            ix = [k for k in left if w in X[k]["_terms"]]
            if hist_ok or w.startswith("@") or coherent_terms(w, [X[k]["_terms"] for k in ix]):
                pick = (cov, b, w, ix)
                break
        if pick is None:
            break
        cov, b, w, ix = pick
        ents_c = set().union(*[X[k]["_ent"] for k in ix])
        ix = sorted(set(ix) | {k for k in left if X[k]["_ent"] & ents_c})          # 같은 회사 기사도 묶음에
        used.update(ix)
        srcs = {X[k].get("source", "") for k in ix}
        cov = len(srcs) + sum(1 for s_ in srcs if s_ in MAJOR)
        # 시장 움직임: 묶음에서 2번 이상 나온 회사, 30% 이상에 나온 지표
        imp = []
        for e in ents_c:
            if sum(1 for k in ix if e in X[k]["_ent"]) >= min(2, len(ix)) and e in moves:
                imp.append((name_of.get(e, e), e, *moves[e]))
        for pat, code, nm in THEMES:
            if sum(1 for k in ix if code in X[k]["_themes"]) >= max(1, 0.3 * len(ix)) and code in moves:
                imp.append((nm, code, *moves[code]))
        z = max((abs(x[3]) for x in imp), default=0.0)
        score = np.log2(1 + cov) * (1 + 0.5 * np.log2(max(b, 1.0))) * (1 + min(z, 6) / 3)
        if score < MIN_SCORE:
            continue
        co: dict[str, int] = {}
        for k in ix:
            for t in X[k]["_terms"] - {w}:
                co[t] = co.get(t, 0) + 1
        second = max(co, key=lambda t: (co[t] * max(burst(t) if t in counts else 1, 1), t)) if co and max(co.values()) >= 2 else None
        disp = lambda t: name_of.get(t[1:], t[1:]) if t.startswith("@") else t  # noqa: E731
        best = sorted(ix, key=lambda k: (X[k].get("source") not in MAJOR,
                                         -(X[k]["time"].value if pd.notna(X[k].get("time")) else 0)))[0]
        out.append({"label": disp(w) + (f" · {disp(second)}" if second else ""), "sources": len(srcs), "burst": b if hist_ok else None,
                    "impact": sorted(imp, key=lambda x: -abs(x[3]))[:3], "score": float(score), "item": X[best], "count": len(ix)})
    out.sort(key=lambda x: -x["score"])
    return out, counts


def issue_lines(issues: list[dict], title: str = "🔥 오늘의 큰 이슈") -> list[str]:
    from html import escape as esc
    if not issues:
        return []
    L = [f"<b>{title}</b>  <i>평소보다 많이 다뤄졌거나 시장이 크게 움직인 이야기 · {len(issues)}개</i>"]
    for x in issues:
        i = x["item"]
        t = esc(i["title"][:70] + ("…" if len(i["title"]) > 70 else ""))
        link = f"<a href=\"{esc(i['link'], quote=True)}\">{t}</a>" if i.get("link") else t
        why = f"언론 {x['sources']}곳" + (f" · 평소의 {x['burst']:.0f}배" if x.get("burst") and x["burst"] >= 2 else "")
        L.append(f"• <b>{esc(x['label'])}</b> ({why}) — {link} <i>{esc(i.get('source', ''))}</i>")
        if x["impact"]:
            mv = [f"{esc(nm)} {r:+.2f}p" if code in LEVEL else f"{esc(nm)} {r:+.1%}" for nm, code, r, z in x["impact"] if abs(z) >= 1.5]
            if mv:
                L.append("   ↳ 시장: " + ", ".join(mv))
    return L
