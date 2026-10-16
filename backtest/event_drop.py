"""대형주 급락 다음 날 매수 검증 (이벤트 스터디).

질문: "삼성전자처럼 큰 종목이 하루에 크게 빠졌을 때 사면 대회 기간(약 26거래일) 동안 좋은가?"
- 대상: universe_kr.csv 앞쪽 코스피 시총 상위 30종목
- 이벤트: 그날 종가가 전날 종가보다 thr 이상 하락 (같은 종목은 5거래일 안에 다시 나온 이벤트는 뺌)
- 매수: 이벤트 날 종가 (오늘 장 마감 전에 사는 경우) / 다음 날 시가 두 가지
- 결과: 5·10·26 거래일 뒤 종가까지 수익률. 같은 종목들의 '아무 날에나 샀을 때'와 비교
- 시장 전체가 같이 빠진 날(30종목 평균 −2% 이하)과 그 종목만 빠진 날을 나눠서도 봐요

실행: python event_drop.py → results/event_drop.txt
"""

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from data import load_all  # noqa: E402
from main import load_config  # noqa: E402

HORIZONS = (1, 2, 3, 5, 10, 26)
ROUND_TRIP = 0.0043  # 한 번 사고팔 때 수수료·세금·체결 손해 (config.yaml 기준)
THRESHOLDS = (0.04, 0.07)


def events(frames, thr, market_ret, gap=5):
    """[(종목, 날짜 위치, 시장 같이 하락?)]"""
    out = []
    for s, f in frames.items():
        r = f["Close"].pct_change()
        last = -10 ** 9
        for i in np.where(r.values <= -thr)[0]:
            if i - last < gap:
                continue
            last = i
            d = f.index[i]
            out.append((s, i, bool(market_ret.get(d, 0.0) <= -0.02)))
    return out


def forward(f, i, h, entry, exit_at="close"):
    """i 일 이벤트, entry='close' 면 그날 종가, 'open' 이면 다음 날 시가에 사서 i+h 일 종가(exit_at='open' 이면 시가)까지."""
    if i + h >= len(f) or (entry == "open" and h < 1):
        return None
    buy = f["Close"].iloc[i] if entry == "close" else f["Open"].iloc[i + 1]
    sell = f["Close"].iloc[i + h] if exit_at == "close" else f["Open"].iloc[i + h]
    return sell / buy - 1


def stats(rets):
    r = np.array([x for x in rets if x is not None])
    if not len(r):
        return {"건수": 0}
    return {"건수": len(r), "평균": r.mean(), "중앙값": np.median(r), "오른 비율": (r > 0).mean(),
            "하위10%": np.percentile(r, 10), "상위10%": np.percentile(r, 90),
            "비용 뺀 평균": r.mean() - ROUND_TRIP, "비용 뺀 승률": (r > ROUND_TRIP).mean()}


def fmt(df):
    shown = df.copy()
    for c in ("평균", "중앙값", "오른 비율", "하위10%", "상위10%", "비용 뺀 평균", "비용 뺀 승률"):
        if c in shown:
            pct = c in ("오른 비율", "비용 뺀 승률")
            shown[c] = shown[c].map(lambda v: "" if v != v else f"{v * 100:.0f}%" if pct else f"{v * 100:+.2f}%")
    return shown.to_string(index=False)


def main():
    cfg = load_config(os.path.join(HERE, "config.yaml"))
    uni = pd.read_csv(os.path.join(HERE, "universe_kr.csv"), dtype={"code": str})
    names = dict(zip(uni["code"], uni["name"]))
    big = list(uni[uni["index"] == "KOSPI200"]["code"])[:30]
    cache = os.path.join(HERE, cfg["report"]["cache_dir"]) if cfg["report"].get("cache_dir") else None
    raw, start, end = load_all(big, cfg["period"], cache, log=lambda *_: None)
    frames = {s: f[f.index >= start] for s, f in raw.items() if len(f[f.index >= start]) > 60}
    closes = pd.DataFrame({s: f["Close"] for s, f in frames.items()})
    market_ret = closes.pct_change().mean(axis=1).to_dict()

    lines = [f"코스피 시총 상위 {len(frames)}종목, {start:%Y-%m-%d} ~ {end:%Y-%m-%d}", ""]
    rows = []
    for h in HORIZONS:
        base = [forward(f, i, h, "close") for f in frames.values() for i in range(1, len(f))]
        rows.append({"조건": "아무 날에나 종가 매수 (기준)", "기간": f"{h}일", **stats(base)})
        for thr in THRESHOLDS:
            ev = events(frames, thr, market_ret)
            for entry, label in (("close", "그날 종가"), ("open", "다음 날 시가")):
                rows.append({"조건": f"−{thr * 100:.0f}% 이상 하락 → {label}", "기간": f"{h}일",
                             **stats([forward(frames[s], i, h, entry) for s, i, _ in ev])})
            for together, label in ((True, "시장도 −2% 이하"), (False, "그 종목만")):
                rows.append({"조건": f"−{thr * 100:.0f}% 이상, {label} → 그날 종가", "기간": f"{h}일",
                             **stats([forward(frames[s], i, h, "close") for s, i, m in ev if m == together])})
    lines += ["대형주 급락 날 매수 후 수익률 ('비용 뺀' 열 말고는 비용 제외):", fmt(pd.DataFrame(rows)), ""]

    # 급락 날 종가에 사서 다음 날 시가(밤사이 갭만) / 다음 날 종가에 파는 초단기
    rows = []
    base = [forward(f, i, 1, "close", "open") for f in frames.values() for i in range(1, len(f))]
    rows.append({"조건": "아무 날에나 종가 매수 → 다음 날 시가 매도 (기준)", **stats(base)})
    for thr in THRESHOLDS:
        ev = events(frames, thr, market_ret)
        for together, label in ((None, "전체"), (True, "시장도 −2% 이하"), (False, "그 종목만")):
            sel = [(s, i) for s, i, m in ev if together is None or m == together]
            rows.append({"조건": f"−{thr * 100:.0f}% 이상, {label} → 다음 날 시가 매도",
                         **stats([forward(frames[s], i, 1, "close", "open") for s, i in sel])})
    lines += ["급락 날 종가 매수 → 다음 날 시가 매도 (밤사이 갭만):", fmt(pd.DataFrame(rows)), ""]

    for code in ("005930", "000660"):
        f = frames.get(code)
        if f is None:
            continue
        ev = events({code: f}, 0.04, market_ret)
        rows = []
        for s, i, m in ev:
            rows.append({"날짜": f"{f.index[i]:%Y-%m-%d}", "그날": f["Close"].iloc[i] / f["Close"].iloc[i - 1] - 1,
                         **{f"{h}일 뒤": forward(f, i, h, "close") for h in HORIZONS}, "시장 같이 하락": m})
        df = pd.DataFrame(rows)
        for c in ["그날"] + [f"{h}일 뒤" for h in HORIZONS]:
            df[c] = df[c].map(lambda v: "-" if v is None or v != v else f"{v * 100:+.1f}%")
        lines += [f"{names.get(code, code)}({code}) 하루 −4% 이상 하락한 날과 그 뒤 (그날 종가 매수):", df.to_string(index=False), ""]

    text = "\n".join(lines)
    print(text)
    out = os.path.join(HERE, cfg["report"].get("output_dir", "results"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "event_drop.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    main()
