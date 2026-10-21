"""최종 검증: 확정한 공식을 잠가 둔 구간(holdout_start ~ 현재)에 '한 번' 적용.

여기서 나온 숫자를 보고 공식을 다시 고치면, 그 구간은 더 이상 '안 본 데이터'가 아닙니다.
그래서 열 때마다 user 데이터 폴더의 holdout_log.jsonl 에 기록하고, 두 번째부터는 경고합니다.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from .. import config as config_mod, strategy
from ..backtest import engine, metrics, validate as V
from ..data.update import load_funding
from ..universe import Panel


def formula_id(cfg: dict) -> str:
    keys = {"formula": strategy.formula(cfg), "portfolio": cfg["portfolio"], "universe": cfg["universe"], "costs": cfg["costs"]}
    return hashlib.sha1(json.dumps(keys, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def main(cfg: dict | None = None, force: bool = False, log=print) -> dict | None:
    cfg = cfg or config_mod.load()
    cache = config_mod.cache_dir(cfg)
    lock = V.HoldoutLock(cache / "holdout_log.jsonl", cfg["validation"]["holdout_start"])
    fm = strategy.formula(cfg)
    if fm["status"] != "확정" and not force:
        log("공식이 아직 '확정'이 아닙니다 (config.toml [formula] status). 확정한 뒤에 여세요.")
        return None
    hist = lock.history()
    fid = formula_id(cfg)
    if hist:
        log(f"주의: 최종 검증 구간을 이미 {len(hist)}번 열었습니다 (마지막 {hist[-1]['time'][:16]}, 공식 {hist[-1]['formula']}).")
        log("다시 보고 공식을 고치면 이 결과는 '처음 보는 데이터' 검증이 아니게 됩니다.")
        try:
            if input("그래도 열까요? 'yes' 입력: ").strip() != "yes":
                return None
        except EOFError:
            return None
    n = lock.open(fid, note=f"status={fm['status']}")
    panel = Panel.load(cache / "panel")
    funding = load_funding(cache)
    W, info = strategy.target_weights(panel, cfg, None, funding)
    c, pf = cfg["costs"], cfg["portfolio"]
    costs = engine.Costs(c["fee"], c["slippage_core"], c["slippage_other"], tuple(info["core"]))
    cols = list(W.columns[(W != 0).any()])
    migrated = frozenset(panel.meta.index[panel.meta["migrated_to"].notna()])
    out = {}
    for mode in ("last", "zero"):
        rules = engine.Rules(rebalance_days=pf["rebalance_days"], max_weight=pf["max_weight_per_coin"],
                             max_total=pf["max_total_invest"], max_orders=pf["max_orders_per_day"],
                             min_trade=pf["min_trade_weight"], delist_mode=mode, always_last=migrated)
        res = engine.run(W[cols], panel.open[cols], panel.close[cols], costs, rules, panel.tradable[cols], start="2018-03-01")
        eq = res.equity.loc[lock.start - pd.Timedelta(days=1):]
        btc = panel.close["BTC"].loc[eq.index[0]:]
        out[mode] = metrics.summarize(eq, btc / btc.iloc[0], res.trades[res.trades["exit"] >= lock.start])
    btc = panel.close["BTC"].loc[lock.start - pd.Timedelta(days=1):]
    log(f"\n최종 검증 ({n}번째 열람) · 공식 {fid} · {lock.start.date()} ~ {btc.index[-1].date()}")
    for k in ("총수익", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)"):
        v1, v0 = out["last"].get(k), out["zero"].get(k)
        f = (lambda v: metrics.fmt(v, "num")) if k == "샤프" else (lambda v: metrics.fmt(v, "pct"))
        log(f"  {k:<16} {f(v1):>9}  (상장폐지 0원: {f(v0)})")
    b = metrics.summarize(btc)
    log(f"  BTC 그냥 보유: 총수익 {metrics.fmt(b['총수익'])}, MDD {metrics.fmt(b['MDD'])}")
    path = config_mod.ROOT / "docs" / f"final-check-{pd.Timestamp.now(tz='Asia/Seoul').date()}-{fid}.json"
    path.write_text(json.dumps({"formula_id": fid, "open_count": n, "formula": fm,
                                "result": {m: {k: float(v) for k, v in d.items()} for m, d in out.items()}},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return out
