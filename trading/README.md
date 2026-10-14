# 매매 프로그램 (한국투자증권, 국내 + 미국 주식)

프로그램이 관심 종목과 보유 종목을 보고 **주문 후보**를 만들어요. **사용자가 `y`를 입력한 것만** 주문해요.
기본값은 모의투자 서버예요. Python 3.9 이상이면 되고, 추가로 설치할 것은 없어요.

## 1. 준비

1. [KIS Developers](https://apiportal.koreainvestment.com)에서 Open API를 신청하고 **모의투자 계좌**를 만든 뒤 모의투자용 앱키와 시크릿을 받아요. 실전용 앱키와는 따로예요.
2. `trading/.env.example` 을 `trading/.env` 로 복사해서 앱키, 시크릿, 계좌번호를 채워요. `.env` 는 git 에 올라가지 않아요.
3. `trading/config.json` 에서 관심 종목, 예산, 한도를 고쳐요.

## 2. 실행 (저장소 맨 위 폴더에서)

```bash
python3 -m trading status               # 잔고
python3 -m trading plan                 # 주문 후보만 보기 (주문 안 함)
python3 -m trading trade                # 후보를 하나씩 확인(y/N/q)해서 주문
python3 -m trading trade --market kr    # 국내만 (us = 미국만)
python3 -m trading order buy AAPL 1     # 주문 하나 직접 내고 10초 뒤 체결 확인 (NYSE 종목은 --exchange NYSE)
python3 -m trading order buy AAPL 1 -y  # 확인(y) 없이 바로 주문
```

- **국내**: 장중(09:00~15:30)에 실행해요. 현재가로 지정가 주문을 내요.
- **미국**: 정규장(한국시간 서머타임 기간 3~11월 22:30~05:00, 그 밖에는 23:30~06:00)에 실행해요. 모의투자는 **지정가 주문만** 돼요.
- 프로그램이 고쳐지면 `python3 -m trading update` 로 최신 파일을 받아요. `.env`, `config.json` 은 그대로 둬요.
- 모든 후보와 주문 결과는 `trading/logs/날짜.jsonl` 에 남아요.

## 대회 모드 (`python -m trading contest`)

Windows 에서는 `trading` 폴더의 **`start_contest.bat` 을 더블클릭**하면 돼요 (최신 버전 받기 → 대회 모드 시작). `show_balance.bat` 은 잔고를 보여 줘요.

**PC 를 계속 켜 두기 싫으면**: `setup_schedule.bat` 을 한 번 더블클릭하세요 (`python -m trading schedule`).
Windows 작업 스케줄러에 평일 09:10(국내)·23:40(미국) 예약을 걸어서, PC 가 **절전 상태여도 깨워서** `contest --once` 로 한 번 돌고 끝내요.
PC 를 완전히 끄면 안 되고 절전으로 두면 돼요. 결과는 `trading/logs/scheduled.txt`. 예약 지우기: `python -m trading unschedule`.
예약을 쓰면 `start_contest.bat` 창은 닫아 두세요. 프로그램이 도는 동안에는 PC 가 절전에 들어가지 않게 막아요.

BanKIS 대학생 모의투자대회(~11/20)용이에요. `backtest/contest.py` 로 과거 5년의 5주 구간 수백 개를 시험해 정한 공식이에요 (모의투자 전용).

| | 국내 | 미국 |
|---|---|---|
| 대상 | `universe_kr.csv` (코스피 시총 상위 150 + 코스닥 상위 100, 코스피200·코스닥150 편입 가능성이 높은 종목) | `config.json` 의 `us.watchlist` |
| 후보 | 어제 종가 > EMA20 > EMA60, RSI14 < 80 | 같음 |
| 고르기 | 20거래일 수익률 상위 5종목 | 60거래일 수익률 1위 (아직 안 가진 종목) |
| 매매 | 2거래일마다 장 시작 직후: 빠진 종목 시장가 매도, 새 종목 총평가의 1/5 시장가 매수 | 미국 장마다 1종목씩 매수, 5종목이 되면 보유 |

- 국내 순위 계산은 250종목 일봉을 받느라 4~5분 걸려요.
- 국내 리밸런스 때마다 수상 조건 진행 상황(매매금액·매매일·종목·주문 수)을 보여 줘요. 주문 접수 기준이라 실제 체결 기준과 조금 다를 수 있어요.
- 상태는 `trading/.cache/contest-demo.json` 에 저장돼서 껐다 켜도 이어서 해요.

## 자동매매 (`python -m trading auto`)

켜 두면 장이 열려 있는 동안 15분마다(`config.json` 의 `auto.interval_min`) 주문 후보를 만들고 **확인 없이 바로 주문**해요.

- 국내: 평일 09:05~15:20 (한국시간). **시장가**로 주문해요.
- 미국: 평일 09:35~15:55 (미국 동부시간, 서머타임 자동 계산). 모의투자는 지정가만 돼서 현재가보다 0.5% 불리한 지정가(`us_limit_slippage_pct`)로 내서 체결되게 해요. 매수 수량은 그만큼 줄여 계획한 금액을 넘지 않아요.
- 같은 날 같은 종목은 한 번만 주문해요. 프로그램을 껐다 켜도 `trading/.cache/` 에 기록돼 있어서 중복되지 않아요.
- 하루 주문 한도(`max_orders_per_day`)를 넘으면 그날은 더 주문하지 않아요.
- 공휴일은 따로 계산하지 않아요. 그날은 서버가 주문을 거절하고 기록만 남아요.
- **멈추기**: 창에서 Ctrl+C, 또는 `trading` 폴더에 `STOP` 이라는 파일을 만들면 다음 확인 때 멈춰요.
- PC가 절전 모드로 들어가면 멈춰요. 켜 두는 동안은 Windows 설정 → 전원에서 절전을 "안 함"으로 두세요.
- 실전 계좌(`KIS_ENV=real`)에서는 `.env` 에 `KIS_AUTO_REAL=yes` 를 직접 넣어야만 자동매매가 돼요.

## 3. 매매 규칙 (`strategy.py`)

기본 규칙은 예시예요. 전략을 정하면 `decide()` 만 바꾸면 돼요.

| | 조건 |
|---|---|
| 매수 | 현재가 > 20일선 > 60일선, 그리고 현재가가 20일선보다 5% 이내로만 높을 때 |
| 매도 | 손실 -8% 이하(손절), 수익 +20% 이상(익절), 현재가가 60일선 아래(추세 이탈) |

숫자는 `config.json` 의 `strategy` 에서 바꿔요.

## 4. 안전장치

- **종목당 금액**: 예산 ÷ 최대 보유 종목 수, 1회 주문 한도(`max_order_krw`, `max_order_usd`), 주문가능금액 중 가장 작은 값이에요.
- **최대 보유 종목 수**: 이미 가진 종목 수까지 포함해서 세요.
- **하루 주문 횟수**: `max_orders_per_day` 를 넘으면 멈춰요.
- **실전 전환**: `.env` 에서 `KIS_ENV=real` 로 바꾸고, 실행할 때 `실전` 이라고 직접 입력해야만 진행돼요.
- **토큰**: `trading/.cache/` 에 파일 권한 600으로 저장해서 하루 동안 다시 써요. 토큰은 발급 횟수 제한이 있어요.

## 5. 테스트

```bash
python3 -m unittest discover -s trading/tests -t .
```

가짜 API 응답으로 검증하는 테스트라서 실제 서버에는 접속하지 않아요. TR 번호, 모의/실전 서버 구분, 수량 계산, 한도, 확인한 주문만 나가는지를 확인해요.
요청 규격은 [한국투자증권 공식 예제](https://github.com/koreainvestment/open-trading-api)(`examples_llm/`)를 따랐어요. 실제 계좌로는 아직 실행해 보지 않았으니, 처음에는 `status` → `plan` 순서로 응답이 제대로 오는지 먼저 확인해 주세요.
