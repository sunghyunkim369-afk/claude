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
    return report(cfg, cache, lock, fm, fid, n, log)


SUBPERIODS = [("2025 상반기", "2025-01-01", "2025-06-30"), ("2025 하반기", "2025-07-01", "2025-12-31"),
              ("2026 (~현재)", "2026-01-01", None),
              ("고점까지 (2025-01 ~ 2025-10-06)", "2025-01-01", "2025-10-06"),
              ("고점 이후 하락 (2025-10-06 ~ 2026-06-30)", "2025-10-06", "2026-06-30"),
              ("저점 이후 (2026-06-30 ~)", "2026-06-30", None)]


def report(cfg, cache, lock, fm, fid, n, log=print) -> dict:
    from .candidates import run_formula
    from .indicator_validation import to_md
    panel = Panel.load(cache / "panel")
    funding = load_funding(cache)
    hs = lock.start - pd.Timedelta(days=1)
    btc = panel.close["BTC"]
    btc_h = btc.loc[hs:]
    runs = {
        "확정 공식 (스윙 중기 중심)": run_formula(panel, cfg, fm, funding),
        "확정 공식 · 상장폐지 0원": run_formula(panel, cfg, fm, funding, delist_mode="zero"),
        "확정 공식 · 하루 늦게 체결": run_formula(panel, cfg, fm, funding, exec_lag=1),
        "확정 공식 · 코어=BTC·ETH만": run_formula(panel, cfg, fm, funding, core=["BTC", "ETH"]),
        "기준: 신호 없이 보유 (코어 보유 + 기타 균등)": run_formula(panel, cfg, {"core_signal": "보유", "others_signal": "보유",
                                                                         "others_select": "균등", "regime_filter": "없음"}, funding),
    }
    rows, dev_rows, curves = [], [], {}
    for name, res in runs.items():
        eq = res.equity.loc[hs:]
        s = metrics.summarize(eq, btc_h, res.trades[res.trades["exit"] >= lock.start], res.weights.loc[eq.index], res.turnover.loc[eq.index])
        s["구분"] = name
        rows.append(s)
        curves[name] = eq
        de = res.equity.loc[:lock.dev_end()]
        d = metrics.summarize(de, btc.loc["2018-03-01":lock.dev_end()])
        d["구분"] = name
        dev_rows.append(d)
    b = metrics.summarize(btc_h)
    b["구분"] = "BTC 그냥 보유"
    rows.append(b)
    curves["BTC 그냥 보유"] = btc_h
    db = metrics.summarize(btc.loc["2018-03-01":lock.dev_end()])
    db["구분"] = "BTC 그냥 보유"
    dev_rows.append(db)
    hold = pd.DataFrame(rows)
    dev = pd.DataFrame(dev_rows)
    sub = []
    for name, eq in curves.items():
        r = {"구분": name}
        for pn, a, z in SUBPERIODS:
            seg = eq.loc[a:z].dropna()
            if len(seg) > 5:
                r[pn] = seg.iloc[-1] / seg.iloc[0] - 1
                r[pn + " MDD"] = metrics.max_drawdown(seg)
        sub.append(r)
    sub = pd.DataFrame(sub)
    mo = pd.DataFrame({k: metrics.monthly(v) for k, v in curves.items() if k in ("확정 공식 (스윙 중기 중심)", "BTC 그냥 보유",
                                                                                 "기준: 신호 없이 보유 (코어 보유 + 기타 균등)")})
    main = runs["확정 공식 (스윙 중기 중심)"]
    inv = main.weights.loc[hs:].sum(axis=1).resample("ME").mean()

    pcols = ["구분", "총수익", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)", "BTC대비 월승률",
             "평균투자비중", "연회전율", "거래수", "거래승률"]
    o = [f"# 최종 검증 (공식 확정 후 한 번만 연 구간) · {pd.Timestamp.now(tz='Asia/Seoul').date()}\n",
         "> 투자 판단을 돕는 분석일 뿐 투자 권유가 아닙니다. 과거 결과는 미래를 보장하지 않으며 원금 손실 가능성이 있습니다.\n",
         f"- **공식**: {fm['core_signal']} (코어) / {fm['others_signal']} + {fm['others_select']} (기타) / 국면 필터 {fm['regime_filter']} "
         f"→ {', '.join(fm['regime_applies_to'])}. 코어 {cfg['portfolio']['core_weight']:.0%} · 기타 {cfg['portfolio']['other_weight']:.0%}, "
         f"리밸런스 {cfg['portfolio']['rebalance_days']}일, 한 코인 최대 {cfg['portfolio']['max_weight_per_coin']:.0%}. 공식 ID `{fid}`.",
         f"- **구간**: {lock.start.date()} ~ {btc_h.index[-1].date()} (개발 때 한 번도 쓰지 않은 데이터). 이번이 **{n}번째 열람**입니다 "
         "(1번째가 아니면 이미 본 구간이라 '처음 보는 데이터' 검증이 아닙니다).",
         "- 규칙은 개발 구간과 같음: 종가 판단 → 다음 날 시가 체결, 업비트 수수료 0.05% + 슬리피지(코어 0.1%, 기타 0.3%), 상장폐지 마지막 가격(0원 처리도 계산).",
         "- 한계: 가격은 바이낸스 달러 기준(업비트 원화 아님). 2026-05-28 이후 기타 그룹 순위는 Coin Metrics 갱신 중단으로 '마지막 시가총액 × 가격 변화'로 근사.\n",
         "## 1. 최종 검증 구간 성적\n", to_md(hold, pcols), "",
         "## 2. 같은 공식의 개발 구간(2018-03 ~ 2024-12) 성적 — 비교용\n",
         "개발 구간은 공식을 고르는 데 쓴 기간이라 낙관적입니다. 최종 검증 구간에서 얼마나 줄었는지가 실제 기대치에 더 가깝습니다.\n",
         to_md(dev, ["구분", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)"]), "",
         "## 3. 구간 안에서 나눠 보기\n"]
    sc = ["구분"] + [c for p_, _, _ in SUBPERIODS for c in (p_, p_ + " MDD") if c in sub.columns]
    o += [to_md(sub, sc), ""]
    o += ["## 4. 월별 수익\n"]
    mo.index = mo.index.strftime("%Y-%m")
    mo["확정 공식 평균 투자 비중"] = inv.reindex(pd.to_datetime(mo.index) + pd.offsets.MonthEnd(0)).to_numpy()
    mo = mo.reset_index().rename(columns={"index": "월"})
    o += [to_md(mo, list(mo.columns)), ""]
    path = config_mod.ROOT / "docs" / f"final-check-{pd.Timestamp.now(tz='Asia/Seoul').date()}.md"
    path.write_text("\n".join(o) + "\n", encoding="utf-8")
    (config_mod.ROOT / "docs" / f"final-check-{pd.Timestamp.now(tz='Asia/Seoul').date()}-{fid}.json").write_text(
        json.dumps({"formula_id": fid, "open_count": n, "formula": fm,
                    "holdout": {r["구분"]: {k: float(v) for k, v in r.items() if isinstance(v, (int, float))} for r in rows}},
                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(f"최종 검증 ({n}번째 열람) 완료: {path}")
    return {"hold": hold, "dev": dev, "sub": sub, "monthly": mo, "path": path}
