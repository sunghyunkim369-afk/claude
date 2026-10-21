"""하한가 근처(하루 −25% 이하) 급락 다음 날 처리 비교 (2015-06-15 ~, 전 종목, marcap 자료).
실행: python limit_down.py <marcap 폴더> > results/limit_down.txt
결과: 다음 날 시가 매도가 반등 노리기·며칠 보유보다 평균적으로 나았어요 → trading/auto.py kr_sell_crashed."""
import sys, numpy as np, pandas as pd
D = sys.argv[1]
fr = []
for y in range(2015, 2027):
    df = pd.read_parquet(f"{D}/marcap-{y}.parquet", columns=["Code","Name","Market","Date","Open","High","Low","Close","ChangesRatio","Marcap","Volume"])
    fr.append(df)
df = pd.concat(fr)
df["Date"] = pd.to_datetime(df["Date"])
df = df[df["Date"] >= "2015-06-15"]
code, name = df["Code"].astype(str), df["Name"].astype(str)
df = df[code.str.fullmatch(r"\d{5}0") & ~name.str.contains(r"우$|우B$|스팩|리츠") & df["Market"].isin(["KOSPI","KOSDAQ","KOSDAQ GLOBAL"])]
df = df.sort_values(["Code","Date"])
g = df.groupby("Code")
for c in ("Open","High","Low","Close","ChangesRatio","Volume"):
    df["n_"+c] = g[c].shift(-1)
df["n2_Close"] = g["Close"].shift(-2)
df["n5_Close"] = g["Close"].shift(-5)
df["p_ret20"] = df["Close"] / g["Close"].shift(20) - 1          # 급락 전 20일 수익률 (급락일 포함)
ev = df[(df["ChangesRatio"] <= -25) & df["n_Open"].gt(0) & df["n_Volume"].gt(0)].copy()
cost = 0.0043
def stats(r):
    r = r.dropna()
    return f"{len(r):5d}건 평균 {r.mean()*100:+6.2f}% 중앙값 {r.median()*100:+6.2f}% 이긴비율 {(r>0).mean()*100:4.0f}% 하위10% {r.quantile(.1)*100:+6.1f}%"
def report(e, label):
    c = e["Close"]
    print(f"\n[{label}] (급락일 종가에 들고 있다고 볼 때, 매도 비용 0.2% 제외 안 함)")
    print("  다음날 시가 매도      ", stats(e["n_Open"]/c-1))
    print("  다음날 종가 매도      ", stats(e["n_Close"]/c-1))
    print("  2일 뒤 종가 매도      ", stats(e["n2_Close"]/c-1))
    print("  5일 뒤 종가 매도      ", stats(e["n5_Close"]/c-1))
    for x in (0.03, 0.05, 0.10):
        tgt = c*(1+x)
        hit = e["n_High"] >= tgt
        r = np.where(hit, np.maximum(e["n_Open"], tgt)/c-1, e["n_Close"]/c-1)
        print(f"  다음날 +{x*100:.0f}% 지정가 매도, 안 닿으면 종가 ", stats(pd.Series(r)), f" (닿은 비율 {hit.mean()*100:.0f}%)")
    print("  다음날 고가(최대, 알 수 없음)", stats(e["n_High"]/c-1))
    print("  다음날 시가 갭: 하한가 다음날 또 −20% 이하로 시작", f"{(e['n_Open']/c-1 <= -0.2).mean()*100:.1f}%")
report(ev, "하한가 근처(−25% 이하) 급락 전체")
report(ev[ev["p_ret20"] > 0], "급락 전 20일 수익률이 플러스였던 종목 (급등 후 폭락, 이번 같은 경우)")
big = ev[ev["Marcap"] >= 3e11]
report(big, "시총 3천억 이상")
