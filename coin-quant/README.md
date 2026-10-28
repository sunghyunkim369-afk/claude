# coin-quant — 코인 "퀀트 + 개인 판단" 도우미

매일 (1) 시장 상태와 이슈를 정리하고, (2) **과거 데이터로 검증한 공식**으로 매수·매도·보유를 추천하고,
(3) 내 판단을 공식 위에 한 겹 더합니다. 기본은 **추천만** 하고, 주문은 내가 확인한 것만 냅니다.

> ⚠️ 투자 판단을 돕는 도구일 뿐 투자 권유가 아닙니다. 과거 성과는 미래를 보장하지 않으며 원금 손실(전액 손실 포함) 가능성이 있습니다.

## 지금 상태 (2026-10-21)

| 단계 | 상태 |
|---|---|
| 데이터 수집 | ✅ 바이낸스 공개 데이터(상장폐지 포함) · Coin Metrics 시가총액 · 환율 · 펀딩비 · 업비트(내 PC에서) |
| 백테스트 엔진 | ✅ 종가 판단 → 다음 봉 시가 체결, 수수료·슬리피지, 상장폐지 2가지 처리, 비중·주문 수 한도 |
| 지표 검증 | ✅ [`docs/validation-2026-10-21.md`](docs/validation-2026-10-21.md) (지표별·워크포워드·위약·DSR) |
| 스윙(추세) 검증 | ✅ [`docs/trend-study-2026-10-21.md`](docs/trend-study-2026-10-21.md) (며칠·몇 주·몇 달 추세 포착률) |
| 4년 주기 검증 | ✅ [`docs/cycle-analysis-2026-10-21.md`](docs/cycle-analysis-2026-10-21.md) |
| 숏(무기한 선물) 검증 | ✅ [`docs/short-study-2026-10-21.md`](docs/short-study-2026-10-21.md) — 2020~24년 추세 숏은 모두 손실, 현물 롱에 숏을 더하면 수익이 더 크게 줄어 **숏은 공식에 넣지 않는 걸 권함** (바이비트 대신 바이낸스 선물 데이터, 바이비트 수수료) |
| 공식 확정 | ✅ 2026-10-21 '스윙(중기 중심)' 확정 (`config.toml [formula]`) |
| 최종 검증 | ✅ 2026-10-21 1번째 열람 [`docs/final-check-2026-10-21.md`](docs/final-check-2026-10-21.md) — 2025-01~2026-10: 총수익 +5.6%, MDD −20% (BTC 보유 −11%, −53%). 손실 방어는 확인, 수익은 개발 구간보다 훨씬 작음 |
| 지정가 매수 검증 | ✅ [`docs/limit-entry-2026-10-21.md`](docs/limit-entry-2026-10-21.md) — '안 닿으면 포기'는 코어에서 손해(강한 추세를 놓침), '신호 유지 시 매일 다시 지정가'는 시장가와 같거나 조금 나음 → 기본값: 코어 −1%·기타 −3% 지정가 매수, 매도는 시장가 (`config.toml [trading]`) |
| 돌파·매물대·저항/지지선 검증 | ✅ [`docs/breakout-2026-10-21.md`](docs/breakout-2026-10-21.md), [`docs/levels-2026-10-21.md`](docs/levels-2026-10-21.md) — 저항 근처·매물대 돌파 뒤가 싼 날보다 나았지만, '돌파 실패면 안 산다'·수평선 기준은 데이터가 지지하지 않음. 공식에 붙여도 개선 없음 → 리포트에 참고 정보로만 표시 |
| 매물대·선 이론 ETF 전후 비교 | ✅ [`docs/levels-regime-2026-10-24.md`](docs/levels-regime-2026-10-24.md) — BTC·코어 지지선이 예전보다 덜 지켜짐(BTC 5일 버팀 71%→44%), 하지만 '속이고 결국 뚫는다'는 확인 안 됨, 변화는 2022년부터. 매매 변형 개선 없음 → 공식 그대로 |
| 2018년 이전까지 확장 검증 | ✅ [`docs/extended-2026-10-26.md`](docs/extended-2026-10-26.md) — 2013~2017(안 본 구간): 보유와 비슷한 수익, MDD −70% vs −85%, 2014~15 약세 +48% vs −43%. '확률 높을 때만 사기'는 승률↑ 수익↓ → 채택 안 함. 중기 +1로 약해진 상태의 신규 매수는 확률 낮음 |
| 단기 스윙 검증 | ✅ [`docs/short-swing-2026-10-21.md`](docs/short-swing-2026-10-21.md) — 며칠짜리 추세는 못 먹음, 단독 운용은 이득 없음, 확정 공식 70% + 단기 추세 30%가 가장 근거 있음(모의로 먼저 확인 권함) |
| 매일 시황·매수·매도 알림 | ✅ `python -m coinquant alert` — 매일 시황(등락·200일선·국면·변동성·펀딩비) + BTC·ETH·SOL 신호(매수 가능 / 신규 진입 대기 / 관망, 바뀐 날 🔔) + 일정·4년 주기·뉴스 체크리스트를 텔레그램·윈도우 알림으로. PC 예약 실행이 자동으로 부르고, PC 없이 GitHub Actions(`.github/workflows/coin-quant-daily.yml`)로도 매일 09:20 발송 |
| 주식(국내·해외) 검증 | ✅ [`docs/stocks-validation-2026-10-27.md`](docs/stocks-validation-2026-10-27.md) — 같은 공식을 지수에 쓰면 큰 하락을 크게 줄이고(나스닥 MDD −36% vs −78%, 코스피 −45% vs −64%), 나스닥·코스피는 무작위 타이밍보다 나음. **개별 종목 고르기 효과는 없음**(신호 뒤 수익 = 아무 날). 2025~ 잠금 |
| 주식 오늘의 소식 | ✅ `python -m coinquant stocks-brief` — 시장 지표·주요 뉴스(해외·국내)·실적·경제지표 일정·지수/ETF 매수 신호(근거 등급)·추세 시작 종목(관심 목록) → 페이지 + 텔레그램. GitHub Actions `stocks-daily.yml` 평일 07:40 KST |
| 체크리스트 규칙 검증 | ✅ [`docs/rules-check-2026-10-28.md`](docs/rules-check-2026-10-28.md) — FOMC 앞 '절반 먼저'(11년에 2번 해당, 조금 손해), 4년 주기 달력 필터(표본 안에선 좋아 보이나 안 본 창에선 손해), 주기 최저 이탈 경고(이후 수익 약하지만 공식 매수를 막으면 손해) → 모두 매매 규칙에서 빼고 참고 정보로만. 위험 뉴스 보류는 검증 불가 |
| 매일 메시지 구성 | 맨 위 '✅ 오늘 할 일'(매수 / 보유만 / 갖고 있으면 매도, 다음 신호까지 거리, 오늘 일정·실적) → 시황 → 신호 → 참고 정보 → 뉴스. `ANTHROPIC_API_KEY`(선택)가 있으면 뉴스를 한국어 '오늘의 핵심'으로 AI 요약, 주식 메시지에 오선 라이브 최신 영상 |
| 리포트·내 판단·주문·예약 실행 | ✅ 동작 (공식이 임시라 추천도 임시) |

## 설치 (Windows)

1. Python 3.11 이상 설치 (설치 화면에서 "Add python.exe to PATH" 체크)
2. 이 폴더에서 `scripts\windows\setup.bat` 실행 → 가상환경·패키지·`.env` 견본 준비
3. 처음 데이터 받기: `.venv\Scripts\python.exe -m coinquant update` (처음엔 20~40분, 이후 매일 1~3분)
4. 리포트: `.venv\Scripts\python.exe -m coinquant report` → `reports\daily-날짜.html`
5. 매일 자동 실행: `powershell -ExecutionPolicy Bypass -File scripts\windows\register_task.ps1` (기본 09:10)

6. 휴대폰 알림(선택): 텔레그램 @BotFather 로 봇을 만들고 `.env` 에 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 입력 (방법은 `.env.example`) → `.venv\Scripts\python.exe -m coinquant alert --test`

7. PC를 꺼 둬도 매일 받으려면(선택): GitHub 저장소 Settings → Secrets and variables → Actions 에 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 등록. `.github/workflows/coin-quant-daily.yml` 이 기본 브랜치에 있어야 예약 실행됨 (Actions 탭에서 수동 실행도 가능)

macOS·리눅스는 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` 후 같은 명령.

## 명령

```
python -m coinquant update              데이터 갱신 + 가격 패널
python -m coinquant validate [--quick]  지표 검증 → docs/validation-날짜.md (최종 검증 구간 제외)
python -m coinquant trend-study         스윙 매매 검증 (기간별 추세 포착률, 추세 조합별 이후 수익)
python -m coinquant candidates          완성 공식 후보 비교 + 파라미터 민감도
python -m coinquant cycle               비트코인 4년 주기 검증
python -m coinquant short-study         숏(무기한 선물) 검증, 현물 롱 + 선물 숏 합산
python -m coinquant short-swing         단기 스윙(며칠~2주) 검증
python -m coinquant breakout            돌파 검증 (직전 고가)
python -m coinquant levels              매물대·수평 저항/지지선 검증
python -m coinquant levels-regime       매물대·선 이론 ETF 승인 전후 비교
python -m coinquant extended            확정 공식 2018년 이전까지 검증 + 확률 높을 때만 사기
python -m coinquant limit-entry         지정가 매수(안 닿으면 포기 / 매일 다시) 검증 + 중기 의미(부트스트랩)
python -m coinquant report [--no-news]  매일 리포트 (HTML/MD)
python -m coinquant daily               update + report + alert (예약 실행이 부르는 명령)
python -m coinquant alert [--test]      매수·매도 알림 (상태 바뀐 날만, --test 는 무조건 보냄)
python -m coinquant judge lock BTC "이유"          보유 고정 (공식이 안 팖)
python -m coinquant judge exclude XRP "이유"       제외 (공식이 안 삼, 갖고 있으면 정리)
python -m coinquant judge weight SOL 0.15 "이유"   비중 직접 지정 (평가금의 15%)
python -m coinquant judge release BTC "이유"       해제 → 공식대로
python -m coinquant judge list                     지금 유효한 판단
   (옵션 --until 2026-12-31 : 그날까지만)
python -m coinquant trade [--auto]      추천 주문 실행 (stage 2 에서만, 주문마다 y/N)
python -m coinquant final-check         확정한 공식으로 최종 검증 구간을 한 번 열기
python -m coinquant stocks-validate     주식(지수·종목)에서 공식 검증 (인터넷 필요, GitHub Actions 에서 돌림)
python -m coinquant stocks-brief [--no-send]  주식 오늘의 소식 페이지 + 텔레그램
python -m coinquant rules-check         코인 체크리스트 규칙 검증 (FOMC·4년 주기·주기 최저)
python -m coinquant check               보안 점검
```

## 검증 원칙 (코드로 강제하는 것)

- **미래 데이터 금지**: t일 종가(UTC 00:00 = KST 09:00, 업비트·바이낸스 일봉 마감 시각)까지로 판단 → t+1일 시가 체결.
  모든 신호는 "T일까지 잘라 계산한 값 = 전체로 계산한 T일 값" 테스트를 통과해야 합니다 (`tests/test_signals.py`).
- **비용**: 매매마다 수수료 0.05% + 슬리피지(코어 0.1%, 기타 0.3%). `config.toml [costs]`.
- **생존 편향 제거**: 바이낸스 상장폐지 페어 포함(787개 중 285개 폐지), 날짜별 시가총액 순위로 그날 대상만 사용.
  상장폐지는 마지막 가격·0원 두 방식 모두 계산. 심볼 재사용(옛 LUNA/새 LUNA)은 공백에서 끊어 다른 코인으로 처리.
- **최종 검증 구간 잠금**: `holdout_start` 이후는 `validate` 가 잘라내고, `final-check` 만 열 수 있으며 열 때마다 기록·경고.
- **워크포워드**: 학습 24개월로 고르고 다음 6개월에 적용, 6개월씩 밀기.
- **위약 비교**: BTC 그냥 보유, 무작위 같은 개수 코인(200회), 고변동성 코인, 같은 보유비율의 무작위 타이밍.
- **시대별 보고**: 2018 겨울, 2020 반감기, 2020~21 강세, 2022 LUNA·FTX, 2024 ETF·반감기, 그 이후.
- **여러 조합 시험 보정**: 시험 개수를 세고 Deflated Sharpe Ratio 로 "최고 결과가 운일 확률"을 같이 표시.

## 데이터 출처

| 출처 | 쓰는 곳 | 비고 |
|---|---|---|
| 바이낸스 공개 데이터 (data.binance.vision) | 과거 가격·거래량 (검증) | 상장폐지 페어 포함, 2017-08~ |
| 바이낸스 시세 API | 최근 며칠 보강 | 막혀 있으면 건너뜀 |
| 바이낸스 USDⓈ-M 무기한 선물 (공개 데이터) | 숏 검증 (일봉·펀딩비, 2020-01~) | 바이비트 공개 데이터는 이 환경에서 막혀 대신 씀 |
| Coin Metrics 공개 데이터 (GitHub) | 날짜별 시가총액 순위, BTC 도미넌스 | 추정 유통 시총 우선 |
| 업비트 API | 원화 가격, 상장 목록·유의종목, 주문 | 내 PC에서 |
| FRED 환율 (GitHub 미러) | 김치 프리미엄 | |
| RSS (SEC, 연준, CoinDesk, Cointelegraph, Decrypt, The Block, 블록미디어) + 업비트 공지 | 오늘의 이슈 | 링크 필수 |

검증은 업비트가 아니라 **바이낸스 달러 가격**으로 했습니다 (업비트는 상장폐지 코인의 과거 가격을 주지 않고,
이 프로그램을 만든 환경에서 업비트 접속이 막혀 있었음). 원화 수익률 = 달러 수익률 × 환율 변화 × 김치 프리미엄 변화라서 흐름은 비슷하지만 같지 않습니다.

## 보안

- 업비트 Open API 키: **'자산조회·주문조회·주문하기'만**, **'출금하기'는 절대 켜지 마세요.** 허용 IP는 이 컴퓨터만.
- 키는 `.env` 에만 (`.gitignore` 로 막혀 있음). 대화창·메신저·GitHub 에 붙여넣지 마세요. `python -m coinquant check` 로 점검.
- 주문 단계: `config.toml [trading]` — `stage = 1` 추천만(기본) → `stage = 2` 주문마다 y/N (`paper = true` 면 모의) →
  자동 주문은 `auto_order = true` + `--auto` 를 둘 다 직접 켰을 때만. 예약 실행은 리포트만 만들고 주문하지 않습니다.

## 폴더

| 위치 | 내용 |
|---|---|
| `coinquant/data/` | 수집기 (바이낸스, Coin Metrics, 업비트, 환율) |
| `coinquant/universe.py` | 가격 패널, 날짜별 대상 코인 (제외 목록, 상장폐지, 심볼 재사용) |
| `coinquant/indicators.py`, `backtest/signals.py` | 지표·신호 |
| `coinquant/backtest/engine.py` | 백테스트 엔진 |
| `coinquant/backtest/validate.py`, `research/` | 시대 구분, 워크포워드, 잠금, 검증 보고서 |
| `coinquant/strategy.py` | 공식 (검증과 매일 추천이 같은 코드) |
| `coinquant/overrides.py` | 내 판단 레이어 · 성과 비교 |
| `coinquant/app/` | 리포트, 뉴스, 주문, 보유 조회 |
| `data_cache/`, `user_data/`, `reports/` | 받은 데이터·내 기록·리포트 (git 제외) |
