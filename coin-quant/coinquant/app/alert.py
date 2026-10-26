"""매수·매도 알림 — `python -m coinquant alert` (매일 예약 실행에서 리포트 다음에 부름)

알리는 것 (공식 그대로, 코어 BTC·ETH·SOL):
  · 매수 가능: 공식 보유 상태 & 중기 점수 +2 이상 (공식이 실제로 새로 사는 자리)
  · 신규 진입 대기: 공식 보유 상태지만 중기 +1 (갖고 있으면 보유, 없으면 기다림)
  · 관망: 공식 신호 꺼짐 (갖고 있으면 매도)
상태가 바뀐 날만 알림 (설정으로 매일 요약도 가능). 함께 붙이는 참고 정보:
  큰 일정(events.toml), 4년 주기 위치, 오늘의 뉴스(위험 키워드) → '개인 판단 체크리스트' (검증한 규칙이 아님).
보내는 곳: 텔레그램(.env 의 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID), 윈도우 알림, reports/alert-날짜.txt (항상).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..backtest import signals as sg
from ..trend import MultiTrend, num
from ..universe import Panel, core_assets

STATE_FILE = "alert_state.json"
DANGER = ("해킹", "거래소 사고·상장폐지", "규제")
EVENT_GUARD_DAYS = 2


def coin_status(mt: MultiTrend, d: pd.Timestamp, a: str) -> dict:
    S, M, L = (mt.h[k] for k in ("단기", "중기", "장기"))
    sc = {k: int(mt.h[k].score.at[d, a]) if not pd.isna(mt.h[k].score.at[d, a]) else 0 for k in ("단기", "중기", "장기")}
    base = bool(M.state.at[d, a]) and not (L.score.at[d, a] <= -2)
    status = "매수 가능" if base and sc["중기"] >= 2 else ("신규 진입 대기" if base else "관망")
    hi, lo = M.chan_hi.at[d, a], M.chan_lo.at[d, a]
    return {"status": status, "score": sc, "close": mt_close(mt, d, a),
            "chan60": float(lo + 0.6 * (hi - lo)), "chan40": float(lo + 0.4 * (hi - lo)), "slow": float(M.slow.at[d, a])}


def mt_close(mt: MultiTrend, d, a) -> float:
    return float(mt.close.at[d, a])


def cycle_line(close_btc: pd.Series) -> tuple[str, bool]:
    """4년 주기 위치 (참고). 반환: (문장, 주기 최저 이탈 여부)."""
    from ..research import cycle
    s = close_btc.dropna()
    h = cycle.HALVINGS[-1]
    seg = s.loc[h:h + pd.Timedelta(days=700)]
    pk = seg.idxmax()
    low_d = s.loc[pk:].idxmin()
    low = s.loc[low_d]
    gaps = []
    for i, hh in enumerate(cycle.HALVINGS[:-1]):
        sg_ = s.loc[hh:hh + pd.Timedelta(days=700)]
        if len(sg_) < 300:
            continue
        p_ = sg_.idxmax()
        nxt = cycle.HALVINGS[i + 1]
        b_ = s.loc[p_:nxt].idxmin()
        gaps.append((b_ - p_).days)
    now_days = (s.index[-1] - pk).days
    broken = bool(s.iloc[-1] < low * 0.999) and low_d == s.index[-1]
    txt = (f"고점 {pk.date()} 후 {now_days}일, 고점 대비 {s.iloc[-1] / s.loc[pk] - 1:+.0%} · 과거 고점→바닥 {min(gaps)}~{max(gaps)}일"
           f" · 이번 최저 ${low:,.0f}({low_d.date()}, 고점 후 {(low_d - pk).days}일) — 이 아래로 마감하면 과거 패턴과 어긋남"
           if gaps else f"고점 {pk.date()} 후 {now_days}일")
    return txt, broken


def build(cfg: dict | None = None, with_news: bool = True, log=print) -> dict:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    panel = Panel.load(cache / "panel")
    core = core_assets(panel, cfg)
    F = sg.Features(panel, core)
    mt = MultiTrend(F.c, F.h, F.l)
    mt.close = F.c
    d = F.c["BTC"].dropna().index[-1]
    st = {a: coin_status(mt, d, a) for a in core if not pd.isna(F.c.at[d, a])}
    from .report import _events, user_dir
    udir = user_dir()
    prev = {}
    pf = udir / STATE_FILE
    if pf.exists():
        try:
            prev = json.loads(pf.read_text(encoding="utf-8")).get("status", {})
        except Exception:
            prev = {}
    changes = {a: (prev.get(a), v["status"]) for a, v in st.items() if prev.get(a) != v["status"]}
    today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    events = _events(7, today)
    try:   # 2013년부터 있는 BTC 가격 (없으면 바이낸스 2017-08~)
        from ..research import cycle
        btc_long = cycle.btc_series(cache).loc[:d]
    except Exception:
        btc_long = panel.close["BTC"]
    cyc, cyc_broken = cycle_line(btc_long)
    news, news_err = [], []
    if with_news:
        try:
            from . import news as news_mod
            news, news_err = news_mod.collect(max_items=5, log=lambda *x: None)
        except Exception as e:
            news_err = [type(e).__name__]

    lines = [f"[coin-quant] {d.date()} 일봉 기준 (KST 09:00 마감)"]
    if changes:
        lines.append("🔔 바뀐 것: " + ", ".join(f"{a} {o or '처음'} → {n}" for a, (o, n) in changes.items()))
    disc = cfg["trading"].get("limit_discount_core", 0.01)
    for a, v in st.items():
        sc = v["score"]
        head = f"• {a} {v['status']} (단기 {sc['단기']:+d} / 중기 {sc['중기']:+d} / 장기 {sc['장기']:+d}) 종가 ${num(v['close'])}"
        if v["status"] == "매수 가능":
            tail = f" → 업비트 어제 종가 −{disc:.0%} 지정가 (안 닿으면 다음 날 다시). 꺼지는 쪽 기준: 종가 ${num(v['chan40'])}·${num(v['slow'])} 아래"
        elif v["status"] == "신규 진입 대기":
            tail = f" → 갖고 있으면 보유, 없으면 대기. 중기 +2 가까운 기준: 종가 ${num(v['chan60'])} 이상(채널 60%선)"
        else:
            tail = f" → 안 삼 (갖고 있으면 매도). 다시 켜지려면 중기 +2 필요 (채널 60%선 ${num(v['chan60'])} 등)"
        lines.append(head + tail)
    lines.append("— 개인 판단 체크리스트 (검증한 규칙 아님, 참고) —")
    buy_now = [a for a, v in st.items() if v["status"] == "매수 가능"]
    near = [e for e in events if e["days"] <= EVENT_GUARD_DAYS]
    for e in events:
        lines.append(f"· 일정 D-{e['days']}: {e['name']} ({e['date']})")
    if buy_now and near:
        lines.append(f"  ↳ 매수 신호가 있지만 큰 일정이 {EVENT_GUARD_DAYS}일 안: 절반 먼저 / 일정 뒤 나머지 같은 방식 고려")
    lines.append(f"· 4년 주기: {cyc}")
    if cyc_broken:
        lines.append("  ↳ ⚠ BTC가 이번 주기 최저 아래로 마감: 과거 주기 패턴과 어긋남 — 새 매수는 신중히")
    risky = [n for n in news if any(c in n.get("categories", []) for c in DANGER)]
    for n in news[:3]:
        lines.append(f"· 뉴스[{n.get('kind', '')[:2]}] {n['title'][:80]} {n.get('link', '')}")
    for n in risky:
        hit = [c for c in n.get("coins", []) if c in buy_now]
        if hit:
            lines.append(f"  ↳ ⚠ {','.join(hit)} 관련 위험 뉴스(해킹·사고·규제): 원문 확인 전엔 진입 보류 고려")
    if with_news and not news:
        lines.append("· 뉴스: 가져온 것 없음" + (f" (실패: {', '.join(news_err[:3])})" if news_err else ""))
    lines.append("※ 투자 권유 아님. 원금 손실 가능. 최종 판단은 본인.")
    text = "\n".join(lines)
    pf.write_text(json.dumps({"date": str(d.date()), "status": {a: v["status"] for a, v in st.items()}},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    return {"text": text, "changes": changes, "status": st, "date": d}


def send_telegram(text: str) -> str:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        return "텔레그램: .env 에 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 없음 (건너뜀)"
    import requests
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"}, timeout=20)
        return "텔레그램: 보냄" if r.ok else f"텔레그램: 실패 (HTTP {r.status_code})"
    except Exception as e:
        return f"텔레그램: 실패 ({type(e).__name__})"   # 토큰이 들어간 URL은 로그에 남기지 않음


def send_windows(title: str, body: str) -> str:
    if os.name != "nt":
        return "윈도우 알림: 윈도우가 아님 (건너뜀)"
    esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")  # noqa: E731
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
          f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(body[:250])}</text>"
          "</binding></visual></toast>');"
          "$t = [Windows.UI.Notifications.ToastNotification]::new($x);"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
          "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($t)")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=30, capture_output=True)
        return "윈도우 알림: 보냄"
    except Exception as e:
        return f"윈도우 알림: 실패 ({type(e).__name__})"


def main(cfg: dict | None = None, test: bool = False, with_news: bool = True, log=print) -> dict:
    cfg = cfg or config_mod.load()
    acfg = {"daily_summary": False, "telegram": True, "windows": True, **cfg.get("alert", {})}
    res = build(cfg, with_news=with_news, log=log)
    out = config_mod.ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"alert-{res['date'].date()}.txt").write_text(res["text"], encoding="utf-8")
    log(res["text"])
    if not (res["changes"] or acfg["daily_summary"] or test):
        log("상태 변화 없음 → 알림 안 보냄 (파일만 저장)")
        return res
    title = "coin-quant " + ("🔔 " + ", ".join(f"{a} {n}" for a, (_, n) in res["changes"].items()) if res["changes"] else "오늘 요약")
    if acfg["telegram"]:
        log(send_telegram(title + "\n" + res["text"]))
    if acfg["windows"]:
        log(send_windows(title, " / ".join(l for l in res["text"].splitlines()[1:5])))
    return res
