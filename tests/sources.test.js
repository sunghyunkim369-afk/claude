// 출처 화이트리스트와 의견·증권 단신 구분 회귀 테스트 (2026-10 출처 점검)
const test = require("node:test");
const assert = require("node:assert");
const { outletTier, isOpinion, rankForIssue, breakingFrom } = require("../scripts/news/lib");

test("이름 안에 다른 언론사 이름이 들어 있어도 통과시키지 않아요", () => {
  assert.equal(outletTier("스카이데일리", "https://www.skyedaily.com"), 0);   // "이데일리" 포함
  assert.equal(outletTier("대한경제", "https://www.dnews.co.kr"), 0);         // "한경" 포함
  assert.equal(outletTier("스카이데일리"), 0);
});

test("스포츠·연예 계열은 같은 언론사 도메인이어도 제외해요", () => {
  assert.equal(outletTier("스포츠동아", "https://sports.donga.com"), 0);
  assert.equal(outletTier("동아일보", "https://www.donga.com"), 0.8);
});

test("정상 언론사·계열 매체는 이름 앞부분이나 도메인으로 통과해요", () => {
  assert.equal(outletTier("연합뉴스TV"), 0.9);
  assert.equal(outletTier("KBS 뉴스"), 0.8);
  assert.equal(outletTier("The Wall Street Journal"), 0.8);
  assert.equal(outletTier("마켓인", "https://marketin.edaily.co.kr"), 0.8);
  assert.equal(outletTier("대한민국 정책브리핑"), 1.0);
  assert.equal(outletTier("모르는신문", "https://unknown.example.com"), 0);
});

test("사설·칼럼·특징주·목표가는 의견·증권 단신으로 봐요", () => {
  for (const t of ["[사설]반도체 관세 압박", "[기자수첩]희토류 공급망", "[무역칼럼] IEEPA 관세 무효 판결", "[특징주] 철강주 관세 우려에 약세", "DS증권, 현대로템 목표가↓…환율 부담"])
    assert.ok(isOpinion(t), t);
  for (const t of ["美, 반도체 관세 25% 부과", "[단독] 정부, 대미 관세 협상안 확정", "[속보] 수출 8% 증가"])
    assert.ok(!isOpinion(t), t);
});

test("의견 기사는 관련도가 높아도 대표 기사로 뽑히지 않아요", () => {
  const list = [
    { title: "[사설] 미국 관세 폭탄, 대응 서둘러야", desc: "", tier: 1 },
    { title: "미국, 한국산 자동차 관세 25% 부과", desc: "", tier: 0.8 },
  ];
  const ranked = rankForIssue(list, "tariff", "us", a => a.tier);
  assert.match(ranked[0].a.title, /부과/);
});

test("속보에는 의견 기사를 넣지 않아요", () => {
  const now = Date.parse("2026-10-06T00:00:00Z");
  const d = new Date(now - 3600e3).toISOString();
  const out = breakingFrom([{ title: "[사설] 관세 협상", date: d, tier: 0.8 }, { title: "관세 협상 타결", date: d, tier: 0.8 }], now);
  assert.deepEqual(out.map(a => a.title), ["관세 협상 타결"]);
});
