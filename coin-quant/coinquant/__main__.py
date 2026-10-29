"""coin-quant 명령 모음.

  python -m coinquant update              데이터 갱신 (바이낸스·Coin Metrics·환율·업비트) + 가격 패널
  python -m coinquant validate [--quick]  지표 검증 (최종 검증 구간 제외) → docs/validation-날짜.md
  python -m coinquant candidates          완성 공식 후보 비교 (개발 구간)
  python -m coinquant cycle               비트코인 4년 주기(반감기 사이클) 검증 → docs/cycle-analysis-날짜.md
  python -m coinquant trend-study         스윙 매매 검증 (며칠·몇 주·몇 달 추세 포착률) → docs/trend-study-날짜.md
  python -m coinquant short-study         숏(무기한 선물) 검증 → docs/short-study-날짜.md
  python -m coinquant short-swing         단기 스윙(며칠~2주) 검증 → docs/short-swing-날짜.md
  python -m coinquant limit-entry         지정가 매수(안 닿으면 포기) 검증 → docs/limit-entry-날짜.md
  python -m coinquant breakout            돌파 검증 → docs/breakout-날짜.md
  python -m coinquant levels              매물대·수평 저항/지지선 검증 → docs/levels-날짜.md
  python -m coinquant levels-regime       매물대·선 이론 ETF 승인 전후 비교 → docs/levels-regime-날짜.md
  python -m coinquant extended            확정 공식 2018년 이전까지 검증 → docs/extended-날짜.md
  python -m coinquant stocks-validate     주식(지수·종목)에서 확정 공식 검증 → docs/stocks-validation-날짜.md
  python -m coinquant stocks-brief        주식 오늘의 소식 → out/briefing/daily/날짜.md + 텔레그램
  python -m coinquant futures-lab         선물 단기 검증 (터틀·슈퍼트렌드·래리 윌리엄스·펀딩비 등 31개)
  python -m coinquant alert [--test]      매수·매도 알림 (텔레그램·윈도우 알림, daily 가 자동으로 부름)
  python -m coinquant report [--no-news]  매일 리포트 → reports/daily-날짜.html
  python -m coinquant daily               update + report (예약 실행용)
  python -m coinquant judge ...           내 판단 넣기/빼기 (judge -h)
  python -m coinquant trade [--auto]      추천 주문 실행 (stage 2 에서만, 주문마다 y/N)
  python -m coinquant final-check         확정한 공식으로 최종 검증 구간을 '한 번' 열기
  python -m coinquant check               보안 점검 (.env, 키 권한 안내)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import config as config_mod


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(config_mod.ROOT / ".env")
    except ImportError:
        pass


def cmd_update(a, cfg):
    from .data.update import update_all
    clone = Path(a.cm_clone) if a.cm_clone else None
    update_all(cfg, config_mod.cache_dir(cfg), upbit_too=not a.no_upbit, cm_clone=clone)


def cmd_validate(a, cfg):
    from .research import indicator_validation
    indicator_validation.main(cfg, quick=a.quick)


def cmd_candidates(a, cfg):
    from .research import candidates
    candidates.main(cfg)
    candidates.sensitivity(cfg)


def cmd_cycle(a, cfg):
    from .research import cycle
    cycle.write(cfg)


def cmd_trend_study(a, cfg):
    from .research import trend_study
    trend_study.main(cfg)


def cmd_short_study(a, cfg):
    from .research import short_study
    short_study.main(cfg)


def cmd_short_swing(a, cfg):
    from .research import short_swing
    short_swing.main(cfg)


def cmd_limit_entry(a, cfg):
    from .research import limit_entry
    limit_entry.main(cfg)


def cmd_breakout(a, cfg):
    from .research import breakout
    breakout.main(cfg)


def cmd_levels(a, cfg):
    from .research import levels_study
    levels_study.main(cfg)


def cmd_levels_regime(a, cfg):
    from .research import levels_regime
    levels_regime.main(cfg)


def cmd_extended(a, cfg):
    from .research import extended
    extended.main(cfg)


def cmd_stocks_validate(a, cfg):
    from .stocks import validate
    validate.main(cfg)


def cmd_stocks_brief(a, cfg):
    from .stocks import brief
    brief.main(cfg, send=not a.no_send)


def cmd_futures_lab(a, cfg):
    from .research import futures_lab
    futures_lab.run(cfg)


def cmd_breakout4h(a, cfg):
    from .research import breakout4h
    breakout4h.run(cfg)


def cmd_stocks_bias(a, cfg):
    from .stocks import bias_check
    bias_check.main(cfg)


def cmd_rules_check(a, cfg):
    from .research import rules_check
    rules_check.main(cfg)


def cmd_report(a, cfg):
    from .app import report
    path, orders = report.build(cfg, with_news=not a.no_news, rebalance_now=getattr(a, "rebalance_now", False))
    print(f"추천 주문 {len(orders)}건 → `python -m coinquant trade` 로 확인 후 실행 (stage 2 일 때)")


def cmd_alert(a, cfg):
    from .app import alert
    alert.main(cfg, test=getattr(a, "test", False), with_news=not getattr(a, "no_news", False), light=getattr(a, "light", False))


def cmd_daily(a, cfg):
    a.cm_clone, a.no_upbit, a.no_news, a.test, a.light = None, False, False, False, False
    cmd_update(a, cfg)
    cmd_report(a, cfg)
    try:
        cmd_alert(a, cfg)
    except Exception as e:  # 알림이 실패해도 리포트는 이미 만들어짐
        print(f"알림 실패: {type(e).__name__}: {e}")


def cmd_judge(a, cfg):
    from .app.report import user_dir
    from .overrides import ACTIONS, Judgments
    j = Judgments(user_dir())
    if a.action == "list":
        act = j.active()
        if not act:
            print("유효한 판단 없음")
        for k, r in act.items():
            v = f" {r['value']:.1%}" if r.get("value") is not None else ""
            print(f"{k:<8} {ACTIONS[r['action']]}{v}  이유: {r['reason']}  ({r['time'][:10]}{', ~' + r['until'] if r.get('until') else ''})")
        return
    if a.action == "weight":
        if len(a.rest) < 2:
            sys.exit('사용법: judge weight SOL 0.15 "이유"')
        value, reason = float(a.rest[0]), " ".join(a.rest[1:])
    else:
        value, reason = None, " ".join(a.rest)
    rec = j.add(a.action, a.asset, reason, value, a.until)
    print(f"기록: {rec['asset']} {ACTIONS[rec['action']]} — {rec['reason']}")


def cmd_trade(a, cfg):
    from .app import trade
    trade.run(cfg, auto=a.auto)


def cmd_final_check(a, cfg):
    from .research import final_check
    final_check.main(cfg, force=a.force)


def cmd_check(a, cfg):
    root = config_mod.ROOT
    ok = True
    env = root / ".env"
    print(f".env 파일: {'있음' if env.exists() else '없음 (.env.example 을 복사해 만드세요)'}")
    try:
        ign = subprocess.run(["git", "check-ignore", "-q", str(env)], cwd=root).returncode == 0
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=root,
                                 capture_output=True).returncode == 0
        print(f".gitignore 가 .env 를 막는지: {'예' if ign else '아니오 ← 위험'}")
        print(f".env 가 git 에 올라가 있는지: {'예 ← 즉시 키 폐기·재발급하세요' if tracked else '아니오'}")
        ok &= ign and not tracked
    except FileNotFoundError:
        print("git 이 없어 .gitignore 점검을 건너뜁니다")
    has = bool(os.environ.get("UPBIT_ACCESS_KEY")) and bool(os.environ.get("UPBIT_SECRET_KEY"))
    print(f"업비트 키: {'있음' if has else '없음 (추천만 쓰면 필요 없음)'}")
    if has:
        try:
            from .data.upbit import UpbitPrivate
            keys = UpbitPrivate().api_keys()
            for k in keys:
                print(f"  키 만료일: {k.get('expire_at')}")
        except Exception as e:
            print(f"  키 확인 실패: {e} (허용 IP가 이 컴퓨터가 아니거나 키가 잘못됨)")
    print("\n꼭 확인 (업비트 웹 → 마이페이지 → Open API 관리):")
    print("  1) 권한: '자산조회', '주문조회', '주문하기'만. '출금하기'·'입금하기'는 끄기 (출금 권한은 절대 켜지 마세요)")
    print("  2) 허용 IP: 이 컴퓨터의 공인 IP만 등록")
    print("  3) 키는 .env 에만. 대화창·메신저·GitHub 에 붙여넣지 않기. 유출 의심 시 즉시 삭제 후 재발급")
    t = cfg["trading"]
    print(f"\n주문 설정: stage={t['stage']} (1=추천만, 2=확인 후 주문), paper={t['paper']}, auto_order={t['auto_order']}, "
          f"1건 최대 {t['max_order_krw']:,}원")
    sys.exit(0 if ok else 1)


def main(argv=None):
    _env()
    ap = argparse.ArgumentParser(prog="coinquant", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="설정 파일 (기본 config.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("update")
    s.add_argument("--no-upbit", action="store_true")
    s.add_argument("--cm-clone", help="(개발용) Coin Metrics 저장소 사본 csv 폴더")
    s.set_defaults(fn=cmd_update)
    s = sub.add_parser("validate")
    s.add_argument("--quick", action="store_true", help="무작위 위약 횟수를 줄여 빨리")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("candidates", help="완성 공식 후보 비교 (개발 구간)")
    s.set_defaults(fn=cmd_candidates)
    s = sub.add_parser("cycle", help="비트코인 4년 주기 검증")
    s.set_defaults(fn=cmd_cycle)
    s = sub.add_parser("trend-study", help="스윙(추세) 매매 검증: 기간별 추세 포착률")
    s.set_defaults(fn=cmd_trend_study)
    s = sub.add_parser("short-study", help="숏(무기한 선물) 검증, 현물 롱 + 선물 숏 합산")
    s.set_defaults(fn=cmd_short_study)
    s = sub.add_parser("short-swing", help="단기 스윙(며칠~2주) 검증")
    s.set_defaults(fn=cmd_short_swing)
    s = sub.add_parser("limit-entry", help="지정가 매수(안 닿으면 포기) 검증 + 중기 의미")
    s.set_defaults(fn=cmd_limit_entry)
    s = sub.add_parser("breakout", help="돌파 검증 (못 뚫으면 안 사고 뚫으면 산다)")
    s.set_defaults(fn=cmd_breakout)
    s = sub.add_parser("levels", help="매물대·수평 저항/지지선 검증")
    s.set_defaults(fn=cmd_levels)
    s = sub.add_parser("levels-regime", help="매물대·선 이론 ETF 승인 전후 비교")
    s.set_defaults(fn=cmd_levels_regime)
    s = sub.add_parser("alert", help="매수·매도 알림 (상태가 바뀐 날만, --test 는 무조건 보냄)")
    s.add_argument("--test", action="store_true")
    s.add_argument("--no-news", action="store_true")
    s.add_argument("--light", action="store_true", help="코어 3종만 받아서 실행 (GitHub Actions 용)")
    s.set_defaults(fn=cmd_alert)
    s = sub.add_parser("stocks-validate", help="주식(지수·종목)에서 확정 공식 검증 (인터넷 필요: yfinance)")
    s.set_defaults(fn=cmd_stocks_validate)
    s = sub.add_parser("stocks-brief", help="주식 오늘의 소식 페이지 + 텔레그램 (인터넷 필요)")
    s.add_argument("--no-send", action="store_true", help="텔레그램 보내지 않음")
    s.set_defaults(fn=cmd_stocks_brief)
    s = sub.add_parser("futures-lab", help="선물 단기 검증: 유명 트레이더·교과서 방식과 파생 지표 (롱·숏, 펀딩비, 레버리지)")
    s.set_defaults(fn=cmd_futures_lab)
    s = sub.add_parser("breakout4h", help="4시간봉 거래량 돌파(롱만): 생존 편향(그때 상위 20, 상폐 포함)·하루 한 번 판단 검증")
    s.set_defaults(fn=cmd_breakout4h)
    s = sub.add_parser("stocks-bias", help="주식 지수 공식 편향 점검: 세계 지수·배당·현금 이자·세금·시작 시점·환율·지연 (인터넷 필요)")
    s.set_defaults(fn=cmd_stocks_bias)
    s = sub.add_parser("rules-check", help="코인 체크리스트 규칙 검증 (FOMC 절반 매수, 4년 주기 필터, 주기 최저 이탈)")
    s.set_defaults(fn=cmd_rules_check)
    s = sub.add_parser("extended", help="확정 공식 2018년 이전까지 검증 + 확률 높을 때만 사기")
    s.set_defaults(fn=cmd_extended)
    s = sub.add_parser("report")
    s.add_argument("--no-news", action="store_true")
    s.add_argument("--rebalance-now", action="store_true", help="리밸런스 날이 아니어도 오늘 목표대로 주문 목록 만들기 (처음 시작할 때)")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("daily")
    s.set_defaults(fn=cmd_daily)
    s = sub.add_parser("judge", help="내 판단: lock/exclude/weight/release/list")
    s.add_argument("action", choices=["lock", "exclude", "weight", "release", "list"])
    s.add_argument("asset", nargs="?", default="")
    s.add_argument("rest", nargs="*", help="(weight 면 비중 먼저) 이유 한 줄")
    s.add_argument("--until", help="유효 기한 YYYY-MM-DD")
    s.set_defaults(fn=cmd_judge)
    s = sub.add_parser("trade")
    s.add_argument("--auto", action="store_true", help="확인 없이 실행 (config 의 auto_order = true 일 때만)")
    s.set_defaults(fn=cmd_trade)
    s = sub.add_parser("final-check")
    s.add_argument("--force", action="store_true", help="공식이 '확정'이 아니어도 열기 (권하지 않음)")
    s.set_defaults(fn=cmd_final_check)
    s = sub.add_parser("check")
    s.set_defaults(fn=cmd_check)
    a = ap.parse_args(argv)
    if a.cmd == "judge" and a.action != "list" and not a.asset:
        ap.error("코인을 적어 주세요. 예: judge lock BTC \"이유\"")
    if a.cmd == "judge" and a.action in ("lock", "exclude", "release") and not a.rest:
        ap.error('이유 한 줄을 적어 주세요. 예: judge lock BTC "ETF 자금 유입"')
    cfg = config_mod.load(a.config)
    a.fn(a, cfg)


if __name__ == "__main__":
    main()
