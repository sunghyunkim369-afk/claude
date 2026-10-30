"""매매일지: 실행할 때마다 잔고·오늘 주문·공식 목표를 정리해 trading/journal/ 에 남기고,
.env 에 GITHUB_TOKEN 이 있으면 GitHub 저장소(프로그램 받는 브랜치)의 journal/ 폴더에도 올려요 (Claude 가 캡처 없이 읽을 수 있게).

  python -m trading journal      지금 일지 쓰고 올리기 (주문 안 함)

올리는 내용: 종목·수량·매입가·현재가·평가금액·손익, 총평가, 오늘 주문, 공식 목표·순위.
계좌번호·앱키는 넣지 않아요.
GITHUB_TOKEN: GitHub → Settings → Developer settings → Fine-grained tokens 에서
  이 저장소 하나만, Contents 권한 Read and write 로 만들어 .env 에 넣어요 (대화창에 붙이지 마세요).
"""

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime

REPO = os.environ.get("JOURNAL_REPO", "sunghyunkim369-afk/claude")
BRANCH = os.environ.get("JOURNAL_BRANCH", "claude/awesome-edison-24zdn0")
START = 100_000_000


def today_orders(here, day):
    path = os.path.join(here, "logs", f"{day}.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") in ("order_sent", "order_failed") and (e.get("order") or {}).get("market", "kr") == "kr":
                o = e.get("order") or {}
                rows.append({"time": e.get("time", "")[11:16], "ok": e["event"] == "order_sent",
                             "side": o.get("side"), "symbol": o.get("symbol"), "name": o.get("name", ""),
                             "qty": o.get("qty"), "price": o.get("limit_price") or o.get("sent_price") or o.get("price"),
                             "reason": o.get("reason", ""), "error": e.get("error", "")})
    return rows


def build(kis, state, here, now=None):
    """(마크다운, 요약 dict)."""
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    bal = kis.kr_balance()
    held = bal["holdings"]
    stocks = sum(h["qty"] * h["price"] for h in held.values())
    total = bal["total"]
    cash = total - stocks
    start = state.d.get("start_total") or START
    manual = state.d.get("manual", {})
    lines = [f"# 매매일지 {day} {now:%H:%M}", "",
             f"- 총평가 **{total:,.0f}원** (시작 {start:,.0f}원 대비 **{(total / start - 1) * 100:+.2f}%**)",
             f"- 주식 {stocks:,.0f}원 / 현금(총평가 − 주식) {cash:,.0f}원",
             f"- 공식: {state.d.get('kr_mode', 'aggressive')}, 약세장 대응 {state.d.get('weak_mode', 'off')}, "
             f"마지막 리밸런스 {(state.d.get('kr_rebalances') or ['-'])[-1]}", "",
             "## 보유", "", "| 종목 | 수량 | 매입가 | 현재가 | 평가금액 | 비중 | 손익 |", "|---|---|---|---|---|---|---|"]
    for code, h in sorted(held.items(), key=lambda x: -x[1]["qty"] * x[1]["price"]):
        v = h["qty"] * h["price"]
        pl = (h["price"] / h["avg_price"] - 1) * 100 if h["avg_price"] else 0.0
        tag = " (수동)" if code in manual else ""
        lines.append(f"| {h['name']}({code}){tag} | {h['qty']:,} | {h['avg_price']:,.0f} | {h['price']:,.0f} | "
                     f"{v:,.0f} | {v / total * 100 if total else 0:.0f}% | {pl:+.2f}% |")
    orders = today_orders(here, day)
    lines += ["", "## 오늘 주문", ""]
    if orders:
        lines += ["| 시각 | 구분 | 종목 | 수량 | 가격 | 결과 | 이유 |", "|---|---|---|---|---|---|---|"]
        for o in orders:
            side = "매수" if o["side"] == "buy" else "매도"
            res = "접수" if o["ok"] else f"실패: {o['error'][:40]}"
            price = f"{o['price']:,.0f}" if isinstance(o["price"], (int, float)) else "시장가"
            lines.append(f"| {o['time']} | {side} | {o['name']}({o['symbol']}) | {o['qty']} | {price} | {res} | {o['reason']} |")
    else:
        lines.append("(이 프로그램으로 낸 주문 없음. 앱에서 직접 한 주문은 잔고에만 보여요)")
    ranked = state.d.get("kr_ranked") or {}
    if ranked:
        lines += ["", f"## 공식 목표 ({ranked.get('session', '-')} 계산)", ""]
        for t in ranked.get("targets", []):
            lines.append(f"- {t[2]}({t[1]}) 20일 {t[0] * 100:+.1f}%")
        if ranked.get("reserve"):
            lines.append("- 다음 순위: " + ", ".join(f"{t[2]}({t[1]}) {t[0] * 100:+.1f}%" for t in ranked["reserve"]))
    if state.d.get("blocked"):
        lines.append(f"- 모의투자 매매불가: {', '.join(state.d['blocked'])}")
    lines.append("")
    summary = {"date": day, "time": now.strftime("%H:%M"), "total": round(total), "stocks": round(stocks),
               "cash": round(cash), "return_pct": round((total / start - 1) * 100, 2),
               "holdings": {c: {"name": h["name"], "qty": h["qty"], "avg": h["avg_price"], "price": h["price"]}
                            for c, h in held.items()}}
    return "\n".join(lines), summary


def save_local(here, md, summary):
    folder = os.path.join(here, "journal")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{summary['date']}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(folder, "history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return folder


def _api(method, url, token, body=None, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "kis-journal"})
    with opener(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


def upload(path, text, token, message, opener=urllib.request.urlopen):
    """GitHub 저장소 journal 브랜치에 파일 하나 올리기 (있으면 덮어쓰기). 브랜치가 없으면 기본 브랜치에서 만들어요."""
    base = f"https://api.github.com/repos/{REPO}"
    try:
        _api("GET", f"{base}/branches/{BRANCH}", token, opener=opener)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        repo = _api("GET", base, token, opener=opener)
        head = _api("GET", f"{base}/git/ref/heads/{repo['default_branch']}", token, opener=opener)
        _api("POST", f"{base}/git/refs", token, {"ref": f"refs/heads/{BRANCH}", "sha": head["object"]["sha"]},
             opener=opener)
    sha = None
    try:
        sha = _api("GET", f"{base}/contents/{path}?ref={BRANCH}", token, opener=opener).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {"message": message, "branch": BRANCH, "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    _api("PUT", f"{base}/contents/{path}", token, body, opener=opener)


def write(kis, state, here, out=print, now=None):
    """일지 쓰기 + (토큰 있으면) 올리기. 실패해도 매매에는 영향 없게 예외를 삼켜요."""
    try:
        md, summary = build(kis, state, here, now)
        folder = save_local(here, md, summary)
        out(f"  매매일지: {os.path.join(folder, summary['date'] + '.md')} (총평가 {summary['total']:,}원, "
            f"{summary['return_pct']:+.2f}%)")
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            with open(os.path.join(folder, "history.jsonl"), encoding="utf-8") as f:
                history = f.read()
            upload(f"journal/{summary['date']}.md", md, token, f"매매일지 {summary['date']} {summary['time']}")
            upload("journal/history.jsonl", history, token, f"일지 기록 {summary['date']} {summary['time']}")
            out("  매매일지를 GitHub(journal/ 폴더)에 올렸어요")
    except Exception as e:
        out(f"  매매일지 실패(매매에는 영향 없음): {e}")
