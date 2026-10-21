"""공식: 설정([formula])대로 날짜별 목표 비중을 만듭니다.

공식은 검증 결과를 보고 같이 정합니다. 확정 전에는 status = "임시" 로 두고 리포트에 그렇게 표시합니다.
백테스트와 매일 추천이 같은 함수를 쓰므로, 리포트의 추천은 검증한 공식과 똑같이 계산됩니다.
"""
from __future__ import annotations

import pandas as pd

from .backtest import signals as sg
from .universe import Panel, core_assets, eligible_others

DEFAULT_FORMULA = {
    "status": "임시",
    "core_signal": "보유",          # timing_signals 이름 또는 "보유"
    "others_signal": "보유",
    "others_select": "균등",        # selection_scores 이름 또는 "균등"
    "others_k": 10,
    "regime_filter": "없음",        # regime_filters 이름 또는 "없음"
    "regime_applies_to": ["others"],  # "core", "others"
}


def formula(cfg: dict) -> dict:
    return {**DEFAULT_FORMULA, **cfg.get("formula", {})}


def _on(name: str, F: sg.Features, cols: list[str]) -> pd.DataFrame:
    if name in ("보유", "", None):
        return pd.DataFrame(True, index=F.c.index, columns=cols)
    sigs = sg.timing_signals(F)
    if name not in sigs:
        raise KeyError(f"모르는 신호: {name}. 가능한 값: 보유, {', '.join(sigs)}")
    return sigs[name]()[cols].fillna(False).astype(bool)


def target_weights(panel: Panel, cfg: dict, allowed: set[str] | None = None,
                   funding: pd.Series | None = None, live_mcap: pd.Series | None = None) -> tuple[pd.DataFrame, dict]:
    """날짜 × 코인 목표 비중과, 리포트용 중간 계산(신호·후보)을 돌려줍니다.

    allowed: 실제로 살 수 있는 코인(예: 업비트 KRW 상장·유의종목 아님). None 이면 제한 없음.
    """
    fm = formula(cfg)
    pf = cfg["portfolio"]
    core = core_assets(panel, cfg)
    elig = eligible_others(panel, cfg, live_mcap=live_mcap)
    if allowed is not None:
        elig = elig & elig.columns.isin(list(allowed))
        core = [c for c in core if c in allowed]
    others = list(elig.columns[elig.any()])
    cols = core + [c for c in others if c not in core]
    F = sg.Features(panel, cols)
    alive = panel.close[cols].notna()

    core_on = _on(fm["core_signal"], F, core)
    w_core = sg.weights_slots(core_on, alive[core]) * pf["core_weight"]

    oth_mem = elig[others]
    if fm["others_select"] == "균등":
        base = sg.weights_slots(pd.DataFrame(True, index=oth_mem.index, columns=others), oth_mem)
    else:
        sc = sg.selection_scores(F)
        if fm["others_select"] not in sc:
            raise KeyError(f"모르는 선택 방법: {fm['others_select']}")
        base = sg.weights_topk(sc[fm["others_select"]]().reindex(columns=others), oth_mem, int(fm["others_k"]))
    oth_on = _on(fm["others_signal"], F, others)
    w_oth = (base * oth_on) * pf["other_weight"]

    if fm["regime_filter"] not in ("없음", "", None):
        reg = sg.regime_filters(panel, funding)[fm["regime_filter"]].reindex(panel.close.index).fillna(False)
        if "core" in fm["regime_applies_to"]:
            w_core = w_core.mul(reg, axis=0)
        if "others" in fm["regime_applies_to"]:
            w_oth = w_oth.mul(reg, axis=0)
    else:
        reg = None
    w = pd.concat([w_core, w_oth], axis=1).fillna(0.0)
    w = w.T.groupby(level=0).sum().T  # 혹시 겹치는 열 합치기
    return w, {"features": F, "core": core, "others": others, "elig": elig, "core_on": core_on,
               "others_on": oth_on, "regime": reg, "formula": fm}
