// 브라우저 에러 수집: 페이지(error-logger.js)가 보낸 에러를 서버 로그에 한 줄 JSON으로 남겨요.
// Eyefeet Cloud(vercel 런타임)의 로그 화면에서 "[client-error]"로 검색하면 모아 볼 수 있어요.

const MAX_BODY = 8 * 1024;          // 요청 1건 최대 크기
const LIMIT_PER_MIN = 30;           // IP당 분당 최대 기록 수 (로그 폭주 방지)
const hits = new Map();             // 인스턴스 메모리 기준의 간단한 제한

const clip = (v, n) => (typeof v === "string" ? v.slice(0, n) : v == null ? undefined : String(v).slice(0, n));

function tooMany(ip) {
  const now = Date.now();
  const h = hits.get(ip) || { t: now, n: 0 };
  if (now - h.t > 60_000) { h.t = now; h.n = 0; }
  h.n += 1;
  hits.set(ip, h);
  if (hits.size > 5000) hits.clear();
  return h.n > LIMIT_PER_MIN;
}

function readBody(req) {
  if (req.body && typeof req.body === "object") return Promise.resolve(req.body);
  if (typeof req.body === "string") return Promise.resolve(JSON.parse(req.body));
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", c => { data += c; if (data.length > MAX_BODY) reject(new Error("too large")); });
    req.on("end", () => { try { resolve(JSON.parse(data || "{}")); } catch (e) { reject(e); } });
    req.on("error", reject);
  });
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.setHeader("Allow", "POST");
    return res.end();
  }
  const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim();
  if (tooMany(ip)) { res.statusCode = 429; return res.end(); }

  let body;
  try { body = await readBody(req); }
  catch { res.statusCode = 400; return res.end(); }

  const items = Array.isArray(body.errors) ? body.errors.slice(0, 10) : [body];
  for (const e of items) {
    // 개인정보가 담길 수 있는 값은 남기지 않고, 길이를 잘라서 기록해요.
    console.error("[client-error] " + JSON.stringify({
      at: new Date().toISOString(),
      type: clip(e.type, 30),
      message: clip(e.message, 500),
      source: clip(e.source, 300),
      line: Number(e.line) || undefined,
      col: Number(e.col) || undefined,
      stack: clip(e.stack, 2000),
      page: clip(e.page, 300),
      ua: clip(req.headers["user-agent"], 200),
    }));
  }
  res.statusCode = 204;
  res.end();
};
