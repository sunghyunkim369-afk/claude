"""설정 읽기. config.toml 을 읽고, 없는 값은 기본값으로 채웁니다."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "data": {"cache_dir": "data_cache", "gap_split_days": 10},
    "universe": {
        "core": ["BTC", "ETH", "SOL"],
        "others_top_n": 30,
        "include_meme": False,
        "rank_method": "mcap",
        "min_history_days": 200,
    },
    "costs": {"fee": 0.0005, "slippage_core": 0.001, "slippage_other": 0.003},
    "portfolio": {
        "core_weight": 0.70,
        "other_weight": 0.30,
        "max_weight_per_coin": 0.35,
        "max_orders_per_day": 10,
        "max_total_invest": 1.0,
        "min_trade_weight": 0.01,
        "rebalance_days": 7,
    },
    "validation": {
        "holdout_start": "2025-01-01",
        "wf_train_months": 24,
        "wf_test_months": 6,
        "random_placebo_draws": 200,
    },
    "report": {"output_dir": "reports", "format": "html", "news_max_items": 5},
    "trading": {"stage": 1, "auto_order": False, "paper": True, "max_order_krw": 100000},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike | None = None) -> dict:
    path = Path(path) if path else Path(os.environ.get("COINQUANT_CONFIG", ROOT / "config.toml"))
    cfg = DEFAULTS
    if path.exists():
        with open(path, "rb") as f:
            cfg = _merge(DEFAULTS, tomllib.load(f))
    return cfg


def cache_dir(cfg: dict) -> Path:
    p = Path(os.environ.get("COINQUANT_DATA", cfg["data"]["cache_dir"]))
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p
