"""바이낸스 공개 데이터(data.binance.vision) 일봉 수집.

- 상장폐지된 페어까지 남아 있어서 생존 편향 없이 과거를 시험할 수 있습니다.
- data.binance.vision 이 막힌 환경에서는 같은 파일이 있는 S3 주소로 받습니다.
- 일봉은 UTC 00:00 에 끊깁니다 (= KST 09:00, 업비트 일봉과 같은 시각).
- 2025-01-01 부터 현물 파일의 시간 단위가 밀리초 → 마이크로초로 바뀌어 둘 다 처리합니다.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

HOSTS = [
    "https://data.binance.vision",
    "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
]
KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]

_session = requests.Session()
_host_ok: str | None = None


def _get(path: str, params: dict | None = None, retries: int = 4) -> requests.Response | None:
    """HOSTS 를 차례로 시도. 404 는 None, 네트워크 오류는 재시도."""
    global _host_ok
    hosts = [_host_ok] if _host_ok else HOSTS
    last_err: Exception | None = None
    for host in hosts:
        for i in range(retries):
            try:
                r = _session.get(host + path, params=params, timeout=60)
                if r.status_code == 404:
                    _host_ok = host
                    return None
                r.raise_for_status()
                _host_ok = host
                return r
            except requests.RequestException as e:  # 막힌 호스트·일시 오류
                last_err = e
                if isinstance(e, requests.ConnectionError) or getattr(e.response, "status_code", 0) in (403, 407):
                    break
                time.sleep(2 ** i)
    raise ConnectionError(f"바이낸스 공개 데이터에 접속할 수 없습니다: {last_err}")


def list_prefix(prefix: str) -> list[str]:
    """S3 목록 (하위 폴더 + 파일 키)."""
    out: list[str] = []
    marker = ""
    while True:
        # 목록 API는 S3 주소에서만 됩니다
        r = _session.get(HOSTS[1], params={"delimiter": "/", "prefix": prefix, "marker": marker}, timeout=60)
        r.raise_for_status()
        t = r.text
        prefixes = re.findall(r"<Prefix>([^<]+)</Prefix>", t)[1:]
        keys = re.findall(r"<Key>([^<]+)</Key>", t)
        out += prefixes + keys
        if "<IsTruncated>true" not in t:
            return out
        nxt = re.findall(r"<NextMarker>([^<]+)</NextMarker>", t)
        marker = nxt[0] if nxt else (prefixes + keys)[-1]


def list_spot_symbols() -> list[str]:
    return [p.rstrip("/").split("/")[-1] for p in list_prefix("data/spot/monthly/klines/")]


def _read_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw = z.read(z.namelist()[0])
    first = raw.split(b"\n", 1)[0]
    header = 0 if first[:1].isalpha() else None
    df = pd.read_csv(io.BytesIO(raw), header=header)
    df = df.iloc[:, :12]
    df.columns = KLINE_COLS
    return df


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    t = pd.to_numeric(df["open_time"])
    unit = pd.Series("ms", index=t.index).where(t < 1e14, "us")
    dates = [pd.Timestamp(v, unit=u) for v, u in zip(t, unit)]
    out = pd.DataFrame({
        "date": pd.DatetimeIndex(dates).normalize(),
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["volume"].astype(float),
        "quote_volume": df["quote_volume"].astype(float),
        "trades": pd.to_numeric(df["trades"], errors="coerce"),
    })
    return out


def fetch_symbol_daily(symbol: str, since_month: str | None = None, since_date: str | None = None) -> pd.DataFrame:
    """한 심볼의 일봉 (월 파일 + 아직 월 파일이 없는 최근 날짜의 일 파일). since_date 이후 일 파일만 받음."""
    keys = [k for k in list_prefix(f"data/spot/monthly/klines/{symbol}/1d/") if k.endswith(".zip")]
    frames = []
    last_month = None
    for k in keys:
        m = re.search(r"-(\d{4}-\d{2})\.zip$", k).group(1)
        last_month = max(last_month or m, m)
        if since_month and m < since_month:
            continue
        r = _get("/" + k)
        if r is not None:
            frames.append(_read_zip(r.content))
    # 월 파일이 아직 없는 최근 날짜는 일 파일로 채움
    dkeys = [k for k in list_prefix(f"data/spot/daily/klines/{symbol}/1d/") if k.endswith(".zip")]
    for k in dkeys:
        mm = re.search(r"-((\d{4}-\d{2})-\d{2})\.zip$", k)
        if last_month and mm.group(2) <= last_month:
            continue
        if since_date and mm.group(1) <= since_date:
            continue
        r = _get("/" + k)
        if r is not None:
            frames.append(_read_zip(r.content))
    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "quote_volume", "trades"])
    df = _normalize(pd.concat(frames, ignore_index=True))
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def update_cache(out_dir: Path, symbols: list[str], workers: int = 16, log=print) -> dict:
    """캐시를 이어받기. 이미 받은 달은 건너뜁니다 (마지막 달은 다시 받음)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def job(sym: str):
        path = out_dir / f"{sym}.csv"
        old = None
        since = None
        if path.exists():
            old = pd.read_csv(path, parse_dates=["date"])
            if len(old):
                last = old["date"].max()
                if last < pd.Timestamp.now() - pd.Timedelta(days=45):
                    return sym, len(old)          # 오래전에 끝난(상장폐지) 페어는 다시 받지 않음
                since = last.strftime("%Y-%m")
        try:
            new = fetch_symbol_daily(sym, since_month=since,
                                     since_date=None if old is None or not len(old) else old["date"].max().strftime("%Y-%m-%d"))
        except Exception as e:  # 한 심볼 실패가 전체를 멈추지 않게
            return sym, f"error: {e}"
        if old is not None and len(old):
            new = pd.concat([old, new]).drop_duplicates("date", keep="last")
        new = new.sort_values("date")
        new.to_csv(path, index=False, date_format="%Y-%m-%d")
        return sym, len(new)

    results = {}
    with ThreadPoolExecutor(workers) as ex:
        for i, (sym, n) in enumerate(ex.map(job, symbols), 1):
            results[sym] = n
            if i % 50 == 0 or i == len(symbols):
                log(f"  바이낸스 일봉 {i}/{len(symbols)} 심볼 완료")
    return results


def rest_topup(out_dir: Path, symbols: list[str], days: int = 10, log=print) -> int:
    """공개 데이터 파일은 하루 늦게 올라오므로, 최근 며칠은 바이낸스 시세 API로 채웁니다.

    오늘(UTC) 아직 안 끝난 봉은 버립니다. API 가 막혀 있으면 조용히 건너뜁니다.
    """
    today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
    done = 0
    for host in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            _session.get(host + "/api/v3/ping", timeout=10).raise_for_status()
        except requests.RequestException:
            continue
        for sym in symbols:
            path = out_dir / f"{sym}.csv"
            if not path.exists():
                continue
            try:
                r = _session.get(host + "/api/v3/klines",
                                 params={"symbol": sym, "interval": "1d", "limit": days}, timeout=20)
                if r.status_code != 200:
                    continue
                df = _normalize(pd.DataFrame(r.json(), columns=KLINE_COLS))
            except requests.RequestException:
                continue
            df = df[df["date"] < today]
            if df.empty:
                continue
            old = pd.read_csv(path, parse_dates=["date"])
            new = pd.concat([old, df]).drop_duplicates("date", keep="last").sort_values("date")
            new.to_csv(path, index=False, date_format="%Y-%m-%d")
            done += 1
        log(f"  바이낸스 시세 API 로 최근 {days}일 보강: {done}개")
        return done
    log("  바이낸스 시세 API 접속 불가 → 공개 데이터 파일만 사용 (최근 하루가 빠질 수 있음)")
    return 0


def fetch_funding_history(symbol: str) -> pd.DataFrame:
    """USDⓈ-M 선물 펀딩비 (8시간마다). 2019-09 이후."""
    keys = [k for k in list_prefix(f"data/futures/um/monthly/fundingRate/{symbol}/") if k.endswith(".zip")]
    frames = []
    for k in keys:
        r = _get("/" + k)
        if r is None:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = z.read(z.namelist()[0])
        first = raw.split(b"\n", 1)[0]
        df = pd.read_csv(io.BytesIO(raw), header=0 if first[:1].isalpha() else None)
        df = df.iloc[:, :3]
        df.columns = ["calc_time", "interval", "rate"] if df.shape[1] == 3 else df.columns
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["time", "rate"])
    df = pd.concat(frames, ignore_index=True)
    t = pd.to_numeric(df["calc_time"])
    df["time"] = pd.to_datetime(t, unit="ms")
    df["rate"] = df["rate"].astype(float)
    return df[["time", "rate"]].drop_duplicates("time").sort_values("time").reset_index(drop=True)
