# TradeCompass

무역 이슈가 국내 섹터·종목에 주는 영향을 정리하는 MVP와, HS코드·관세 계산 도구입니다.

| 폴더 | 내용 |
|---|---|
| `tradecompass-mvp/` | 대시보드 MVP (정적 페이지, 데이터는 `data.js`) |
| `hs-code-finder/` | HS코드 찾기 · 코드 좁히기 질문 · 관세/FTA 계산 |
| `shared/` | 페이지 공통 스크립트 (브라우저 에러 수집기, AI 호출 도우미) |
| `api/` | 서버 함수 (eyefeet `vercel` 런타임에서 실행) |

## 배포

| 위치 | 주소 | 배포 방식 |
|---|---|---|
| Eyefeet Cloud | https://tcmvp.eyefeet.com | 테넌트가 이 저장소의 `main`을 가져가 `npm run build`로 `dist/`를 만들고 배포해요 (런타임 `vercel`). 무료 플랜은 컨테이너 1개라 **정지 → 배포 → 시작** 순서로 배포해요 |
| GitHub Pages | https://sunghyunkim369-afk.github.io/claude/ | 기본 브랜치에 푸시하면 `.github/workflows/pages.yml`이 배포 (정적 파일만, `api/` 없음) |

## 실제 무역 뉴스 (매주 갱신)

대시보드의 Top10 이슈·섹터 노출도·리스크·뉴스는 연합뉴스·한국경제·매일경제·WTO RSS와 Google 뉴스 검색(신뢰 언론사 30여 곳만)으로 모은 **실제 기사**로 계산해요. 공식과 근거 논문은 [docs/news-algorithm.md](docs/news-algorithm.md).

- 수집: `.github/workflows/news-collect.yml` 이 6시간마다 `data/news/archive.json` 에 기사를 쌓아요.
- 발행: `.github/workflows/news-publish.yml` 이 매주 월요일 06:47(KST)에 `tradecompass-mvp/data.js`, `data/news/latest.json` 을 만들어 두 브랜치에 올리고 GitHub Pages 를 다시 배포해요.
- eyefeet: `/api/data` 가 `main` 의 `latest.json` 을 전달해서, 재배포 없이 새 주간 데이터가 보여요.
- 직접 실행: `node scripts/news/collect.js --backfill` → `node scripts/news/publish.js`
- (선택) GitHub → Settings → Secrets → Actions 에 `AI_API_KEY` 를 넣으면 Eyefeet AI 가 기사 분류·주간 요약을 보완해요.

## 직구·여행 통관 도우미

`tradecompass-mvp/tools.js` — 실시간 연동 없이 동작하는 일반인용 도구 세 가지 (기준값은 파일 위쪽 `RULES`·`CATS`·`ITEMS` 에 모여 있어요)

- **여행자 면세 계산기** (`#/travel`): 기본 $800, 술 합계 2L·$400(병 수 제한 없음), 향수 100ml, 담배 200개비, 품목별 간이세율, 자진신고 30% 감면(최대 20만 원) vs 미신고 가산세 40%/60% 비교
- **직구 반입 체커** (`#/check`): 자주 사는 품목의 가능·조건부·불가 + 잘 모르는 함정, 합산과세·되팔기·반품 관세환급·개인통관고유부호 도용 확인
- **통관 진행 조회** (`#/track`): 번호를 복사해 관세청 유니패스로 연결, 진행 단계별 의미와 할 일
- AI(eyefeet에서만): 쇼핑 목록 글 → 계산기 자동 입력(`travel`), 제품 설명 → 기준표 항목 찾기(`customs`), 문자·상태 문구 해석 + 사칭 의심(`track`). AI는 입력 정리·해석만 하고, 세금 계산과 반입 판정은 기준표로 해요. 문자 속 링크가 관세청·정부(go.kr) 주소인지는 AI 없이도 바로 검사해요.

## 회원가입 · 로그인

- 화면: `tradecompass-mvp/auth.js` — 머리글 로그인·회원가입 버튼, 창, 로그인 후 이메일·로그아웃 버튼. 로그인하면 이 브라우저에 저장해 둔 관심 섹터를 계정으로 옮기고, 이후엔 계정에 저장돼 다른 브라우저에서도 같아요.
- 서버: `api/auth.js` — `signup` · `login` · `logout` · `me` · `watch`.
  - 저장소: `DATABASE_URL` 이 있으면 PostgreSQL(`tc_users` 테이블 자동 생성), 없으면 서버 파일(`AUTH_FILE`, 기본은 임시 폴더 — **재배포하면 지워질 수 있어요**).
  - 보안: 비밀번호는 scrypt 해시로만 저장, HMAC 서명 쿠키(HttpOnly·Secure·SameSite=Lax), "로그인 상태 유지" 체크 시 30일·아니면 브라우저 세션, 다른 사이트 요청 차단, IP당 분당 10회 제한, 로그인 실패 메시지 통일.
- 환경변수 없이도 동작해요: `SESSION_SECRET` 이 없으면 서버가 처음 실행될 때 무작위 비밀값을 만들어 회원 저장소 옆에 보관해요. 오래 쓰려면 `DATABASE_URL`(권장)을 넣으세요. 처음 연결하면 그동안 파일에 저장된 회원(비밀번호 해시·관심 섹터·로그인 상태)을 DB로 자동으로 옮기고, 파일은 `users.json.migrated-…` 로 백업해 둬요. 잠시 끄려면 `AUTH_DISABLED=1`.
- 테스트: `npm test` (`tests/auth.test.js` — 가입→로그아웃→로그인, 30일 유지 쿠키, 다른 브라우저에서 같은 관심 섹터, 평문 비밀번호 없음, 입력 검증·중복·잘못된 로그인, 위조 쿠키·외부 요청·연속 시도). `TEST_DATABASE_URL` 을 주면 PostgreSQL 로도 같은 시나리오를 돌려요(GitHub Actions `Tests` 가 자동으로 함).

## 24시간 속보

- 6시간마다 수집 직후 `scripts/news/breaking.js` 가 최근 24시간 신뢰 언론사 기사를 최신순(최대 15건)으로 `data/news/breaking.json`, `tradecompass-mvp/breaking.js` 에 써요. 두 브랜치에 올리고 GitHub Pages 를 다시 배포해요. eyefeet 은 `/api/breaking` 으로 재배포 없이 최신본을 받아요.
- 화면: 홈(요약 바로 아래 6건)과 무역 뉴스 맨 위(15건). 브라우저에서도 지금 시각 기준 24시간이 지난 기사는 숨겨요. 조치 방향 라벨은 기존 규칙(강화·완화·동향·중립) 그대로.
- 테스트: `tests/breaking.test.js` (23시간 59분 포함, 24시간 1분 제외, 미래 시각·비신뢰 출처·홍보 기사 제외, 최신순).

## AI 답변 안전장치

`api/ai.js` 의 모든 작업(브리핑 질문, 섹터 분석, HS 코드, 면세 계산기, 반입 체커, 통관 문구 해석)에 공통 적용:

1. 시스템 프롬프트 규칙: 종목·자산 매수·매도 권유, 목표가·수익 보장 금지, 모르면 모른다고 하고 공식 출처 안내.
2. 응답 후처리 필터: 매수·매도·목표가·수익 보장·종목 추천 같은 문장은 지우고, 다 지워지면 중립 문구로 바꿔요. 투자 판단을 묻는 질문이거나 필터가 작동하면 답변 위에 안내 문구(`notice`)를 붙여요.
3. 로그: 요청마다 `[ai-log]`(작업·질문·응답 요약·필터 건수, 이메일·전화번호·긴 숫자는 가림, IP 없음), 필터가 작동하면 `[ai-filter]`. Eyefeet 로그 화면에서 검색해 점검해요.
- 테스트: `tests/ai-safety.test.js` (삼성전자 사도 돼? · SK하이닉스 팔까? · ETF 추천 · 정상 답변 · 통관/면세 응답 · 로그 가림). 배포 후 실제 AI 점검: Actions → **AI safety check** 실행(또는 `node scripts/ai-safety-check.js`).

## 상태 확인 · 오류 집계

- `/api/health` (`/health` 로도 열림): 가동 여부, 환경변수 설정 여부, 회원 저장소 종류, **최근 24시간 서버 오류 건수(출처별)**. 내용은 넣지 않아요.
- `/api/admin-errors`: 최근 오류 50건 목록. `ADMIN_TOKEN` 환경변수를 넣어야 열리고, `Authorization: Bearer <ADMIN_TOKEN>` 헤더가 필요해요. 메시지 속 개인정보는 가려져 있어요.
  예) `curl -H "Authorization: Bearer $ADMIN_TOKEN" https://tcmvp.eyefeet.com/api/admin-errors`
- 집계는 서버 메모리 기준이라 재시작하면 0부터 다시 세요. 모든 오류는 `[server-error]` 로그에도 남아요.

## AI 분석 (Eyefeet AI · 깃솔트 로컬 AI)

브라우저는 `shared/ai-client.js`로 `POST /api/ai`를 부르고, `api/ai.js`가 환경변수의 AI 주소·키로 Eyefeet AI(qwen3-30b-a3b)를 호출해요.
eyefeet 주소에서만 동작하고, GitHub Pages·아티팩트에서는 버튼이 꺼지며 eyefeet 사이트로 안내해요.

| 작업 | 쓰는 곳 | 내용 |
|---|---|---|
| `hs` | HS코드 페이지 "AI에게 설명해서 찾기" | 품목 설명 → HS 6자리 후보 3개 + 확인 질문. 류별 분류 기준표를 함께 보내 오답을 줄이고, 화면에서 목록 품목·규칙과 대조해 다르면 경고해요 |
| `sector` | 대시보드 섹터 상세 "AI 영향 분석" | 섹터 노출도·관련 이슈 → 영향받는 기업 유형, 지켜볼 점, 지금 확인할 일 |
| `ask` | 대시보드 "오늘 브리핑에 대해 AI에게 묻기" | 오늘 데이터만 근거로 질문에 답변 (자료에 없으면 없다고 답함) |

- 응답은 20~40초 걸려요. 화면에 경과 시간을 보여주고 1분이 넘으면 멈춰요.
- IP당 분당 6회로 제한하고, 질문 400자·참고 자료 6,000자까지만 받아요.
- AI 응답은 정해진 JSON으로 검사한 뒤에만 화면에 보여줘요. 실패는 서버 로그에 `[ai-error]`로 남아요.
- 환경변수: `AI_BASE_URL`(`https://www.eyefeetai.com/api/chat/completions`), `AI_API_KEY`(비밀), `AI_MODEL`(선택), `AI_API_STYLE`(`openai`).

## 에러 로깅

1. 두 페이지는 맨 먼저 `shared/error-logger.js`를 불러와요. 처리되지 않은 에러, Promise 거부, 스크립트·스타일 로드 실패를 잡아요.
2. eyefeet 주소에서만 `POST /api/log`로 보내요. GitHub Pages·아티팩트·로컬 파일에서는 보내지 않아요.
   - 한 페이지에서 최대 10건까지 보내고, 같은 에러는 한 번만 보내요.
3. `api/log.js`가 서버 로그에 `[client-error] {...}` 한 줄 JSON으로 남겨요.
   - IP당 분당 30건을 넘으면 429를 돌려줘요.
   - 필드 길이를 잘라서 남기고, 사용자 입력 값은 기록하지 않아요.
4. **확인 방법:** Eyefeet Cloud 테넌트의 로그 화면에서 `[client-error]`로 검색해요.
5. **직접 기록하기:** 코드에서 `window.tcLog("메시지", { 추가정보 })`를 호출해요.

## 모니터링

- **`GET /api/health`:** `{ ok, time, env }`를 돌려줘요. `env`는 필요한 환경변수가 **설정됐는지 여부(true/false)만** 보여주고, 값은 절대 내보내지 않아요.
- **매시간 가동 점검:** `.github/workflows/uptime.yml`이 매시간(17분) eyefeet 페이지·`/api/health`와 GitHub Pages를 확인해요.
  - 실패하면 `uptime` 라벨이 붙은 "🔴 사이트 접속 장애" 이슈를 열어요 (저장소 알림 메일로 전달).
  - 정상으로 돌아오면 그 이슈에 기록을 남기고 닫아요.
  - Actions 탭에서 **Run workflow**로 바로 실행해 볼 수 있어요.

## 환경변수와 비밀 값

- **목록:** 필요한 변수 목록은 `.env.example`에 있어요. **실제 값은 저장소에 절대 올리지 않아요.**
  - `.gitignore`가 `.env`, `.env.*`를 막아요.
- **넣는 위치**

  | 누가 쓰나 | 넣는 곳 |
  |---|---|
  | 서버 함수(`api/`) | Eyefeet Cloud 테넌트 설정의 환경변수 |
  | GitHub Actions (매일 데이터 생성 등) | 저장소 Settings → Secrets and variables → Actions |
  | 로컬 테스트 | `.env.example`을 `.env`로 복사해 채우기 (커밋 안 됨) |

- **브라우저 노출 금지:** API 키는 `api/` 서버 함수에서만 `process.env`로 읽어요. 브라우저 코드(`tradecompass-mvp/`, `hs-code-finder/`, `shared/`)에는 키를 넣지 않아요.
- **유출 시:** 키가 채팅·이슈·커밋 등에 노출되면 즉시 발급처에서 폐기하고 새 키로 바꿔요.

## 보안 헤더

`vercel.json`이 eyefeet(vercel 런타임)에서 다음 헤더를 붙여요.
- `Content-Security-Policy`: 스크립트·연결은 같은 출처만, 글꼴은 Google Fonts만 허용
- `X-Frame-Options: SAMEORIGIN`: 다른 사이트가 이 사이트를 프레임에 넣지 못하게 막아요
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy`
- `Permissions-Policy`: 카메라·마이크·위치 사용 차단
- `api/` 응답은 캐시하지 않아요 (`no-store`)

GitHub Pages는 헤더를 설정할 수 없어서 이 헤더가 적용되지 않아요.

## GitSalt 제출 (과제)

교수님 안내에 따라 코드를 GitSalt(https://gitsalt.com/ksunghyun0125/tradecompass)에도 올려요. GitHub `main` 에 변경이 생기면 `.github/workflows/gitsalt-mirror.yml` 이 GitSalt 로 그대로 복사해요(뉴스 수집·발행 뒤에도).

처음 한 번 설정:
1. GitSalt 로그인 → 오른쪽 위 프로필 → **설정 → 애플리케이션 → 새 토큰 생성**, 권한에서 **repository: 읽기 및 쓰기** 선택
2. GitHub 저장소 **Settings → Secrets and variables → Actions → New repository secret**, 이름 `GITSALT_TOKEN`, 값에 토큰 붙여넣기 (토큰은 채팅·코드에 쓰지 않아요)
3. GitHub **Actions → GitSalt mirror → Run workflow**. 처음 실행 때 원래 저장소 `calc` 를 `tradecompass` 로 이름을 바꾸고 내용을 덮어써요.

원본은 GitHub 예요. GitSalt 는 제출·배포용 사본이라:
- 코드 수정은 GitHub 에서 해요. GitSalt 에서 직접 커밋하면 다음 복사가 실패해서 알려 줘요(덮어쓰지 않아요).
- 모든 자동 작업(`.github/workflows`)은 `github.server_url` 이 GitHub 일 때만 돌아요. GitSalt 의 Actions 가 켜져 있어도 뉴스 수집·발행이 두 곳에서 따로 돌며 기록이 갈라지는 일이 없어요.
- GitSalt 에는 Actions 실행 서버(러너)가 없어서 GitSalt 에서 직접 테스트를 돌리지 않아요. 대신 GitHub 에서 돌린 테스트 결과를 GitSalt 커밋 옆에 ✅/❌("tests (GitHub Actions)")로 붙이고, 누르면 GitHub 실행 기록으로 가요. GitSalt 저장소 설정의 Actions 는 꺼 두세요(켜 두면 실행되지 않는 작업이 '대기 중'으로 쌓여요).
- 사이트 실행에 필요한 것은 모두 저장소 안에 있어서, eyefeet 가 GitHub 와 GitSalt 중 어디서 코드를 가져가도 똑같이 동작해요. (주간 순위·속보 최신본은 `/api/data`·`/api/breaking` 이 GitHub 에서 받아요. 실패하면 페이지에 들어 있는 데이터를 써요.)
