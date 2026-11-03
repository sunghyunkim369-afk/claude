"""확정 공식을 일봉·4시간봉·1시간봉으로 계산해 비교 (같은 기간 숫자 ×6·×24, 업비트 현물 비용 0.15%, 바이낸스 선물 1시간봉 2020-09~).
실행: PYTHONPATH=. python coinquant/research/timeframe_check.py  — 결과: docs/timeframe-check-2026-11-03.md"""
import time, numpy as np, pandas as pd
from coinquant import config as cm
from coinquant.research import futures_lab as fl
from coinquant.trend import HORIZONS, horizon_trend
t0=time.time()
cfg=cm.load(); cache=cm.cache_dir(cfg)
h1=fl.load_1h(cache,log=lambda *a:None)
def bars(rule,need):
    agg={"open":"first","high":"max","low":"min","close":"last"}
    per={}
    for a,df in h1.items():
        g=df.resample(rule,label="left",closed="left"); x=g.agg(agg); per[a]=x[g.size()>=need].dropna()
    return {k:pd.DataFrame({a:per[a][k] for a in per}).sort_index() for k in agg}
COST=0.0015
def formula_pos(B,mult):
    c,h,l=B["close"],B["high"],B["low"]
    hz={k:{kk:(v*mult if isinstance(v,int) else v) for kk,v in p.items()} for k,p in HORIZONS.items()}
    M=horizon_trend(c,h,l,hz["중기"]); L=horizon_trend(c,h,l,hz["장기"])
    return (M.state & ~(L.score<=-2)).astype(float).where(c.notna(),0.0)
def run(B,pos,bpy):
    c=B["close"]; r=c.pct_change().fillna(0)
    R=pos.shift(1).fillna(0)*r - pos.diff().abs().fillna(pos.abs())*COST
    pr=R.where(c.notna()).mean(axis=1)
    out={}
    for lab,(a,b) in {"dev":("2020-09-15","2024-12-31"),"hold":("2025-01-01","2026-10-31")}.items():
        x=pr.loc[a:b]; d=(1+x).groupby(x.index.floor("1D")).prod()-1   # 일 수익으로 모아 같은 기준 비교
        p=fl.perf(d,365); p["trades"]=int((pos.diff().abs().loc[a:b]>0).sum().sum()); out[lab]=(p,d)
    return out
def sharpe_ci(d,n=600,block=30,seed=1):
    x=d.to_numpy(); T=len(x); rng=np.random.default_rng(seed); k=int(np.ceil(T/block)); s=[]
    for _ in range(n):
        idx=(rng.integers(0,T-block,k)[:,None]+np.arange(block)).ravel()[:T]; y=x[idx]; s.append(y.mean()/y.std()*np.sqrt(365))
    return np.quantile(s,[0.05,0.95])
D=bars("1D",24); B4=bars("4h",4); B1={k:v for k,v in {"open":None}.items()}
res={}
res["일봉 (지금 공식)"]=run(D,formula_pos(D,1),365)
res["4시간봉 · 같은 기간(숫자×6) · 4시간마다 판단"]=run(B4,formula_pos(B4,6),2190)
res["4시간봉 · 같은 봉 개수(6배 빠름)"]=run(B4,formula_pos(B4,1),2190)
print("4h done",f"{time.time()-t0:.0f}s",flush=True)
B1=bars("1h",1)
res["1시간봉 · 같은 기간(숫자×24) · 매시간 판단"]=run(B1,formula_pos(B1,24),8760)
print("1h done",f"{time.time()-t0:.0f}s",flush=True)
# 보유
r=D["close"].pct_change().fillna(0).where(D["close"].notna()).mean(axis=1)
for lab,(a,b) in {"dev":("2020-09-15","2024-12-31"),"hold":("2025-01-01","2026-10-31")}.items():
    p=fl.perf(r.loc[a:b],365); print(f"(참고) 계속 보유 {lab}: 연 {p['cagr']:+.1%} MDD {p['mdd']:+.0%} 샤프 {p['sharpe']:.2f}")
for nm,o in res.items():
    (pd_,dd),(ph,_)=o["dev"],o["hold"]
    lo,hi=sharpe_ci(dd)
    print(f"{nm:34s} | 2020.09~24: 연 {pd_['cagr']:+.1%} MDD {pd_['mdd']:+.0%} 샤프 {pd_['sharpe']:.2f} (90% {lo:.2f}~{hi:.2f}) 거래 {pd_['trades']} | 2025~: 연 {ph['cagr']:+.1%} MDD {ph['mdd']:+.0%} 샤프 {ph['sharpe']:.2f} 거래 {ph['trades']}")
# 같은 전략을 4시간 수익률 그대로 써서 샤프 구간을 재면 좁아지나?
c=B4["close"]; pos=formula_pos(B4,6); R=(pos.shift(1).fillna(0)*c.pct_change().fillna(0)-pos.diff().abs().fillna(pos.abs())*COST).where(c.notna()).mean(axis=1).loc["2020-09-15":"2024-12-31"]
x=R.to_numpy(); T=len(x); rng=np.random.default_rng(1); blk=180; k=int(np.ceil(T/blk)); s=[]
for _ in range(600):
    idx=(rng.integers(0,T-blk,k)[:,None]+np.arange(blk)).ravel()[:T]; y=x[idx]; s.append(y.mean()/y.std()*np.sqrt(2190))
print("같은 전략, 4시간 수익률로 잰 샤프 90% 구간:", np.round(np.quantile(s,[0.05,0.95]),2), f"(봉 수 {T:,}개 vs 일 {T//6:,}개)")
