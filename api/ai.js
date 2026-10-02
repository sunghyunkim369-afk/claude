// AI 분석: 브라우저 → /api/ai → Eyefeet AI(깃솔트 로컬 AI) → 정해진 JSON 형식으로 정리해 돌려줘요.
// API 키와 AI 주소는 서버 환경변수에만 있고, 브라우저에는 절대 내려가지 않아요.
//
// 작업(task)
//   hs      품목 설명 → HS 6자리 후보 최대 3개 + 확인 질문      (HS코드 페이지 "AI에게 물어보기")
//   sector  섹터 정보·관련 이슈 → 영향 분석                       (대시보드 섹터 상세)
//   ask     오늘 브리핑 내용 + 질문 → 답변                        (대시보드 "AI에게 묻기")
//   travel  여행 쇼핑 목록(자유 글) → 계산기 입력 항목            (여행자 면세 계산기 "AI로 한 번에 입력")
//   customs 사려는 제품 설명 + 반입 기준표 → 해당 기준·확인할 점   (직구 반입 체커 "AI에게 물어보기")
//   track   통관 상태 문구·문자 + 단계 설명 → 뜻·할 일·사칭 의심   (통관 진행 조회 "문자 해석")
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
  travel: {
    max: 900, maxInput: 600, noContext: true,
    system: `당신은 해외여행 귀국자의 쇼핑 목록을 세관 면세 계산기 입력값으로 정리하는 보조자입니다.
사용자가 쓴 글에서 물건마다 종류·수량·가격을 뽑으세요. 수량이 여러 개면 가격은 합계로 계산하세요(단가 × 수량).
가격은 글에 쓴 통화 그대로 두고 currency 에 USD, KRW, JPY, EUR, CNY, GBP, THB, VND, TWD, HKD, SGD 중 하나를 쓰세요. 통화가 없으면 USD.
cat: gen(전자제품·화장품·잡화·식품 등 일반), cloth(옷·신발), fur(모피), deer(녹용), lux(개당 200만 원이 넘는 보석·고급 시계·명품 가방).
술은 liquor 에 type: wine(와인), spirit(위스키·브랜디·보드카·진·럼·데킬라), kaoliang(고량주), other(맥주·사케·소주·리큐어 등), ml 은 전체 용량(병 수 × 병 용량, 모르면 0).
향수는 perfume(전체 ml 와 가격), 담배는 tobacco(궐련 개비 수, 1보루=200).
글에 없는 물건이나 가격은 만들지 말고, 모호한 점은 notes 에 짧게 쓰세요.
${JSON_ONLY}
{"items":[{"name":"물건 이름","cat":"gen","price":0,"currency":"USD"}],"liquor":[{"name":"술 이름","type":"spirit","ml":0,"price":0,"currency":"USD"}],"perfume":{"ml":0,"price":0,"currency":"USD"},"tobacco":0,"notes":["확인할 점"]}`,
    user: (q) => `[쇼핑 목록]\n${q}`,
    parse: (j) => {
      const CUR = ["USD", "KRW", "JPY", "EUR", "CNY", "GBP", "THB", "VND", "TWD", "HKD", "SGD"];
      const cur = (c) => CUR.includes(String(c).toUpperCase()) ? String(c).toUpperCase() : "USD";
      const n = (v) => { const x = Number(String(v).replace(/[^\d.]/g, "")); return isFinite(x) && x > 0 ? Math.round(x * 100) / 100 : 0; };
      return {
        items: arr(j.items).map(i => ({ name: str(i.name, 60), cat: ["gen", "cloth", "fur", "deer", "lux"].includes(i.cat) ? i.cat : "gen", price: n(i.price), currency: cur(i.currency) })).filter(i => i.price).slice(0, 15),
        liquor: arr(j.liquor).map(l => ({ name: str(l.name, 60), type: ["wine", "spirit", "kaoliang", "other"].includes(l.type) ? l.type : "other", ml: n(l.ml), price: n(l.price), currency: cur(l.currency) })).filter(l => l.price || l.ml).slice(0, 8),
        perfume: j.perfume && typeof j.perfume === "object" ? { ml: n(j.perfume.ml), price: n(j.perfume.price), currency: cur(j.perfume.currency) } : { ml: 0, price: 0, currency: "USD" },
        tobacco: n(j.tobacco),
        notes: arr(j.notes).map(x => str(x, 160)).slice(0, 4),
      };
    },
  },
  customs: {
    max: 700, maxInput: 300,
    system: `당신은 한국 해외직구 통관 기준을 안내하는 보조자입니다.
사용자가 사려는 제품이 아래 [반입 기준표]의 어느 항목에 해당하는지 고르고, 기준표 내용만 근거로 설명하세요.
기준표에 없는 규정·수치는 만들지 마세요. 해당 항목이 없으면 match 를 비우고 verdict 를 check 로 하세요.
성분 때문에 막힐 수 있는 제품(영양제·보조제·의약품)은 확인할 성분을 ingredients 에 쓰세요(제품에 흔히 들어 있다고 알려진 것만).
${JSON_ONLY}
{"match":["기준표 id"],"verdict":"ok|limit|ban|check","reason":"두 문장 이내 설명","checks":["사기 전에 확인할 일"],"ingredients":["확인할 성분"]}`,
    user: (q, ctx) => `[반입 기준표]\n${ctx}\n\n[사려는 제품]\n${q}`,
    parse: (j) => ({
      match: arr(j.match).map(m => str(m, 30)).slice(0, 3),
      verdict: ["ok", "limit", "ban", "check"].includes(j.verdict) ? j.verdict : "check",
      reason: str(j.reason, 400),
      checks: arr(j.checks).map(c => str(c, 160)).slice(0, 3),
      ingredients: arr(j.ingredients).map(c => str(c, 40)).slice(0, 5),
    }),
  },
  track: {
    max: 700, maxInput: 600,
    system: `당신은 해외직구 택배의 통관 진행 문구와 안내 문자를 쉽게 풀어 주는 보조자입니다.
아래 [통관 단계 설명]을 근거로, 사용자가 붙여 넣은 문구가 어느 단계인지, 무슨 뜻인지, 지금 무엇을 하면 되는지 짧게 알려 주세요.
문자에 관세청·유니패스가 아닌 링크로 결제를 요구하거나, 개인정보·카드번호를 묻거나, 급하게 재촉하면 사칭(스미싱) 의심으로 표시하세요.
모르는 내용은 추측하지 말고 관세청 고객지원센터(125)나 배송사 문의를 권하세요.
${JSON_ONLY}
{"stage":"단계 이름 또는 모름","meaning":"두 문장 이내 뜻","actions":["지금 할 일"],"scam":"none|suspect","scam_reason":"의심 이유 한 문장(없으면 빈칸)"}`,
    user: (q, ctx) => `[통관 단계 설명]\n${ctx}\n\n[붙여 넣은 문구]\n${q}`,
    parse: (j) => ({
      stage: str(j.stage, 40), meaning: str(j.meaning, 400),
      actions: arr(j.actions).map(a => str(a, 160)).slice(0, 3),
      scam: j.scam === "suspect" ? "suspect" : "none", scam_reason: str(j.scam_reason, 200),
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
  const maxInput = task.maxInput || MAX_INPUT;
  if (query.length > maxInput) return send(res, 400, { error: `${maxInput}자까지 쓸 수 있어요.` });
  if (taskName !== "hs" && !task.noContext && context.length < 10) return send(res, 400, { error: "분석할 자료가 없어요." });
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
