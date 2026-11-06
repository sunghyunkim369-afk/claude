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


_AMBIG = set("""nasdaq dow target visa block match news fox ball gap public equity booking progressive snap general united southern
               republic waste corning edison hess cboe intuit ross pool best steel nucor sysco tapestry gen digital realty prudential
               lennar paychex aflac humana chubb loews masco textron ameren mosaic halliburton jacobs leidos regency kellanova
               genuine parts moody idex nordson ppl nvr host dover fastenal""".split())


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
            if (k, tk) in seen or len(k) < 3 or k in _AMBIG:
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
                ents: list | None = None, day: str | None = None, max_n: int = MAX_ISSUES,
                closes: dict[str, pd.Series] | None = None, context: dict[str, str] | None = None) -> tuple[list[dict], dict[str, int]]:
    """(이슈 목록, 오늘 말별 언론사 수). hist 에 오늘 날짜는 빼고 평소를 계산.
    closes: {기호: 가격 기록} — 있으면 '과거 비슷한 움직임 뒤' 통계. context: {기호: '하락 추세 10일째' 같은 설명}."""
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
        iss = {"label": disp(w) + (f" · {disp(second)}" if second else ""), "sources": len(srcs), "burst": b if hist_ok else None,
               "impact": sorted(imp, key=lambda x: -abs(x[3]))[:3], "score": float(score), "item": X[best], "count": len(ix),
               "titles": [X[k]["title"] for k in ix][:12], "srcset": srcs, "_imp": imp}
        out.append(iss)
    out.sort(key=lambda x: -x["score"])
    out = merge_events(out)
    shown: set[str] = set()
    for x in out:                                      # 묶은 뒤에 생각할 거리 계산, 같은 줄은 한 번만
        keep = []
        for t in insights(x, x["_imp"], moves, closes or {}, context or {}):
            key = re.sub(r"[\d.%+−-]", "", t)[:40]
            if key not in shown:
                shown.add(key)
                keep.append(t)
        x["insights"] = keep
    return out, counts


def merge_events(issues: list[dict]) -> list[dict]:
    """같은 지표(예: 코스피)를 크게(평소의 2.5배 이상) 움직인 이야기들은 한 사건으로.
    대표 기사는 가장 많은 언론이 다룬 묶음에서, 나머지 묶음은 '관련'(원인·장치·대응 같은 다른 각도)으로."""
    groups: dict[str, list[dict]] = {}
    order: list = []
    for x in issues:
        top = max(x["impact"], key=lambda m: abs(m[3]), default=None)
        big = top and (abs(top[3]) >= 2 or (top[1] not in LEVEL and abs(top[2]) >= 0.03))   # 평소의 2배 이상 또는 하루 3% 이상
        key = top[1] if big else id(x)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(x)
    merged = []
    for key in order:
        g = groups[key]
        if len(g) == 1 and isinstance(key, int):
            merged.append(g[0])
            continue
        g = sorted(g, key=lambda x: -len(x["srcset"]))
        head = dict(g[0])
        top = max(head["impact"], key=lambda m: abs(m[3]))
        head["event"] = f"{top[0]} {top[2]:+.1%}" if top[1] not in LEVEL else f"{top[0]} {top[2]:+.2f}p"
        head["angles"] = [(x["label"], x["item"]) for x in g[1:]]
        head["srcset"] = set().union(*[x["srcset"] for x in g])
        head["sources"] = len(head["srcset"])
        seen_i, imp = set(), []
        for x in g:
            for m in x["_imp"]:
                if m[1] not in seen_i:
                    seen_i.add(m[1])
                    imp.append(m)
        head["_imp"] = imp
        head["impact"] = sorted(imp, key=lambda m: -abs(m[3]))[:3]
        head["titles"] = [t for x in g for t in x["titles"]][:12]
        head["score"] = max(x["score"] for x in g)
        merged.append(head)
    return sorted(merged, key=lambda x: -x["score"])


CORE_MKT = [("^KS11", "코스피"), ("^GSPC", "S&P 500"), ("^IXIC", "나스닥"), ("BTC-USD", "비트코인")]


def analog(s: pd.Series, level: bool, z_now: float, horizon: int = 20) -> dict | None:
    """과거에 하루 움직임이 지금과 같은 방향으로 z 의 75% 이상이었던 날(5일 안 중복 제외) → horizon 거래일 뒤."""
    s = s.dropna()
    if len(s) < 300:
        return None
    r = s.diff() if level else s.pct_change()
    z = r / r.rolling(60, min_periods=40).std().shift(1)
    days, thr = [], None
    for frac in (0.75, 0.6, 0.5, 0.4):          # 비슷한 날이 5번 이상 나올 때까지 조금씩 넓힘 (평소의 2배 아래로는 안 내림)
        thr = frac * z_now
        if abs(thr) < 2:
            thr = 2.0 if z_now > 0 else -2.0
        hit = ((z <= thr) if z_now < 0 else (z >= thr)) & (np.arange(len(s)) < len(s) - horizon - 1)
        days, last = [], -99
        for i in np.flatnonzero(hit.to_numpy()):
            if i - last > 5:
                days.append(i)
                last = i
        if len(days) >= 5 or abs(thr) <= 2:
            break
    if len(days) < 5:
        return None
    fwd = np.array([(s.iat[i + horizon] - s.iat[i]) if level else (s.iat[i + horizon] / s.iat[i] - 1) for i in days])
    return {"n": len(days), "med": float(np.median(fwd)), "up": float((fwd > 0).mean()), "worst": float(fwd.min()),
            "since": s.index[0].year, "thr": abs(float(thr))}


def insights(iss: dict, imp: list, moves: dict, closes: dict, context: dict) -> list[str]:
    """데이터로 만든 '생각할 거리' (HTML, 최대 3줄). 새 사실을 만들지 않고 숫자 비교만."""
    from html import escape as esc
    L = []
    big = [x for x in imp if abs(x[3]) >= 2]
    # (1) 과거 비슷한 움직임 뒤
    for nm, code, r, z in sorted(big, key=lambda x: -abs(x[3]))[:1]:
        if code in closes:
            a = analog(closes[code], code in LEVEL, z)
            if a:
                unit = (lambda v: f"{v:+.2f}p") if code in LEVEL else (lambda v: f"{v:+.1%}")
                L.append(f"과거 {esc(nm)}: 하루에 평소 변동의 {a['thr']:.1f}배 넘게 {'오른' if z > 0 else '빠진'} 날 {a['n']}번({a['since']}~) → "
                         f"20거래일 뒤 오른 비율 {a['up']:.0%}, 중앙 {unit(a['med'])}, 최악 {unit(a['worst'])}")
    # (2) 언론 반응 vs 시장 반응이 엇갈림
    zmax = max((abs(x[3]) for x in imp), default=0.0)
    if iss["sources"] >= 8 and imp and zmax < 1.0:
        L.append("엇갈림: 언론은 크게 다뤘는데 관련 시장은 평소 수준으로만 움직임 — 이미 가격에 반영됐거나, 시장이 덜 중요하게 본다는 뜻일 수 있음")
    if iss["sources"] <= 3 and zmax >= 4:
        L.append("엇갈림: 시장은 크게 움직였는데 다룬 언론은 적음 — 원인이 아직 덜 알려졌을 수 있음")
    # (3) 같은 이야기 안에서 오른 쪽·내린 쪽
    ups = [x for x in imp if x[3] >= 1.5 and x[1] not in LEVEL]
    dns = [x for x in imp if x[3] <= -1.5 and x[1] not in LEVEL]
    if ups and dns:
        L.append("득실이 갈림: " + ", ".join(f"{esc(n)} {r:+.1%}" for n, _, r, _ in ups[:2]) + " ↔ " +
                 ", ".join(f"{esc(n)} {r:+.1%}" for n, _, r, _ in dns[:2]) + " — 누가 이득·손해인지가 이 이야기의 핵심")
    # (4) 나라별 반응 차이 (이 이야기가 한쪽 시장에만 큰지)
    core = [(nm, code, *moves[code]) for code, nm in CORE_MKT if code in moves]
    mine = {x[1] for x in big}
    hi = max([c for c in core if c[1] in mine], key=lambda x: abs(x[3]), default=None)
    others = [c for c in core if c[1] not in mine]
    if hi and others:
        lo = min(others, key=lambda x: abs(x[3]))
        if abs(hi[3]) >= 2.5 and abs(lo[3]) < 1:
            L.append(f"시장별 차이: {esc(hi[0])} {hi[2]:+.1%}(평소의 {abs(hi[3]):.1f}배) vs {esc(lo[0])} {lo[2]:+.1%} — 같은 뉴스에 반응 크기가 다른 이유는?")
    # (5) 추세 맥락 — 과거 통계 다음으로 보여 줌
    for nm, code, r, z in big:
        if code in context:
            L.insert(1 if L and L[0].startswith("과거") else 0, f"추세: {esc(nm)} {esc(context[code])} (이 프로그램의 지수 추세 신호)")
            break
    return L[:3]


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
        lab = " · ".join(p for p in x["label"].split(" · ") if not (x.get("event") and x["event"].startswith(p)))
        head = (f"{x['event']} · {lab}" if lab else x["event"]) if x.get("event") else x["label"]
        L.append(f"• <b>{esc(head)}</b> ({why}) — {link} <i>{esc(i.get('source', ''))}</i>")
        for lab, it in x.get("angles", [])[:3]:
            tt = esc(it["title"][:55] + ("…" if len(it["title"]) > 55 else ""))
            lk = f"<a href=\"{esc(it['link'], quote=True)}\">{tt}</a>" if it.get("link") else tt
            L.append(f"   ↳ 관련: <b>{esc(lab)}</b> — {lk}")
        if x["impact"]:
            mv = [f"{esc(nm)} {r:+.2f}p" if code in LEVEL else f"{esc(nm)} {r:+.1%}" for nm, code, r, z in x["impact"] if abs(z) >= 1.5]
            if mv:
                L.append("   ↳ 시장: " + ", ".join(mv))
        for t in (x.get("insights") or [])[:2] + (x.get("ai") or [])[:2]:
            L.append(f"   ↳ {t}")
    return L
