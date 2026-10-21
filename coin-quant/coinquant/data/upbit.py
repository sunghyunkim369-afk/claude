"""업비트 API (KRW 마켓).

시세 조회는 키 없이 됩니다. 주문·잔고는 .env 의 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 가 필요합니다.
키를 만들 때: '자산조회'·'주문조회'·'주문하기'만 켜고, '출금하기'는 절대 켜지 마세요. 허용 IP 도 꼭 지정하세요.

업비트 일봉은 UTC 00:00(= KST 09:00)에 끊깁니다. 바이낸스 일봉과 같은 시각입니다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

BASE = "https://api.upbit.com/v1"
_session = requests.Session()


def _get(path: str, params: dict | None = None, headers: dict | None = None, retries: int = 4):
    for i in range(retries):
        r = _session.get(BASE + path, params=params, headers=headers, timeout=30)
        if r.status_code == 429:  # 초당 요청 제한
            time.sleep(0.5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


# ---------- 시세 (키 불필요) ----------

def markets(krw_only: bool = True) -> pd.DataFrame:
    data = _get("/market/all", {"is_details": "true"})
    df = pd.DataFrame(data)
    if krw_only:
        df = df[df["market"].str.startswith("KRW-")]
    df["symbol"] = df["market"].str.split("-").str[1]
    # 유의종목(투자유의) 표시
    if "market_event" in df:
        df["warning"] = df["market_event"].apply(lambda e: bool(e and e.get("warning")))
    elif "market_warning" in df:
        df["warning"] = df["market_warning"].eq("CAUTION")
    else:
        df["warning"] = False
    return df.reset_index(drop=True)


def daily_candles(market: str, since: pd.Timestamp | None = None, max_pages: int = 50) -> pd.DataFrame:
    """일봉 전체(최대 200개씩 거꾸로). since 이전에 닿으면 멈춤."""
    rows: list[dict] = []
    to = None
    for _ in range(max_pages):
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        page = _get("/candles/days", params)
        if not page:
            break
        rows += page
        oldest = page[-1]["candle_date_time_utc"]
        if since is not None and pd.Timestamp(oldest) <= since:
            break
        if len(page) < 200:
            break
        to = oldest.replace("T", " ")  # 이 시각 '이전' 캔들을 받음
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "value_krw"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["candle_date_time_utc"]).dt.normalize(),
        "open": df["opening_price"].astype(float),
        "high": df["high_price"].astype(float),
        "low": df["low_price"].astype(float),
        "close": df["trade_price"].astype(float),
        "volume": df["candle_acc_trade_volume"].astype(float),
        "value_krw": df["candle_acc_trade_price"].astype(float),
    })
    return out.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def ticker(markets_: list[str]) -> pd.DataFrame:
    out = []
    for i in range(0, len(markets_), 100):
        out += _get("/ticker", {"markets": ",".join(markets_[i:i + 100])})
    return pd.DataFrame(out)


def update_cache(out_dir: Path, markets_: list[str], log=print) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(markets_, 1):
        p = out_dir / f"{m}.csv"
        old = pd.read_csv(p, parse_dates=["date"]) if p.exists() else None
        since = old["date"].max() if old is not None and len(old) else None
        new = daily_candles(m, since=since)
        if old is not None:
            new = pd.concat([old, new]).drop_duplicates("date", keep="last").sort_values("date")
        new.to_csv(p, index=False, date_format="%Y-%m-%d")
        if i % 20 == 0 or i == len(markets_):
            log(f"  업비트 일봉 {i}/{len(markets_)}")


# ---------- 인증 (주문·잔고) ----------

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _jwt(payload: dict, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(sig)}"


def auth_header(access: str, secret: str, params: dict | None = None) -> dict:
    payload = {"access_key": access, "nonce": str(uuid.uuid4())}
    if params:
        q = urlencode(params, doseq=True).encode()
        payload["query_hash"] = hashlib.sha512(q).hexdigest()
        payload["query_hash_alg"] = "SHA512"
    return {"Authorization": f"Bearer {_jwt(payload, secret)}"}


def tick_size(price: float) -> float:
    """업비트 KRW 마켓 지정가 호가 단위 (보수적으로 큰 쪽).

    업비트는 호가 단위를 바꾼 적이 있어서, 옛 규칙·새 규칙 어느 쪽에서도 유효하도록 '둘의 배수'가 되는 큰 단위를 씁니다.
    (매수 지정가는 아래로 내림하므로 가격이 아주 조금 더 낮아질 뿐입니다.) 주문이 '호가 단위' 오류로 거절되면 이 표를 확인하세요.
    """
    for lo, t in ((1_000_000, 1000), (500_000, 500), (100_000, 100), (10_000, 50), (1_000, 5), (100, 1),
                  (10, 0.1), (1, 0.01), (0.1, 0.001), (0.01, 0.0001)):
        if price >= lo:
            return t
    return 0.00001


def round_down_tick(price: float) -> float:
    t = tick_size(price)
    return round(int(price / t) * t, 8)


class UpbitPrivate:
    """잔고 조회·주문. 키는 .env 에서만 읽습니다."""

    def __init__(self, access: str | None = None, secret: str | None = None):
        self.access = access or os.environ.get("UPBIT_ACCESS_KEY", "")
        self.secret = secret or os.environ.get("UPBIT_SECRET_KEY", "")
        if not self.access or not self.secret:
            raise RuntimeError(".env 에 UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 가 없습니다")

    def accounts(self) -> list[dict]:
        return _get("/accounts", headers=auth_header(self.access, self.secret))

    def api_keys(self) -> list[dict]:
        """키 목록·만료일 (권한 점검용)."""
        return _get("/api_keys", headers=auth_header(self.access, self.secret))

    def order(self, market: str, side: str, *, krw: float | None = None, volume: float | None = None) -> dict:
        """시장가 주문. 매수는 krw(원), 매도는 volume(수량)."""
        if side == "bid":
            params = {"market": market, "side": "bid", "ord_type": "price", "price": f"{krw:.0f}"}
        elif side == "ask":
            params = {"market": market, "side": "ask", "ord_type": "market", "volume": f"{volume:.8f}"}
        else:
            raise ValueError(side)
        h = auth_header(self.access, self.secret, params)
        r = _session.post(BASE + "/orders", json=params, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()

    def limit_order(self, market: str, side: str, price: float, volume: float) -> dict:
        """지정가 주문 (취소할 때까지 유효)."""
        p = round_down_tick(price)
        params = {"market": market, "side": side, "ord_type": "limit", "price": f"{p:f}".rstrip("0").rstrip("."),
                  "volume": f"{volume:.8f}"}
        h = auth_header(self.access, self.secret, params)
        r = _session.post(BASE + "/orders", json=params, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_order(self, uuid_: str) -> dict:
        q = {"uuid": uuid_}
        return _get("/order", q, headers=auth_header(self.access, self.secret, q))

    def cancel(self, uuid_: str) -> dict:
        q = {"uuid": uuid_}
        r = _session.delete(BASE + "/order", params=q, headers=auth_header(self.access, self.secret, q), timeout=30)
        r.raise_for_status()
        return r.json()
