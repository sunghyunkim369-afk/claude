"""단기 모드 (모의) — 근거: docs/short-swing-2026-10-21.md

규칙 (미리 정한 그대로, 숫자를 새로 고르지 않음):
- 코인별로 단기 점수(8/21일선·20일 채널·14일 기울기) +2 이상이면 켜고, 0 이하면 끔. 코어 3개에 똑같이 나눔.
- 전체 자금 중 sleeve(기본 30%)만. 나머지 70%는 확정 공식 그대로.
- 개발 구간(2018-03~2024-12) 코어3: 확정 공식만 샤프 1.65 / MDD -42% → 70:30 섞으면 1.77 / -38% (조금 나음).
  단기만 따로 쓰면 MDD -54%, 비용 연 3%로 이득 없음 → 단독 운용은 하지 않음.
- 깨끗한 검증 구간이 남아 있지 않아 start 날짜부터 앞으로의 새 데이터로 '모의' 성적을 쌓고, 2~3개월 뒤 실제 비중을 판단.
모의 성적은 저장 파일 없이 가격 기록에서 매번 다시 계산 (예약이 빠진 날이 있어도 틀어지지 않음).
"""
from __future__ import annotations

import pandas as pd

from ..trend import MultiTrend, num

COST = 0.0015   # 한 번 사거나 팔 때: 업비트 수수료 0.05% + 슬리피지 0.1%


def paper(close: pd.DataFrame, state: pd.DataFrame, start: pd.Timestamp) -> dict:
    """start 날 종가부터 규칙대로 사고팔았다고 치고 계산. 판단·체결 모두 종가(매일 09:00 KST 마감 직후 주문 가정)."""
    c = close.loc[start:]
    st = state.reindex(c.index).fillna(False).astype(float)
    if c.empty:
        return {"ret": 0.0, "days": 0, "trades": 0, "open": {}, "btc": 0.0}
    turn = st.diff().abs()
    turn.iloc[0] = st.iloc[0]                       # 시작날 켜져 있으면 그날 산 것으로
    pos = st.shift(1).fillna(0.0)                   # 어제 정한 상태로 오늘 수익
    daily = (pos * c.pct_change().fillna(0.0) - turn * COST).mean(axis=1)
    eq = float((1 + daily).prod() - 1)
    opened = {}
    for a in c.columns:
        s = st[a]
        if s.iloc[-1] > 0:
            off = s[s == 0]
            ent = s.index[0] if off.empty else s.index[s.index > off.index[-1]][0]
            opened[a] = {"date": ent, "price": float(c.at[ent, a]), "pnl": float(c[a].iloc[-1] / c.at[ent, a] - 1 - 2 * COST)}
    btc = float(c["BTC"].iloc[-1] / c["BTC"].iloc[0] - 1) if "BTC" in c else 0.0
    return {"ret": eq, "days": len(c) - 1, "trades": int((turn > 0).sum().sum()), "open": opened, "btc": btc}


def lines(mt: MultiTrend, d: pd.Timestamp, cfg: dict) -> tuple[list[str], list[str]]:
    """(오늘 할 일에 넣을 줄, 단기 모드 칸 줄). 꺼져 있으면 ([], [])."""
    sc = cfg.get("short_mode", {})
    if not sc.get("enabled", False):
        return [], []
    S = mt.h["단기"]
    cols = [a for a in S.state.columns if not pd.isna(mt.close.at[d, a])]
    state = S.state[cols].loc[:d]
    sleeve = float(sc.get("sleeve", 0.3))
    start = pd.Timestamp(sc.get("start", str(d.date())))
    mode = "모의" if sc.get("paper", True) else "실제"
    prev_d = state.index[state.index < d]
    flips = []
    if len(prev_d):
        p = prev_d[-1]
        flips = [(a, bool(state.at[d, a])) for a in cols if bool(state.at[d, a]) != bool(state.at[p, a])]
    todo = []
    if flips:
        todo.append(f"⚡ <b>단기({mode}) 신호</b>: " + ", ".join(f"{a} {'켜짐 → 매수' if x else '꺼짐 → 매도'}" for a, x in flips)
                    + ("  <i>기록만, 실제 주문 아님</i>" if mode == "모의" else ""))
    L = [f"<b>⚡ 단기 모드 ({mode})</b>  <i>자금의 {sleeve:.0%} · 단기 +2 켜기 / 0 이하 끄기 · 며칠~2주</i>"]
    for a in cols:
        s = int(S.score.at[d, a]) if not pd.isna(S.score.at[d, a]) else 0
        if state.at[d, a]:
            L.append(f"🟢 <b>{a} 보유</b>  단기 {s:+d}  ↳ 0 이하로 마감하면 매도")
        else:
            trig = float(S.chan_lo.at[d, a] + 0.6 * (S.chan_hi.at[d, a] - S.chan_lo.at[d, a]))
            L.append(f"⚪ {a} 대기  단기 {s:+d}  ↳ 켜지는 기준 대략 ${num(trig)}↑")
    pp = paper(mt.close[cols].loc[:d], state, start)
    if pp["days"] > 0:
        L.append(f"📒 {mode} 성적 {start:%y/%m/%d}~: <b>{pp['ret']:+.1%}</b> (단기 몫 기준 · 매매 {pp['trades']}번, 비용 포함) · 같은 기간 BTC 보유 {pp['btc']:+.1%}")
        for a, o in pp["open"].items():
            L.append(f"   ↳ {a} {o['date']:%m/%d} ${num(o['price'])} 진입 → {o['pnl']:+.1%}")
    else:
        L.append(f"📒 {mode} 성적: {start:%m/%d}부터 기록 시작")
    if mode == "모의":
        L.append("   <i>2~3개월 모의 성적을 보고 실제 비중을 결정 · 지금은 주문 없음</i>")
    return todo, L
