# 반자동 매매 프로그램 (한국투자증권, 국내 + 미국 주식)

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
```

- **국내**: 장중(09:00~15:30)에 실행해요. 현재가로 지정가 주문을 내요.
- **미국**: 정규장(한국시간 서머타임 기간 3~11월 22:30~05:00, 그 밖에는 23:30~06:00)에 실행해요. 모의투자는 **지정가 주문만** 돼요.
- 프로그램이 고쳐지면 `python3 -m trading update` 로 최신 파일을 받아요. `.env`, `config.json` 은 그대로 둬요.
- 모든 후보와 주문 결과는 `trading/logs/날짜.jsonl` 에 남아요.

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
