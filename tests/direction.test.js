// 무역 조치 방향(강화·완화·없음) 분류 회귀 테스트: node --test
// 2026-10 실제 기사 115건을 사람이 직접 판정해 찾은 오류 유형을 대표 사례로 고정해 둬요.
const test = require("node:test");
const assert = require("node:assert/strict");
const { classify } = require("../scripts/news/lib");
const dir = (title) => { const d = classify({ title, desc: "", lang: "ko" }).direction; return d === "up" || d === "down" ? d : "none"; };

const CASES = [
  // 이전 버그: "0%"(무관세) 패턴이 "300%"·"10%"에 걸려 완화로 분류됨
  ["트럼프 “美에 공장 안 지으면 관세 300%”…한국에도 투자 압박 수위 높였다", "up"],
  ["\"관세 10% 할인되나요\"…대만 해안에 나타난 '트럼프 바위'", "none"],
  ["K-의약품, 美 관세 '0%' 수혜…韓제약바이오 안도", "down"],
  // "협력 강화"는 규제 강화가 아니에요
  ["한일, 5차 경제안보 대화…AI·반도체·공급망 협력 강화", "none"],
  ["李대통령, 중앙亞 5개국 정상 만난다…핵심광물·에너지 협력 강화", "none"],
  ["관세청, 원유 수입선 다변화 관세행정 지원대책 확대", "none"],
  // 놓치던 강화: 맞불·N% 관세, 반덤핑 관세, 관세 조사, 차단
  ["트럼프 경고에도…캐나다, 미국에 '50% 맞불 관세'", "up"],
  ["인도, 방글라산 황마 제품에 톤당 최대 60만원 반덤핑 관세", "up"],
  ["\"관세 더 내라\"…인도, 삼성·LG OLED 부품 관세 조사 중", "up"],
  ["美 의회 \"러 에너지 구매국 100% 관세\" 가결", "up"],
  ["관세청, 합성니코틴 '꼼수 통관' 차단", "up"],
  // 원래도 맞던 강화·완화 (깨지지 않게)
  ["日정부, 조만간 러시아 추가 제재 검토…'그림자 선단' 등 정조준", "up"],
  ["미, 캐나다산 유제품·주류·오토바이 29일부터 수입금지", "up"],
  ["트럼프, 아일랜드 위스키 10% 관세 철폐 선언", "down"],
  ["美中, 600억달러 관세 낮춘다…희토류 등 핵심갈등은 그대로", "down"],
  ["한·중·일 정상, 3국 FTA 연내 개시 합의", "down"],
  ["아르헨티나, '메르코수르-싱가포르 FTA' 발효", "down"],
  ["S. Korea wins zero-tariff status under US pharmaceutical trade measure", "down"],
  // 기업 거래 "deals"는 무역 완화가 아니에요
  ["KKR Bets on Korea AI Supply Chain After Record $3 Billion Deals", "none"],
  // 방향 없는 동향·분석
  ["8월 수출 983억 달러 '역대 3위'…반도체 세 달 연속 400억 달러", "none"],
  ["美 10년물 5% 돌파…환율 다시 1350원 시험대", "none"],
];

for (const [title, want] of CASES) {
  test(`${want.padEnd(4)} ← ${title}`, () => assert.equal(dir(title), want));
}
