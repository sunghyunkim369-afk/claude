// Top 10 대표 기사 선정 규칙 단위 테스트: node --test
const test = require("node:test");
const assert = require("node:assert/strict");
const { isPromo, issueRelevance, rankForIssue, classify, finalize } = require("../scripts/news/lib");
const { LEAD_MIN_RELEVANCE } = require("../scripts/news/config");

const art = (title, desc = "", w = 1) => ({ title, desc, w, lang: "ko" });
const W = a => a.w;

test("제품 출시·행사 홍보 기사는 걸러요", () => {
  assert.equal(isPromo("HD현대사이트솔루션, 국내 최대 18t급 전동지게차 출시"), true);
  assert.equal(isPromo("부산항만공사, 스마트항만 세미나 개최"), true);
  // 무역 핵심 단어가 있으면 홍보 단어가 있어도 남겨요
  assert.equal(isPromo("미국, 한국산 철강 반덤핑 관세 발표…업계 설명회 개최"), false);
  assert.equal(isPromo("홍해 운임 급등에 해운사 신규 노선 출시"), false);
  assert.equal(finalize(art("HD현대사이트솔루션, 국내 최대 18t급 전동지게차 출시", "항만 물류 현장에 투입"), "shipping"), null);
});

test("해운·물류 이슈의 대표 기사로 전동지게차 출시 기사가 뽑히지 않아요 (무게가 더 커도)", () => {
  const list = [
    art("HD현대사이트솔루션, 국내 최대 18t급 전동지게차 출시", "항만·물류 현장에 투입", 9),
    art("홍해 우회 장기화…컨테이너 운임 3주 연속 상승", "해상운임 지수 상승", 2),
    art("부산항 물동량 회복", "항만 물동량 증가", 3),
  ];
  const ranked = rankForIssue(list, "shipping", "", W);
  assert.match(ranked[0].a.title, /운임|부산항/);
  assert.ok(!ranked.some(x => /지게차/.test(x.a.title)), "홍보성 기사는 후보에서도 빠져요");
});

test("관련도: 주제 단어가 제목에 있어야 기준을 넘고, 상대국이 제목에 있으면 더 높아요", () => {
  const withCountry = issueRelevance(art("美, 한국산 자동차에 25% 관세 부과"), "tariff", "us");
  const noCountry = issueRelevance(art("자동차 관세 협상 장기화"), "tariff", "us");
  const descOnly = issueRelevance(art("트럼프 정상회담 결과 발표", "관세 문제도 논의"), "tariff", "us");
  assert.ok(withCountry >= LEAD_MIN_RELEVANCE && withCountry > noCountry);
  assert.ok(noCountry >= LEAD_MIN_RELEVANCE);
  assert.ok(descOnly < LEAD_MIN_RELEVANCE, "요약에만 주제가 있으면 대표가 될 수 없어요");
});

test("기준을 넘는 기사가 있으면 그중 무게 순, 없으면 관련도 1등을 써요", () => {
  const list = [
    art("트럼프 정상회담 결과 발표", "관세 문제도 논의", 10),   // 무게는 크지만 관련도 미달
    art("美 상호관세 15% 유지 확인", "", 2),
    art("미국 관세 압박에 자동차 업계 긴장", "", 3),
  ];
  const ranked = rankForIssue(list, "tariff", "us", W);
  assert.equal(ranked[0].a.title, "미국 관세 압박에 자동차 업계 긴장");
  assert.ok(ranked[0].rel >= LEAD_MIN_RELEVANCE);
  assert.equal(ranked.at(-1).a.title, "트럼프 정상회담 결과 발표");
  const none = rankForIssue([art("정상회담 결과", "관세 논의", 5), art("회담 일정 공개", "", 1)], "tariff", "us", W);
  assert.equal(none[0].a.title, "정상회담 결과");   // 둘 다 미달이면 관련도 높은 쪽
});

test("다른 뜻으로 쓰인 주제 단어는 그 주제로 분류하지 않아요 (마약 공급망)", () => {
  assert.notEqual(classify(art("마약 공급망 원천차단…밀수·제조사범 검거 34%↑")).topic, "supply_chain");
  assert.ok(issueRelevance(art("마약 공급망 원천차단…밀수·제조사범 검거"), "supply_chain", "") < LEAD_MIN_RELEVANCE);
  assert.equal(classify(art("中 희토류 수출 제한…핵심광물 공급망 비상")).topic, "supply_chain");
});
