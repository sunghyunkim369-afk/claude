"""지금 보유 비중 읽기.

1) 업비트 키가 있으면 실제 잔고 (조회 권한만 씀)
2) 없으면 모의 계좌 user_data/paper_portfolio.json (trade 명령이 모의 체결을 기록)
3) 둘 다 없으면 전부 현금으로 봄
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd


def paper_path(user_dir: Path) -> Path:
    return user_dir / "paper_portfolio.json"


def load_paper(user_dir: Path, start_krw: float = 10_000_000) -> dict:
    p = paper_path(user_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"cash_krw": start_krw, "qty": {}}


def save_paper(user_dir: Path, state: dict) -> None:
    user_dir.mkdir(parents=True, exist_ok=True)
    paper_path(user_dir).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def current(cfg: dict, user_dir: Path, prices_krw: pd.Series, log=print) -> tuple[pd.Series, float, str]:
    """(코인별 비중, 총평가 원화, 출처 설명)."""
    paper = cfg["trading"].get("paper", True)
    if not paper and os.environ.get("UPBIT_ACCESS_KEY"):
        try:
            from ..data.upbit import UpbitPrivate
            acc = UpbitPrivate().accounts()
            cash = sum(float(a["balance"]) + float(a["locked"]) for a in acc if a["currency"] == "KRW")
            qty = {a["currency"]: float(a["balance"]) + float(a["locked"]) for a in acc if a["currency"] != "KRW"}
            return _weights(qty, cash, prices_krw) + ("업비트 실제 잔고",)
        except Exception as e:
            log(f"  업비트 잔고 조회 실패 → 모의 계좌 사용: {e}")
    st = load_paper(user_dir)
    return _weights(st["qty"], st["cash_krw"], prices_krw) + ("모의 계좌(user_data/paper_portfolio.json)",)


def _weights(qty: dict, cash: float, px: pd.Series) -> tuple[pd.Series, float]:
    val = pd.Series({a: q * float(px.get(a, float("nan"))) for a, q in qty.items() if q > 0}, dtype=float).dropna()
    total = cash + val.sum()
    return (val / total if total > 0 else val * 0), float(total)
