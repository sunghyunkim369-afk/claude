"""가상 장부: Claude 가 추천한 주문을 '그 가격에 체결됐다'고 보고 계산해요 (실제 계좌와 다를 수 있어요).
실제로 다르게 했으면 알려 주세요. 장부를 실제 잔고로 다시 맞춰요.

  python journal/ledger.py                     지금 장부 보기
  python journal/ledger.py <marcap 폴더>        마지막 종가로 평가 (Claude 쪽에서 씀)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "ledger.json")
COST = {"buy": 0.00015, "sell": 0.00015 + 0.0018}


def load():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


def save(book):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=1)


def apply(book, date, side, code, name, qty, price, note=""):
    """주문 하나를 장부에 반영 (수수료·세금 포함)."""
    pos = book["holdings"].setdefault(code, {"name": name, "qty": 0, "avg": 0.0})
    gross = qty * price
    if side == "buy":
        fee = gross * COST["buy"]
        pos["avg"] = (pos["avg"] * pos["qty"] + gross) / (pos["qty"] + qty)
        pos["qty"] += qty
        book["cash"] -= gross + fee
    else:
        fee = gross * COST["sell"]
        pos["qty"] -= qty
        book["cash"] += gross - fee
        if pos["qty"] <= 0:
            del book["holdings"][code]
    book["orders"].append({"date": date, "side": side, "code": code, "name": name, "qty": qty, "price": price,
                           "fee": round(fee), "note": note})


def report(book, prices=None):
    prices = prices or {}
    lines, stock = [], 0.0
    for code, p in book["holdings"].items():
        px = prices.get(code, p.get("last", p["avg"]))
        v = p["qty"] * px
        stock += v
        lines.append(f"  {p['name']}({code}) {p['qty']:,}주 매입 {p['avg']:,.0f} → {px:,.0f} ({px / p['avg'] - 1:+.2%}) 평가 {v:,.0f}")
    total = stock + book["cash"]
    head = f"총평가 {total:,.0f}원 (시작 1억 대비 {total / 1e8 - 1:+.2%}), 현금 {book['cash']:,.0f}원"
    return "\n".join([head] + lines)


if __name__ == "__main__":
    b = load()
    prices = {}
    if len(sys.argv) > 1:
        import pandas as pd
        df = pd.read_parquet(os.path.join(sys.argv[1], "marcap-2026.parquet"), columns=["Code", "Date", "Close"])
        last = df.sort_values("Date").groupby("Code").tail(1)
        prices = dict(zip(last["Code"].astype(str), last["Close"]))
    print(report(b, prices))
