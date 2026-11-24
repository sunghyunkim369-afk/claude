# AI 무역팀 'Claude만' 모드 실측 — 새 세션 인계 메모

이전 세션에서 AI 무역팀(무역나침반, GitSalt `leesumin1517/tradecompass`)을 만들었고, 'Claude만' 모드를 실제 Claude API로 돌려 보려다
그 세션에 `ANTHROPIC_API_KEY`가 없어서 멈췄다. 사용자가 환경변수에 키를 넣고 새 세션을 열면 아래 순서로 이어 간다.
**모든 답과 보고는 한국어로.** 키·비밀값은 출력·커밋하지 않는다.

## 순서
1. 키 확인(값은 출력하지 말 것): `[ -n "$ANTHROPIC_API_KEY" ] && echo set || echo missing`
2. 코드 받기: `git clone https://gitsalt.com/leesumin1517/tradecompass.git /home/user/tradecompass && cd /home/user/tradecompass && git checkout feat/sunghyun-ai-team-2`
   (gitsalt.com 인증은 환경의 네트워크 비밀값으로 프록시가 넣어 준다)
3. 실행(약 10~12분, 백그라운드 권장):
   `cd /home/user/claude/tools/ai-team-demo && TC_ROOT=/home/user/tradecompass python3 run_claude_demo.py claude_demo.json`
   - 모델은 `TEAM_CLAUDE_MODEL`(기본 `claude-opus-5-5,claude-sonnet-5-5`: 부장·비서는 첫 모델, 토론 역할은 번갈아)
   - EyefeetAI 키는 이 실행에 필요 없다(Claude만 모드)
4. 결과: `claude_demo.json`과 요약 `claude_demo.md`. 요약을 사용자에게 보여 주고, 아래를 평가해 알려 줄 것
   - 의견 충돌이 실제로 났는지(입장 표·지표), 근거 없는 동조가 되돌려졌는지
   - 사실 정확도: UL 62368-1·Recognized Component, FCC Part 15, 미국 원산지 표시(실질적 변형), Section 301·관세에 대한 말이
     맞는지 웹 검색으로 확인(틀린 말은 무엇이 틀렸는지)
   - 바이어 메일이 보류·미합의 조건을 확정처럼 쓰지 않았는지, 내부 정보·한글이 섞이지 않았는지
   - 유출 점검(`leak`)이 비었는지
   - 이전 EyefeetAI 실측(조미김 일본·대만, BLE 센서 독일)과 비교한 느낌

## 사례(가상)
USB-C 65W GaN 충전기 모듈 5,000개를 미국 소형 가전 브랜드에 OEM 첫 공급. 바이어는 모듈 단위 UL 인증·FCC 시험성적서·원산지 표시를 요구,
변압기·PCB는 중국산, 결제는 30% 선금+70% 선적 후 30일, 2027-03-15까지 LA 도착. 2차 회의는 "UL 인정 부품 10~12주·1,800만 원" 상황.

## 지켜 줄 것(사용자 규칙)
- 로그인·회원 코드(`auth.js`·`auth_api.py`)는 건드리지 않는다. main/dev에 직접 푸시하지 않는다.
- 개발 결과물에 특정 기업 이름을 적지 않는다("중소기업 실무자들의 입장에서 고려하였다" 식으로).
- 화면 금지어: 추천·우수·인기·평점·순위·랭킹·베스트. 투자 권유 금지, 추정치는 '추정' 표시.
- 'EyefeetAI'라고 부른다('교수님 AI'라고 쓰지 않는다).
