"""검증 도구: 시대별 나눠 보기, 워크포워드, 최종 검증 구간 잠금, 시험 횟수 기록."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import metrics

# (이름, 시작, 끝). 2025-01-01 이후는 최종 검증 구간이라 여기에 넣지 않습니다.
ERAS = [
    ("2018 겨울", "2018-03-01", "2018-12-31"),
    ("2019 반등·횡보", "2019-01-01", "2019-12-31"),
    ("2020-03 코로나 급락", "2020-02-10", "2020-04-30"),
    ("2020 반감기 전후(±6개월)", "2019-11-11", "2020-11-11"),
    ("2020~21 강세", "2020-10-01", "2021-11-10"),
    ("2022 약세 전체", "2021-11-10", "2022-12-31"),
    ("2022-05 LUNA 붕괴", "2022-05-01", "2022-06-30"),
    ("2022-11 FTX 붕괴", "2022-11-01", "2022-12-31"),
    ("2023 회복", "2023-01-01", "2023-12-31"),
    ("2024-01 현물 ETF 승인(±3개월)", "2023-10-10", "2024-04-10"),
    ("2024 반감기 전후(±6개월)", "2023-10-20", "2024-10-20"),
    ("2024 반감기 이후", "2024-04-20", "2024-12-31"),
]


def era_table(eq: pd.Series, bench: dict[str, pd.Series]) -> pd.DataFrame:
    """시대마다 수익·MDD 와 비교 대상의 수익·MDD."""
    rows = []
    for name, s, e in ERAS:
        seg = eq.loc[s:e].dropna()
        if len(seg) < 20:
            continue
        row = {"시대": name, "수익": seg.iloc[-1] / seg.iloc[0] - 1, "MDD": metrics.max_drawdown(seg)}
        for bn, b in bench.items():
            bs = b.loc[s:e].dropna()
            if len(bs) >= 20:
                row[f"{bn} 수익"] = bs.iloc[-1] / bs.iloc[0] - 1
                row[f"{bn} MDD"] = metrics.max_drawdown(bs)
        rows.append(row)
    return pd.DataFrame(rows)


def era_consistency(eq: pd.Series, base: pd.Series) -> dict:
    """비교 대상보다 '수익이 나았던' 시대 수, 'MDD가 얕았던' 시대 수."""
    t = era_table(eq, {"기준": base})
    if t.empty:
        return {"시대수": 0, "수익우위시대": 0, "MDD우위시대": 0}
    main = [n for n, _, _ in ERAS if not n.startswith(("2022-05", "2022-11", "2020-03"))]
    t = t[t["시대"].isin(main)]
    return {
        "시대수": len(t),
        "수익우위시대": int((t["수익"] > t["기준 수익"]).sum()),
        "MDD우위시대": int((t["MDD"] > t["기준 MDD"]).sum()),
    }


def wf_windows(start: str, end: str, train_m: int, test_m: int) -> list[tuple]:
    """(학습시작, 학습끝, 검증시작, 검증끝) 목록. 검증 구간이 겹치지 않게 test_m 씩 밉니다."""
    out = []
    t0 = pd.Timestamp(start)
    endt = pd.Timestamp(end)
    while True:
        tr_end = t0 + pd.DateOffset(months=train_m) - pd.Timedelta(days=1)
        te_start = tr_end + pd.Timedelta(days=1)
        te_end = min(te_start + pd.DateOffset(months=test_m) - pd.Timedelta(days=1), endt)
        if te_start >= endt:
            break
        out.append((t0, tr_end, te_start, te_end))
        t0 = t0 + pd.DateOffset(months=test_m)
    return out


def score(eq: pd.Series, how: str = "sharpe") -> float:
    r = eq.pct_change().dropna()
    if how == "sharpe":
        return metrics.sharpe(r)
    if how == "calmar":
        mdd = metrics.max_drawdown(eq)
        return metrics.cagr(eq) / abs(mdd) if mdd < 0 else np.nan
    raise ValueError(how)


class TrialLog:
    """몇 개 조합을 시험했는지 남깁니다 (여러 개 중 고르면 우연히 좋아 보일 수 있으니까)."""

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, family: str, name: str, universe: str, eq: pd.Series):
        r = eq.pct_change().dropna()
        self.rows.append({"family": family, "name": name, "universe": universe,
                          "sr_daily": r.mean() / r.std() if r.std() > 0 else 0.0,
                          "skew": float(r.skew()), "kurt": float(r.kurt() + 3), "n": len(r)})

    def count(self, family: str | None = None, universe: str | None = None) -> int:
        return len([x for x in self.rows if (family is None or x["family"] == family)
                    and (universe is None or x["universe"] == universe)])

    def dsr(self, family: str, universe: str, name: str) -> float:
        rows = [x for x in self.rows if x["family"] == family and x["universe"] == universe]
        best = next(x for x in rows if x["name"] == name)
        return metrics.deflated_sharpe(best["sr_daily"], [x["sr_daily"] for x in rows], best["n"],
                                       best["skew"], best["kurt"])


class HoldoutLock:
    """최종 검증 구간은 공식을 확정한 뒤 한 번만 봅니다. 볼 때마다 기록이 남습니다."""

    def __init__(self, path: Path, start: str):
        self.path = path
        self.start = pd.Timestamp(start)

    def dev_end(self) -> pd.Timestamp:
        return self.start - pd.Timedelta(days=1)

    def history(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def open(self, formula_id: str, note: str = "") -> int:
        h = self.history()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "formula": formula_id,
                                "note": note}, ensure_ascii=False) + "\n")
        return len(h) + 1
