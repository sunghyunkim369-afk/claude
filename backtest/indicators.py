"""지표 계산. pandas-ta 가 있으면 pandas-ta 로, 없으면 같은 공식의 내장 계산으로 해요.

모든 값은 그날 종가까지의 데이터만 써요 (미래 데이터 없음).
"""

import pandas as pd

try:
    import pandas_ta as ta
except Exception:  # 설치 안 됨(Python 3.12 미만 등)
    ta = None


# ---------- 내장 계산 (pandas-ta 와 같은 정의: EMA 는 SMA 로 시작, RSI·ATR·ADX 는 Wilder RMA) ----------

def _ema(s, n):
    sma = s.rolling(n, min_periods=n).mean()
    first = sma.first_valid_index()
    out = pd.Series(float("nan"), index=s.index)
    if first is None:
        return out
    seed = s.copy()
    seed.loc[:first] = float("nan")
    seed.loc[first] = sma.loc[first]
    return seed.ewm(span=n, adjust=False, ignore_na=True).mean().where(out.index >= first)


def _rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _true_range(h, l, c):
    prev = c.shift(1)
    return pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)


def _rsi(c, n):
    d = c.diff()
    up = _rma(d.clip(lower=0), n)
    down = _rma((-d).clip(lower=0), n)
    return 100 * up / (up + down)


def _atr(h, l, c, n):
    return _rma(_true_range(h, l, c), n)


def _adx(h, l, c, n):
    up = h.diff()
    down = -l.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = _atr(h, l, c, n)
    plus_di = 100 * _rma(plus_dm, n) / atr
    minus_di = 100 * _rma(minus_dm, n) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return _rma(dx, n)


def _macd(c, fast, slow, signal):
    macd = _ema(c, fast) - _ema(c, slow)
    return macd, _ema(macd.dropna(), signal).reindex(c.index)


# ---------- 공개 함수 ----------

def add_indicators(df, p, use_pandas_ta=True):
    """df(Open/High/Low/Close/Volume)에 지표 열을 붙여서 새 DataFrame 으로 돌려줘요."""
    d = df.copy()
    h, l, c, v = d["High"], d["Low"], d["Close"], d["Volume"]
    if use_pandas_ta and ta is not None:
        d["ema_fast"] = ta.ema(c, p["ema_fast"])
        d["ema_slow"] = ta.ema(c, p["ema_slow"])
        d["adx"] = ta.adx(h, l, c, p["adx"]).iloc[:, 0]
        m = ta.macd(c, p["macd_fast"], p["macd_slow"], p["macd_signal"])
        d["macd"] = m.iloc[:, 0]
        d["macd_signal"] = m.iloc[:, 2]
        d["rsi"] = ta.rsi(c, p["rsi"])
        d["atr"] = ta.atr(h, l, c, p["atr"])
        d["vol_ma"] = ta.sma(v, p["volume_ma"])
    else:
        d["ema_fast"] = _ema(c, p["ema_fast"])
        d["ema_slow"] = _ema(c, p["ema_slow"])
        d["adx"] = _adx(h, l, c, p["adx"])
        d["macd"], d["macd_signal"] = _macd(c, p["macd_fast"], p["macd_slow"], p["macd_signal"])
        d["rsi"] = _rsi(c, p["rsi"])
        d["atr"] = _atr(h, l, c, p["atr"])
        d["vol_ma"] = v.rolling(p["volume_ma"], min_periods=p["volume_ma"]).mean()
    return d


def engine_name(use_pandas_ta=True):
    return "pandas-ta" if (use_pandas_ta and ta is not None) else "내장 계산"
