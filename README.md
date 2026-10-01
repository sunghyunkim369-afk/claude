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
