"""매일 리포트: `python -m coinquant report`

1. 시장 상태  2. 오늘의 이슈  3. 계산상 추천  4. 내 판단 반영과 성과  5. 위험 경고
추천 주문 목록은 user_data/orders-latest.json 에도 저장해서 `trade` 명령이 그대로 씁니다.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config as config_mod
from .. import indicators as ind
from .. import strategy
from ..backtest import engine, metrics, signals as sg, validate as V
from ..data import fx as fx_mod
from ..data.update import load_funding
from ..overrides import ACTIONS, Judgments
from ..universe import Panel
from . import news as news_mod
from . import portfolio
from .doc import DISCLAIMER, Doc

KST = "Asia/Seoul"


def _pct(v, d=1):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.{d}f}%"


def _p(v, d=1):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.{d}f}%"


def _money(v, cur="KRW"):
    if v is None or np.isnan(v):
        return "-"
    if cur == "KRW":
        return f"{v:,.0f}원" if v >= 100 else f"{v:,.2f}원"
    return f"${v:,.2f}" if v >= 1 else f"${v:,.6f}"


def user_dir() -> Path:
    p = config_mod.ROOT / "user_data"
    p.mkdir(exist_ok=True)
    return p


def _krw_prices(cache: Path, panel: Panel, d: pd.Timestamp) -> tuple[pd.Series, str, pd.Series | None]:
    """원화 가격: 업비트 일봉 캐시가 있으면 그것, 없으면 달러 × 환율(김치 프리미엄 빠짐)."""
    fx = fx_mod.load_usdkrw(cache / "fx_usdkrw.csv")
    rate = float(fx.loc[:d].iloc[-1]) if fx is not None and len(fx.loc[:d]) else np.nan
    up_dir = cache / "upbit" / "krw_1d"
    usd = panel.close.loc[d].dropna()
    if up_dir.exists() and any(up_dir.glob("KRW-*.csv")):
        px, prem = {}, {}
        for p in up_dir.glob("KRW-*.csv"):
            a = p.stem.split("-", 1)[1]
            df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
            if d in df.index:
                px[a] = df.loc[d, "close"]
                if a in usd and rate > 0:
                    prem[a] = df.loc[d, "close"] / (usd[a] * rate) - 1
        return pd.Series(px, dtype=float), "업비트 KRW 종가", pd.Series(prem, dtype=float)
    return usd * rate, f"바이낸스 달러 종가 × 환율 {rate:,.1f} (김치 프리미엄 미반영)", None


def _signal_history(panel: Panel, cfg: dict, signal: str, asset: str, dev_end: pd.Timestamp, cache: dict) -> dict:
    """그 신호가 그 코인에서 과거(개발 구간)에 어땠는지: 거래 승률·평균·최악."""
    key = (signal, asset)
    if key in cache:
        return cache[key]
    p = panel.slice(None, dev_end)
    F = sg.Features(p, [asset])
    on = strategy._on(signal, F, [asset])
    w = sg.weights_single(on, asset, p.close[[asset]].notna())
    c = cfg["costs"]
    costs = engine.Costs(c["fee"], c["slippage_core"], c["slippage_other"], tuple(cfg["universe"]["core"]))
    res = engine.run(w, p.open[[asset]], p.close[[asset]], costs, engine.Rules(), p.tradable[[asset]])
    t = res.trades["ret"].dropna()
    out = {"n": len(t), "win": (t > 0).mean() if len(t) else np.nan, "mean": t.mean() if len(t) else np.nan,
           "median": t.median() if len(t) else np.nan, "worst": t.min() if len(t) else np.nan}
    cache[key] = out
    return out


def _events(within_days: int, today: pd.Timestamp) -> list[dict]:
    p = config_mod.ROOT / "events.toml"
    if not p.exists():
        return []
    ev = tomllib.loads(p.read_text(encoding="utf-8")).get("event", [])
    out = []
    for e in ev:
        d = pd.Timestamp(e["date"])
        if today.normalize() <= d <= today.normalize() + pd.Timedelta(days=within_days):
            out.append({**e, "days": (d - today.normalize()).days})
    return out


def _era_features(btc: pd.Series, end: pd.Timestamp) -> np.ndarray:
    seg = btc.loc[:end]
    r90 = seg.iloc[-1] / seg.iloc[-91] - 1 if len(seg) > 91 else np.nan
    vol = np.log(seg).diff().tail(30).std() * np.sqrt(365)
    sma = seg.rolling(200).mean()
    above = (seg > sma).tail(60).mean()
    return np.array([r90, vol, above])


def _combo_stats() -> pd.DataFrame | None:
    p = config_mod.ROOT / "reports" / "research" / "trend_combo_forward.csv"
    return pd.read_csv(p) if p.exists() else None


def _swing_plan(doc: Doc, cfg: dict, F, d, core, final_w, cur_w, info) -> None:
    """코인마다: 기간별 추세 판정 + 근거 숫자 + 청산 기준 가격 + 과거 같은 조합의 이후 20일 성적."""
    from ..strategy import multitrend
    from ..trend import num
    mt = multitrend(F)
    combos = mt.combo()
    stats = _combo_stats()
    doc.h("3-1. 스윙 매매 계획 (기간별 추세와 근거)")
    doc.p("판정: 기간마다 근거 3개(이동평균·채널 위치·회귀 추세선)를 −1/0/+1로 매겨 합이 +2 이상이면 상승, −2 이하면 하락. "
          "매매 상태는 +2 이상에서 켜지고 0 이하가 돼야 꺼집니다. 근거 숫자는 모두 어제(UTC 00:00 마감) 종가 기준.", "muted")
    assets = list(dict.fromkeys(core + [a for a in final_w.index if a in F.c.columns] + [a for a in cur_w.index if a in F.c.columns]))
    for a in assets:
        if a not in F.c.columns or np.isnan(F.c.at[d, a]):
            continue
        snap = mt.snapshot(d, a)
        combo = combos.at[d, a]
        tw, cw = final_w.get(a, 0.0), cur_w.get(a, 0.0)
        act = "매수/늘리기" if tw - cw >= cfg["portfolio"]["min_trade_weight"] else (
            "매도/줄이기" if cw - tw >= cfg["portfolio"]["min_trade_weight"] else ("보유 유지" if tw > 0 else "관망(안 삼)"))
        doc.h(f"{a} — 추세 {combo or '판단 불가'} (단기/중기/장기) → {act}", 3)
        doc.table(["기간", "판정(점수)", "매매 상태", "이동평균", "채널 위치", "회귀 추세선"],
                  [[r["기간"], f"{r['판정']} ({int(r['점수']):+d})" if not pd.isna(r["점수"]) else r["판정"],
                    r["보유 상태(히스테리시스)"], r["이동평균"], r["채널"], r["회귀"]] for r in snap])
        c = snap[0]["종가"]
        lv = [f"{r['기간'].split('(')[0]}: 채널 하단 40%선 {num(r['채널 하단 40%선'])} ({r['채널 하단 40%선'] / c - 1:+.1%}), "
              f"느린 EMA {num(r['느린 EMA'])} ({r['느린 EMA'] / c - 1:+.1%})" for r in snap if not pd.isna(r["채널 하단 40%선"])]
        items = [f"목표 비중 {_p(tw)} (지금 {_p(cw)}) — 공식 신호: {info['formula']['core_signal'] if a in core else info['formula']['others_signal']}",
                 "약해지는 기준 가격(종가가 이 아래면 그 기간 근거가 −로 바뀜): " + " · ".join(lv)]
        if stats is not None and combo:
            grp = "코어" if a in core else "기타"
            m = stats[(stats["그룹"] == grp) & (stats["조합(단기/중기/장기)"] == combo) & (stats["기간(일)"] == 20)]
            if len(m):
                r = m.iloc[0]
                items.append(f"과거({grp} 코인, 2018-03~개발 구간 끝) 같은 조합 '{combo}' 이후 20일: 중앙값 {_pct(r['중앙값'])}, "
                             f"하위10% {_pct(r['하위10%'])}, 오른 비율 {_p(r['오른 비율'], 0)}, 사례 {int(r['사례 수'])}건(서로 겹침 많음)")
        doc.ul(items)


def build(cfg: dict | None = None, with_news: bool = True, rebalance_now: bool = False,
          log=print) -> tuple[Path, list[dict]]:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    udir = user_dir()
    panel = Panel.load(cache / "panel")
    funding = load_funding(cache)
    lock = V.HoldoutLock(cache / "holdout_log.jsonl", cfg["validation"]["holdout_start"])
    dev_end = lock.dev_end()
    d = panel.close["BTC"].dropna().index[-1]          # 마지막으로 '끝난' 일봉
    now_kst = pd.Timestamp.now(tz=KST)
    fm = strategy.formula(cfg)

    # 살 수 있는 코인: 업비트 KRW 상장 + 유의종목 아님 (목록이 있을 때)
    allowed, warn_list = None, []
    mk = cache / "upbit_markets.csv"
    if mk.exists():
        m = pd.read_csv(mk)
        allowed = set(m.loc[~m["warning"].astype(bool), "symbol"])
        warn_list = list(m.loc[m["warning"].astype(bool), "symbol"])

    live_mcap, mcap_src = None, "Coin Metrics"
    try:
        from ..data.coingecko import top_mcap
        live_mcap = top_mcap()
        mcap_src = "CoinGecko 현재 시가총액"
    except Exception:
        ext = panel.meta.get("mcap_extended_after")
        if ext is not None and ext.notna().any():
            mcap_src = f"Coin Metrics({ext.dropna().iloc[0]}까지) + 이후 가격 변화로 연장 (CoinGecko 접속 실패)"
    W, info = strategy.target_weights(panel, cfg, allowed, funding, live_mcap)
    F = info["features"]
    core = info["core"]
    px_krw, px_src, kimchi = _krw_prices(cache, panel, d)

    doc = Doc(f"코인 퀀트 일일 리포트 · {now_kst:%Y-%m-%d} (KST)")
    doc.p(f"**{DISCLAIMER}**", "warn")
    doc.p(f"판단 기준: {d:%Y-%m-%d} 일봉 종가 (UTC 00:00 = KST 09:00 마감). 가격: {px_src}. "
          f"기타 그룹 순위: {mcap_src}. 공식 상태: **{fm['status']}**" + (" — 검증 후 확정 전이라 참고만 하세요." if fm["status"] != "확정" else ""), "muted")

    # ---------------- 1. 시장 상태
    doc.h("1. 시장 상태")
    regs = sg.regime_filters(panel, funding)
    btc = panel.close["BTC"]
    sma200 = btc.rolling(200).mean()
    rows = []
    for name, s in regs.items():
        rows.append([name, "예" if bool(s.get(d, False)) else "아니오"])
    dist200 = btc.loc[d] / sma200.loc[d] - 1
    bull = bool(regs["BTC 200일선 위"].get(d, False))
    verdict = ("상승 국면" if bull and regs["BTC EMA50>200"].get(d, False) else
               "회복 시도(200일선 위, 중기 추세 아직 약함)" if bull else
               "하락 국면(BTC 200일선 아래)")
    doc.p(f"**국면 판정: {verdict}** · BTC 200일선 대비 {_pct(dist200)}")
    if funding is not None:
        f7 = funding.loc[:d].tail(7).mean()
        rows.append(["BTC 펀딩비 7일 평균 (8시간당)", f"{f7 * 100:.4f}%"])
    if kimchi is not None and "BTC" in kimchi:
        rows.append(["김치 프리미엄 (BTC)", _pct(kimchi["BTC"], 2)])
    if panel.mcap.notna().any().any():
        dom = ind.btc_dominance(panel.mcap)
        if not np.isnan(dom.get(d, np.nan)):
            rows.append(["BTC 도미넌스 (순위 대상 코인 기준) / 30일 변화", f"{_p(dom.loc[d])} / {_pct(dom.loc[d] - dom.shift(30).loc[d], 2)}p"])
    try:  # 4년 주기 위치 (참고 정보, 매매 규칙 아님 — docs/cycle-analysis 참고)
        from ..research import cycle
        bs = cycle.btc_series(cache).loc[:d]
        h = cycle.HALVINGS[-1]
        day = (d - h).days
        seg = bs.loc[h:]
        pk_d = seg.idxmax()
        low_after = bs.loc[pk_d:].min()
        rows.append(["4년 주기 위치 (참고)", f"반감기({h.date()}) 후 {day}일 · 사이클 고점 {pk_d.date()} 대비 {_pct(bs.iloc[-1] / seg.max() - 1)}"
                     f" · 고점 후 최저 ${low_after:,.0f} (이 아래로 내려가면 과거 주기 패턴과 어긋남)"])
    except Exception:
        pass
    doc.table(["항목", "값"], rows)

    e20, e50, e200 = F.ema(20).loc[d], F.ema(50).loc[d], F.ema(200).loc[d]
    ml, ms, mh = (x.loc[d] for x in F.macd())
    vr = ind.volume_ratio(F.v, 20).loc[d]
    rv = F.vol(30).loc[d]
    atrp = (F.atr(14) / F.c).loc[d]
    r1, r7, r30 = (ind.ret(F.c, n).loc[d] for n in (1, 7, 30))
    rows = []
    for a in core:
        c = F.c.loc[d, a]
        pos = "".join("▲" if c > e[a] else "▼" for e in (e20, e50, e200))
        align = "정배열" if c > e20[a] > e50[a] > e200[a] else ("역배열" if c < e20[a] < e50[a] < e200[a] else "혼조")
        rows.append([a, _money(px_krw.get(a, np.nan)), _money(c, "USD"), f"{pos} ({align})",
                     ("MACD>시그널" if ml[a] > ms[a] else "MACD<시그널") + f", 히스토그램 {'+' if mh[a] > 0 else '−'}",
                     f"{vr[a]:.2f}배" if not np.isnan(vr[a]) else "-", _p(rv[a]), _p(atrp[a], 2),
                     _pct(r1[a]), _pct(r7[a]), _pct(r30[a])])
    doc.h("코어 3종 지표", 3)
    doc.table(["코인", "원화", "달러", "20/50/200 EMA 위치", "MACD", "거래량(20일 평균 대비)", "30일 변동성(연)", "ATR14/가격",
               "1일", "7일", "30일"], rows)

    # ---------------- 2. 이슈
    doc.h("2. 오늘의 이슈")
    if with_news:
        items, errs = news_mod.collect(cfg["report"]["news_max_items"], log=log)
        if items:
            for i, it in enumerate(items, 1):
                when = it["time"].tz_localize("UTC").tz_convert(KST).strftime("%m-%d %H:%M") if not pd.isna(it["time"]) else "시각 미상"
                coins = ", ".join(it["coins"]) or "시장 전반"
                cats = ", ".join(it["categories"]) or "-"
                doc.p(f"**{i}. [{it['title']}]({it['link']})** · {it['source']} · {when} KST")
                doc.ul([f"구분: **{it['kind']}**", f"주제: {cats}", f"영향 코인: {coins}"])
        else:
            doc.p("가져온 이슈가 없습니다 (뉴스 출처 접속 실패 또는 해당 기사 없음).")
        if errs:
            doc.p("접속 실패한 출처: " + ", ".join(errs), "muted")
        doc.p("'확인된 사실/보도/추측' 구분은 출처·표현으로 자동 분류한 것이라 틀릴 수 있습니다. 링크의 원문을 확인하세요.", "muted")
    else:
        doc.p("(뉴스 수집 생략)")

    # ---------------- 3. 추천
    doc.h("3. 계산상 추천 (공식만)")
    reb = int(cfg["portfolio"]["rebalance_days"])
    is_reb = rebalance_now or ((d - pd.Timestamp("2018-03-01")).days % reb) == 0
    w_today = W.loc[d]
    w_today = w_today[w_today > 1e-9]
    w_today = pd.Series(engine.apply_caps(w_today.to_numpy(), cfg["portfolio"]["max_weight_per_coin"],
                                          cfg["portfolio"]["max_total_invest"]), index=w_today.index)
    cur_w, total_krw, src = portfolio.current(cfg, udir, px_krw, log)
    jd = Judgments(udir)
    active = jd.active()
    final_w = Judgments.apply(w_today, cur_w, active, cfg["portfolio"]["max_total_invest"])
    jd.journal(d, w_today, final_w)
    doc.p(f"공식: 코어 {fm['core_signal']} · 기타 {fm['others_select']}"
          f"{'(' + str(fm['others_k']) + '개)' if fm['others_select'] != '균등' else ''} + {fm['others_signal']} · "
          f"국면 필터 {fm['regime_filter']} · 리밸런스 {reb}일마다 "
          f"(오늘은 {'리밸런스 날' if is_reb else '리밸런스 날 아님 → 공식상 주문 없음, 아래는 참고용 목표'}). "
          f"현재 보유 출처: {src}, 평가금 {_money(total_krw)}.")
    hist_cache: dict = {}
    min_tr = cfg["portfolio"]["min_trade_weight"]
    rows, orders = [], []
    idx = final_w.index.union(cur_w.index).union(w_today.index)
    for a in sorted(idx, key=lambda x: -(final_w.get(x, 0) + cur_w.get(x, 0))):
        fw, tw, cw = w_today.get(a, 0.0), final_w.get(a, 0.0), cur_w.get(a, 0.0)
        delta = tw - cw
        action = "보유" if abs(delta) < min_tr and not (tw == 0 and cw > 0) else ("매수" if delta > 0 else "매도")
        if tw == 0 and cw == 0:
            continue
        group = "코어" if a in core else "기타"
        sig_name = fm["core_signal"] if a in core else fm["others_signal"]
        on = (info["core_on"] if a in core else info["others_on"])
        sig_state = "켜짐" if (a in on.columns and bool(on.loc[d, a])) else ("-" if a not in on.columns else "꺼짐")
        h = _signal_history(panel, cfg, sig_name, a, dev_end, hist_cache) if a in panel.close else {}
        why = [f"신호 '{sig_name}' {sig_state}"]
        if a in F.c.columns:
            why.append(f"30일 {_pct(ind.ret(F.c, 30).loc[d, a])}, 200일선 대비 {_pct(F.c.loc[d, a] / F.sma(200).loc[d, a] - 1)}")
        if a in active:
            why.append(f"내 판단: {ACTIONS[active[a]['action']]}")
        rows.append([a, group, action, _p(cw), _p(fw), _p(tw), " · ".join(why),
                     f"{h.get('n', 0)}회 / {_p(h.get('win'), 0)} / {_pct(h.get('mean'))} / {_pct(h.get('worst'))}" if h else "-"])
        if action != "보유" and is_reb:
            orders.append({"asset": a, "side": "bid" if delta > 0 else "ask", "weight_delta": float(delta),
                           "krw": float(abs(delta) * total_krw), "target_weight": float(tw), "full_exit": bool(tw == 0)})
    cash_w = 1 - final_w.sum()
    doc.table(["코인", "그룹", "추천", "지금 비중", "공식 목표", "최종 목표(내 판단 반영)", "근거", "과거(개발 구간): 거래수/승률/평균/최악"], rows)
    doc.p(f"현금 목표 비중 {_p(cash_w)}. '과거' 열은 같은 신호를 그 코인 하나에 적용했을 때 {dev_end:%Y-%m-%d}까지의 "
          "거래 단위 성적(비용 포함)입니다. 최종 검증 구간은 잠가 두어 여기 포함하지 않았습니다.", "muted")
    if not is_reb:
        orders = []
    (udir / "orders-latest.json").write_text(json.dumps({"date": d.strftime("%Y-%m-%d"), "total_krw": total_krw,
                                                         "orders": orders}, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------- 3-1. 스윙 매매 계획 (기간별 추세와 근거)
    _swing_plan(doc, cfg, F, d, core, final_w, cur_w, info)

    # ---------------- 4. 내 판단
    doc.h("4. 내 판단 반영 결과와 성과")
    if active:
        doc.table(["코인", "판단", "값", "이유", "기록 시각", "유효 기한"],
                  [[a, ACTIONS[r["action"]], _p(r["value"]) if r.get("value") is not None else "-", r["reason"],
                    r["time"][:16].replace("T", " "), r.get("until") or "-"] for a, r in active.items()])
    else:
        doc.p("지금 유효한 내 판단이 없습니다. `python -m coinquant judge lock BTC \"이유\"` 처럼 넣을 수 있어요.")
    cmp_ = jd.compare(panel.close)
    if cmp_ and cmp_.get("days", 0) >= 2 and "formula_return" in cmp_:
        doc.p(f"일지 {cmp_['days']}일 기준 가상 성과(다음 날 종가 수익, 매매비용 0.2% 근사): 공식만 {_pct(cmp_['formula_return'])} vs "
              f"공식+내 판단 {_pct(cmp_['final_return'])} · 내 판단이 이긴 날 {_p(cmp_['win_days'], 0)}")
        pj = jd.per_judgment(panel.close)
        if not pj.empty:
            doc.table(list(pj.columns), [[*r[:-1], _pct(r[-1], 2)] for r in pj.itertuples(index=False)])
    else:
        doc.p("성과 비교는 일지가 2일 이상 쌓이면 표시됩니다.", "muted")

    # ---------------- 5. 위험 경고
    doc.h("5. 위험 경고")
    warns = []
    if funding is not None and funding.loc[:d].tail(7).mean() > 0.0003:
        warns.append(f"선물 과열: BTC 펀딩비 7일 평균 {funding.loc[:d].tail(7).mean() * 100:.3f}%/8h (0.03% 초과)")
    if dist200 > 0.6:
        warns.append(f"BTC가 200일선보다 {_pct(dist200)} 위 — 과거 과열 구간과 비슷한 이격")
    for e in _events(7, now_kst.tz_localize(None)):
        warns.append(f"큰 일정 D-{e['days']}: {e['name']} ({e['date']})" + (f" [출처]({e['source']})" if e.get("source") else ""))
    univ = core + [c for c in info["elig"].columns if bool(info["elig"].loc[d].get(c, False))]
    r1d = ind.ret(panel.close[univ], 1).loc[d]
    for a, v in r1d.sort_values().items():
        if (a in core and v <= -0.10) or (a not in core and v <= -0.15):
            warns.append(f"급락: {a} 하루 {_pct(v)} (과거 급락 다음 날 처리 비교는 docs/validation 4절)")
    for a in warn_list:
        if a in cur_w.index:
            warns.append(f"업비트 유의종목 보유 중: {a}")
    # 공식이 약했던 시대와 비슷한지
    try:
        dev = panel.slice(None, dev_end)
        Wd, _ = strategy.target_weights(dev, cfg, None, funding)
        c = cfg["costs"]
        costs = engine.Costs(c["fee"], c["slippage_core"], c["slippage_other"], tuple(core))
        cols = list(Wd.columns[(Wd != 0).any()])
        res = engine.run(Wd[cols], dev.open[cols], dev.close[cols], costs,
                         engine.Rules(rebalance_days=reb, max_weight=cfg["portfolio"]["max_weight_per_coin"],
                                      min_trade=min_tr), dev.tradable[cols], start="2018-03-01")
        btc_eq = dev.close["BTC"].loc["2018-03-01":]
        et = V.era_table(res.equity, {"BTC": btc_eq})
        now_f = _era_features(btc, d)
        feats = []
        for name, s, e in V.ERAS:
            if name in set(et["시대"]):
                ef = np.nanmean([_era_features(btc, t) for t in pd.date_range(s, e, freq="15D")], axis=0)
                feats.append((name, ef))
        if feats:
            M = np.array([f for _, f in feats])
            sd = np.nanstd(M, axis=0) + 1e-9
            dist = [np.nansum(((f - now_f) / sd) ** 2) for _, f in feats]
            near = feats[int(np.argmin(dist))][0]
            row = et[et["시대"] == near].iloc[0]
            weak = row["수익"] < row["BTC 수익"]
            warns.append(f"지금 시장(BTC 90일 수익·30일 변동성·200일선 위 비율)은 '{near}' 시기와 가장 비슷합니다. 그 시기 현재 공식 성과: "
                         f"수익 {_pct(row['수익'])}, MDD {_pct(row['MDD'])} (BTC {_pct(row['BTC 수익'])})"
                         + (" → 공식이 BTC 보유보다 약했던 시대입니다." if weak else "."))
    except Exception as e:  # 경고 계산 실패가 리포트를 막지 않게
        warns.append(f"(시대 비교 계산 실패: {type(e).__name__})")
    doc.ul(warns or ["특이 경고 없음"])

    doc.h("안내", 3)
    doc.p(DISCLAIMER, "muted")
    doc.p("API 키: 조회·주문 권한만, 출금 권한은 절대 켜지 마세요. 허용 IP를 지정하고, 키는 .env 에만 두세요(GitHub·대화창 금지).", "muted")

    out_dir = config_mod.ROOT / cfg["report"]["output_dir"]
    out_dir.mkdir(exist_ok=True)
    stem = f"daily-{now_kst:%Y-%m-%d}"
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(doc.md(), encoding="utf-8")
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(doc.html(), encoding="utf-8")
    log(f"리포트: {html_path if cfg['report']['format'] == 'html' else md_path}")
    return (html_path if cfg["report"]["format"] == "html" else md_path), orders
