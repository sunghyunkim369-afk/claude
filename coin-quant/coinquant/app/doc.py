"""리포트 블록을 마크다운과 HTML 로 같이 쓰기."""
from __future__ import annotations

import html
import re

DISCLAIMER = ("이 프로그램은 투자 판단을 돕는 도구일 뿐이며 투자 권유가 아닙니다. 과거 성과는 미래를 보장하지 않고, "
              "코인은 변동성이 매우 커서 원금 손실(전액 손실 포함) 가능성이 있습니다. 최종 판단과 책임은 본인에게 있습니다.")


class Doc:
    def __init__(self, title: str):
        self.title = title
        self.blocks: list[tuple] = []

    def h(self, text, level=2):
        self.blocks.append(("h", level, text))

    def p(self, text, cls=""):
        self.blocks.append(("p", text, cls))

    def ul(self, items):
        self.blocks.append(("ul", list(items)))

    def table(self, header: list[str], rows: list[list]):
        self.blocks.append(("table", header, rows))

    # ---- 마크다운
    def md(self) -> str:
        out = [f"# {self.title}", ""]
        for b in self.blocks:
            if b[0] == "h":
                out += ["#" * b[1] + " " + b[2], ""]
            elif b[0] == "p":
                out += [("> " if b[2] == "warn" else "") + b[1], ""]
            elif b[0] == "ul":
                out += [f"- {x}" for x in b[1]] + [""]
            elif b[0] == "table":
                out.append("| " + " | ".join(b[1]) + " |")
                out.append("|" + "---|" * len(b[1]))
                out += ["| " + " | ".join(str(c) for c in r) + " |" for r in b[2]]
                out.append("")
        return "\n".join(out)

    # ---- HTML
    @staticmethod
    def _inline(s: str) -> str:
        s = html.escape(str(s))
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        return s

    def html(self) -> str:
        body = []
        for b in self.blocks:
            if b[0] == "h":
                body.append(f"<h{b[1]}>{self._inline(b[2])}</h{b[1]}>")
            elif b[0] == "p":
                cls = f' class="{b[2]}"' if b[2] else ""
                body.append(f"<p{cls}>{self._inline(b[1])}</p>")
            elif b[0] == "ul":
                body.append("<ul>" + "".join(f"<li>{self._inline(x)}</li>" for x in b[1]) + "</ul>")
            elif b[0] == "table":
                head = "".join(f"<th>{self._inline(c)}</th>" for c in b[1])
                rows = "".join("<tr>" + "".join(f"<td>{self._inline(c)}</td>" for c in r) + "</tr>" for r in b[2])
                body.append(f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
        return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(self.title)}</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--card:#fff;--warn:#fff4e5;--warnline:#e8a33d;--up:#127a4b;--down:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161614;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--card:#1f1e1b;--warn:#2b2214;--warnline:#c98a2b;--up:#5cc794;--down:#f08a80}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Segoe UI","Malgun Gothic","Noto Sans KR",sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:24px;margin:0 0 8px}} h2{{font-size:19px;margin:32px 0 10px;padding-top:12px;border-top:1px solid var(--line)}}
h3{{font-size:16px;margin:20px 0 8px}} p{{margin:8px 0}} a{{color:inherit}}
.warn{{background:var(--warn);border-left:4px solid var(--warnline);padding:10px 12px;border-radius:4px}}
.muted{{color:var(--muted);font-size:13px}}
.tw{{overflow-x:auto;margin:8px 0 16px;border:1px solid var(--line);border-radius:6px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{font-weight:600;color:var(--muted)}} tr:last-child td{{border-bottom:none}}
</style></head><body><main><h1>{html.escape(self.title)}</h1>
{chr(10).join(body)}
</main></body></html>"""
