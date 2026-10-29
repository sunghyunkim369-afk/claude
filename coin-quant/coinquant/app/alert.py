"""매수·매도 알림 — `python -m coinquant alert` (매일 예약 실행에서 리포트 다음에 부름)

알리는 것 (공식 그대로, 코어 BTC·ETH·SOL):
  · 매수 가능: 공식 보유 상태 & 중기 점수 +2 이상 (공식이 실제로 새로 사는 자리)
  · 신규 진입 대기: 공식 보유 상태지만 중기 +1 (갖고 있으면 보유, 없으면 기다림)
  · 관망: 공식 신호 꺼짐 (갖고 있으면 매도)
매일 시황(코인별 등락·200일선·국면·변동성·펀딩비) + 상태가 바뀐 날은 🔔 표시 (config [alert] daily_summary). 함께 붙이는 참고 정보:
  큰 일정(events.toml), 4년 주기 위치, 뉴스 → '참고 정보' (매매 규칙으로 검증해 보니 공식을 이기지 못함, docs/rules-check).
--light: 코어 3종만 받아서 실행 (PC 없이 GitHub Actions 에서 매일 보내기, .github/workflows/coin-quant-daily.yml).
보내는 곳: 텔레그램(.env 의 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID), 윈도우 알림, reports/alert-날짜.txt (항상).
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from ..trend import MultiTrend, num

STATE_FILE = "alert_state.json"
DANGER = ("해킹", "거래소 사고·상장폐지", "규제")


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
    txt = (f"고점({pk:%y/%m/%d}) 후 {now_days}일 · 고점 대비 {s.iloc[-1] / s.loc[pk] - 1:+.0%} · 과거엔 고점 후 {min(gaps)}~{max(gaps)}일에 바닥\n"
           f"이번 최저 ${low:,.0f}({low_d:%y/%m/%d}) 아래로 마감하면 과거 패턴과 어긋남"
           if gaps else f"고점({pk:%y/%m/%d}) 후 {now_days}일")
    return txt, broken


def light_update(cache: Path, core: list[str], log=print) -> None:
    """코어 3종만 빠르게 받기 (GitHub Actions 처럼 전체 데이터가 없는 곳용, 처음 2~5분·이후 수십 초)."""
    from ..data import binance_public, coinmetrics, fx
    spot = cache / "binance" / "spot_1d"
    binance_public.update_cache(spot, [f"{a}USDT" for a in core], workers=6, log=log)
    binance_public.rest_topup(spot, [f"{a}USDT" for a in core], log=log)   # 공개 파일이 아직 안 올라온 어제 봉
    try:
        binance_public.fetch_funding_history("BTCUSDT").to_csv(cache / "binance" / "funding_BTCUSDT.csv", index=False)
    except Exception as e:
        log(f"  펀딩비 실패: {type(e).__name__}")
    if not (cache / "coinmetrics" / "btc.csv").exists():   # 4년 주기용 긴 BTC 가격 (한 번만)
        coinmetrics.download(["btc"], cache / "coinmetrics", log=log)
    try:
        fx.download_usdkrw(cache / "fx_usdkrw.csv")
    except Exception as e:
        log(f"  환율 실패: {type(e).__name__}")


def load_core(cache: Path, core: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """바이낸스 일봉 CSV(BTCUSDT 등)에서 종가·고가·저가. 전체 패널 없이도 동작."""
    fr = {}
    for a in core:
        f = cache / "binance" / "spot_1d" / f"{a}USDT.csv"
        if f.exists():
            fr[a] = pd.read_csv(f, parse_dates=["date"]).set_index("date").sort_index()
    idx = pd.date_range(min(x.index[0] for x in fr.values()), max(x.index[-1] for x in fr.values()))
    get = lambda k: pd.DataFrame({a: x[k] for a, x in fr.items()}).reindex(idx)  # noqa: E731
    return get("close"), get("high"), get("low")


def krw_short(v: float) -> str:
    """106,221,934 → '1억 622만원', 3,080,680 → '308만원', 133,787 → '13.4만원'."""
    if v is None or np.isnan(v):
        return ""
    if v >= 1e8:
        eok, man = divmod(int(round(v / 1e4)), 10000)
        return f"{eok}억 {man:,}만원" if man else f"{eok}억원"
    if v >= 1e6:
        return f"{v / 1e4:,.0f}만원"
    return f"{v / 1e4:,.1f}만원"


def _pc(v: float) -> str:
    return "-" if v is None or np.isnan(v) else f"{v:+.1%}"


def market_lines(c: pd.DataFrame, cache: Path) -> list[str]:
    """시황 요약 (텔레그램 HTML): 코인별 가격·등락·200일선, 국면, 변동성, 펀딩비, 환율."""
    from ..data.fx import load_usdkrw
    from ..data.update import load_funding
    d = c["BTC"].dropna().index[-1]
    fxs = load_usdkrw(cache / "fx_usdkrw.csv")
    rate = float(fxs.loc[:d].iloc[-1]) if fxs is not None and len(fxs.loc[:d]) else np.nan
    out = ["<b>📊 시황</b>"]
    for a in c.columns:
        s = c[a].loc[:d].dropna()
        if len(s) < 31:
            continue
        r = lambda n: s.iloc[-1] / s.iloc[-1 - n] - 1  # noqa: E731
        sma = s.rolling(200).mean().iloc[-1]
        krw = f" · {krw_short(s.iloc[-1] * rate)}" if not np.isnan(rate) else ""
        out.append(f"<b>{a}</b> ${num(s.iloc[-1])}{krw}")
        out.append(f"   1일 {_pc(r(1))} · 7일 {_pc(r(7))} · 30일 {_pc(r(30))} · 200일선 {_pc(s.iloc[-1] / sma - 1)}")
    b = c["BTC"].loc[:d].dropna()
    e50, e200 = b.ewm(span=50, adjust=False).mean().iloc[-1], b.ewm(span=200, adjust=False).mean().iloc[-1]
    above = b.iloc[-1] > b.rolling(200).mean().iloc[-1]
    regime = "상승 국면" if above and e50 > e200 else ("회복 시도" if above else "하락 국면")
    vol = np.log(b).diff().tail(30).std() * np.sqrt(365)
    extra = [f"변동성 {vol:.0%}"]
    fu = load_funding(cache)
    if fu is not None and len(fu.loc[:d].dropna()):
        f7 = fu.loc[:d].dropna().tail(7).mean()
        extra.append(f"펀딩비 {f7 * 100:.3f}%{' 과열' if f7 > 0.0003 else ''}")
    if not np.isnan(rate):
        extra.append(f"환율 {rate:,.0f}원")
    out.append(f"국면: <b>{regime}</b> · " + " · ".join(extra))
    return out


ICON = {"매수 가능": "🟢", "신규 진입 대기": "🟡", "관망": "⚪"}


def to_plain(h: str) -> str:
    """텔레그램 HTML → 일반 글 (파일·윈도우 알림용). 링크는 '제목 (주소)'."""
    h = re.sub(r'<a href="([^"]+)">(.*?)</a>', r"\2 (\1)", h)
    return html.unescape(re.sub(r"</?(b|i|u|code)>", "", h))


def build(cfg: dict | None = None, with_news: bool = True, log=print) -> dict:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    core = list(cfg["universe"]["core"])
    c, h, l_ = load_core(cache, core)
    mt = MultiTrend(c, h, l_)
    mt.close = c
    d = c["BTC"].dropna().index[-1]
    st = {a: coin_status(mt, d, a) for a in c.columns if not pd.isna(c.at[d, a])}
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
        btc_long = c["BTC"]
    cyc, cyc_broken = cycle_line(btc_long)
    news, news_err = [], []
    if with_news:
        try:
            from . import news as news_mod
            news, news_err = news_mod.collect(max_items=5, log=lambda *x: None)
        except Exception as e:
            news_err = [type(e).__name__]

    e = html.escape
    today_kst = pd.Timestamp.now(tz="Asia/Seoul")
    L = [f"<b>🪙 코인 · {today_kst:%m/%d}({'월화수목금토일'[today_kst.weekday()]})</b>  <i>{d:%m/%d} 일봉 기준</i>"]
    yday = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None) - pd.Timedelta(days=1)
    if d < yday:
        L.append(f"⚠ 어제({yday:%m/%d}) 일봉이 아직 안 올라와 {d:%m/%d} 기준")
    if changes:
        L.append("🔔 <b>신호 바뀜</b>: " + ", ".join(f"{a} {e(o or '처음')} → <b>{e(n)}</b>" for a, (o, n) in changes.items()))

    # ✅ 오늘 할 일 — 이 칸만 보고 결정할 수 있게
    disc = cfg["trading"].get("limit_discount_core", 0.01)
    buy = [a for a, v in st.items() if v["status"] == "매수 가능"]
    hold = [a for a, v in st.items() if v["status"] == "신규 진입 대기"]
    sell = [a for a, v in st.items() if v["status"] == "관망"]
    L += ["", "<b>✅ 오늘 할 일</b>"]
    if buy:
        L.append(f"🟢 <b>매수: {'·'.join(buy)}</b> — 업비트 어제 종가 −{disc:.0%} 지정가 (안 닿으면 내일 다시)")
    else:
        L.append("• <b>새로 살 것: 없음</b>")
    if hold:
        L.append(f"🟡 <b>보유만: {'·'.join(hold)}</b> (갖고 있으면 유지, 새로 사지 않음)")
    if sell:
        L.append(f"⚪ <b>갖고 있으면 매도: {'·'.join(sell)}</b>")
    gaps = {a: v["chan60"] / v["close"] - 1 for a, v in st.items() if v["status"] != "매수 가능" and v["close"] > 0}
    if gaps:
        a = min(gaps, key=lambda k: abs(gaps[k]))
        L.append(f"🎯 다음 매수 신호 가장 가까운 것: <b>{a} ${num(st[a]['chan60'])}↑</b> (지금보다 {gaps[a]:+.1%}, 대략)")
    from .short_mode import lines as short_lines
    try:
        sm_todo, sm_lines = short_lines(mt, d, cfg)
    except Exception as ex:
        sm_todo, sm_lines = [], [f"⚡ 단기 모드: 계산 실패 ({type(ex).__name__})"]
    L += sm_todo
    from .breakout_mode import lines as bo_lines
    try:
        bo_todo, bo_lines_ = bo_lines(cfg, log=log)
    except Exception as ex:
        bo_todo, bo_lines_ = [], [f"⚡ 4시간 돌파: 계산 실패 ({type(ex).__name__})"]
    L += bo_todo
    for x in events:
        if x["days"] <= 1:
            when = f" (발표 {e(x['kst'])} 한국시간)" if x.get("kst") else ""
            L.append(f"⚠️ {'오늘' if x['days'] == 0 else '내일'} <b>{e(x['name'])}</b>{when} — 변동 클 수 있음")

    try:   # 어젯밤 미국 주요 지표·금리 결정 결과
        from ..stocks.news import results_block
        rb = results_block()
    except Exception:
        rb = []
    if rb:
        L += [""] + rb
    L += [""] + market_lines(c, cache)
    L += ["", "<b>🧭 공식 신호</b>  <i>중기 +2 이상 매수 · 0 이하 매도</i>"]
    for a, v in st.items():
        sc = v["score"]
        L.append(f"{ICON[v['status']]} <b>{a} {v['status']}</b>  단기 {sc['단기']:+d} · 중기 <b>{sc['중기']:+d}</b> · 장기 {sc['장기']:+d}")
        if v["status"] == "매수 가능":
            L.append(f"   ↳ 약해지는 선 ${num(v['chan40'])} 아래로 마감하면 주의")
        elif v["status"] == "신규 진입 대기":
            L.append(f"   ↳ 매수 기준 약 ${num(v['chan60'])}↑")
        else:
            L.append(f"   ↳ 다시 매수 기준 약 ${num(v['chan60'])}↑")

    if bo_lines_:
        L += [""] + bo_lines_
    if sm_lines:
        L += [""] + sm_lines
    L += ["", "<b>📋 참고 정보</b>  <i>매매 규칙 아님 — 검증해 보니 공식을 이기지 못함 (docs/rules-check)</i>"]
    for x in events:
        L.append(f"📅 D-{x['days']} {e(x['name'])}")
    cy = cyc.split("\n")
    L.append(f"🔄 4년 주기: {e(cy[0])}")
    L += [f"   ↳ {e(x)}" for x in cy[1:]]
    if cyc_broken:
        L.append("   ↳ ⚠ 이번 주기 최저 아래로 마감 — 과거엔 이후 2~4달 수익이 평소보다 약했음 (공식 매수는 막지 않음)")
    ai_news = None
    if news:
        from . import ai
        ai_news = ai.summarize({"코인 뉴스": news[:15]}, "아래 코인 뉴스 제목들에서 오늘 코인 시장에 중요한 흐름을 최대 3줄로 정리하세요.",
                               cfg, log=log)
    if ai_news:
        L += ["", "<b>🗞 뉴스 핵심</b>  <i>AI 요약 · 원문 확인 권장</i>", ai_news]
    if news:
        L += ["", "<b>📰 뉴스</b>"]
        for n in news[:3]:
            kind = n.get("kind", "")[:2]
            title = e(n["title"][:70] + ("…" if len(n["title"]) > 70 else ""))
            link = n.get("link", "")
            L.append(f"• <a href=\"{e(link, quote=True)}\">{title}</a> <i>{e(kind)}</i>" if link else f"• {title} <i>{e(kind)}</i>")
        for n in news:
            hit = [x for x in n.get("coins", []) if x in buy]
            if hit and any(cat in n.get("categories", []) for cat in DANGER):
                L.append(f"   ↳ ⚠ {','.join(hit)} 위험 뉴스(해킹·사고·규제): 원문을 확인하세요 (이 규칙은 검증 불가)")
    elif with_news:
        L.append("📰 뉴스: 가져온 것 없음" + (f" ({e(', '.join(news_err[:3]))})" if news_err else ""))
    L += ["", "<i>투자 권유 아님 · 원금 손실 가능 · 최종 판단은 본인</i>"]
    h_ = "\n".join(L)
    pf.write_text(json.dumps({"date": str(d.date()), "status": {a: v["status"] for a, v in st.items()}},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    return {"text": to_plain(h_), "html": h_, "changes": changes, "status": st, "date": d}


def send_telegram(text: str, html_mode: bool = False) -> str:
    # 붙여 넣을 때 섞인 공백·줄바꿈·따옴표 제거 (예: "bot123:ABC" 처럼 앞에 bot 을 붙인 경우도)
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"\'')
    tok = tok[3:] if tok.lower().startswith("bot") and ":" in tok else tok
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip().strip('"\'')
    if not tok or not chat:
        return "텔레그램: TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 없음 (PC는 .env, GitHub 는 Settings → Secrets 에 넣기) → 건너뜀"
    import requests
    try:
        data = {"chat_id": chat, "text": text, "disable_web_page_preview": "true"}
        if html_mode:
            data["parse_mode"] = "HTML"
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)
        if not r.ok and html_mode and "parse" in r.text.lower():   # 서식 오류면 서식 없이 다시
            data.pop("parse_mode")
            data["text"] = to_plain(text)
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)
        if r.ok:
            return "텔레그램: 보냄"
        try:   # 텔레그램 오류 설명 (토큰은 들어 있지 않음)
            why = r.json().get("description", "")
        except Exception:
            why = ""
        hint = {"chat not found": " → chat id 가 틀렸거나, 내 봇에게 먼저 말을 걸지 않음",
                "Unauthorized": " → 토큰이 틀림 (BotFather /mybots → API Token 확인)",
                "bot was blocked by the user": " → 내가 봇을 차단함 (봇 화면에서 차단 해제)"}
        tip = next((v for k, v in hint.items() if k.lower() in why.lower()), "")
        return f"텔레그램: 실패 (HTTP {r.status_code}: {why}){tip}"
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


def main(cfg: dict | None = None, test: bool = False, with_news: bool = True, light: bool = False, log=print) -> dict:
    cfg = cfg or config_mod.load()
    acfg = {"daily_summary": False, "telegram": True, "windows": True, **cfg.get("alert", {})}
    if light:
        light_update(config_mod.cache_dir(cfg), list(cfg["universe"]["core"]), log=log)
    from .report import user_dir
    sent_file = user_dir() / "alert_sent.json"
    today = str(pd.Timestamp.now(tz="Asia/Seoul").date())
    if os.environ.get("CQ_SCHEDULED") == "1":
        try:
            if json.loads(sent_file.read_text(encoding="utf-8")).get("date") == today:
                log(f"오늘({today}) 이미 보냄 → 예약 재시도 건너뜀")
                return {}
        except Exception:
            pass
    res = build(cfg, with_news=with_news, log=log)
    out = config_mod.ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"alert-{res['date'].date()}.txt").write_text(res["text"], encoding="utf-8")
    log(res["text"])
    if not (res["changes"] or acfg["daily_summary"] or test):
        log("상태 변화 없음 → 알림 안 보냄 (파일만 저장)")
        return res
    title = "coin-quant " + ("🔔 " + ", ".join(f"{a} {n}" for a, (_, n) in res["changes"].items()) if res["changes"] else "오늘 시황")
    if acfg["telegram"]:
        msg = send_telegram(res["html"], html_mode=True)
        log(msg)
        if msg.endswith("보냄"):
            sent_file.write_text(json.dumps({"date": today}), encoding="utf-8")
    if acfg["windows"]:
        sig = [l for l in res["text"].splitlines() if l[:1] in ("🟢", "🟡", "⚪")]
        log(send_windows(title, " / ".join(sig)))
    return res
