// 회원 기능 시나리오 테스트: node --test
// 기본은 서버 파일 저장소로, TEST_DATABASE_URL 이 있으면 PostgreSQL 로도 같은 시나리오를 돌려요.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { Jar } = require("./_http");

process.env.SESSION_SECRET = "test-secret-0123456789abcdef0123456789";
const PW = "trade1234";

function suite(label, setup) {
  test(label, async (t) => {
    const ctx = await setup();
    delete require.cache[require.resolve("../api/auth.js")];
    const auth = require("../api/auth.js");
    t.after(async () => { await auth._reset(); await ctx.cleanup(); });
    const email = `kim${Date.now()}@test.kr`;

    await t.test("① 가입 → 로그아웃 → 로그인", async () => {
      const a = new Jar();
      let r = await a.call(auth, { action: "signup", name: "김무역", email, password: PW, agree: true, watch: ["semi"] });
      assert.equal(r.status, 200); assert.equal(r.body.user.email, email);
      assert.equal((await a.call(auth, { action: "me" })).status, 200);
      r = await a.call(auth, { action: "logout" });
      assert.equal(r.status, 200); assert.match(r.setCookie, /Max-Age=0/);
      assert.equal((await a.call(auth, { action: "me" })).status, 401);
      r = await a.call(auth, { action: "login", email, password: PW });
      assert.equal(r.status, 200); assert.equal(r.body.user.name, "김무역");
    });

    await t.test("로그인 상태 유지: 체크하면 30일, 안 하면 브라우저 세션 쿠키", async () => {
      const a = new Jar();
      let r = await a.call(auth, { action: "login", email, password: PW, remember: true });
      assert.match(r.setCookie, /Max-Age=2592000/);
      assert.match(r.setCookie, /HttpOnly/); assert.match(r.setCookie, /Secure/); assert.match(r.setCookie, /SameSite=Lax/);
      r = await a.call(auth, { action: "login", email, password: PW, remember: false });
      assert.doesNotMatch(r.setCookie, /Max-Age/);
    });

    await t.test("② 관심 섹터를 저장하면 다른 브라우저에서 로그인해도 같다", async () => {
      const pc = new Jar(), phone = new Jar();
      await pc.call(auth, { action: "login", email, password: PW });
      const r = await pc.call(auth, { action: "watch", watch: ["ship", "auto", "ship", "hack"] });
      assert.deepEqual(r.body.user.watch, ["ship", "auto"]);   // 중복·없는 섹터는 걸러요
      const r2 = await phone.call(auth, { action: "login", email, password: PW });
      assert.deepEqual(r2.body.user.watch, ["ship", "auto"]);
      assert.deepEqual((await phone.call(auth, { action: "me" })).body.user.watch, ["ship", "auto"]);
    });

    await t.test("③ 저장소에 평문 비밀번호가 없다", async () => {
      const raw = await ctx.dump();
      assert.ok(!raw.includes(PW), "평문 비밀번호가 저장돼 있으면 안 돼요");
      assert.match(raw, /s1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+/);   // scrypt 해시 형식
    });

    await t.test("입력 검증 · 중복 가입 · 잘못된 로그인", async () => {
      const a = new Jar();
      assert.equal((await a.call(auth, { action: "signup", name: "x", email: "bad-email", password: PW, agree: true })).body.field, "email");
      assert.equal((await a.call(auth, { action: "signup", name: "x", email: "s@test.kr", password: "short1", agree: true })).body.field, "password");
      assert.equal((await a.call(auth, { action: "signup", name: "x", email: "s@test.kr", password: "onlyletters", agree: true })).body.field, "password");
      assert.equal((await a.call(auth, { action: "signup", name: "x", email: "s@test.kr", password: PW, agree: false })).body.field, "agree");
      const dup = await a.call(auth, { action: "signup", name: "또", email: email.toUpperCase(), password: PW, agree: true });
      assert.equal(dup.status, 409);
      const wrong = await a.call(auth, { action: "login", email, password: "wrong9999" });
      const nobody = await a.call(auth, { action: "login", email: "nobody@test.kr", password: PW });
      assert.equal(wrong.status, 401); assert.equal(nobody.status, 401);
      assert.equal(wrong.body.error, nobody.body.error);   // 가입 여부를 드러내지 않아요
    });

    await t.test("위조 쿠키·다른 사이트 요청·연속 시도 차단", async () => {
      const a = new Jar();
      await a.call(auth, { action: "login", email, password: PW });
      a.cookie = a.cookie.replace(/\.[^.]+$/, ".forged");
      assert.equal((await a.call(auth, { action: "me" })).status, 401);
      assert.equal((await a.call(auth, { action: "me" }, { origin: "https://evil.example" })).status, 403);
      let r; for (let i = 0; i < 11; i++) r = await a.call(auth, { action: "login", email, password: "x" }, { ip: "9.9.9.9" });
      assert.equal(r.status, 429);
    });
  });
}

suite("회원 기능 (서버 파일 저장소)", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "tc-auth-"));
  const file = path.join(dir, "users.json");
  delete process.env.DATABASE_URL; process.env.AUTH_FILE = file;
  return { dump: async () => fs.readFileSync(file, "utf8"), cleanup: async () => fs.rmSync(dir, { recursive: true, force: true }) };
});

if (process.env.TEST_DATABASE_URL) {
  suite("회원 기능 (PostgreSQL)", async () => {
    process.env.DATABASE_URL = process.env.TEST_DATABASE_URL; delete process.env.AUTH_FILE;
    const { Client } = require("pg");
    const c = new Client({ connectionString: process.env.DATABASE_URL }); await c.connect();
    await c.query("DROP TABLE IF EXISTS tc_users");
    return {
      dump: async () => JSON.stringify((await c.query("SELECT * FROM tc_users")).rows),
      cleanup: async () => { await c.end(); delete process.env.DATABASE_URL; },
    };
  });
}

test("SESSION_SECRET 이 없어도 서버가 비밀값을 만들어 보관하고 회원 기능이 동작해요", async () => {
  const keep = process.env.SESSION_SECRET; delete process.env.SESSION_SECRET;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "tc-auth-nosecret-"));
  delete process.env.DATABASE_URL; process.env.AUTH_FILE = path.join(dir, "users.json");
  delete require.cache[require.resolve("../api/auth.js")];
  const auth = require("../api/auth.js");
  const a = new Jar();
  const r = await a.call(auth, { action: "signup", name: "비밀", email: "nosecret@test.kr", password: PW, agree: true });
  assert.equal(r.status, 200);
  assert.equal((await a.call(auth, { action: "me" })).status, 200);
  const secretFile = path.join(dir, "session-secret");
  assert.ok(fs.readFileSync(secretFile, "utf8").length >= 32);
  assert.equal((fs.statSync(secretFile).mode & 0o077), 0, "비밀값 파일은 주인만 읽을 수 있어요");
  // 서버를 다시 시작해도(모듈 다시 로드) 같은 비밀값이라 로그인이 유지돼요
  await auth._reset();
  delete require.cache[require.resolve("../api/auth.js")];
  assert.equal((await a.call(require("../api/auth.js"), { action: "me" })).status, 200);
  await require("../api/auth.js")._reset();
  fs.rmSync(dir, { recursive: true, force: true });
  process.env.SESSION_SECRET = keep;
});

test("AUTH_DISABLED=1 이면 '준비 중'(503)", async () => {
  process.env.AUTH_DISABLED = "1";
  delete require.cache[require.resolve("../api/auth.js")];
  const r = await new Jar().call(require("../api/auth.js"), { action: "me" });
  delete process.env.AUTH_DISABLED;
  assert.equal(r.status, 503); assert.equal(r.body.code, "not_configured");
});
