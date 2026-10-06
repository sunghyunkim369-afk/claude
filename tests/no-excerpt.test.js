// 팀 규칙 C5: 화면·공개 데이터 파일에는 기사 발췌(summary·desc)가 없어야 해요. 제목·언론사·시각·링크만.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const read = (f) => JSON.parse(fs.readFileSync(path.join(__dirname, "..", "data", "news", f), "utf8"));

test("latest.json 의 뉴스·이슈와 breaking.json 에 기사 발췌가 없어요", () => {
  const d = read("latest.json"), b = read("breaking.json");
  for (const x of [...d.news, ...d.issues, ...b.items]) {
    assert.ok(!("summary" in x) && !("desc" in x), `발췌가 남은 항목: ${x.title}`);
  }
  for (const i of d.issues) for (const a of i.articles || []) assert.ok(!("summary" in a) && !("desc" in a));
});

test("섹터·뉴스·이슈에 종목 목록(stocks)이 없어요 (R12)", () => {
  const d = read("latest.json");
  for (const x of [...d.sectors, ...d.news, ...d.issues]) assert.ok(!("stocks" in x), `stocks 가 남은 항목: ${x.name || x.title}`);
});
