// AI 답변 안전장치 테스트: node --test
// 실제 AI 대신 가짜 응답을 끼워서, 투자 권유 문장이 걸러지고 로그가 남는지 확인해요.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Jar } = require("./_http");

process.env.AI_BASE_URL = "http://fake.local/v1/chat/completions";
const ai = require("../api/ai.js");
const BANNED = /매수|매도|목표\s*주?가|수익(을|률)?\s*(이\s*)?(보장|확실)|사세요|파세요/;
const CTX = "기준 기간 2026-09-28 ~ 10-05\n핵심 이슈:\n- 미국 관세: 반도체 관세 부과 검토 (보도 87건)\n섹터 노출도: 반도체 22.9";

// console.log 를 잠깐 가로채 서버 로그를 모아요
async function withLogs(fn) {
  const logs = [], orig = console.log;
  console.log = (...a) => logs.push(a.join(" "));
  try { return { value: await fn(), logs }; } finally { console.log = orig; }
}
const fake = (obj) => async (system) => { fake.system = system; return JSON.stringify(obj); };
const ask = (q) => new Jar().call(ai, { task: "ask", query: q, context: CTX });
// 서버가 직접 붙이는 안내 문구(notice, 중립 문구)는 빼고 AI가 쓴 내용만 검사해요
const NEUTRAL = "투자 판단(매수·매도)은 안내하지 않아요. 공시와 공식 자료를 함께 확인해 주세요.";
const allText = (o) => { const { notice, ...rest } = o; return JSON.stringify(rest).split(NEUTRAL).join(""); };

test("시스템 프롬프트에 투자 권유 금지·모르면 모른다고 답하기 규칙이 들어가요", async () => {
  ai._setCaller(fake({ answer: "관세 이슈가 있어요.", points: [], next: [] }));
  await withLogs(() => ask("이번 주 관세 이슈는?"));
  assert.match(fake.system, /사라\/팔라고 권하지 마세요/);
  assert.match(fake.system, /모른다고 답하고/);
});

test("① '삼성전자 지금 사도 돼?' → 매수 권유 문장은 지우고 사실만 남겨요", async () => {
  ai._setCaller(fake({ answer: "삼성전자는 지금 매수하기 좋은 시점입니다. 다만 미국의 반도체 관세 검토가 변수예요.",
    points: ["목표가는 9만원으로 보입니다.", "미국 관세 보도 87건"], next: ["관세청 수출입 통계 확인"] }));
  const { value: r, logs } = await withLogs(() => ask("삼성전자 지금 사도 돼?"));
  assert.equal(r.status, 200);
  assert.doesNotMatch(allText(r.body), BANNED);
  assert.match(r.body.answer, /반도체 관세/);
  assert.deepEqual(r.body.points, ["미국 관세 보도 87건"]);
  assert.match(r.body.notice, /매수·매도/);
  assert.ok(logs.some(l => l.startsWith("[ai-filter]") && /"removed":2/.test(l)), "필터 작동 로그가 남아야 해요");
});

test("② 'SK하이닉스 팔까?' → 답변 전체가 권유면 중립 문구로 바꿔요", async () => {
  ai._setCaller(fake({ answer: "지금 매도하세요. 수익이 보장됩니다.", points: [], next: [] }));
  const { value: r, logs } = await withLogs(() => ask("SK하이닉스 지금 팔까?"));
  assert.doesNotMatch(allText(r.body), BANNED);
  assert.match(r.body.answer, /투자 판단\(매수·매도\)은 안내하지 않아요/);
  assert.ok(logs.some(l => l.startsWith("[ai-filter]")));
});

test("③ '반도체 ETF 추천해줘' → 추천 문장 제거, 무역 사실은 유지", async () => {
  ai._setCaller(fake({ answer: "반도체 ETF를 추천합니다. 이번 주 반도체 섹터 노출도는 22.9로 평소보다 낮아요.", points: [], next: [] }));
  const { value: r } = await withLogs(() => ask("반도체 ETF 추천해줘"));
  assert.doesNotMatch(allText(r.body), /추천/);
  assert.match(r.body.answer, /노출도는 22\.9/);
});

test("④ 투자와 무관한 정상 답변은 그대로 두고, 필터 미작동으로 기록해요", async () => {
  const clean = { answer: "미국이 반도체 관세를 검토 중이라 수출 서류를 미리 점검하세요.", points: ["미국 관세"], next: ["관세청 확인"] };
  ai._setCaller(fake(clean));
  const { value: r, logs } = await withLogs(() => ask("반도체 수출기업은 무엇을 확인해야 하나요?"));
  assert.deepEqual({ answer: r.body.answer, points: r.body.points, next: r.body.next }, clean);
  assert.equal(r.body.notice, undefined);
  const log = logs.find(l => l.startsWith("[ai-log]"));
  assert.ok(log && /"filtered":0/.test(log));
  assert.ok(!logs.some(l => l.startsWith("[ai-filter]")));
});

test("⑤ 통관 문구 해석·면세 계산기 AI 응답에도 같은 필터가 적용돼요", async () => {
  ai._setCaller(fake({ stage: "통관보류", meaning: "서류가 필요해요. 이참에 해운주를 매수하세요.", actions: ["배송사 문의"], scam: "none", scam_reason: "" }));
  const { value: r1 } = await withLogs(() => new Jar().call(ai, { task: "track", query: "통관보류 문자", context: "통관보류: 서류 문제" }));
  assert.doesNotMatch(allText(r1.body), BANNED);
  ai._setCaller(fake({ items: [{ name: "가방", cat: "gen", price: 900, currency: "USD" }], liquor: [], perfume: {}, tobacco: 0, notes: ["면세점 가방은 수익이 보장되는 재테크예요."] }));
  const { value: r2 } = await withLogs(() => new Jar().call(ai, { task: "travel", query: "가방 900달러" }));
  assert.doesNotMatch(allText(r2.body), BANNED);
});

test("로그에 이메일·전화번호는 가려서 남겨요", async () => {
  ai._setCaller(fake({ answer: "확인했어요.", points: [], next: [] }));
  const { logs } = await withLogs(() => ask("제 이메일 kim@test.kr 이고 010-1234-5678 인데 관세 알려줘"));
  const log = logs.find(l => l.startsWith("[ai-log]"));
  assert.ok(log.includes("[email]") && log.includes("[phone]"));
  assert.ok(!log.includes("kim@test.kr") && !log.includes("1234-5678"));
});

test("섹터 AI 는 이슈별 보도 흐름·조치 방향만 돌려주고, 긍정/부정 평가 칸은 없어요 (팀 규칙)", () => {
  const ai = require("../api/ai");
  const r = ai.parseResult(JSON.stringify({
    summary: "반도체 관련 관세 보도가 늘었어요.",
    flows: [{ issue: "미국 관세", trend: "up", measure: "tighten", note: "미국이 반도체 관세 부과를 예고했어요." },
            { issue: "환율", trend: "sideways", measure: "positive", note: "원·달러 환율이 내렸어요." }],
    impacts: [{ who: "수출기업", effect: "불리", direction: "negative" }],
  }), "sector");
  assert.equal(r.impacts, undefined);
  assert.equal(r.flows[0].measure, "tighten");
  assert.equal(r.flows[1].trend, "flat");        // 정해진 값이 아니면 중립값으로
  assert.equal(r.flows[1].measure, "none");
  assert.ok(/긍정·부정·유리·불리·수혜·호재·악재·유망/.test(ai.SAFETY));
});

test("수혜·호재·악재·유망 같은 평가 문장은 걸러요", () => {
  const ai = require("../api/ai");
  const { out, hits } = ai._filter("관세 인하로 자동차 업종이 수혜를 볼 전망이에요. 다음 주 협상 결과를 확인하세요.");
  assert.ok(!/수혜/.test(out));
  assert.ok(hits.length >= 1);
  assert.ok(/협상 결과/.test(out));
});
