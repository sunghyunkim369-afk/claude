// AI 품목분류 도우미: 브라우저 → /api/ai → Eyefeet AI(깃솔트 로컬 AI) → 결과
// API 키와 AI 주소는 서버 환경변수에만 있고, 브라우저에는 절대 내려가지 않아요.
//
// 환경변수 (Eyefeet Cloud 테넌트 설정에 넣기)
//   AI_BASE_URL   AI 호출 주소. Eyefeet AI는 https://www.eyefeetai.com/api/chat/completions
//                 (…/chat/completions 로 끝나는 전체 주소면 그대로 쓰고, 서버 주소만 넣으면 /v1/chat/completions 를 붙여요)
//   AI_API_KEY    eyefeetai.com 설정 → 계정 → API 키에서 발급
//   AI_MODEL      사용할 모델 이름 (예: qwen3-30b-a3b)
//   AI_API_STYLE  "openai"(기본) 또는 "ollama"(/api/chat)

const MAX_INPUT = 400;             // 품목 설명 최대 글자 수
const LIMIT_PER_MIN = 6;           // IP당 분당 호출 수
const TIMEOUT_MS = 40_000;        // 로컬 AI는 응답이 느릴 수 있어요
const hits = new Map();

const SYSTEM = `당신은 한국 수출입 품목분류(HS 코드)를 돕는 보조자입니다.
사용자가 설명한 물건에 대해 HS 2022 기준 6자리 후보를 최대 3개까지 제시하세요.
분류는 통칙(특히 본질적 특성, 주된 재질·용도)에 따라 판단하고, 확신이 낮으면 낮다고 밝히세요.
코드를 바꿀 수 있는 핵심 확인 질문을 최대 3개 제시하세요 (예: 주된 재질, 편물/직물, 전기 사용 여부, 용도).
반드시 아래 JSON만 출력하세요. 설명 문장이나 코드 블록 표시는 쓰지 마세요.
{"candidates":[{"hs6":"6자리 숫자","name":"품목명(한국어)","why":"한 문장 근거","confidence":"high|medium|low"}],"questions":["확인 질문"],"caution":"주의할 점 한 문장"}`;

function tooMany(ip) {
  const now = Date.now();
  const h = hits.get(ip) || { t: now, n: 0 };
  if (now - h.t > 60_000) { h.t = now; h.n = 0; }
  h.n += 1; hits.set(ip, h);
  if (hits.size > 5000) hits.clear();
  return h.n > LIMIT_PER_MIN;
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

function send(res, status, obj) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(obj));
}

// AI 응답에서 JSON만 꺼내 형식을 검사해요 (모델이 앞뒤에 글을 붙여도 동작하게)
function parseResult(text) {
  // Qwen3 등은 답 앞에 <think>…</think> 생각 과정을 붙일 수 있어서 먼저 지워요
  const clean = String(text).replace(/<think>[\s\S]*?<\/think>/gi, "");
  const m = clean.match(/\{[\s\S]*\}/);
  if (!m) throw new Error("no json");
  const j = JSON.parse(m[0]);
  const candidates = (Array.isArray(j.candidates) ? j.candidates : [])
    .map(c => ({
      hs6: String(c.hs6 || "").replace(/\D/g, "").slice(0, 6),
      name: String(c.name || "").slice(0, 80),
      why: String(c.why || "").slice(0, 300),
      confidence: ["high", "medium", "low"].includes(c.confidence) ? c.confidence : "low",
    }))
    .filter(c => /^\d{6}$/.test(c.hs6))
    .slice(0, 3);
  const questions = (Array.isArray(j.questions) ? j.questions : []).map(q => String(q).slice(0, 200)).slice(0, 3);
  return { candidates, questions, caution: String(j.caution || "").slice(0, 300) };
}

async function callAI(query) {
  const base = (process.env.AI_BASE_URL || "").replace(/\/+$/, "");
  const style = (process.env.AI_API_STYLE || "openai").toLowerCase();
  const model = process.env.AI_MODEL || "";
  const headers = { "Content-Type": "application/json" };
  if (process.env.AI_API_KEY) headers.Authorization = `Bearer ${process.env.AI_API_KEY}`;
  // "/no_think": Qwen3 계열에서 생각 과정을 건너뛰어 빠르게 답하게 해요 (다른 모델은 무시)
  const messages = [{ role: "system", content: SYSTEM }, { role: "user", content: `품목 설명: ${query} /no_think` }];

  const url = /\/chat\/completions$|\/api\/chat$/.test(base) ? base
    : style === "ollama" ? `${base}/api/chat` : `${base}/v1/chat/completions`;
  const body = style === "ollama"
    ? { model, messages, stream: false, format: "json", options: { temperature: 0.2 } }
    : { model, messages, stream: false, temperature: 0.2, max_tokens: 700 };

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { method: "POST", headers, body: JSON.stringify(body), signal: ctrl.signal });
    if (!r.ok) throw new Error(`AI HTTP ${r.status}`);
    const j = await r.json();
    return style === "ollama" ? j.message?.content : j.choices?.[0]?.message?.content;
  } finally { clearTimeout(timer); }
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return send(res, 405, { error: "POST만 받아요." }); }
  if (!process.env.AI_BASE_URL) return send(res, 503, { error: "AI 서버가 아직 연결되지 않았어요. 관리자에게 AI 환경변수 설정을 요청하세요." });

  const ip = String(req.headers["x-forwarded-for"] || req.socket?.remoteAddress || "").split(",")[0].trim();
  if (tooMany(ip)) return send(res, 429, { error: "요청이 많아요. 1분 뒤에 다시 시도해 주세요." });

  let query;
  try { query = String((await readBody(req)).query || "").trim(); }
  catch { return send(res, 400, { error: "요청 형식이 올바르지 않아요." }); }
  if (query.length < 2) return send(res, 400, { error: "품목을 두 글자 이상 설명해 주세요." });
  if (query.length > MAX_INPUT) return send(res, 400, { error: `설명은 ${MAX_INPUT}자까지 쓸 수 있어요.` });

  try {
    const result = parseResult(await callAI(query));
    return send(res, 200, result);
  } catch (e) {
    console.error("[ai-error] " + JSON.stringify({ at: new Date().toISOString(), message: String(e.message).slice(0, 200) }));
    const timeout = e.name === "AbortError";
    return send(res, 502, { error: timeout ? "AI 응답이 늦어요. 잠시 뒤 다시 시도해 주세요." : "AI 응답을 받지 못했어요. 잠시 뒤 다시 시도해 주세요." });
  }
};

module.exports.parseResult = parseResult;
