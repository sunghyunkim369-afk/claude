"""검증 결과를 마크다운 한 장으로."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest import metrics, validate as V

# 판정 기준 (결과를 보기 전에 정한 규칙)
RULE_TEXT = """판정 기준 (결과를 보기 전에 정해 둔 규칙, 셋 다 통과해야 '후보'):
- **위험↓**: 전체 MDD가 '같은 대상 계속 보유'보다 5%p 이상 얕고, 주요 시대의 2/3 이상에서 MDD가 더 얕음
- **샤프↑**: 샤프가 보유보다 높고, (계산한 경우) 같은 보유비율·보유기간의 무작위 신호보다 상위 10% 안
- **수익 유지**: 연복리가 보유 대비 2%p 넘게 깎이지 않음 (손실은 줄였는데 수익을 많이 깎으면 탈락)"""


def verdict(r: pd.Series) -> str:
    marks = []
    n = r.get("시대수", 0) or 0
    risk = (r.get("MDD차(vs 보유)", np.nan) > 0.05) and n > 0 and r.get("MDD우위시대", 0) >= np.ceil(n * 2 / 3)
    pct = r.get("무작위 타이밍 대비 샤프 백분위", np.nan)
    shp = (r.get("샤프차(vs 보유)", np.nan) > 0) and (np.isnan(pct) or pct >= 0.9)
    keep = r.get("CAGR차(vs 보유)", np.nan) >= -0.02
    marks += ["위험↓"] if risk else []
    marks += ["샤프↑"] if shp else []
    marks += ["수익유지"] if keep else []
    tag = "**후보**" if (risk and shp and keep) else ""
    return (tag + " " + "·".join(marks)).strip() or "-"


def _md(df, cols):
    from .indicator_validation import to_md
    return to_md(df, cols)


def render_md(ctx, timing, sel, reg, crash, wf, cad, era_curves, secs) -> str:
    cfg = ctx.cfg
    p = ctx.p
    meta = p.meta
    dev_end = ctx.lock.dev_end().date()
    n_trials = len(ctx.trials.rows)
    elig = ctx.elig["mcap"]
    yearly = {y: int(elig.loc[str(y)].any().sum()) for y in range(2018, ctx.lock.dev_end().year + 1)} if len(elig) else None
    ever = elig.columns[elig.any()]
    delisted_ever = meta.loc[[a for a in ever if a in meta.index], "delisted"].sum()
    out = []
    w = out.append
    w(f"# 지표 검증 결과 (개발 구간 2018-03 ~ {dev_end})\n")
    w("> 투자 판단을 돕는 도구일 뿐입니다. 과거 결과는 미래 수익을 보장하지 않으며 원금 손실 가능성이 있습니다.\n")
    w("## 0. 조건과 한계 (먼저 읽어 주세요)\n")
    w(f"- **가격 데이터**: 바이낸스 공개 데이터 일봉 (USDT 페어, 그 이전은 BTC 페어×BTC 가격). 코인 {p.close.shape[1]}개, "
      f"그중 상장폐지 {int(meta['delisted'].sum())}개 포함. **업비트 KRW 가격이 아닙니다** (이 작업 환경에서 업비트 접속이 막혀 있음). "
      "원화 가격 = 달러 가격 × 환율 × (1+김치 프리미엄)이라 수익률 흐름은 비슷하지만 똑같지 않습니다.")
    w("- **체결**: t일 종가(UTC 00:00 = KST 09:00)까지의 정보로 판단 → t+1일 시가에 체결. '하루 늦게 체결' 열은 t+1일 종가 체결.")
    c = cfg["costs"]
    w(f"- **비용**: 매매마다 수수료 {c['fee']:.2%} + 슬리피지 코어 {c['slippage_core']:.1%} / 기타 {c['slippage_other']:.1%}.")
    w(f"- **생존 편향**: 날짜별 시가총액(Coin Metrics, 추정 유통 시총 우선) 상위 {cfg['universe']['others_top_n']}개를 그날 기준으로 뽑음. "
      f"기타 그룹에 한 번이라도 들어온 코인 {len(ever)}개 중 {int(delisted_ever)}개가 나중에 바이낸스에서 상장폐지. "
      "상장폐지는 '마지막 가격에 매도'(기본)와 '0원'(폐지=0원 열) 둘 다 계산. 이름만 바뀐 코인(MATIC→POL, FTM→S 등)은 0원 처리하지 않음.")
    w(f"- **기타 그룹 제외**: 스테이블코인·래핑 토큰·거래소 토큰(BNB, FTT 등)·레버리지 토큰, 밈코인({'포함' if cfg['universe']['include_meme'] else '제외'}). "
      f"상장 {cfg['universe']['min_history_days']}일 미만 코인 제외 (200일 지표가 계산되도록).")
    if yearly is not None:
        w("- **기타 그룹 크기**: 연도별로 한 번이라도 들어온 코인 수 " +
          ", ".join(f"{y}년 {v}개" for y, v in yearly.items()) +
          ". 2018년 상반기는 시가총액 데이터·200일 이력이 있는 코인이 적어서 기타 그룹이 작습니다(6월 6개 → 9월 24개).")
    w("- **시가총액 순위의 한계**: Coin Metrics 무료 데이터에 있는 코인만 순위에 들어갑니다. 2018년 당시 상위였지만 여기 없는 코인(예: NANO, STEEM 등)은 빠졌을 수 있어서, "
      "'거래대금 순위' 버전(바이낸스 전체 페어 기준)으로도 같이 시험했습니다.")
    w(f"- **최종 검증 구간 {cfg['validation']['holdout_start']} ~ 현재는 잠가 두었습니다.** 이 문서의 숫자는 모두 그 이전 데이터입니다. "
      "공식을 확정한 뒤 `python -m coinquant final-check` 로 한 번만 엽니다.")
    w(f"- **시험한 조합 수: {n_trials}개.** 많이 시험할수록 '제일 좋아 보이는 것'이 우연일 가능성이 커집니다 (8절).")
    w("- **검증 못 한 것**: 김치 프리미엄(업비트 과거 가격 필요), 미결제약정(이번엔 미수집), ATR은 손절선(샹들리에)으로만 시험.")
    w(f"- 계산 시간 {secs / 60:.1f}분\n")

    # 1. 타이밍
    w("## 1. 타이밍 신호 (켜지면 보유, 꺼지면 현금 · 매일 판단)\n")
    w(RULE_TEXT + "\n")
    w("열 설명: CAGR=연복리, MDD=최대 낙폭, 월중앙값/월하위10%=월별 수익률 분포, 초과CAGR=BTC 그냥 보유 대비, "
      "CAGR차·MDD차=같은 대상 계속 보유 대비(+면 좋음), 시대=수익이 나았던 시대 수/MDD가 얕았던 시대 수(전체 시대 수), "
      "무작위 백분위=보유비율·평균 보유기간이 같은 무작위 신호 중 몇 %보다 샤프가 높은지.\n")
    t = timing.copy()
    t["시대(수익/MDD)"] = t.apply(lambda r: f"{int(r['수익우위시대'])}/{int(r['MDD우위시대'])} ({int(r['시대수'])})"
                                 if not pd.isna(r.get("시대수")) else "-", axis=1)
    t["판정"] = t.apply(lambda r: verdict(r) if r["신호"] != "(신호 없음: 계속 보유)" else "기준", axis=1)
    for u in t["대상"].unique():
        d = t[t["대상"] == u]
        extra = ["CAGR(폐지=0원)"] if u.startswith("기타") else ["CAGR(하루 늦게 체결)", "무작위 타이밍 대비 샤프 백분위"]
        w(f"### {u}\n")
        w(_md(d, ["신호", "판정", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)",
                  "CAGR차(vs 보유)", "MDD차(vs 보유)", "거래수", "거래승률", "거래중앙값", "최악거래", "평균투자비중",
                  "시대(수익/MDD)"] + extra))
        w("")
    # 2. 선택
    w("## 2. 기타 그룹: 상위 30개 중 K개 고르기 (주 1회 리밸런스)\n")
    w("위약 비교: '상위30 전체 균등', '무작위 K개(여러 번)', '고변동성 상위'. 무작위 백분위가 90% 이상이어야 '고르는 규칙이 운보다 낫다'고 볼 만합니다.\n")
    w("주의: 'BTC대비 30일 상대강도 상위'는 같은 날 모든 코인을 BTC로 나누는 것이라 '30일 수익률 상위'와 순위가 똑같습니다(사실상 같은 시험). "
      "타이밍 신호(1절)에서는 '켜짐/꺼짐' 기준이 달라서 결과가 다릅니다.\n")
    s = sel.copy()
    s["시대(수익/MDD)"] = s.apply(lambda r: f"{int(r['수익우위시대'])}/{int(r['MDD우위시대'])} ({int(r['시대수'])})"
                                 if not pd.isna(r.get("시대수")) else "-", axis=1)
    for m in s["순위기준"].unique():
        w(f"### 순위 기준: {'시가총액' if m == 'mcap' else '거래대금(30일 중앙값)'}\n")
        w(_md(s[s["순위기준"] == m], ["방법", "K", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률",
                                     "초과CAGR(vs BTC)", "CAGR차(vs 전체균등)", "무작위 대비 CAGR 백분위", "무작위 대비 샤프 백분위",
                                     "무작위 하위10% CAGR", "무작위 상위10% CAGR", "CAGR(폐지=0원)", "시대(수익/MDD)", "연회전율"]))
        w("")
    # 3. 국면
    w("## 3. 시장 국면 필터 (국면이 나쁘면 현금)\n")
    w("손실(MDD)을 얼마나 줄였는지와 수익(CAGR)을 얼마나 깎았는지를 같이 봅니다.\n")
    r = reg.copy()
    r["시대(수익/MDD)"] = r.apply(lambda x: f"{int(x['수익우위시대'])}/{int(x['MDD우위시대'])} ({int(x['시대수'])})"
                                 if not pd.isna(x.get("시대수")) else "-", axis=1)
    w(_md(r, ["대상", "국면 필터", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "CAGR차(vs 필터없음)",
              "MDD차(vs 필터없음)", "평균투자비중", "시대(수익/MDD)"]))
    w("")
    # 4. 급락
    w("## 4. 하루 급락 다음 날 처리 비교\n")
    w("급락일 종가 기준으로 그 뒤 7일·30일 수익. '바로 매도'는 다음 날 시가에 팔고(되사는 비용 포함) 현금 보유, "
      "'반등 기다려 매도'는 5일 안 첫 상승 마감 다음 날 시가에 매도. 같은 날 여러 코인이 같이 빠지는 경우가 많아서 "
      "'서로 다른 날짜' 수가 실제 표본 크기에 더 가깝습니다.\n")
    w(_md(crash, ["그룹", "기준", "기간(일)", "처리", "사건수", "서로 다른 날짜", "평균", "중앙값", "하위10%", "최악", "보유보다 나음 비율"]))
    w("")
    # 5. 워크포워드
    w("## 5. 워크포워드 (학습 24개월 성적으로 신호 1개 선택 → 다음 6개월에만 적용, 6개월씩 밀기)\n")
    w("학습 구간에서 고른 신호를 한 번도 안 본 다음 구간에 적용해 이어 붙인 결과입니다. 1절의 '전체 기간에서 제일 좋았던 신호'보다 이 숫자가 실제에 가깝습니다.\n")
    for u, (choices, res) in wf.items():
        if not res:
            continue
        w(f"### {u} ({res['기간']})\n")
        rows = []
        for k in ("워크포워드", "같은 대상 보유", "BTC 보유"):
            x = dict(res[k])
            x["전략"] = k
            rows.append(x)
        w(_md(pd.DataFrame(rows), ["전략", "연복리(CAGR)", "MDD", "샤프", "월중앙값", "월하위10%", "월승률", "초과CAGR(vs BTC)"]))
        w("")
        w(_md(choices, ["학습", "검증", "고른 신호", "학습 샤프", "후보 수"]))
        w("")
    # 6. 리밸런스
    w("## 6. 리밸런스 주기 (비용 포함)\n")
    w("1절에서 샤프 상위 2개 신호(전체 기간 기준으로 고른 것이라 낙관적)와 '계속 보유'를 매일/3일/7일로 비교.\n")
    cad = cad.copy()
    c = ctx.cfg["costs"]
    rate = cad["대상"].map(lambda u: c["fee"] + (c["slippage_other"] if u.startswith("기타") else c["slippage_core"]))
    cad["연간 비용 부담(추정)"] = cad["연회전율"] * rate
    w("연간 비용 부담(추정) = 연회전율(1년에 평가금의 몇 배를 사고팔았나) × 매매 1회 비용률. 평가금 대비 1년에 비용으로 나간 비율입니다.\n")
    w(_md(cad, ["대상", "신호", "리밸런스(일)", "연복리(CAGR)", "MDD", "샤프", "월하위10%", "연회전율", "연간 비용 부담(추정)", "거래수"]))
    w("")
    # 7. 시대별
    w("## 7. 시대·사건별 성과\n")
    for u, cs in era_curves.items():
        base = cs["보유"]
        bench = {"보유": base, "BTC": ctx.btc}
        for n, eq in cs.items():
            if n == "보유":
                continue
            et = V.era_table(eq, bench)
            w(f"### {u} · {n}\n")
            w(_md(et, ["시대", "수익", "MDD", "보유 수익", "보유 MDD", "BTC 수익", "BTC MDD"]))
            w("")
    # 8. 다중 시험
    w("## 8. 여러 조합 시험 보정\n")
    rows = []
    fams = pd.DataFrame(ctx.trials.rows)
    for (fam, u), d in fams.groupby(["family", "universe"]):
        best = d.sort_values("sr_daily", ascending=False).iloc[0]
        rows.append({"묶음": fam, "대상": u, "시험 수": len(d), "최고": best["name"],
                     "최고 연환산 샤프": best["sr_daily"] * np.sqrt(365),
                     "중앙값 샤프": d["sr_daily"].median() * np.sqrt(365),
                     "우연 아닐 확률(DSR)": ctx.trials.dsr(fam, u, best["name"])})
    w("DSR(Deflated Sharpe Ratio): 시험한 개수와 수익 분포의 치우침을 반영해 '최고 샤프가 운이 아닐 확률'을 계산. "
      "95% 이상이면 우연이 아닐 가능성이 높고, 그보다 낮으면 '여러 개 중 하나가 우연히 좋았을 수 있음'으로 봐야 합니다. "
      "주의: 여기서 '보유'도 시험 하나로 셌고, 신호끼리 서로 비슷해서(상관이 높아서) 실제 독립 시험 수는 더 적습니다.\n")
    t8 = pd.DataFrame(rows)
    t8["우연 아닐 확률(DSR)"] = t8["우연 아닐 확률(DSR)"].map(lambda v: f"{v:.0%}")
    w(_md(t8, ["묶음", "대상", "시험 수", "최고", "최고 연환산 샤프", "중앙값 샤프", "우연 아닐 확률(DSR)"]))
    w("")
    return "\n".join(out)
