"""Coin Metrics 무료 공개 데이터 (github.com/coinmetrics/data) — 날짜별 시가총액.

- CapMrktCurUSD: 유통 공급량 × 가격 (정식, 136종 정도)
- CapMrktEstUSD: 추정 유통 시가총액 (2019-06 이후, 700종 이상) ← 있으면 이것을 씀
- 상장폐지·사라진 코인(LUNA, FTT, XEM 등)도 남아 있어 날짜별 순위를 만들 수 있습니다.
- 무료 데이터에는 대부분 코인의 '가격' 칸이 비어 있어서, 가격은 바이낸스에서, 시가총액만 여기서 씁니다.
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

RAW = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{asset}.csv"
COLS = ["time", "PriceUSD", "CapMrktCurUSD", "CapMrktEstUSD", "volume_reported_spot_usd_1d"]


def _slim(df: pd.DataFrame) -> pd.DataFrame:
    keep = [c for c in COLS if c in df.columns]
    df = df[keep].rename(columns={"time": "date"})
    for c in COLS[1:]:
        if c not in df:
            df[c] = float("nan")
    return df


def download(assets: list[str], out_dir: Path, local_clone: Path | None = None, workers: int = 16, log=print) -> list[str]:
    """필요한 코인 파일만 받아 필요한 칸만 저장. local_clone 이 있으면 거기서 복사."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def job(a: str):
        dst = out_dir / f"{a}.csv"
        try:
            if local_clone and (local_clone / f"{a}.csv").exists():
                df = pd.read_csv(local_clone / f"{a}.csv", usecols=lambda c: c in COLS)
            else:
                r = requests.get(RAW.format(asset=a), timeout=120)
                if r.status_code == 404:
                    return a, False
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text), usecols=lambda c: c in COLS)
        except Exception:
            return a, False
        _slim(df).to_csv(dst, index=False)
        return a, True

    ok = []
    with ThreadPoolExecutor(workers) as ex:
        for a, good in ex.map(job, assets):
            if good:
                ok.append(a)
    log(f"  Coin Metrics {len(ok)}/{len(assets)} 종 저장")
    return ok


def load(out_dir: Path, asset: str) -> pd.DataFrame | None:
    p = out_dir / f"{asset}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
    # 추정 유통 시가총액 우선. 정식 값은 총 공급량 기준이라 부풀려진 코인이 있음 (예: XVG 65억 vs 0.7억 달러)
    df["mcap"] = df["CapMrktEstUSD"].fillna(df["CapMrktCurUSD"])
    return df


def available_assets(out_dir: Path) -> set[str]:
    return {p.stem for p in out_dir.glob("*.csv")}


def candidates_for(base: str, all_ids: set[str] | None) -> list[str]:
    """바이낸스 심볼 → Coin Metrics 아이디 후보 (가격 상관으로 최종 확인).

    all_ids 가 None 이면 (목록을 못 받았을 때) 이름 규칙으로 만든 후보를 그대로 돌려줍니다.
    """
    b = base.lower()
    for pre in ("1000000", "1000", "1m"):
        if b.startswith(pre) and len(b) > len(pre) + 1:
            b = b[len(pre):]
            break
    alias = {
        "iota": ["miota"], "matic": ["matic_eth"], "shib": ["shib_eth"], "avax": ["avaxc", "avaxp"],
        "luna": ["luna", "luna2"], "lunc": ["luna"], "ustc": ["ust"], "one": ["one_harmony"],
        "tao": ["tao_bittensor"], "bifi": ["bifi_beef"], "ong": ["ong_ontologygas"],
        "win": ["win_wink"], "hot": ["hot_holo"], "mtl": ["mtl_metal"], "bttc": ["bttc", "btt"],
        "render": ["render", "rndr"], "rndr": ["rndr", "render"], "pol": ["pol", "pol_eth"],
        "ven": ["vet_eth"], "vet": ["vet", "vet_eth"], "eos": ["eos", "eos_eth"], "trx": ["trx", "trx_eth"],
    }
    out = list(alias.get(b, []))
    for c in (b, f"{b}_eth"):
        if c not in out:
            out.append(c)
    if all_ids is None:
        return out
    out += sorted(i for i in all_ids if i.startswith(b + "_") and i not in out)
    return [c for c in out if c in all_ids]

