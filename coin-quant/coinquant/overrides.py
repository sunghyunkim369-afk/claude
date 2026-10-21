"""내 판단 한 겹: 공식 위에 덮어쓰기. 나머지 코인은 공식 그대로.

명령 (이유 한 줄 필수):
  python -m coinquant judge lock BTC "ETF 자금 유입 지속, 단기 신호 무시"     # 보유 고정: 공식이 팔지 않음
  python -m coinquant judge exclude XRP "소송 리스크"                          # 제외: 공식이 사지 않음 (갖고 있으면 정리)
  python -m coinquant judge weight SOL 0.15 "생태계 성장"                       # 비중 직접 지정 (평가금 대비)
  python -m coinquant judge release BTC "이벤트 종료"                          # 해제 → 공식대로
  python -m coinquant judge list
옵션: --until 2026-12-31 (그날까지만 유효)

모든 판단은 user_data/judgments.jsonl 에 시각·이유와 함께 쌓이고(지우지 않음),
매일 리포트가 '공식만' 비중과 '공식+내 판단' 비중을 user_data/journal.csv 에 남겨 나중에 성과를 비교합니다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ACTIONS = {"lock": "보유 고정", "exclude": "제외", "weight": "비중 지정", "release": "해제"}


class Judgments:
    def __init__(self, user_dir: Path):
        self.dir = user_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "judgments.jsonl"
        self.journal_path = self.dir / "journal.csv"

    # ---------- 기록
    def add(self, action: str, asset: str, reason: str, value: float | None = None, until: str | None = None) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"action 은 {', '.join(ACTIONS)} 중 하나")
        if not reason or not reason.strip():
            raise ValueError("이유 한 줄을 꼭 적어 주세요")
        if action == "weight" and (value is None or not 0 <= value <= 1):
            raise ValueError("비중은 0~1 사이 (예: 0.15 = 15%)")
        rec = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": action,
               "asset": asset.upper(), "value": value, "until": until, "reason": reason.strip()}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def history(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def active(self, on: pd.Timestamp | None = None) -> dict[str, dict]:
        """코인별 현재 유효한 판단 (마지막 기록 기준, release 면 없음)."""
        on = on or pd.Timestamp.now(tz="UTC").tz_localize(None)
        cur: dict[str, dict] = {}
        for r in self.history():
            t = pd.Timestamp(r["time"]).tz_localize(None) if pd.Timestamp(r["time"]).tzinfo else pd.Timestamp(r["time"])
            if t > on:
                continue
            if r["action"] == "release":
                cur.pop(r["asset"], None)
            else:
                cur[r["asset"]] = r
        return {a: r for a, r in cur.items() if not r.get("until") or pd.Timestamp(r["until"]) >= on.normalize()}

    # ---------- 적용
    @staticmethod
    def apply(formula_w: pd.Series, current_w: pd.Series, active: dict[str, dict], max_total: float = 1.0) -> pd.Series:
        """공식 비중(오늘) → 공식+내 판단 비중.

        - 보유 고정: max(공식 비중, 지금 보유 비중) → 공식이 줄이거나 팔지 않음
        - 제외: 0
        - 비중 지정: 그 값
        판단으로 비중 합이 한도를 넘으면, 판단이 없는 코인들 비중을 같은 비율로 줄입니다.
        """
        idx = formula_w.index.union(current_w.index).union(pd.Index(list(active)))
        f = formula_w.reindex(idx).fillna(0.0)
        cur = current_w.reindex(idx).fillna(0.0)
        out = f.copy()
        fixed = []
        for a, r in active.items():
            if r["action"] == "lock":
                out[a] = max(f[a], cur[a])
            elif r["action"] == "exclude":
                out[a] = 0.0
            elif r["action"] == "weight":
                out[a] = float(r["value"])
            fixed.append(a)
        tot = out.sum()
        if tot > max_total + 1e-12:
            free = [a for a in idx if a not in fixed]
            over = tot - max_total
            fs = out[free].sum()
            if fs > 0:
                out[free] *= max(0.0, 1 - over / fs)
            if out.sum() > max_total + 1e-12:  # 판단만으로도 넘치면 판단 비중을 비례 축소
                out *= max_total / out.sum()
        return out[out > 1e-9]

    # ---------- 일지와 성과 비교
    def journal(self, date: pd.Timestamp, formula_w: pd.Series, final_w: pd.Series) -> None:
        idx = formula_w.index.union(final_w.index)
        df = pd.DataFrame({"date": date.strftime("%Y-%m-%d"), "asset": idx,
                           "w_formula": formula_w.reindex(idx).fillna(0).values,
                           "w_final": final_w.reindex(idx).fillna(0).values})
        if self.journal_path.exists():
            old = pd.read_csv(self.journal_path)
            old = old[old["date"] != date.strftime("%Y-%m-%d")]
            df = pd.concat([old, df])
        df.sort_values(["date", "asset"]).to_csv(self.journal_path, index=False)

    def compare(self, close: pd.DataFrame, cost_rate: float = 0.002) -> dict | None:
        """일지에 남은 비중으로 '공식만' vs '공식+내 판단' 가상 성과 (다음 날 종가 수익, 비용 근사 포함)."""
        if not self.journal_path.exists():
            return None
        j = pd.read_csv(self.journal_path, parse_dates=["date"])
        if j["date"].nunique() < 2:
            return {"days": int(j["date"].nunique())}
        out = {}
        r = close.pct_change(fill_method=None).shift(-1)  # t일 비중 → t+1일 수익
        for col in ("w_formula", "w_final"):
            w = j.pivot_table(index="date", columns="asset", values=col, aggfunc="sum").fillna(0.0)
            w = w.reindex(columns=[c for c in w.columns if c in close.columns])
            rr = (w * r.reindex(index=w.index, columns=w.columns).fillna(0.0)).sum(axis=1)
            turn = w.diff().abs().sum(axis=1).fillna(w.iloc[0].abs().sum())
            rr = rr - turn * cost_rate
            out[col] = rr
        diff = j.pivot_table(index="date", columns="asset", values="w_final", aggfunc="sum").fillna(0) - \
            j.pivot_table(index="date", columns="asset", values="w_formula", aggfunc="sum").fillna(0)
        contrib = (diff * r.reindex(index=diff.index, columns=diff.columns).fillna(0)).sum()
        eq = {k: (1 + v).cumprod() for k, v in out.items()}
        return {
            "days": len(out["w_formula"]),
            "formula_return": float(eq["w_formula"].iloc[-1] - 1),
            "final_return": float(eq["w_final"].iloc[-1] - 1),
            "by_asset": contrib[contrib.abs() > 1e-9].sort_values(),
            "win_days": float((out["w_final"] - out["w_formula"] > 0).mean()),
        }

    def per_judgment(self, close: pd.DataFrame) -> pd.DataFrame:
        """판단 하나하나가 유효했던 기간 동안 공식 대비 얼마나 더/덜 벌었는지 (일지 기준)."""
        if not self.journal_path.exists():
            return pd.DataFrame()
        j = pd.read_csv(self.journal_path, parse_dates=["date"])
        r = close.pct_change(fill_method=None).shift(-1)
        rows = []
        hist = self.history()
        for i, h in enumerate(hist):
            if h["action"] == "release":
                continue
            start = pd.Timestamp(h["time"]).tz_localize(None).normalize()
            end = None
            for later in hist[i + 1:]:
                if later["asset"] == h["asset"]:
                    end = pd.Timestamp(later["time"]).tz_localize(None).normalize()
                    break
            if h.get("until"):
                end = min(end or pd.Timestamp(h["until"]), pd.Timestamp(h["until"]))
            d = j[(j["asset"] == h["asset"]) & (j["date"] >= start) & ((j["date"] < end) if end is not None else True)]
            if d.empty or h["asset"] not in close:
                c = np.nan
            else:
                rr = r[h["asset"]].reindex(d["date"]).fillna(0).to_numpy()
                c = float(((d["w_final"] - d["w_formula"]).to_numpy() * rr).sum())
            rows.append({"시작": start.date(), "끝": end.date() if end is not None else "진행 중", "판단": ACTIONS[h["action"]],
                         "코인": h["asset"], "값": h.get("value"), "이유": h["reason"], "일수": len(d),
                         "공식 대비 기여(평가금 대비)": c})
        return pd.DataFrame(rows)
