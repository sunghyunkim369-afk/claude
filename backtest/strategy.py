"""매수·매도 신호. 신호는 그날 종가 기준으로 계산하고, 체결은 엔진이 다음 날 시가에 해요.

손절·트레일링 스탑 가격도 여기서 계산해요 (전날까지의 값으로 정해진 가격이라 장중 주문으로 걸어둘 수 있어요).
"""

import pandas as pd


def add_signals(d, entry, exit_cfg):
    d = d.copy()
    prev_macd, prev_sig = d["macd"].shift(1), d["macd_signal"].shift(1)
    golden = (prev_macd <= prev_sig) & (d["macd"] > d["macd_signal"])
    dead = (prev_macd >= prev_sig) & (d["macd"] < d["macd_signal"])
    d["golden_cross"] = golden.fillna(False)
    d["dead_cross"] = dead.fillna(False)
    d["buy_signal"] = (
        (d["Close"] > d["ema_slow"])
        & (d["adx"] > entry["adx_min"])
        & d["golden_cross"]
        & (d["rsi"] < entry["rsi_max"])
        & (d["Volume"] > d["vol_ma"])
    ).fillna(False)
    d["sell_signal"] = d["dead_cross"] if exit_cfg.get("macd_dead_cross", True) else False
    # 신호 순위(빈자리보다 신호가 많을 때): ADX 가 높을수록 추세가 강하다고 봐요.
    d["rank"] = d["adx"]
    return d


def initial_stop(entry_price, atr_at_signal, exit_cfg):
    return entry_price - exit_cfg["stop_atr"] * atr_at_signal


def stop_level(initial, highest_high, atr, exit_cfg):
    """오늘 장중에 걸어둘 매도 가격: 손절가와 트레일링 가격 중 높은 쪽.
    highest_high, atr 는 어제 종가까지의 값이어야 해요."""
    trail = highest_high - exit_cfg["trail_atr"] * atr if pd.notna(atr) else float("-inf")
    return max(initial, trail)
