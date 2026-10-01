// 뉴스 파이프라인 설정: 출처, 이슈 분류표, 섹터, 점수 가중치
// 바꿀 일이 생기면 이 파일만 고치면 돼요. (자세한 근거: docs/news-algorithm.md)

// ── 1. 출처 ─────────────────────────────────────────────────────────
// tier: 신뢰도 가중치 (정부·국제기구 1.0 > 통신사 0.9 > 주요 경제지·방송 0.8)
// kind: "rss" 는 피드 전체를 받아 무역 관련 기사만 남기고, "gnews" 는 Google 뉴스 검색 결과에서
//       아래 OUTLETS 화이트리스트에 있는 언론사 기사만 남겨요.
const FEEDS = [
  { id: "yna-economy", name: "연합뉴스", tier: 0.9, url: "https://www.yna.co.kr/rss/economy.xml" },
  { id: "yna-industry", name: "연합뉴스", tier: 0.9, url: "https://www.yna.co.kr/rss/industry.xml" },
  { id: "yna-international", name: "연합뉴스", tier: 0.9, url: "https://www.yna.co.kr/rss/international.xml" },
  { id: "korea-motie", name: "산업통상자원부", tier: 1.0, url: "https://www.korea.kr/rss/dept_motie.xml", official: true },
  { id: "korea-kcs", name: "관세청", tier: 1.0, url: "https://www.korea.kr/rss/dept_kcs.xml", official: true },
  { id: "korea-moef", name: "기획재정부", tier: 1.0, url: "https://www.korea.kr/rss/dept_moef.xml", official: true },
  { id: "korea-mof", name: "해양수산부", tier: 1.0, url: "https://www.korea.kr/rss/dept_mof.xml", official: true },
  { id: "hankyung-economy", name: "한국경제", tier: 0.8, url: "https://www.hankyung.com/feed/economy" },
  { id: "hankyung-intl", name: "한국경제", tier: 0.8, url: "https://www.hankyung.com/feed/international" },
  { id: "mk-economy", name: "매일경제", tier: 0.8, url: "https://www.mk.co.kr/rss/30100041/" },
  { id: "wto-news", name: "WTO", tier: 1.0, url: "https://www.wto.org/library/rss/latest_news_e.xml", official: true, lang: "en" },
];

// Google 뉴스 검색에서 받아들이는 언론사 (이름 일부가 맞으면 통과)
const OUTLETS = [
  { match: ["연합뉴스", "Yonhap"], tier: 0.9 }, { match: ["뉴시스"], tier: 0.9 }, { match: ["뉴스1"], tier: 0.9 },
  { match: ["Reuters", "로이터"], tier: 0.9 }, { match: ["Bloomberg", "블룸버그"], tier: 0.9 }, { match: ["Associated Press", "AP News"], tier: 0.9 },
  { match: ["한국경제", "한경"], tier: 0.8 }, { match: ["매일경제", "매경"], tier: 0.8 }, { match: ["서울경제"], tier: 0.8 },
  { match: ["머니투데이"], tier: 0.8 }, { match: ["이데일리"], tier: 0.8 }, { match: ["아시아경제"], tier: 0.8 },
  { match: ["헤럴드경제"], tier: 0.8 }, { match: ["파이낸셜뉴스"], tier: 0.8 }, { match: ["조선비즈"], tier: 0.8 },
  { match: ["전자신문"], tier: 0.8 }, { match: ["디지털타임스"], tier: 0.8 }, { match: ["연합인포맥스"], tier: 0.8 },
  { match: ["중앙일보"], tier: 0.8 }, { match: ["조선일보"], tier: 0.8 }, { match: ["동아일보"], tier: 0.8 },
  { match: ["한겨레"], tier: 0.8 }, { match: ["경향신문"], tier: 0.8 }, { match: ["KBS"], tier: 0.8 },
  { match: ["MBC"], tier: 0.8 }, { match: ["SBS"], tier: 0.8 }, { match: ["YTN"], tier: 0.8 },
  { match: ["Financial Times"], tier: 0.8 }, { match: ["Wall Street Journal", "WSJ"], tier: 0.8 },
  { match: ["Nikkei"], tier: 0.8 }, { match: ["CNBC"], tier: 0.8 },
  { match: ["산업통상자원부", "관세청", "기획재정부", "정책브리핑"], tier: 1.0 },
];

// ── 2. 이슈 분류표 ───────────────────────────────────────────────────
// 무역 이슈를 고정된 주제로 나눠요. 같은 주제 기사들이 하나의 "이슈"가 돼요.
// q: Google 뉴스 검색어 (Korean outlets) · kw: 기사 제목·요약에서 찾는 단어 · risk: 공급망 리스크로도 보여줄지
const TOPICS = [
  { id: "tariff", label: "관세", q: "관세 OR 상호관세 OR 보복관세", kw: ["관세", "상호관세", "보복관세", "tariff", "duties"] },
  { id: "export_control", label: "수출통제", q: "수출통제 OR 수출 규제 OR 엔티티리스트", kw: ["수출통제", "수출 통제", "수출규제", "수출 규제", "수출제한", "엔티티 리스트", "엔티티리스트", "export control", "entity list"], risk: true },
  { id: "trade_remedy", label: "반덤핑·무역구제", q: "반덤핑 OR 상계관세 OR 세이프가드", kw: ["반덤핑", "상계관세", "세이프가드", "무역구제", "anti-dumping", "countervailing", "safeguard"] },
  { id: "agreement", label: "FTA·통상협정", q: "FTA OR 통상협정 OR 통상장관", kw: ["FTA", "자유무역협정", "통상협정", "CEPA", "RCEP", "CPTPP", "IPEF", "통상장관", "통상교섭", "trade agreement", "trade deal"] },
  { id: "sanctions", label: "경제제재", q: "경제제재 OR 대러 제재 OR 이란 제재", kw: ["제재", "sanction"], risk: true },
  { id: "shipping", label: "해운·물류", q: "해상운임 OR 컨테이너 운임 OR 홍해 해운", kw: ["해운", "운임", "컨테이너", "홍해", "수에즈", "파나마 운하", "물류대란", "항만", "freight", "shipping"], risk: true },
  { id: "supply_chain", label: "공급망·핵심광물", q: "공급망 OR 핵심광물 OR 희토류", kw: ["공급망", "핵심광물", "희토류", "요소수", "supply chain", "critical mineral", "rare earth"], risk: true },
  { id: "fx", label: "환율", q: "원달러 환율 수출", kw: ["환율", "원·달러", "원/달러", "원달러", "exchange rate"] },
  { id: "energy", label: "유가·원자재", q: "국제유가 OR 원자재 가격 수입", kw: ["유가", "원유", "LNG", "원자재", "oil price", "crude"], risk: true },
  { id: "subsidy", label: "보조금·산업정책", q: "IRA 보조금 OR 반도체법 OR 칩스법", kw: ["IRA", "인플레이션감축법", "칩스법", "반도체법", "보조금", "CHIPS Act", "subsid"] },
  { id: "customs", label: "통관·원산지", q: "관세청 통관 OR 원산지 단속", kw: ["통관", "관세청", "원산지", "검역", "밀수", "customs"] },
  { id: "export_trend", label: "수출입 동향", q: "수출 증가 OR 수출 감소 OR 무역수지", kw: ["수출액", "수입액", "무역수지", "수출 증가", "수출 감소", "수출이", "수출입동향", "exports rose", "exports fell", "trade balance"] },
];

// 무역 관련 기사인지 거르는 기본 단어 (TOPICS 단어에 더해)
const TRADE_WORDS = ["수출", "수입", "무역", "통상", "관세", "export", "import", "trade"];

// ── 3. 섹터 ──────────────────────────────────────────────────────────
// stocks: 섹터 대표 종목 (시세가 아니라 "어느 기업들이 영향권인지" 보여주는 용도)
const SECTORS = [
  { id: "semi", name: "반도체", kw: ["반도체", "메모리", "HBM", "D램", "낸드", "파운드리", "칩", "semiconductor", "chip"],
    stocks: [{ name: "삼성전자", code: "005930" }, { name: "SK하이닉스", code: "000660" }, { name: "한미반도체", code: "042700" }] },
  { id: "auto", name: "자동차", kw: ["자동차", "완성차", "전기차", "하이브리드", "자동차부품", "automobile", "vehicle", " car"],
    stocks: [{ name: "현대차", code: "005380" }, { name: "기아", code: "000270" }, { name: "HL만도", code: "204320" }] },
  { id: "battery", name: "2차전지", kw: ["배터리", "2차전지", "이차전지", "양극재", "음극재", "리튬", "battery", "lithium"],
    stocks: [{ name: "LG에너지솔루션", code: "373220" }, { name: "삼성SDI", code: "006400" }, { name: "포스코퓨처엠", code: "003670" }] },
  { id: "steel", name: "철강·금속", kw: ["철강", "강판", "알루미늄", "구리", "아연", "희토류", "핵심광물", "steel", "aluminum", "copper"],
    stocks: [{ name: "POSCO홀딩스", code: "005490" }, { name: "현대제철", code: "004020" }, { name: "고려아연", code: "010130" }] },
  { id: "chem", name: "석유화학·에너지", kw: ["석유화학", "정유", "유가", "원유", "LNG", "화학", "에너지", "oil", "petrochemical", "energy"],
    stocks: [{ name: "SK이노베이션", code: "096770" }, { name: "LG화학", code: "051910" }, { name: "S-Oil", code: "010950" }] },
  { id: "ship", name: "조선·해운물류", kw: ["조선", "선박", "해운", "운임", "컨테이너", "항만", "물류", "홍해", "shipbuilding", "shipping", "freight"],
    stocks: [{ name: "HMM", code: "011200" }, { name: "HD한국조선해양", code: "009540" }, { name: "팬오션", code: "028670" }] },
  { id: "machinery", name: "기계·전자", kw: ["기계", "장비", "가전", "디스플레이", "전자부품", "스마트폰", "machinery", "electronics", "display"],
    stocks: [{ name: "LG전자", code: "066570" }, { name: "두산에너빌리티", code: "034020" }, { name: "삼성전기", code: "009150" }] },
  { id: "consumer", name: "소비재·농식품", kw: ["농산물", "식품", "화장품", "K-푸드", "라면", "쌀", "소고기", "농축산", "food", "cosmetic", "agricultur"],
    stocks: [{ name: "CJ제일제당", code: "097950" }, { name: "농심", code: "004370" }, { name: "아모레퍼시픽", code: "090430" }] },
];

// ── 4. 조치 방향 (강화/완화) 단어 ────────────────────────────────────
const TIGHTEN = ["부과", "인상", "강화", "제재", "금지", "제한", "통제", "발동", "조사 착수", "보복", "확대 적용", "impose", "raise", "hike", "tighten", "ban", "restrict", "curb"];
const EASE = ["인하", "철폐", "면제", "유예", "완화", "해제", "타결", "철회", "감면", "합의", "lower", "cut", "exempt", "suspend", "ease", "lift", "waive", "deal"];
const KOREA_WORDS = ["한국", "국내", "韓", "우리나라", "우리 기업", "수출기업", "K-", "Korea", "Korean", "Seoul"];

// ── 5. 점수 설정 ─────────────────────────────────────────────────────
const SCORE = {
  windowDays: 7,          // 이번 주
  baselineWeeks: 4,       // 비교 기준: 직전 4주 평균
  halfLifeDays: 3.5,      // 오래된 기사일수록 반감 (3.5일마다 절반)
  wVolume: 0.6,           // 보도량 비중 (팀의 0.6/0.4 공식을 안정화해 유지)
  wMomentum: 0.4,         // 증가 추세 비중
  relevanceFloor: 0.6,    // 한국 관련도가 0이어도 남기는 최소 배수
  dupJaccard: 0.55,       // 제목 유사도 이 이상이면 같은 기사로 묶음
  maxPerSector: 3,        // Top10 안에서 같은 주 섹터 이슈 최대 개수 (다양성)
  archiveDays: 42,        // 기사 보관 기간
};

module.exports = { FEEDS, OUTLETS, TOPICS, TRADE_WORDS, SECTORS, TIGHTEN, EASE, KOREA_WORDS, SCORE };
