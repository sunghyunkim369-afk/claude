"""데이터 한꺼번에 갱신: `python -m coinquant update`"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import binance_public, coinmetrics, fx, upbit
from .. import universe

QUOTES = ("USDT", "BTC")


def update_all(cfg: dict, cache: Path, *, upbit_too: bool = True, cm_clone: Path | None = None,
               log=print) -> None:
    spot_dir = cache / "binance" / "spot_1d"

    log("1) 바이낸스 공개 데이터 심볼 목록")
    syms = binance_public.list_spot_symbols()
    (cache / "binance").mkdir(parents=True, exist_ok=True)
    (cache / "binance" / "symbols.json").write_text(json.dumps(syms))
    want = [s for s in syms if s.endswith(QUOTES)]
    log(f"   USDT·BTC 페어 {len(want)}개 (상장폐지 포함)")
    res = binance_public.update_cache(spot_dir, want, log=log)
    bad = [k for k, v in res.items() if isinstance(v, str)]
    if bad:
        log(f"   실패 {len(bad)}개 다시 시도")
        binance_public.update_cache(spot_dir, bad, workers=4, log=log)
    alive = [s for s in want if (spot_dir / f"{s}.csv").exists()
             and pd.read_csv(spot_dir / f"{s}.csv", usecols=["date"])["date"].max() >= str(pd.Timestamp.now().date() - pd.Timedelta(days=5))]
    binance_public.rest_topup(spot_dir, alive, log=log)

    log("2) 펀딩비 (BTC, ETH, SOL 선물)")
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        try:
            binance_public.fetch_funding_history(s).to_csv(cache / "binance" / f"funding_{s}.csv", index=False)
        except Exception as e:
            log(f"   {s} 펀딩비 실패: {e}")

    log("3) Coin Metrics 시가총액 (바이낸스에 있는 코인만)")
    bases = sorted({s[: -len(q)] for s in want for q in QUOTES if s.endswith(q)})
    ids = _cm_ids(cache, cm_clone)
    need = sorted({c for b in bases for c in coinmetrics.candidates_for(b, ids)})
    coinmetrics.download(need, cache / "coinmetrics", local_clone=cm_clone, log=log)

    log("4) 원/달러 환율")
    try:
        fx.download_usdkrw(cache / "fx_usdkrw.csv")
    except Exception as e:
        log(f"   환율 실패: {e}")

    if upbit_too:
        log("5) 업비트 KRW 일봉")
        try:
            mk = upbit.markets()
            mk.to_csv(cache / "upbit_markets.csv", index=False)
            upbit.update_cache(cache / "upbit" / "krw_1d", list(mk["market"]), log=log)
        except Exception as e:
            log(f"   업비트 접속 실패 (이 컴퓨터에서 업비트가 막혀 있으면 건너뜀): {e}")

    log("6) 가격 패널 만들기")
    panel = universe.build_panel(cfg, cache, log=log)
    panel.save(cache / "panel")
    log("완료")


def _cm_ids(cache: Path, clone: Path | None) -> set[str] | None:
    """Coin Metrics 에 있는 코인 아이디 목록."""
    if clone and clone.exists():
        return {p.stem for p in clone.glob("*.csv")}
    lst = cache / "coinmetrics_ids.json"
    if lst.exists():
        return set(json.loads(lst.read_text()))
    import requests
    # GitHub API 가 막히면 git 트리 대신 바이낸스 심볼 기반 후보를 그대로 시도
    try:
        r = requests.get("https://api.github.com/repos/coinmetrics/data/git/trees/master?recursive=1", timeout=60)
        r.raise_for_status()
        ids = {Path(t["path"]).stem for t in r.json()["tree"] if t["path"].startswith("csv/")}
        lst.write_text(json.dumps(sorted(ids)))
        return ids
    except Exception:
        return None


def load_funding(cache: Path, symbol: str = "BTCUSDT") -> pd.Series | None:
    p = cache / "binance" / f"funding_{symbol}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["time"])
    return df.set_index("time")["rate"].resample("D").mean()
