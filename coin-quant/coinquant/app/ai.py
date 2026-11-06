"""뉴스 AI 요약 (선택) — Claude API 키(ANTHROPIC_API_KEY)가 있을 때만 동작, 없으면 None.

- 주어진 기사 제목·출처·구분만 근거로 한국어 요약 (새 사실·숫자를 만들지 않게 지시)
- 매수·매도 권유는 하지 않음 (매매 판단은 검증한 공식 신호만)
- 모델·노력 수준은 config.toml [ai] 에서 바꿀 수 있음
"""
from __future__ import annotations

import html
import os
import re

SYSTEM = """당신은 한국 개인 투자자를 위한 아침 시황 편집자입니다.
입력으로 받은 뉴스 제목 목록(출처·시각·구분 포함)만 근거로, 오늘 시장에서 중요한 흐름을 한국어로 정리합니다.

규칙:
- 입력에 없는 사실·숫자·회사 이름을 만들지 마세요. 제목만으로 확실하지 않으면 '보도', '전망'처럼 표현을 낮추세요.
- '전망·추측'으로 표시된 기사는 사실처럼 쓰지 말고 전망이라고 밝히세요.
- 매수·매도·비중 같은 투자 권유는 하지 마세요. 매매 판단은 따로 계산한 신호가 합니다.
- 같은 이야기를 다룬 기사는 하나로 묶으세요. 광고성·지역 행사 기사는 빼세요.
- 출력은 아래 형식의 줄만 쓰고, 다른 말(인사, 머리말, 맺음말)은 쓰지 마세요.
  - 줄마다 '- '로 시작, 핵심 단어는 **굵게** 한 번만, 한 줄은 70자 이내.
  - 지시받은 줄 수를 넘기지 마세요."""


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic()


def to_html(text: str) -> str:
    """'**굵게**' 표시만 텔레그램 HTML 로 바꾸고 나머지는 이스케이프."""
    out = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        body = html.escape(line.lstrip("- ").strip())
        body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
        out.append(f"• {body}")
    return "\n".join(out)


def summarize(sections: dict[str, list[dict]], instruction: str, cfg: dict | None = None, log=print) -> str | None:
    """sections: {"해외": [기사...], "국내": [...], "오선 라이브": [...]} → 텔레그램 HTML 줄들. 키가 없거나 실패하면 None."""
    client = _client()
    if client is None:
        return None
    acfg = {"model": "claude-opus-5-5", "effort": "low", **((cfg or {}).get("ai", {}))}
    lines = []
    for name, items in sections.items():
        if not items:
            continue
        lines.append(f"[{name}]")
        for i in items[:20]:
            extra = f" | 설명: {i['desc'][:300]}" if i.get("desc") else ""
            lines.append(f"- {i.get('title', '')} ({i.get('source', '')}, {i.get('kind', '')}){extra}")
    if not lines:
        return None
    try:
        import anthropic
        req = dict(model=acfg["model"], max_tokens=8000, system=SYSTEM,
                   output_config={"effort": acfg["effort"]},
                   messages=[{"role": "user", "content": instruction + "\n\n" + "\n".join(lines)}])
        try:   # 안전 필터에 걸리면 서버가 다른 모델로 다시 시도 (Claude API)
            r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **req)
        except TypeError:   # SDK 가 fallbacks 인자를 모르는 버전이면 요청 본문으로
            r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **req)
    except anthropic.APIStatusError as e:
        log(f"  AI 요약 실패: HTTP {e.status_code}")
        return None
    except anthropic.APIConnectionError:
        log("  AI 요약 실패: 연결 안 됨")
        return None
    if r.stop_reason == "refusal":
        log("  AI 요약: 거절됨 → 건너뜀")
        return None
    text = "".join(b.text for b in r.content if b.type == "text")
    h = to_html(text)
    return h or None


ISSUE_SYSTEM = """당신은 한국 개인 투자자를 위한 시장 해설가입니다. 주어진 '이슈'마다 기사 제목들과 숫자(시장 움직임·과거 통계)만 근거로 씁니다.
규칙:
- 입력에 없는 사실·숫자·회사·날짜를 만들지 마세요. 확실하지 않으면 '~로 보도됨', '~일 수 있음'으로 낮추세요.
- 사라·팔아라 같은 투자 권유는 하지 마세요.
- 이슈마다 정확히 두 줄:
  의미: (왜 시장에 중요한지, 어떤 연결고리로 영향이 가는지 한 문장, 60자 안팎)
  생각할 거리: (투자자가 스스로 따져볼 질문 한 문장 — 반대 시나리오나 다음에 확인할 것)
- 출력 형식: [번호] 다음 줄에 두 줄. 다른 말 쓰지 마세요."""


def issue_insights(issues: list[dict], cfg: dict | None = None, log=print) -> None:
    """issues 각각에 'ai' (HTML 줄 2개)를 채움. 키가 없거나 실패하면 그대로."""
    client = _client()
    if client is None or not issues:
        return
    acfg = {"model": "claude-opus-5-5", "effort": "low", **((cfg or {}).get("ai", {}))}
    parts = []
    for n, x in enumerate(issues, 1):
        mv = ", ".join(f"{nm} {r:+.2%}" for nm, code, r, z in x.get("impact", []))
        facts = " / ".join(re.sub(r"<[^>]+>", "", t) for t in x.get("insights", []))
        titles = "\n".join(f"  - {t}" for t in x.get("titles", [])[:10])
        parts.append(f"[{n}] 주제: {x['label']} (언론 {x['sources']}곳)\n시장: {mv or '없음'}\n데이터: {facts or '없음'}\n제목:\n{titles}")
    try:
        import anthropic
        req = dict(model=acfg["model"], max_tokens=4000, system=ISSUE_SYSTEM, output_config={"effort": acfg["effort"]},
                   messages=[{"role": "user", "content": "\n\n".join(parts)}])
        try:
            r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **req)
        except TypeError:
            r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **req)
    except Exception as e:
        log(f"  이슈 해설 실패: {type(e).__name__}")
        return
    if getattr(r, "stop_reason", "") == "refusal":
        return
    text = "".join(b.text for b in r.content if b.type == "text")
    chunks = re.findall(r"\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)", text, re.S)
    for num, body in chunks:
        k = int(num) - 1
        if not 0 <= k < len(issues):
            continue
        lines = []
        for ln in body.strip().splitlines():
            ln = ln.strip().lstrip("-• ").strip()
            m = re.match(r"(의미|생각할 거리)\s*:\s*(.+)", ln)
            if m:
                lines.append(f"<b>{m.group(1)}</b>: {html.escape(m.group(2))}")
        if lines:
            issues[k]["ai"] = lines[:2]
