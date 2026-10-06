// 영문 제목 번역 (서버): AI 를 가짜로 바꿔 끼워 확인해요
const test = require("node:test");
const assert = require("node:assert/strict");
const ai = require("../api/ai");
const { localize, _cache } = require("../api/_translate");

test("영문 기사 제목은 번역을 제목으로, 원문은 orig 로 보내고 같은 제목은 다시 번역하지 않아요", async () => {
  process.env.AI_BASE_URL = "http://ai.test";
  _cache.clear();
  let calls = 0;
  ai._setCaller(async (sys, user) => { calls++; return JSON.stringify({ t: user.split("\n").map((_, i) => `번역 ${i + 1}`) }); });
  const data = { news: [{ title: "US hikes tariffs on steel", lang: "en" }, { title: "한국어 기사", link: "x" }],
    issues: [{ title: "US hikes tariffs on steel", lang: "en", articles: [{ title: "EU probes Chinese EVs", lang: "en" }] }] };
  const out = await localize(data);
  assert.equal(out.news[0].title, "번역 1");
  assert.equal(out.news[0].orig, "US hikes tariffs on steel");
  assert.equal(out.news[1].title, "한국어 기사");
  assert.equal(out.issues[0].articles[0].title, "번역 2");
  await localize({ news: [{ title: "US hikes tariffs on steel", lang: "en" }] });
  assert.equal(calls, 1, "캐시된 제목은 다시 묻지 않아요");
});

test("AI 가 이상한 답을 하거나 없으면 원문 제목 그대로", async () => {
  _cache.clear();
  const orig = console.error; console.error = () => {};
  ai._setCaller(async () => "죄송합니다");
  const out = await localize({ news: [{ title: "Tariff news", lang: "en" }] });
  console.error = orig;
  assert.equal(out.news[0].title, "Tariff news");
  assert.equal(out.news[0].orig, undefined);
  delete process.env.AI_BASE_URL;
  const out2 = await localize({ news: [{ title: "Tariff news", lang: "en" }] });
  assert.equal(out2.news[0].title, "Tariff news");
  ai._setCaller(null);
});
