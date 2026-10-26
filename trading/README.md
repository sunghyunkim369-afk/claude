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

국내는 **입상형(기본)**: 20거래일 수익률 상위 3종목(총평가의 1/3씩), 2거래일마다 교체로 시작해요.
약세장 대응은 기본으로 꺼져 있어요. `python -m trading contest --weak half` 로 켜면 약세장(250종목 중 어제 종가가 60일선 위인 종목이 40% 미만)일 때 3종목 그대로 금액만 절반으로 줄여요 (`--weak off` 로 끄기).
생존 편향을 뺀 검증에서 손실은 줄지만 +30% 확률과 수상 조건 충족률도 떨어져서 기본은 꺼 뒀어요.
리밸런스 때 **안정형**(아래 표)으로 자동으로 바꿔요. 직접 바꾸기: `python -m trading contest --mode stable` (또는 `aggressive`).
계좌를 초기화했다면 `python -m trading contest --reset` 으로 진행 상태를 지우세요.
리밸런스 날이 아니어도 지금 바로 공식대로 맞추려면 `python -m trading contest --now` (장중에).

미국 입상형(선택): `python -m trading contest --us-mode aggressive` 로 켜요 (`basic` 으로 되돌리기).
레버리지 ETF(TQQQ·SOXL 등)·고변동 16종목 중 20거래일 수익률 1위 1종목에 거의 전부, 2거래일마다 교체.
수상 조건(5종목·5일)을 채울 때까지는 미국 장마다 다른 상위 종목을 1주씩 더 사요.
NYSE Arca 상장 ETF(SOXL 등)는 거래소 AMEX 로 주문해요.

| (안정형) | 국내 | 미국 |
|---|---|---|
| 대상 | `universe_kr.csv` (코스피 시총 상위 150 + 코스닥 상위 100, 코스피200·코스닥150 편입 가능성이 높은 종목) | `config.json` 의 `us.watchlist` |
| 후보 | 어제 종가 > EMA20 > EMA60, RSI14 < 80 | 같음 |
| 고르기 | 20거래일 수익률 상위 5종목 | 60거래일 수익률 1위 (아직 안 가진 종목) |
| 매매 | 2거래일마다 장 시작 직후: 빠진 종목 시장가 매도, 새 종목 총평가의 1/5 시장가 매수 | 미국 장마다 1종목씩 매수, 5종목이 되면 보유 |

### 수동 매매 (`python -m trading manual ...`)

내 판단으로 산 종목은 **자동매매가 팔지도, 더 사지도 않아요.** 자동매매 몫은 (총평가 − 수동 보유분) ÷ 3 으로 줄어요.

```bash
python -m trading manual check            # 오늘 급락 반등 조건인지 (시총 상위 30 평균 −2% 이하 + −7% 이상 빠진 종목)
python -m trading manual buy 005930       # 총평가의 20% 를 1% 높은 지정가로 매수 + 수동 보유로 등록 (--pct 10 처럼 비율 변경)
python -m trading manual sell 005930      # 전부 시장가 매도 + 등록 해제
python -m trading manual keep 053800      # 가진 종목을 총평가의 1/3 만 남기고 수동 보유로 (넘는 만큼은 다음 리밸런스 때 한 번 매도)
python -m trading manual add 005930       # MTS 로 직접 산 종목을 수동 보유로 등록 (주문 안 함)
python -m trading manual remove 005930    # 등록 해제 → 다음 리밸런스부터 자동매매가 관리 (목표가 아니면 팔아요)
python -m trading manual list             # 수동 보유 종목과 손익
```

- `buy`·`sell` 은 OpenAPI 주문이라 대회 OpenAPI 체결 횟수에도 들어가요. 확인(y) 없이 내려면 `-y`.
- 급락 반등 조건(`check`)은 과거 5년 검증 기준이에요: 그날 종가 근처(15:10~15:20)에 사서 다음 날 시가에 팔면
  비용 빼고 평균 +0.9%(62% 이익), 5일 보유 평균 +3.1%(64% 이익). 그 종목만 빠진 날은 효과가 없었어요.
- `start_contest.bat` 을 켜 둔 채로 다른 창에서 써도 돼요. 다음 확인 때 수동 목록을 다시 읽어요.
- 수동 보유 종목이 20일선 아래로 내려오면 리밸런스 때 알려 줘요 (파는 건 직접 `manual sell`).
- `contest --reset` 을 하면 수동 목록도 지워져요.

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
