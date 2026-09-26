// 무역나침반 MVP 데이터
// AI 파이프라인이 하루 한 번 이 파일(또는 같은 구조의 JSON)을 새로 만들어 덮어쓰는 것을 전제로 합니다.
// 지금 들어 있는 값은 화면 확인용 샘플입니다.
window.TC_DATA = {
  meta: {
    date: "2026-09-26",
    updatedAt: "07:00",
    sources: 2481,
    sample: true
  },

  // Today's Bearing: 오늘의 한 줄 요약 + 핵심 포인트
  bearing: {
    headline: "반도체 장비 수출 통제 강화가 오늘의 최대 변수입니다",
    points: [
      "미국의 대중 반도체 장비 추가 통제로 장비·소재 섹터 노출도가 크게 올랐어요.",
      "홍해 우회 항로 장기화로 해운 운임이 8주째 상승하고 있어요.",
      "EU의 한국산 배터리 소재 관세 유예 검토는 2차전지에 우호적이에요."
    ]
  },

  // 섹터별 노출도: 최근 7일 뉴스 보도량과 방향을 0~100 지수로 (50 = 중립)
  sectors: [
    { id: "semi", name: "반도체", score: 71.4, state: "강화", up: 5, down: 1, neutral: 2,
      summary: "대중 장비 수출 통제와 HBM 공급망 재편 이슈가 집중됐어요.",
      stocks: [{ name: "삼성전자", code: "005930" }, { name: "SK하이닉스", code: "000660" }, { name: "한미반도체", code: "042700" }] },
    { id: "ship", name: "해운·물류", score: 64.2, state: "강화", up: 3, down: 0, neutral: 2,
      summary: "홍해 리스크 재확대로 컨테이너 운임 상승이 이어지고 있어요.",
      stocks: [{ name: "HMM", code: "011200" }, { name: "팬오션", code: "028670" }] },
    { id: "battery", name: "2차전지", score: 58.9, state: "완화", up: 1, down: 3, neutral: 1,
      summary: "EU 관세 유예 검토 소식으로 규제 부담이 줄어드는 흐름이에요.",
      stocks: [{ name: "LG에너지솔루션", code: "373220" }, { name: "포스코퓨처엠", code: "003670" }] },
    { id: "auto", name: "자동차", score: 52.3, state: "보합", up: 2, down: 2, neutral: 3,
      summary: "친환경차 수출 호조와 미국 관세 논의가 맞서 있어요.",
      stocks: [{ name: "현대차", code: "005380" }, { name: "HL만도", code: "204320" }] },
    { id: "steel", name: "철강·소재", score: 46.1, state: "보합", up: 1, down: 1, neutral: 2,
      summary: "중국 수출 물량 증가로 가격 압박이 이어지고 있어요.",
      stocks: [{ name: "POSCO홀딩스", code: "005490" }, { name: "현대제철", code: "004020" }] }
  ],

  // 오늘의 핵심 무역 이슈
  // impact: 영향도(0~100) · score: 이슈 점수(50 = 중립) · reports/prev: 최근 7일·직전 7일 보도 건수
  // up/down/neutral: 조치 강화·완화·중립 건수 · articles: 대표 기사
  issues: [
    { keyword: "반도체 장비 수출 통제", title: "美, 중국산 반도체 장비 추가 수출 통제", source: "미 상무부", time: "35분 전", impact: 92, tag: "반도체 공급망",
      sectors: ["semi", "steel"], score: 71.40, reports: 14, prev: 6, up: 5, down: 0, neutral: 1,
      summary: "첨단 패키징 장비까지 통제 대상이 넓어져 국내 장비·소재 기업의 중국 매출에 영향이 예상돼요.",
      articles: [
        { title: "미국, 첨단 패키징 장비까지 대중 수출 제한 확대 검토", source: "Reuters", at: "2026-09-26 15:42" },
        { title: "美 상무부, 반도체 장비 수출 통제 대상 추가 발표", source: "미 상무부", at: "2026-09-26 09:10" }
      ] },
    { keyword: "홍해 우회 항로", title: "홍해 운항 리스크 재확대…운임 8주 최고", source: "Lloyd's List", time: "1시간 전", impact: 86, tag: "해운·물류",
      sectors: ["ship", "auto"], score: 64.20, reports: 11, prev: 7, up: 3, down: 0, neutral: 2,
      summary: "주요 선사의 수에즈 우회가 이어지며 유럽 노선 운임이 다시 올랐어요.",
      articles: [
        { title: "상하이컨테이너운임지수 3주 연속 상승…유럽 노선 주도", source: "S&P Global", at: "2026-09-26 12:05" }
      ] },
    { keyword: "EU 배터리 소재 관세", title: "EU, 한국산 배터리 소재 관세 유예 검토", source: "Reuters", time: "2시간 전", impact: 78, tag: "2차전지",
      sectors: ["battery"], score: 58.90, reports: 6, prev: 4, up: 1, down: 3, neutral: 1,
      summary: "역내 공급 부족을 이유로 양극재 등 일부 품목의 관세 부과를 미루는 방안이 논의되고 있어요.",
      articles: [
        { title: "EU 집행위, 배터리 소재 관세 유예안 회원국 회람", source: "Reuters", at: "2026-09-26 10:31" }
      ] },
    { keyword: "친환경차 수출", title: "8월 친환경차 수출 19.6% 증가", source: "산업통상자원부", time: "3시간 전", impact: 64, tag: "자동차",
      sectors: ["auto"], score: 52.30, reports: 5, prev: 5, up: 2, down: 1, neutral: 2,
      summary: "하이브리드 중심으로 북미·EU 수출이 늘었어요.",
      articles: [
        { title: "8월 친환경차 수출 19.6% 증가…북미·EU 동반 성장", source: "산업통상자원부", at: "2026-09-26 14:18" }
      ] },
    { keyword: "관세", title: "관세청, 전자담배 용액 고강도 통관관리 대책 시행", source: "관세청", time: "3일 전", impact: 57, tag: "통관",
      sectors: ["steel", "auto", "battery"], score: 50.00, reports: 3, prev: 3, up: 2, down: 0, neutral: 1,
      summary: "일부 품목의 통관 서류 요구가 강화되고 있어요.",
      articles: [
        { title: "관세청, 전자담배 용액 고강도 통관관리 대책 시행중", source: "관세청", at: "2026-09-23 10:50" }
      ] }
  ],

  // 뉴스 × 연관 종목 분석 (dir: up / down / flat = AI가 판단한 영향 방향)
  news: [
    { time: "15:42", source: "Reuters", tag: "반도체", title: "미국, 첨단 패키징 장비까지 대중 수출 제한 확대 검토",
      summary: "AI 가속기용 HBM 공급망의 불확실성이 단기 확대될 가능성.",
      stocks: [{ name: "SK하이닉스", dir: "down" }, { name: "한미반도체", dir: "down" }] },
    { time: "14:18", source: "산업통상자원부", tag: "자동차", title: "8월 친환경차 수출 19.6% 증가…북미·EU 동반 성장",
      summary: "하이브리드 중심 믹스 개선으로 완성차와 부품사 마진 기대 상향.",
      stocks: [{ name: "현대차", dir: "up" }, { name: "HL만도", dir: "up" }] },
    { time: "12:05", source: "S&P Global", tag: "해운", title: "상하이컨테이너운임지수 3주 연속 상승…유럽 노선 주도",
      summary: "선복 조정과 우회 항로 장기화로 4분기 운임 하방이 제한될 전망.",
      stocks: [{ name: "HMM", dir: "up" }, { name: "팬오션", dir: "up" }] },
    { time: "10:31", source: "Reuters", tag: "2차전지", title: "EU 집행위, 배터리 소재 관세 유예안 회원국 회람",
      summary: "양극재·전구체 수출 기업의 가격 경쟁력 부담이 줄어들 수 있음.",
      stocks: [{ name: "포스코퓨처엠", dir: "up" }, { name: "LG에너지솔루션", dir: "flat" }] },
    { time: "09:12", source: "관세청", tag: "통관", title: "관세청, 전자담배 용액 고강도 통관관리 대책 시행",
      summary: "해당 품목 수입 시 성분 확인 서류 요구가 강화됨.",
      stocks: [] }
  ],

  // 공급망 리스크 신호
  risks: [
    { level: "심각", title: "홍해 우회 항로 장기화", detail: "유럽향 해상 운송 지연", effect: "운임 +14.2%" },
    { level: "주의", title: "中 희토류 통관 지연", detail: "배터리·모터 소재 조달", effect: "리드타임 +6일" },
    { level: "주의", title: "美 반도체 장비 통제 확대", detail: "장비 수출 인허가 필요", effect: "대상 품목 확대" },
    { level: "관찰", title: "파나마 운하 수위 회복", detail: "미주 동안 노선 정상화", effect: "통항 제한 완화" }
  ],

  // 국가·품목별 무역 흐름 (월간 잠정치, 단위: 십억 달러)
  trade: {
    period: "2026년 8월 잠정치",
    total: 57.9, yoy: 7.5, balance: 3.8,
    countries: [
      { code: "CN", name: "중국", value: 11.8, yoy: -2.1 },
      { code: "US", name: "미국", value: 10.4, yoy: 9.6 },
      { code: "VN", name: "베트남", value: 5.1, yoy: 4.3 },
      { code: "EU", name: "EU", value: 5.0, yoy: 6.8 },
      { code: "JP", name: "일본", value: 2.5, yoy: 1.2 }
    ],
    items: [
      { name: "반도체", hs: "8542", value: 13.9, yoy: 8.7 },
      { name: "자동차", hs: "8703", value: 5.6, yoy: 11.2 },
      { name: "석유제품", hs: "2710", value: 4.1, yoy: -6.4 },
      { name: "선박", hs: "8901", value: 2.3, yoy: 21.5 }
    ]
  }
};
