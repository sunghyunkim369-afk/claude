"""대회 매매 가능 종목: 코스피200 + 코스닥150 (대회 기준일 2026-09-21).

1순위: KRX 지수 구성종목 (FinanceDataReader SnapDataReader, 코스피200=1028, 코스닥150=2203)
2순위(실패 시): 시가총액 상위 근사 (코스피 상위 200, 코스닥 상위 150, 우선주·스팩·리츠 제외).
  실제 지수는 업종 대표성 등으로 뽑아서 근사 목록과 조금 달라요. 대회 공지의 공식 목록으로 확인하세요.

실행: python universe.py → universe_kr.csv (자동매매 프로그램도 이 파일로 매매 종목을 제한해요)
"""

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "universe_kr.csv")


def from_krx():
    import FinanceDataReader as fdr
    frames = []
    for code, index in (("1028", "KOSPI200"), ("2203", "KOSDAQ150")):
        df = fdr.SnapDataReader(f"KRX/INDEX/STOCK/{code}")
        if df is None or len(df) < 100:
            raise ValueError(f"{index} 구성종목을 받지 못했어요 ({0 if df is None else len(df)}개)")
        frames.append(pd.DataFrame({"code": df["Code"].astype(str).str.zfill(6), "name": df["Name"], "index": index}))
    return pd.concat(frames, ignore_index=True), "KRX 지수 구성종목"


def _common_only(df):
    name = df["Name"].astype(str)
    code = df["Code"].astype(str)
    bad = name.str.contains(r"우$|우B$|우\(|스팩|리츠|REIT", regex=True) | ~code.str.fullmatch(r"\d{5}0")
    return df[~bad]


def from_marcap():
    import FinanceDataReader as fdr
    frames = []
    for market, n, index in (("KOSPI", 200, "KOSPI200"), ("KOSDAQ", 150, "KOSDAQ150")):
        df = _common_only(fdr.StockListing(market)).sort_values("Marcap", ascending=False).head(n)
        frames.append(pd.DataFrame({"code": df["Code"].astype(str).str.zfill(6), "name": df["Name"], "index": index}))
    return pd.concat(frames, ignore_index=True), "시가총액 상위 근사"


def build(log=print):
    errors = []
    for fn in (from_krx, from_marcap):
        try:
            df, source = fn()
            df["source"] = source
            df = df.drop_duplicates("code")
            log(f"매매 가능 종목 {len(df)}개 ({source})")
            return df
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
    raise RuntimeError("종목 목록을 만들지 못했어요: " + "; ".join(errors))


def load():
    if not os.path.exists(PATH):
        return None
    return pd.read_csv(PATH, dtype={"code": str})


if __name__ == "__main__":
    df = build()
    df.to_csv(PATH, index=False, encoding="utf-8-sig")
    print(df.groupby("index").size().to_string())
    sys.exit(0)
