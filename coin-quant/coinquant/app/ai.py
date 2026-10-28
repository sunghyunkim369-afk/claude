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
