// 회원 기능: 회원가입 · 로그인 · 로그아웃 · 내 정보 · 관심 섹터 저장
// POST /api/auth  { action: "signup" | "login" | "logout" | "me" | "watch", ... }
//
// 필요한 환경변수 (Eyefeet Cloud 테넌트 설정, 값은 저장소에 절대 넣지 않아요)
//   SESSION_SECRET  (선택) 로그인 쿠키 서명용 임의 문자열 (32자 이상).
//                   없으면 서버가 처음 실행될 때 무작위 값을 만들어 회원 저장소(DB 또는 파일)에 보관해서 써요.
//   DATABASE_URL    (권장) PostgreSQL 주소. 있으면 회원 정보를 DB에 저장해요.
//   AUTH_FILE       (선택) DB가 없을 때 쓰는 서버 파일 경로. 기본: 임시 폴더/tradecompass/users.json
//                   ※ 파일 저장은 서버를 다시 배포하면 지워질 수 있어요. 오래 쓰려면 DATABASE_URL 을 넣으세요.
//
// 보안
// - 비밀번호는 scrypt(무작위 salt)로 해시해서만 저장해요. 원문은 어디에도 남기지 않아요.
// - 로그인 상태는 HMAC 서명한 쿠키(HttpOnly, Secure, SameSite=Lax)로 유지해요.
//   "로그인 상태 유지"를 고르면 30일, 아니면 브라우저를 닫으면 끝나는 쿠키(최대 1일)예요.
// - 다른 사이트에서 보낸 요청(Origin 불일치)은 거절하고, IP당 분당 시도 횟수를 제한해요.
// - 로그인 실패 메시지는 "이메일 또는 비밀번호가 맞지 않아요" 하나로 통일해요(가입 여부 노출 방지).
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const metrics = require("./_metrics");

const COOKIE = "tc_session";
const LIMIT_PER_MIN = 10;
const SECTORS = ["semi", "auto", "battery", "steel", "chem", "ship", "machinery", "consumer"];
const hits = new Map();

// ── 저장소 1: PostgreSQL ──
function pgStore(url) {
  const { Pool } = require("pg");
  const pool = new Pool({ connectionString: url, max: 3,
    ssl: /sslmode=disable|localhost|127\.0\.0\.1/.test(url) ? false : { rejectUnauthorized: false } });
  let ready = null;
  const init = () => ready || (ready = pool.query(`CREATE TABLE IF NOT EXISTS tc_users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    pass_hash TEXT NOT NULL,
    watch JSONB NOT NULL DEFAULT '[]',
    agreed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login TIMESTAMPTZ)`).catch(e => { ready = null; throw e; }));
  const q = async (sql, args) => { await init(); return (await pool.query(sql, args)).rows; };
  return {
    kind: "postgres",
    async create(u) {
      const r = await q("INSERT INTO tc_users (email, name, pass_hash, watch) VALUES ($1, $2, $3, $4) ON CONFLICT (email) DO NOTHING RETURNING id, email, name, watch",
        [u.email, u.name, u.passHash, JSON.stringify(u.watch)]);
      return r[0] || null;
    },
    async byEmail(email) { return (await q("SELECT id, email, name, pass_hash, watch FROM tc_users WHERE email = $1", [email]))[0] || null; },
    async byId(id) { return (await q("SELECT id, email, name, watch FROM tc_users WHERE id = $1", [id]))[0] || null; },
    async setWatch(id, watch) { return (await q("UPDATE tc_users SET watch = $2 WHERE id = $1 RETURNING id, email, name, watch", [id, JSON.stringify(watch)]))[0] || null; },
    async touch(id) { await q("UPDATE tc_users SET last_login = now() WHERE id = $1", [id]); },
    // 쿠키 서명 비밀값: 없으면 만들어서 DB에 보관 (여러 서버가 같은 값을 쓰도록 먼저 넣은 값이 이겨요)
    async secret() {
      await init();
      await pool.query("CREATE TABLE IF NOT EXISTS tc_settings (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
      await pool.query("INSERT INTO tc_settings (k, v) VALUES ('session_secret', $1) ON CONFLICT (k) DO NOTHING", [crypto.randomBytes(32).toString("hex")]);
      return (await pool.query("SELECT v FROM tc_settings WHERE k = 'session_secret'")).rows[0].v;
    },
    end: () => pool.end(),
  };
}

// ── 저장소 2: 서버 파일 (DB가 없을 때) ──
// 쓰기는 한 번에 하나씩, 임시 파일에 쓴 뒤 이름을 바꿔 통째로 교체해요(중간에 끊겨도 파일이 깨지지 않게).
function fileStore(file) {
  let chain = Promise.resolve();
  const load = () => { try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return { seq: 0, users: [] }; } };
  const save = (d) => {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(d), { mode: 0o600 });
    fs.renameSync(tmp, file);
  };
  const locked = (fn) => (chain = chain.then(() => fn(), () => fn()));
  const pub = (u) => u && { id: u.id, email: u.email, name: u.name, watch: u.watch };
  return {
    kind: "file",
    create: (u) => locked(() => {
      const d = load();
      if (d.users.some(x => x.email === u.email)) return null;
      const row = { id: ++d.seq, email: u.email, name: u.name, pass_hash: u.passHash, watch: u.watch,
        agreed_at: new Date().toISOString(), created_at: new Date().toISOString(), last_login: null };
      d.users.push(row); save(d);
      return pub(row);
    }),
    byEmail: async (email) => { const u = load().users.find(x => x.email === email); return u ? { ...pub(u), pass_hash: u.pass_hash } : null; },
    byId: async (id) => pub(load().users.find(x => x.id === id)),
    setWatch: (id, watch) => locked(() => {
      const d = load(), u = d.users.find(x => x.id === id);
      if (!u) return null;
      u.watch = watch; save(d); return pub(u);
    }),
    touch: (id) => locked(() => { const d = load(), u = d.users.find(x => x.id === id); if (u) { u.last_login = new Date().toISOString(); save(d); } }),
    // 쿠키 서명 비밀값: 없으면 만들어서 회원 파일 옆에 보관 (주인만 읽기)
    secret: () => locked(() => {
      const f = path.join(path.dirname(file), "session-secret");
      try { const v = fs.readFileSync(f, "utf8").trim(); if (v.length >= 32) return v; } catch {}
      const v = crypto.randomBytes(32).toString("hex");
      fs.mkdirSync(path.dirname(f), { recursive: true, mode: 0o700 });
      fs.writeFileSync(f, v, { mode: 0o600 });
      return v;
    }),
    end: async () => {},
  };
}

let store = null;
function getStore() {
  if (!store) store = process.env.DATABASE_URL ? pgStore(process.env.DATABASE_URL)
    : fileStore(process.env.AUTH_FILE || path.join(os.tmpdir(), "tradecompass", "users.json"));
  return store;
}

// ── 비밀번호 ──
const scrypt = (pw, salt) => new Promise((ok, no) => crypto.scrypt(pw, salt, 64, { N: 16384, r: 8, p: 1 }, (e, k) => e ? no(e) : ok(k)));
async function hashPw(pw) {
  const salt = crypto.randomBytes(16);
  return `s1$${salt.toString("base64")}$${(await scrypt(pw, salt)).toString("base64")}`;
}
async function checkPw(pw, stored) {
  const [v, s, h] = String(stored).split("$");
  if (v !== "s1" || !s || !h) return false;
  const a = await scrypt(pw, Buffer.from(s, "base64")), b = Buffer.from(h, "base64");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
// 가입 안 된 이메일로 로그인할 때도 같은 시간이 걸리게 하는 가짜 해시
const DUMMY = "s1$AAAAAAAAAAAAAAAAAAAAAA==$" + Buffer.alloc(64).toString("base64");

// ── 로그인 쿠키 ──
const b64 = (s) => Buffer.from(s).toString("base64url");
let SECRET = null;
async function loadSecret() {
  if (process.env.SESSION_SECRET && process.env.SESSION_SECRET.length >= 16) return (SECRET = process.env.SESSION_SECRET);
  if (!SECRET) SECRET = await getStore().secret();
  return SECRET;
}
const sign = (data) => crypto.createHmac("sha256", SECRET).update(data).digest("base64url");
function makeToken(user, days) {
  const body = b64(JSON.stringify({ uid: user.id, exp: Date.now() + days * 86_400_000 }));
  return `${body}.${sign(body)}`;
}
function readToken(req) {
  const m = String(req.headers.cookie || "").match(new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`));
  if (!m) return null;
  const [body, sig] = m[1].split(".");
  if (!body || !sig) return null;
  const good = sign(body);
  if (sig.length !== good.length || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(good))) return null;
  try { const p = JSON.parse(Buffer.from(body, "base64url").toString()); return p.exp > Date.now() ? p : null; } catch { return null; }
}
// remember=true → 30일 유지 쿠키 / false → 브라우저 닫으면 끝나는 쿠키(토큰 자체도 1일 뒤 만료)
function setSession(res, user, remember) {
  const days = remember ? 30 : 1;
  const age = remember ? `; Max-Age=${days * 86_400}` : "";
  res.setHeader("Set-Cookie", `${COOKIE}=${makeToken(user, days)}; Path=/; HttpOnly; Secure; SameSite=Lax${age}`);
}
const clearSession = (res) => res.setHeader("Set-Cookie", `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`);

// ── 공통 ──
function send(res, status, obj) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(obj));
}
function readBody(req) {
  if (req.body && typeof req.body === "object") return Promise.resolve(req.body);
  if (typeof req.body === "string") return Promise.resolve(JSON.parse(req.body));
  return new Promise((resolve, reject) => {
    let d = "";
    req.on("data", c => { d += c; if (d.length > 4096) reject(new Error("too large")); });
    req.on("end", () => { try { resolve(JSON.parse(d || "{}")); } catch (e) { reject(e); } });
    req.on("error", reject);
  });
}
function tooMany(ip) {
  const now = Date.now(), h = hits.get(ip) || { t: now, n: 0 };
  if (now - h.t > 60_000) { h.t = now; h.n = 0; }
  h.n += 1; hits.set(ip, h);
  if (hits.size > 5000) hits.clear();
  return h.n > LIMIT_PER_MIN;
}
// 같은 사이트에서 온 요청인지 (다른 사이트가 로그인된 사용자 대신 요청을 보내는 것 방지)
function sameOrigin(req) {
  const origin = req.headers.origin;
  if (!origin) return true;   // 일부 브라우저는 같은 출처 POST 에 Origin 을 안 붙여요
  try { return new URL(origin).host === String(req.headers["x-forwarded-host"] || req.headers.host || "").split(",")[0].trim(); }
  catch { return false; }
}
const publicUser = (u) => ({ name: u.name, email: u.email, watch: Array.isArray(u.watch) ? u.watch : [] });
const cleanWatch = (w) => Array.isArray(w) ? [...new Set(w.filter(x => SECTORS.includes(x)))] : [];
const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;
const pwProblem = (pw) =>
  typeof pw !== "string" || pw.length < 8 ? "비밀번호는 8자 이상이어야 해요."
  : pw.length > 72 ? "비밀번호는 72자까지 쓸 수 있어요."
  : !/[A-Za-z]/.test(pw) || !/\d/.test(pw) ? "비밀번호에 영문과 숫자를 함께 넣어 주세요." : "";

async function handler(req, res) {
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return send(res, 405, { error: "POST만 받아요." }); }
  if (process.env.AUTH_DISABLED === "1") {
    return send(res, 503, { error: "회원 기능을 잠시 멈췄어요.", code: "not_configured" });
  }
  if (!sameOrigin(req)) return send(res, 403, { error: "허용되지 않은 요청이에요." });
  try { await loadSecret(); }
  catch (e) {
    metrics.record("auth", `secret: ${e.message}`);
    return send(res, 503, { error: "회원 기능을 준비하지 못했어요. 잠시 뒤 다시 시도해 주세요.", code: "not_configured" });
  }

  let body;
  try { body = await readBody(req); } catch { return send(res, 400, { error: "요청 형식이 올바르지 않아요." }); }
  const action = String(body.action || "");
  const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim();
  if ((action === "login" || action === "signup") && tooMany(ip)) return send(res, 429, { error: "시도가 너무 많아요. 1분 뒤에 다시 해 주세요." });

  try {
    const db = getStore();

    if (action === "signup") {
      const name = String(body.name || "").trim(), email = String(body.email || "").trim().toLowerCase(), pw = body.password;
      if (!name || name.length > 20) return send(res, 400, { error: "이름은 1~20자로 적어 주세요.", field: "name" });
      if (!EMAIL.test(email)) return send(res, 400, { error: "이메일 형식을 확인해 주세요.", field: "email" });
      const p = pwProblem(pw); if (p) return send(res, 400, { error: p, field: "password" });
      if (body.agree !== true) return send(res, 400, { error: "개인정보 수집·이용에 동의해 주세요.", field: "agree" });
      const u = await db.create({ email, name, passHash: await hashPw(pw), watch: cleanWatch(body.watch) });
      if (!u) return send(res, 409, { error: "이미 가입된 이메일이에요. 로그인해 주세요.", field: "email" });
      setSession(res, u, !!body.remember);
      console.log(JSON.stringify({ tag: "[auth]", event: "signup", uid: u.id, store: db.kind, at: new Date().toISOString() }));
      return send(res, 200, { user: publicUser(u) });
    }

    if (action === "login") {
      const email = String(body.email || "").trim().toLowerCase(), pw = String(body.password || "");
      const u = await db.byEmail(email);
      const ok = await checkPw(pw, u ? u.pass_hash : DUMMY);
      if (!u || !ok) return send(res, 401, { error: "이메일 또는 비밀번호가 맞지 않아요." });
      await db.touch(u.id);
      setSession(res, u, !!body.remember);
      return send(res, 200, { user: publicUser(u) });
    }

    if (action === "logout") { clearSession(res); return send(res, 200, { ok: true }); }

    const tok = readToken(req);
    if (!tok) return send(res, 401, { error: "로그인이 필요해요." });
    if (action === "me") {
      const u = await db.byId(tok.uid);
      return u ? send(res, 200, { user: publicUser(u) }) : send(res, 401, { error: "로그인이 필요해요." });
    }
    if (action === "watch") {
      const u = await db.setWatch(tok.uid, cleanWatch(body.watch));
      return u ? send(res, 200, { user: publicUser(u) }) : send(res, 401, { error: "로그인이 필요해요." });
    }
    return send(res, 400, { error: "알 수 없는 요청이에요." });
  } catch (e) {
    metrics.record("auth", `${action}: ${e.message}`);
    return send(res, 500, { error: "잠시 문제가 생겼어요. 조금 뒤 다시 시도해 주세요." });
  }
}

module.exports = handler;
// 상태 확인·테스트용
module.exports.storeKind = () => (process.env.DATABASE_URL ? "postgres" : "file");
module.exports._reset = async () => { if (store) await store.end(); store = null; SECRET = null; hits.clear(); };
