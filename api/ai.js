// AI 분석: 브라우저 → /api/ai → Eyefeet AI(깃솔트 로컬 AI) → 정해진 JSON 형식으로 정리해 돌려줘요.
// API 키와 AI 주소는 서버 환경변수에만 있고, 브라우저에는 절대 내려가지 않아요.
//
// 작업(task)
//   hs      품목 설명 → HS 6자리 후보 최대 3개 + 확인 질문      (HS코드 페이지 "AI에게 물어보기")
//   sector  섹터 정보·관련 이슈 → 영향 분석                       (대시보드 섹터 상세)
//   ask     오늘 브리핑 내용 + 질문 → 답변                        (대시보드 "AI에게 묻기")
//
// 환경변수 (Eyefeet Cloud 테넌트 설정에 넣기)
//   AI_BASE_URL   AI 호출 주소. Eyefeet AI는 https://www.eyefeetai.com/api/chat/completions
//                 (…/chat/completions 로 끝나는 전체 주소면 그대로 쓰고, 서버 주소만 넣으면 /v1/chat/completions 를 붙여요)
//   AI_API_KEY    eyefeetai.com 설정 → 계정 → API 키에서 발급
//   AI_MODEL      사용할 모델 이름 (선택, 기본값 qwen3-30b-a3b)
//   AI_API_STYLE  "openai"(기본) 또는 "ollama"(/api/chat)

const MAX_INPUT = 400;             // 품목 설명·질문 최대 글자 수
const MAX_CONTEXT = 6000;          // 화면에서 넘겨주는 참고 자료 최대 글자 수
const LIMIT_PER_MIN = 6;           // IP당 분당 호출 수
const TIMEOUT_MS = 45_000;         // 로컬 AI는 응답이 느릴 수 있어요
const hits = new Map();

const JSON_ONLY = "반드시 아래 JSON만 출력하세요. 앞뒤 설명 문장이나 코드 블록 표시는 쓰지 마세요.";

// 자주 틀리는 류의 분류 기준. 모델이 이 기준과 어긋난 후보를 내지 않도록 함께 보내요.
const HS_GUIDE = `[분류 기준표]
- 신발(64류): 겉감(갑피) 재질로 호를 정함. 갑피 섬유(메시·캔버스) → 6404 (밑창 고무·플라스틱이면 운동화 6404.11, 그 밖 6404.19, 밑창 가죽 6404.20). 갑피 천연가죽 → 6403. 갑피·밑창 모두 고무·플라스틱 → 6402. 6401은 바느질·접착 없이 성형한 방수 신발에만 씀.
- 의류: 니트·저지 같은 편물 → 61류, 직물 → 62류. 섬유는 무게 기준 가장 많은 것. 티셔츠 6109(면 6109.10, 그 밖 6109.90), 스웨터 6110, 남성 바지 6203.4x·여성 6204.6x(편물은 6103.4x·6104.6x).
- 가방·지갑(4202): 겉면 재질로 정함. 천연가죽 1, 플라스틱 시트(인조가죽 PU 포함)·섬유 2, 그 밖 9. 핸드백 4202.2x, 지갑 4202.3x, 배낭·여행가방 등 4202.9x.
- 전자: 스마트폰 8517.13, 스마트워치(통신 기능) 8517.62, 노트북 8471.30, 키보드·마우스 8471.60, 모니터 8528.52(TV 튜너 있으면 8528.72), 이어폰·헤드폰 8518.30, 리튬이온 보조배터리 8507.60, 충전기·어댑터 8504.40, USB 케이블 8544.42, LED 전구 8539.52, 게임기 9504.50.
- 식품: 코코아 함유 → 18류(판초콜릿 1806.3x), 코코아 없는 사탕·젤리 1704.90, 껌 1704.10, 볶은 커피 0901.21, 인스턴트 커피 2101.11(믹스 2101.12), 라면 1902.30, 단맛 비스킷 1905.31, 조미김 2008.99, 마른김 1212.21.
- 화장품: 향수 3303, 기초·색조 3304, 샴푸 3305.10, 치약 3306.10, 화장비누 3401.11.
- 그 밖: 장난감·인형 9503, 자전거 8712(전기자전거 8711.60), 가구 9403, 보온병 9617, 장신구 7113(도금한 것은 7117).
- 부품은 어느 기계 전용인지에 따라 그 기계의 부분품 호로, 나사·볼트 같은 범용 부품은 재질 쪽 호로 감.`;

const TASKS = {
  hs: {
    max: 700,
    system: `당신은 한국 수출입 품목분류(HS 코드)를 돕는 보조자입니다.
사용자가 설명한 물건에 대해 HS 2022 기준 6자리 후보를 최대 3개까지 제시하세요.
아래 기준표와 통칙(본질적 특성, 주된 재질·용도)에 맞춰 판단하고, 기준표와 어긋난 코드는 후보로 내지 마세요.
확신이 낮으면 confidence를 low로 하고, 코드를 바꿀 수 있는 확인 질문을 최대 3개 제시하세요.
${HS_GUIDE}
${JSON_ONLY}
{"candidates":[{"hs6":"6자리 숫자","name":"품목명(한국어)","why":"한 문장 근거","confidence":"high|medium|low"}],"questions":["확인 질문"],"caution":"주의할 점 한 문장"}`,
    user: (q) => `품목 설명: ${q}`,
    parse: (j) => ({
      candidates: arr(j.candidates).map(c => ({
        hs6: str(c.hs6, 20).replace(/\D/g, "").slice(0, 6),
        name: str(c.name, 80), why: str(c.why, 300),
        confidence: ["high", "medium", "low"].includes(c.confidence) ? c.confidence : "low",
      })).filter(c => /^\d{6}$/.test(c.hs6)).slice(0, 3),
      questions: arr(j.questions).map(q => str(q, 200)).slice(0, 3),
      caution: str(j.caution, 300),
    }),
  },
  sector: {
    max: 900,
    system: `당신은 한국 수출입 기업을 돕는 무역 애널리스트입니다.
주어진 섹터 정보와 관련 이슈만 근거로, 이 섹터의 국내 기업이 받을 영향을 정리하세요.
자료에 없는 수치나 사실은 만들지 말고, 투자 권유 표현은 쓰지 마세요. 문장은 짧고 쉬운 한국어로 쓰세요.
${JSON_ONLY}
{"summary":"두 문장 이내 요약","impacts":[{"who":"영향받는 기업 유형","effect":"영향 한 문장","direction":"positive|negative|mixed"}],"watch":["앞으로 지켜볼 점"],"actions":["수출입 실무자가 지금 확인할 일"]}`,
    user: (q, ctx) => `[섹터 자료]\n${ctx}`,
    parse: (j) => ({
      summary: str(j.summary, 400),
      impacts: arr(j.impacts).map(i => ({
        who: str(i.who, 60), effect: str(i.effect, 200),
        direction: ["positive", "negative", "mixed"].includes(i.direction) ? i.direction : "mixed",
      })).filter(i => i.who && i.effect).slice(0, 4),
      watch: arr(j.watch).map(w => str(w, 160)).slice(0, 3),
      actions: arr(j.actions).map(a => str(a, 160)).slice(0, 3),
    }),
  },
  ask: {
    max: 800,
    system: `당신은 한국 수출입 기업을 돕는 무역 애널리스트입니다.
아래 오늘의 브리핑 자료만 근거로 사용자의 질문에 답하세요. 자료에 없는 내용은 "오늘 자료에는 없어요"라고 말하고 추측하지 마세요.
투자 권유 표현은 쓰지 말고, 짧고 쉬운 한국어로 답하세요.
${JSON_ONLY}
{"answer":"세 문장 이내 답변","points":["근거가 된 자료 속 이슈나 섹터"],"next":["추가로 확인하면 좋은 것"]}`,
    user: (q, ctx) => `[오늘의 브리핑 자료]\n${ctx}\n\n[질문]\n${q}`,
    parse: (j) => ({
      answer: str(j.answer, 600),
      points: arr(j.points).map(p => str(p, 160)).slice(0, 4),
      next: arr(j.next).map(n => str(n, 160)).slice(0, 3),
    }),
  },
};

const arr = (v) => (Array.isArray(v) ? v : []);
const str = (v, n) => (v == null ? "" : String(v)).slice(0, n);

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
    req.on("data", c => { d += c; if (d.length > 16_384) reject(new Error("too large")); });
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

// AI 응답에서 JSON만 꺼내요 (Qwen3 등이 앞에 붙이는 <think>…</think>와 앞뒤 문장은 버려요)
function extractJson(text) {
  const clean = String(text).replace(/<think>[\s\S]*?<\/think>/gi, "");
  const m = clean.match(/\{[\s\S]*\}/);
  if (!m) throw new Error("no json");
  return JSON.parse(m[0]);
}
const parseResult = (text, task = "hs") => TASKS[task].parse(extractJson(text));

async function callAI(system, user, maxTokens) {
  const base = (process.env.AI_BASE_URL || "").replace(/\/+$/, "");
  const style = (process.env.AI_API_STYLE || "openai").toLowerCase();
  const model = process.env.AI_MODEL || "qwen3-30b-a3b";   // 비어 있으면 Eyefeet AI 안내의 기본 모델
  const headers = { "Content-Type": "application/json" };
  if (process.env.AI_API_KEY) headers.Authorization = `Bearer ${process.env.AI_API_KEY}`;
  // "/no_think": Qwen3 계열에서 생각 과정을 건너뛰어 빠르게 답하게 해요 (다른 모델은 무시)
  const messages = [{ role: "system", content: system }, { role: "user", content: `${user} /no_think` }];

  const url = /\/chat\/completions$|\/api\/chat$/.test(base) ? base
    : style === "ollama" ? `${base}/api/chat` : `${base}/v1/chat/completions`;
  const body = style === "ollama"
    ? { model, messages, stream: false, format: "json", options: { temperature: 0.2 } }
    : { model, messages, stream: false, temperature: 0.2, max_tokens: maxTokens };

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

  let body;
  try { body = await readBody(req); }
  catch { return send(res, 400, { error: "요청 형식이 올바르지 않아요." }); }

  const taskName = TASKS[body.task] ? body.task : "hs";
  const task = TASKS[taskName];
  const query = String(body.query || "").trim();
  const context = typeof body.context === "string" ? body.context : JSON.stringify(body.context || "");
  if (taskName !== "sector" && query.length < 2) return send(res, 400, { error: "두 글자 이상 입력해 주세요." });
  if (query.length > MAX_INPUT) return send(res, 400, { error: `${MAX_INPUT}자까지 쓸 수 있어요.` });
  if (taskName !== "hs" && context.length < 10) return send(res, 400, { error: "분석할 자료가 없어요." });
  if (context.length > MAX_CONTEXT) return send(res, 400, { error: "분석할 자료가 너무 길어요." });

  try {
    const text = await callAI(task.system, task.user(query, context), task.max);
    return send(res, 200, task.parse(extractJson(text)));
  } catch (e) {
    console.error("[ai-error] " + JSON.stringify({ at: new Date().toISOString(), task: taskName, message: String(e.message).slice(0, 200) }));
    const timeout = e.name === "AbortError";
    return send(res, 502, { error: timeout ? "AI 응답이 늦어요. 잠시 뒤 다시 시도해 주세요." : "AI 응답을 받지 못했어요. 잠시 뒤 다시 시도해 주세요." });
  }
};

module.exports.parseResult = parseResult;
