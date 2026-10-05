// 상태 확인·오류 집계 테스트: node --test
const test = require("node:test");
const assert = require("node:assert/strict");
const { Jar } = require("./_http");
const metrics = require("../api/_metrics");
const health = require("../api/health");
const admin = require("../api/admin-errors");

test("서버 오류가 집계되고 /health 에는 건수만 나와요", async () => {
  metrics.reset();
  const orig = console.error; console.error = () => {};
  metrics.record("ai", "AI HTTP 502 for kim@test.kr");
  metrics.record("auth", "login: db down");
  console.error = orig;
  const r = await new Jar().call(health, null, { method: "GET" });
  assert.equal(r.body.ok, true);
  assert.equal(r.body.errors.last24h, 2);
  assert.deepEqual(r.body.errors.bySource, { ai: 1, auth: 1 });
  assert.ok(!JSON.stringify(r.body).includes("502"), "상태 확인에는 오류 내용이 나오지 않아요");
});

test("관리자 오류 목록: 토큰 없으면 404, 틀리면 401, 맞으면 목록(개인정보 가림)", async () => {
  delete process.env.ADMIN_TOKEN;
  assert.equal((await new Jar().call(admin, null, { method: "GET" })).status, 404);
  process.env.ADMIN_TOKEN = "admin-test-token-123";
  assert.equal((await new Jar().call(admin, null, { method: "GET", headers: { authorization: "Bearer wrong" } })).status, 401);
  const r = await new Jar().call(admin, null, { method: "GET", headers: { authorization: "Bearer admin-test-token-123" } });
  assert.equal(r.status, 200);
  assert.equal(r.body.recent[0].source, "auth");
  assert.ok(r.body.recent.some(e => e.message.includes("[email]")));
  assert.ok(!JSON.stringify(r.body).includes("kim@test.kr"));
});
