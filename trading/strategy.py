"""매매 규칙. 전략을 바꾸려면 이 파일의 decide() 만 고치면 돼요.

기본 규칙 (이동평균 추세추종 + 손절·익절):
- 매수: 현재가 > 20일선 > 60일선 (상승 추세) 이고, 현재가가 20일선보다 5% 이내로만 높을 때 (너무 오른 뒤 쫓아 사지 않기)
- 매도: 손실 -8% 이하(손절), 수익 +20% 이상(익절), 또는 현재가가 60일선 아래로 내려갈 때(추세 이탈)
"""

DEFAULTS = {
    "short_ma": 20,
    "long_ma": 60,
    "max_above_short_pct": 5.0,
    "stop_loss_pct": -8.0,
    "take_profit_pct": 20.0,
}


def sma(values, n):
    return sum(values[-n:]) / n


def decide(closes, price, holding=None, params=None):
    """매수/매도 판단.

    closes: 과거 일봉 종가 (오래된 → 최근). 오늘 장중이면 마지막 값은 오늘 값이라 현재가로 바꿔 계산해요.
    price: 현재가
    holding: 보유 중이면 {"qty", "avg_price"} 아니면 None
    반환: ("buy" | "sell" | None, 이유)
    """
    p = {**DEFAULTS, **(params or {})}
    series = list(closes) + [price]
    if len(series) < p["long_ma"] + 1:
        return None, f"일봉 데이터 부족 ({len(series)}개, {p['long_ma'] + 1}개 필요)"

    ma_s = sma(series, p["short_ma"])
    ma_l = sma(series, p["long_ma"])

    if holding:
        pnl = (price / holding["avg_price"] - 1) * 100 if holding.get("avg_price") else 0.0
        if pnl <= p["stop_loss_pct"]:
            return "sell", f"손절 ({pnl:+.1f}%)"
        if pnl >= p["take_profit_pct"]:
            return "sell", f"익절 ({pnl:+.1f}%)"
        if price < ma_l:
            return "sell", f"추세 이탈: 현재가 {price:,.2f} < {p['long_ma']}일선 {ma_l:,.2f} ({pnl:+.1f}%)"
        return None, f"보유 유지 ({pnl:+.1f}%)"

    if price > ma_s > ma_l:
        gap = (price / ma_s - 1) * 100
        if gap <= p["max_above_short_pct"]:
            return "buy", f"상승 추세, {p['short_ma']}일선 위 {gap:.1f}%"
        return None, f"상승 추세지만 {p['short_ma']}일선보다 {gap:.1f}% 높아 대기"
    return None, "상승 추세 아님"
