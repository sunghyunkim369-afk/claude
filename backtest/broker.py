"""주문 인터페이스. 백테스트 엔진은 Broker 만 알고, 체결 방식은 구현체가 정해요.

- SimulatedBroker: 백테스트용. 다음 날 시가(또는 스탑 가격)에 슬리피지·수수료·세금을 반영해 체결
- KisBroker: 실전 확장용 예시. 같은 Order 를 한국투자증권 API 주문으로 바꿔요 (../trading/kis.py 사용)
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Order:
    symbol: str
    market: str            # "KR" 또는 "US"
    side: str              # "buy" 또는 "sell"
    qty: int
    kind: str = "open"     # "open": 시가 주문, "stop": 장중 스탑(가격 이하로 내려가면 매도)
    stop_price: float = 0.0
    reason: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Fill:
    order: Order
    date: object
    qty: int
    price: float           # 슬리피지 반영한 체결가
    fee: float             # 수수료 + 세금
    gross: float           # price * qty


class Broker(ABC):
    @abstractmethod
    def submit(self, order, bar, date, cash=None):
        """주문을 내고 체결되면 Fill, 안 되면 None. bar 는 그날 시가/고가/저가/종가."""


class SimulatedBroker(Broker):
    def __init__(self, costs, slippage):
        self.costs = costs
        self.slippage = slippage

    def _fee(self, market, side, gross):
        c = self.costs[market]
        rate = c["buy_fee"] if side == "buy" else c["sell_fee"] + c.get("sell_tax", 0.0)
        return gross * rate

    def submit(self, order, bar, date, cash=None):
        if order.kind == "stop":
            if bar["Low"] > order.stop_price:
                return None
            # 시가부터 스탑 아래로 갭하락했으면 시가에, 아니면 스탑 가격에 체결돼요.
            raw = min(bar["Open"], order.stop_price)
        else:
            raw = bar["Open"]
        price = raw * (1 + self.slippage if order.side == "buy" else 1 - self.slippage)
        qty = order.qty
        if order.side == "buy" and cash is not None:
            rate = self.costs[order.market]["buy_fee"]
            qty = min(qty, math.floor(cash / (price * (1 + rate))))
        if qty <= 0:
            return None
        gross = price * qty
        return Fill(order, date, qty, price, self._fee(order.market, order.side, gross), gross)


class KisBroker(Broker):
    """실전·모의투자 확장 예시: trading/kis.py 의 Kis 클라이언트로 주문을 보내요.
    백테스트에는 쓰지 않아요. 주문 접수만 하고 체결 확인은 별도 조회가 필요해요."""

    def __init__(self, kis, us_exchange="NASD", us_limit_slippage=0.005):
        self.kis = kis
        self.us_exchange = us_exchange
        self.us_limit_slippage = us_limit_slippage

    def submit(self, order, bar=None, date=None, cash=None):
        if order.kind == "stop":
            raise NotImplementedError("스탑 주문은 실시간 시세를 보고 조건 충족 시 시장가로 내도록 구현해야 해요")
        if order.market == "KR":
            res = self.kis.kr_order(order.side, order.symbol, order.qty, None)  # 시장가
            return res
        price = self.kis.us_price(order.symbol, self.us_exchange)["price"]
        k = self.us_limit_slippage
        limit = round(price * (1 + k if order.side == "buy" else 1 - k), 2)
        return self.kis.us_order(order.side, order.symbol, self.us_exchange, order.qty, limit)
