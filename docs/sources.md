# 뉴스·사진 출처 확인 기록

팀 규칙: 새 수집원은 robots.txt 와 이용약관을 먼저 확인하고 여기에 기록한 뒤에만 써요. 같은 곳에 보내는 요청 사이는 2초 이상, 재시도 간격은 점점 늘려요(`scripts/news/lib.js` fetchText, `scripts/news/collect.js`).
robots.txt 는 `.github/workflows/robots-probe.yml` 로 다시 확인할 수 있어요 (`User-agent: *` 구간 기준).

## robots.txt 확인 (2026-10-06, 줄바꿈(CRLF) 처리 고친 뒤 다시 확인)

처음 확인 때 Windows 줄바꿈(CRLF) 파일에서 `User-agent: *` 구간을 못 읽어 일부 금지를 놓쳤어요 (WTO 등). 아래는 고친 뒤 결과예요.

| 출처 | 쓰는 경로 | robots.txt (`User-agent: *`) | 판단 |
|---|---|---|---|
| Google 뉴스 | `news.google.com/rss/search` | `Disallow: /` 이고 `/rss` 는 Allow 목록에 없음 | **금지 경로 → 2026-10-06 삭제** (주제 검색 12개·정부/영문 검색·과거 채우기 모두). 예전에 모은 기사는 archive.json 에 남아 있다가 기간이 지나면 빠져요 |
| 연합뉴스 | `www.yna.co.kr/rss/*.xml` | `Allow: /`, 금지는 `/search/`·`/view/A**`(외국어판) 등 개별 경로뿐, `/rss/` 허용 | 사용 (공식 RSS) |
| 한국경제 | `www.hankyung.com/feed/*` | `/api/`·`/print/`·`/tag/` 등만 금지, `/feed/` 허용 | **꺼 둠** (`enabled:false`). 이용약관(자동 수집 조항) 확인 전까지 쓰지 않음 |
| 매일경제 | `www.mk.co.kr/rss/...` | `Allow: /` | 사용 (공식 RSS). 이용약관 확인 필요 |
| WTO | `www.wto.org/library/rss/latest_news_e.xml` | **`Disallow: /` (전체 금지)** | **2026-10-06 삭제** |
| USTR | `ustr.gov/rss.xml` | `/core/`·`/admin/`·`/search/` 등만 금지, `/rss.xml` 허용 | 사용 |
| 백악관 | `www.whitehouse.gov/presidential-actions/feed/` | 검색(`?s=`)만 금지 | 사용 |
| 미 관보 | `www.federalregister.gov/api/v1/documents.rss` | `User-agent: *` 구간 없음 (제한 없음) | 사용 (공식 API·RSS) |
| EU 집행위원회 | `ec.europa.eu/commission/presscorner/api/rss` | 금지 목록 앞 60줄에 `/commission/` 없음 (archives·cgi-bin·옛 부서 경로 등) | 사용. 목록 전체는 다시 확인 필요 |
| Nikkei Asia | `asia.nikkei.com/rss/feed/nar` | `User-Agent: *` 아래 금지 없음 | 사용 |
| SCMP | `www.scmp.com/rss/318421/feed` | 추적 파라미터·로그인·`/static/`·`/ajax` 등만 금지, `/rss/` 없음 (앞 60줄 기준) | 사용. 목록 전체는 다시 확인 필요 |
| gCaptain | `gcaptain.com/feed/` | `/wp-admin/` 과 물음표(`?`) 붙은 주소만 금지, `/feed/` 허용 | 사용 (물음표 없는 주소로만 요청) |
| The Loadstar | `theloadstar.com/feed/` | `/page/`·`/tag/`·`/category/`·`/wp-admin/` 만 금지 | 사용 |
| Wikimedia Commons | `commons.wikimedia.org/w/api.php` (사진 후보 검색) | `/w/`·`/api/` 금지 | **크롤러 기준 금지 경로.** Wikimedia 는 API 사용을 별도 정책(API Etiquette: User-Agent 표기·속도 제한)으로 허용하지만, 팀 규칙상 금지 경로라 **추가 검색은 하지 않음**. 이미 받은 47장은 저장소에 WebP 로 들어 있음 (`tradecompass-mvp/photos.js` 에 사진별 라이선스·작가·원본 주소) |

## 정책브리핑(korea.kr) 직접 RSS — 후보 조사 중

- robots.txt: `User-Agent: *` 에 `Allow: /` (전체 허용)
- 짐작한 주소 `/rss/policy.xml`·`/rss/pressrelease.xml`·`/rss/dept_motie.xml` 은 404, `/rss/dept_kcs.xml`·`/etc/rss.do` 는 15초 안에 응답 없음 → **아직 쓸 수 있는 RSS 주소를 찾지 못함.** RSS 안내 페이지에서 실제 주소를 찾는 점검을 다시 돌리는 중
- 이용약관·저작권: 정책브리핑 콘텐츠는 대부분 공공누리 표시가 붙어 있지만 기사마다 유형이 달라요. 제목·링크만 쓰더라도 쓰기 전에 약관을 확인해 여기에 적어요
- 아직 `config.js` 에 넣지 않았어요

이용약관은 아직 출처별로 확인하지 못했어요. 새로 의존하기 전에 확인해 이 표에 적어요.
