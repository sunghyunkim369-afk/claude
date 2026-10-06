# 뉴스·사진 출처 확인 기록

팀 규칙: 새 수집원은 robots.txt 와 이용약관을 먼저 확인하고 여기에 기록한 뒤에만 써요. 같은 곳에 보내는 요청 사이는 2초 이상, 재시도 간격은 점점 늘려요(`scripts/news/lib.js` fetchText, `scripts/news/collect.js`).
robots.txt 는 `.github/workflows/robots-probe.yml` 로 다시 확인할 수 있어요 (`User-agent: *` 구간 기준).

## robots.txt 확인 (2026-10-06)

| 출처 | 쓰는 경로 | robots.txt (`User-agent: *`) | 판단 |
|---|---|---|---|
| Google 뉴스 | `news.google.com/rss/search` | `Disallow: /` 이고 `/rss` 는 Allow 목록에 없음 | **금지 경로.** 통합 때 빠질 예정이라 새로 기대는 기능을 만들지 않음. 10-06 추가했던 영문 세계 무역 검색(en-global)은 뺐음. 기존 주제 검색은 통합 전까지 유지 |
| 연합뉴스 | `www.yna.co.kr/rss/*.xml` | `*` 에 대한 제한 없음 | 사용 (공식 RSS) |
| 한국경제 | `www.hankyung.com/feed/*` | `*` 에 대한 제한 없음 | 사용 중. **이용약관(자동 수집 조항)은 수민 확인 중**, 결과 전까지 새로 의존하지 않음 |
| 매일경제 | `www.mk.co.kr/rss/...` | `*` 에 대한 제한 없음 | 사용 (공식 RSS). 이용약관 확인 필요 |
| WTO | `www.wto.org/library/rss/latest_news_e.xml` | `*` 에 대한 제한 없음 | 사용 |
| USTR | `ustr.gov/rss.xml` | `/core/`·`/admin/`·`/search/` 등만 금지, `/rss.xml` 허용 | 사용 |
| 백악관 | `www.whitehouse.gov/presidential-actions/feed/` | 검색(`?s=`)만 금지 | 사용 |
| 미 관보 | `www.federalregister.gov/api/v1/documents.rss` | `*` 에 대한 제한 없음 | 사용 (공식 API·RSS) |
| EU 집행위원회 | `ec.europa.eu/commission/presscorner/api/rss` | 확인한 금지 목록(앞 40줄)에 해당 경로 없음 | 사용. 목록 전체는 다시 확인 필요 |
| Nikkei Asia | `asia.nikkei.com/rss/feed/nar` | `User-Agent: *` 아래 금지 없음 | 사용 |
| SCMP | `www.scmp.com/rss/318421/feed` | 로그인·정적 파일 등만 금지(앞 40줄 기준) | 사용. 목록 전체는 다시 확인 필요 |
| gCaptain | `gcaptain.com/feed/` | `*` 에 대한 제한 없음 | 사용 |
| The Loadstar | `theloadstar.com/feed/` | `/page/`·`/tag/`·`/category/`·`/wp-admin/` 만 금지 | 사용 |
| Wikimedia Commons | `commons.wikimedia.org/w/api.php` (사진 후보 검색) | `/w/`·`/api/` 금지 | **크롤러 기준 금지 경로.** Wikimedia 는 API 사용을 별도 정책(API Etiquette: User-Agent 표기·속도 제한)으로 허용하지만, 팀 규칙상 금지 경로라 **추가 검색은 하지 않음**. 이미 받은 47장은 저장소에 WebP 로 들어 있음 (`tradecompass-mvp/photos.js` 에 사진별 라이선스·작가·원본 주소) |

이용약관은 아직 출처별로 확인하지 못했어요. 새로 의존하기 전에 확인해 이 표에 적어요.
