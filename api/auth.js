// 회원 기능: 회원가입 · 로그인 · 로그아웃 · 내 정보 · 관심 섹터 저장
// POST /api/auth  { action: "signup" | "login" | "logout" | "me" | "watch", ... }
//
// 필요한 환경변수 (Eyefeet Cloud 테넌트 설정, 값은 저장소에 절대 넣지 않아요)
//   DATABASE_URL    PostgreSQL 접속 주소 (회원 정보 저장)
//   SESSION_SECRET  로그인 쿠키 서명용 임의 문자열 (32자 이상 권장)
// 둘 중 하나라도 없으면 503 으로 "회원 기능 준비 중"을 알려요.
//
// 보안
// - 비밀번호는 scrypt(무작위 salt)로 해시해서만 저장해요. 원문은 어디에도 남기지 않아요.
// - 로그인 상태는 HMAC 서명한 쿠키(HttpOnly, Secure, SameSite=Lax)로 유지해요.
// - 다른 사이트에서 보낸 요청(Origin 불일치)은 거절하고, IP당 분당 시도 횟수를 제한해요.
// - 로그인 실패 메시지는 "이메일 또는 비밀번호가 맞지 않아요" 하나로 통일해요(가입 여부 노출 방지).
const crypto = require("crypto");

const COOKIE = "tc_session";
const LIMIT_PER_MIN = 10;
const SECTORS = ["semi", "auto", "battery", "steel", "chem", "ship", "machinery", "consumer"];
const hits = new Map();
let pool = null, ready = null;

// ── 저장소 (PostgreSQL) ──
function db() {
  if (!pool) {
    const { Pool } = require("pg");
    pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 3,
      ssl: /sslmode=disable|localhost|127\.0\.0\.1/.test(process.env.DATABASE_URL) ? false : { rejectUnauthorized: false } });
  }
  if (!ready) ready = pool.query(`CREATE TABLE IF NOT EXISTS tc_users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    pass_hash TEXT NOT NULL,
    watch JSONB NOT NULL DEFAULT '[]',
    agreed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login TIMESTAMPTZ)`).catch(e => { ready = null; throw e; });
  return ready.then(() => pool);
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
const sign = (data) => crypto.createHmac("sha256", process.env.SESSION_SECRET).update(data).digest("base64url");
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
function setCookie(res, value, days) {
  const age = value ? days * 86_400 : 0;
  res.setHeader("Set-Cookie", `${COOKIE}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${age}`);
}

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

module.exports = async function handler(req, res) {
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return send(res, 405, { error: "POST만 받아요." }); }
  if (!process.env.DATABASE_URL || !process.env.SESSION_SECRET) {
    return send(res, 503, { error: "회원 기능이 아직 연결되지 않았어요. (관리자: DATABASE_URL, SESSION_SECRET 설정 필요)", code: "not_configured" });
  }
  if (!sameOrigin(req)) return send(res, 403, { error: "허용되지 않은 요청이에요." });

  let body;
  try { body = await readBody(req); } catch { return send(res, 400, { error: "요청 형식이 올바르지 않아요." }); }
  const action = String(body.action || "");
  const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim();
  if ((action === "login" || action === "signup") && tooMany(ip)) return send(res, 429, { error: "시도가 너무 많아요. 1분 뒤에 다시 해 주세요." });

  try {
    const pg = await db();

    if (action === "signup") {
      const name = String(body.name || "").trim(), email = String(body.email || "").trim().toLowerCase(), pw = body.password;
      if (!name || name.length > 20) return send(res, 400, { error: "이름은 1~20자로 적어 주세요.", field: "name" });
      if (!EMAIL.test(email)) return send(res, 400, { error: "이메일 형식을 확인해 주세요.", field: "email" });
      const p = pwProblem(pw); if (p) return send(res, 400, { error: p, field: "password" });
      if (body.agree !== true) return send(res, 400, { error: "개인정보 수집·이용에 동의해 주세요.", field: "agree" });
      const r = await pg.query(
        "INSERT INTO tc_users (email, name, pass_hash, watch) VALUES ($1, $2, $3, $4) ON CONFLICT (email) DO NOTHING RETURNING id, email, name, watch",
        [email, name, await hashPw(pw), JSON.stringify(cleanWatch(body.watch))]);
      if (!r.rows[0]) return send(res, 409, { error: "이미 가입된 이메일이에요. 로그인해 주세요.", field: "email" });
      setCookie(res, makeToken(r.rows[0], 7), 7);
      console.log(JSON.stringify({ tag: "[auth]", event: "signup", uid: r.rows[0].id, at: new Date().toISOString() }));
      return send(res, 200, { user: publicUser(r.rows[0]) });
    }

    if (action === "login") {
      const email = String(body.email || "").trim().toLowerCase(), pw = String(body.password || "");
      const r = await pg.query("SELECT id, email, name, pass_hash, watch FROM tc_users WHERE email = $1", [email]);
      const u = r.rows[0];
      const ok = await checkPw(pw, u ? u.pass_hash : DUMMY);
      if (!u || !ok) return send(res, 401, { error: "이메일 또는 비밀번호가 맞지 않아요." });
      const days = body.remember ? 30 : 1;
      await pg.query("UPDATE tc_users SET last_login = now() WHERE id = $1", [u.id]);
      setCookie(res, makeToken(u, days), days);
      return send(res, 200, { user: publicUser(u) });
    }

    if (action === "logout") { setCookie(res, "", 0); return send(res, 200, { ok: true }); }

    const tok = readToken(req);
    if (!tok) return send(res, 401, { error: "로그인이 필요해요." });
    if (action === "me") {
      const r = await pg.query("SELECT email, name, watch FROM tc_users WHERE id = $1", [tok.uid]);
      return r.rows[0] ? send(res, 200, { user: publicUser(r.rows[0]) }) : send(res, 401, { error: "로그인이 필요해요." });
    }
    if (action === "watch") {
      const r = await pg.query("UPDATE tc_users SET watch = $2 WHERE id = $1 RETURNING email, name, watch", [tok.uid, JSON.stringify(cleanWatch(body.watch))]);
      return send(res, 200, { user: publicUser(r.rows[0]) });
    }
    return send(res, 400, { error: "알 수 없는 요청이에요." });
  } catch (e) {
    console.error("[auth-error] " + JSON.stringify({ at: new Date().toISOString(), action, message: String(e.message).slice(0, 200) }));
    return send(res, 500, { error: "잠시 문제가 생겼어요. 조금 뒤 다시 시도해 주세요." });
  }
};
