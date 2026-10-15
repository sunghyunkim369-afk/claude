// 뜻이 여러 개인 주제 단어(제재·공급망)와 이슈 관련도 (2026-10 점수식 점검에서 찾은 오분류)
const test = require("node:test");
const assert = require("node:assert/strict");
const { finalize, issueRelevance } = require("../scripts/news/lib");
const art = (title, desc = "") => ({ title, desc, source: "연합뉴스", tier: 0.9 });
const topicOf = (title, desc) => { const c = finalize(art(title, desc)); return c ? c.topic : null; };

test("국내 행정 제재·담합은 경제제재가 아니에요", () => {
  for (const t of ["보험광고 규정위반 4년반에 4.7만건…협회 제재금 부과 10건뿐", "단통법 폐지 후에도 판매점 제재 3천건…절반은 비용 고지 위반",
    "피해기업 구제 늦어질라…기술 탈취 공정위 제재까지 703일 걸려", "설탕·밀가루·전분당 담합…삼양사, 공정위 과징금 1위 불명예"]) {
    assert.notEqual(topicOf(t), "sanctions", t);
  }
});

test("나라 사이의 제재는 그대로 경제제재예요", () => {
  for (const t of ["美, '이란 석유제품 불법거래' 튀르키예·UAE·인도 기업 제재", "\"日, 러 자금줄 '그림자 선단' 겨냥 추가 제재 검토\"(종합)",
    "'그레이엄 러 제재법', 공화 분열·민주 반대에 하원서 좌초 기류", "결혼식까지 폭격‥경제제재 한다더니 다시 군사작전 시작?"]) {
    assert.equal(topicOf(t), "sanctions", t);
  }
});

test("기업 ESG·금융·보안의 '공급망'은 공급망·핵심광물이 아니에요", () => {
  for (const t of ["[바이오스냅] 동아쏘시오그룹, 협력사 대상 ESG 공급망 교육", "\"대기업 거래실적으로 中企대출\"…올해 '공급망금융' 5조원 돌파",
    "서상덕 S2W 대표, 韓-英 공급망 보안 협력 제안"]) {
    assert.notEqual(topicOf(t), "supply_chain", t);
  }
});

test("무역·산업 공급망 기사는 그대로예요", () => {
  for (const t of ["한일 경제안보대화…\"AI·반도체 등 첨단기술 육성, 공급망 안전 협력\"", "[단독]소부장 공급망 다변화 추진에도 특정국 90% 이상 의존 품목 되레 증가",
    "알시시 이집트 대통령 7~9일 국빈 방한…공급망 등 협의", "“첨단산업에 필수”…수은, 구리 등 ‘7대 핵심광물’에 3조원 풀었다"]) {
    assert.equal(topicOf(t), "supply_chain", t);
  }
});

test("관광·행사 기사의 '항만'과 영문 'trade secrets'는 무역이 아니에요", () => {
  assert.notEqual(topicOf("지방공항·항만서 지역여행 쉽게…관광공사, 외국인 편의 개선"), "shipping");
  assert.notEqual(topicOf("[부산소식] 부산항 노사정 한마음 체육대회"), "shipping");
  const a = { title: "Freightmate admits using Flexport docs as AI trade secrets case settles", desc: "" };
  assert.ok(issueRelevance(a, "shipping", "") < 1, "영업비밀 소송이 관련도 만점이면 안 돼요");
});

test("관련도: 주제 단어 하나만으로는 만점이 아니고, 무역 단어가 더 있어야 올라가요", () => {
  // 상대국 없는 이슈에 0.3 을 그냥 주지 않아요
  assert.equal(issueRelevance(art("[외환] 원/달러 환율 4.3원 오른 1,342.8원(15:30 기준가)"), "fx", ""), 0.6);
  assert.equal(issueRelevance(art("환율, 수출기업 달러 매도 등에 3.2원 내린 1,340원"), "fx", ""), 1);
  // 다른 뜻으로 쓰인 주제 단어는 관련도에서도 0
  assert.ok(issueRelevance(art("보험광고 규정위반 4년반에 4.7만건…협회 제재금 부과 10건뿐"), "sanctions", "") < 0.6);
  // 상대국 이슈는 그대로: 주제 0.6 + 상대국 0.3
  assert.equal(issueRelevance(art("美, 이란 석유제품 불법거래 기업 제재"), "sanctions", "us"), 0.9);
});
