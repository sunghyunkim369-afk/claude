"""가격 패널과 날짜별 대상 코인 목록.

핵심 규칙
- '오늘' 상위 목록으로 과거를 시험하지 않습니다. 날짜마다 그날까지의 정보로 순위를 매깁니다.
- 상장폐지된 페어도 모두 넣습니다. 마지막 거래일 다음 날을 '상장폐지일'로 봅니다.
- 시계열이 gap_split_days 이상 끊기면 다른 코인으로 나눕니다 (예: 옛 LUNA / 새 LUNA).
- 스테이블코인·래핑 토큰·거래소 토큰·레버리지 토큰은 뺍니다. 밈코인은 설정으로.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .data import coinmetrics

STABLE = {
    "USDT", "USDC", "BUSD", "TUSD", "PAX", "USDP", "USDS", "USDSB", "DAI", "FDUSD", "UST", "USTC", "SUSD",
    "PYUSD", "USDE", "USD1", "RLUSD", "BFUSD", "XUSD", "AEUR", "EURI", "EUR", "GBP", "AUD", "BRL", "RUB",
    "TRY", "BIDR", "IDRT", "NGN", "UAH", "ZAR", "BKRW", "BVND", "PAXG", "XAUT", "USDD", "TUSDB", "GUSD",
    "FRAX", "LUSD", "CUSD", "EURC", "USDQ", "USDJ", "UAX", "U",
}
WRAPPED = {"WBTC", "WETH", "WBETH", "BETH", "STETH", "BTCB", "RENBTC", "HBTC", "BNSOL", "CBBTC", "WSOL", "WEETH", "WBNB"}
EXCHANGE = {"BNB", "FTT", "HT", "HTX", "OKB", "KCS", "LEO", "CRO", "GT", "MX", "BGB", "WRX", "WOO", "TKO", "CET", "BIX", "BEST", "ASD"}
MEME = {
    "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BOME", "MEME", "TURBO", "PEOPLE", "NEIRO", "SATS", "MEW",
    "PNUT", "ACT", "BRETT", "POPCAT", "TRUMP", "MOODENG", "GOAT", "DOGS", "BABYDOGE", "CAT", "CHEEMS", "TST",
    "PENGU", "FARTCOIN", "SPX", "MOG", "BROCCOLI714", "MUBARAK", "PUMP", "BANANAS31", "1MBABYDOGE",
}
# 이름·토큰 교체로 바이낸스 페어가 끝난 경우: 코인이 죽은 게 아니라서 '0원 처리' 시험에서도 마지막 가격으로 처리
MIGRATED = {
    "MATIC": "POL", "FTM": "S", "RNDR": "RENDER", "LEND": "AAVE", "EOS": "A", "AGIX": "FET", "OCEAN": "FET",
    "GNT": "GLM", "ERD": "EGLD", "NPXS": "PUNDIX", "STRAT": "STRAX", "XZC": "FIRO", "KEEP": "T", "NU": "T",
    "TOMO": "VIC", "BCHABC": "BCH", "BCHSV": "BSV", "BCC": "BCH", "MKR": "SKY", "VEN": "VET",
}
_LEV_BASES = {"BTC", "ETH", "BNB", "TRX", "XRP", "DOT", "LINK", "ADA", "EOS", "XTZ", "LTC", "BCH", "YFI",
              "UNI", "SUSHI", "FIL", "AAVE", "SXP", "XLM", "1INCH"}
LEVERAGED = {b + s for b in _LEV_BASES for s in ("UP", "DOWN", "BULL", "BEAR")} | {"BULL", "BEAR"}


def strip_multiplier(base: str) -> str:
    for pre in ("1000000", "1000", "1M"):
        if base.startswith(pre) and len(base) > len(pre) + 1:
            return base[len(pre):]
    return base


def classify(base: str) -> str:
    b = strip_multiplier(base)
    if base in LEVERAGED:
        return "leveraged"
    if b in STABLE:
        return "stable"
    if b in WRAPPED:
        return "wrapped"
    if b in EXCHANGE:
        return "exchange"
    if b in MEME or base in MEME:
        return "meme"
    return "normal"


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume_usd: pd.DataFrame
    mcap: pd.DataFrame
    meta: pd.DataFrame          # index=asset: base, kind, first, last, delisted, cm_id, source
    tradable: pd.DataFrame = field(repr=False)  # 그날 실제 봉이 있었는지

    @property
    def assets(self) -> list[str]:
        return list(self.close.columns)

    def slice(self, start=None, end=None) -> "Panel":
        sl = slice(start, end)
        return Panel(self.open.loc[sl], self.high.loc[sl], self.low.loc[sl], self.close.loc[sl],
                     self.volume_usd.loc[sl], self.mcap.loc[sl], self.meta, self.tradable.loc[sl])

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for name in ("open", "high", "low", "close", "volume_usd", "mcap", "tradable"):
            getattr(self, name).to_pickle(path / f"{name}.pkl")
        self.meta.to_csv(path / "meta.csv")

    @classmethod
    def load(cls, path: Path) -> "Panel":
        d = {n: pd.read_pickle(path / f"{n}.pkl") for n in ("open", "high", "low", "close", "volume_usd", "mcap", "tradable")}
        meta = pd.read_csv(path / "meta.csv", index_col=0, parse_dates=["first", "last"])
        return cls(meta=meta, **d)


def _read(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty:
        return None
    return df.set_index("date").sort_index()


def _usd_series(spot_dir: Path, base: str, btc: pd.DataFrame) -> tuple[pd.DataFrame, str] | None:
    """USDT 페어 우선, 그 이전 기간은 BTC 페어 × BTCUSDT 로 채움."""
    u = _read(spot_dir / f"{base}USDT.csv")
    b = _read(spot_dir / f"{base}BTC.csv") if base != "BTC" else None
    if b is not None:
        bc = btc["close"].reindex(b.index)
        b = pd.DataFrame({
            "open": b["open"] * btc["open"].reindex(b.index),
            "high": b["high"] * bc, "low": b["low"] * bc, "close": b["close"] * bc,
            "quote_volume": b["quote_volume"] * bc,
        }).dropna()
    if u is None and b is None:
        return None
    if u is None:
        return b, "BTC"
    u = u[["open", "high", "low", "close", "quote_volume"]]
    if b is not None and len(b):
        early = b[b.index < u.index.min()]
        if len(early):
            return pd.concat([early, u]), "BTC+USDT"
    return u, "USDT"


def _split_gaps(df: pd.DataFrame, gap_days: int) -> list[pd.DataFrame]:
    gaps = df.index.to_series().diff().dt.days
    cut = list(df.index[gaps > gap_days])
    parts, start = [], df.index.min()
    for c in cut:
        parts.append(df[(df.index >= start) & (df.index < c)])
        start = c
    parts.append(df[df.index >= start])
    return [p for p in parts if len(p)]


def _match_cm(price: pd.Series, cm_dir: Path, base: str, cm_ids: set[str]) -> tuple[str | None, pd.Series | None]:
    """후보 Coin Metrics 아이디 중 가격 변화와 시가총액 변화의 상관이 0.7 이상인 것들을 기간별로 이어 붙임.

    예: TRX = trx(메인넷 이후) + trx_eth(2018년 ERC20 시절). 겹치는 날은 데이터가 긴 쪽 우선.
    """
    r = np.log(price).diff()
    good = []
    for cid in coinmetrics.candidates_for(base, cm_ids):
        d = coinmetrics.load(cm_dir, cid)
        if d is None or d["mcap"].notna().sum() < 30:
            continue
        m = d["mcap"].where(d["mcap"] > 0)
        rc = np.log(m).diff().reindex(r.index)
        ok = r.notna() & rc.notna() & (r.abs() < 1) & (rc.abs() < 1)
        if ok.sum() < 30:
            continue
        c = r[ok].corr(rc[ok])
        if c > 0.7:
            good.append((int(ok.sum()), cid, m))
    if not good:
        return None, None
    good.sort(key=lambda x: -x[0])
    merged = good[0][2]
    for _, _, m in good[1:]:
        merged = merged.combine_first(m)
    return "+".join(cid for _, cid, _ in good), merged


def build_panel(cfg: dict, cache: Path, log=print) -> Panel:
    spot_dir = cache / "binance" / "spot_1d"
    cm_dir = cache / "coinmetrics"
    cm_ids = coinmetrics.available_assets(cm_dir) if cm_dir.exists() else set()
    gap = int(cfg["data"]["gap_split_days"])

    btc = _read(spot_dir / "BTCUSDT.csv")
    if btc is None:
        raise FileNotFoundError("BTCUSDT 일봉이 없습니다. 먼저 `python -m coinquant update` 를 실행하세요")
    bases = sorted({p.stem[:-4] for p in spot_dir.glob("*USDT.csv")} | {p.stem[:-3] for p in spot_dir.glob("*BTC.csv")})
    bases = [b for b in bases if b]
    end_all = btc.index.max()

    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in ("open", "high", "low", "close", "vol", "mcap")}
    meta = []
    for base in bases:
        kind = classify(base)
        if kind == "leveraged":
            continue
        got = _usd_series(spot_dir, base, btc)
        if got is None:
            continue
        df, source = got
        df = df[(df["close"] > 0) & df["close"].notna()]
        for i, part in enumerate(_split_gaps(df, gap)):
            aid = base if i == 0 else f"{base}#{i + 1}"
            cid, mcap = _match_cm(part["close"], cm_dir, base, cm_ids) if cm_ids else (None, None)
            for k, c in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("vol", "quote_volume")):
                cols[k][aid] = part[c]
            if mcap is not None:
                cols["mcap"][aid] = mcap.reindex(part.index)
            last = part.index.max()
            meta.append({
                "asset": aid, "base": base, "kind": kind, "source": source, "cm_id": cid,
                "first": part.index.min(), "last": last,
                "delisted": bool(last < end_all - pd.Timedelta(days=2)),
                "migrated_to": MIGRATED.get(base) if i == 0 else None,
            })

    idx = pd.date_range(btc.index.min(), end_all, freq="D")
    def frame(d):  # noqa: E306
        return pd.DataFrame(d).reindex(idx).sort_index(axis=1)
    close_raw = frame(cols["close"])
    tradable = close_raw.notna()
    # 짧은 공백(거래 정지)은 직전 가격 유지, 마지막 거래일 이후는 비움
    meta_df = pd.DataFrame(meta).set_index("asset").sort_index()
    alive = pd.DataFrame(False, index=idx, columns=close_raw.columns)
    for a, row in meta_df.iterrows():
        alive.loc[row["first"]:row["last"], a] = True
    close = close_raw.ffill().where(alive)
    open_ = frame(cols["open"]).where(tradable, close)
    high = frame(cols["high"]).where(tradable, close)
    low = frame(cols["low"]).where(tradable, close)
    vol = frame(cols["vol"]).where(alive, np.nan).fillna(0.0).where(alive)
    mcap = frame(cols["mcap"]).reindex(columns=close.columns).ffill(limit=5)
    # Coin Metrics 공개 데이터가 끝난 뒤(갱신 중단·지연)는 '마지막 시가총액 × 이후 가격 변화'로 연장 (공급량 일정 가정)
    cm_end = mcap.dropna(how="all").index.max() if mcap.notna().any().any() else None
    if cm_end is not None and cm_end < idx.max():
        base_m = mcap.loc[cm_end]
        base_p = close.loc[cm_end]
        later = close.loc[cm_end + pd.Timedelta(days=1):]
        ext = later.div(base_p).mul(base_m)
        mcap.loc[ext.index] = mcap.loc[ext.index].fillna(ext)
        meta_df.attrs["mcap_extended_after"] = str(cm_end.date())
    mcap = mcap.where(alive)
    if meta_df.attrs.get("mcap_extended_after"):
        log(f"  시가총액: Coin Metrics 데이터가 {meta_df.attrs['mcap_extended_after']} 까지라 이후는 가격 변화로 연장")
        meta_df["mcap_extended_after"] = meta_df.attrs["mcap_extended_after"]
    log(f"  패널: 코인 {close.shape[1]}개 (상장폐지 {int(meta_df['delisted'].sum())}개), "
        f"시가총액 연결 {meta_df['cm_id'].notna().sum()}개, {idx.min().date()} ~ {idx.max().date()}")
    return Panel(open_, high, low, close, vol, mcap, meta_df, tradable)


def eligible_others(panel: Panel, cfg: dict, method: str | None = None,
                    live_mcap: pd.Series | None = None) -> pd.DataFrame:
    """날짜별 '기타' 그룹 후보 (True/False). 그날까지의 정보만 씁니다.

    live_mcap: (매일 추천용) 오늘 시가총액(CoinGecko, 심볼→달러). 주면 마지막 날짜 순위를 이것으로 매깁니다.
    """
    u = cfg["universe"]
    method = method or u["rank_method"]
    core = set(u["core"])
    kinds = panel.meta["kind"]
    allowed = kinds.isin(["normal", "meme"] if u["include_meme"] else ["normal"])
    allowed &= ~panel.meta["base"].isin(core)
    # 스테이블 판정은 이름 목록(STABLE)으로만. 가격으로 판정하면 미래 가격을 보게 되거나(전체 기간 중앙값)
    # 잠깐 1달러 근처였던 일반 코인(GHST, KLAY 등)을 잘못 뺍니다.
    cols = allowed[allowed].index
    alive = panel.close[cols].notna()
    age = alive.cumsum()
    ok = alive & (age >= u["min_history_days"])
    if method == "mcap":
        score = panel.mcap[cols].copy()
        if live_mcap is not None and len(live_mcap):
            last = score.index[-1]
            score.loc[last] = [live_mcap.get(strip_multiplier(panel.meta.loc[c, "base"]), float("nan")) for c in cols]
    elif method == "volume":
        score = panel.volume_usd[cols].rolling(30, min_periods=20).median()
    else:
        raise ValueError(method)
    score = score.where(ok)
    rank = score.rank(axis=1, ascending=False, method="first")
    out = (rank <= u["others_top_n"]).reindex(columns=panel.close.columns, fill_value=False)
    return out.fillna(False)


def core_assets(panel: Panel, cfg: dict) -> list[str]:
    return [a for a in cfg["universe"]["core"] if a in panel.close.columns]
