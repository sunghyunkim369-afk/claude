// 영문 기사 제목 한국어 번역 (서버 내부 모듈 — "_" 로 시작해서 주소로 열리지 않아요)
// /api/data, /api/breaking 이 화면에 보내기 직전에 lang:"en" 이고 아직 번역이 없는 제목을 AI로 번역해 붙여요.
// - 번역한 제목은 메모리에 기억해서 같은 제목은 다시 번역하지 않아요 (서버가 다시 시작되면 처음부터)
// - AI 가 없거나 느리면(기본 6초) 번역 없이 원문 제목을 그대로 보내요. 다음 요청 때 이어서 번역해요.
// - 화면에서는 번역을 크게, 원문 제목을 작게("원문: …") 보여줘요.
const ai = require("./ai");
const metrics = require("./_metrics");

const CACHE = globalThis.__tcTitleKo || (globalThis.__tcTitleKo = new Map());
const MAX_CACHE = 3000, BATCH = 20, BUDGET_MS = 6000;
let inflight = null;

const SYSTEM = `당신은 무역 뉴스 편집자입니다. 영어 기사 제목을 한국어 기사 제목으로 번역하세요.
- 뜻만 옮기고 내용을 더하거나 빼지 마세요. 고유명사·숫자·단위는 그대로 두세요.
- 각 제목은 50자 이내의 자연스러운 한국어 제목으로.
- 입력 순서 그대로, JSON만 출력: {"t":["번역1","번역2",...]}`;

async function translateBatch(titles) {
  const text = await ai.callAI(SYSTEM, titles.map((t, i) => `${i + 1}. ${t}`).join("\n"), 1200);
  const m = String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").match(/\{[\s\S]*\}/);
  const out = m ? JSON.parse(m[0]).t : null;
  if (!Array.isArray(out) || out.length !== titles.length) throw new Error("번역 개수가 맞지 않아요");
  titles.forEach((t, i) => {
    const ko = typeof out[i] === "string" ? out[i].trim().slice(0, 90) : "";
    if (/[가-힣]/.test(ko)) CACHE.set(t, ko);           // 한글이 없는 답은 버려요
  });
  if (CACHE.size > MAX_CACHE) [...CACHE.keys()].slice(0, CACHE.size - MAX_CACHE).forEach(k => CACHE.delete(k));
}

// 데이터 안의 영문 기사(제목 필드를 가진 객체)를 모두 찾아요
function collect(node, found = []) {
  if (Array.isArray(node)) node.forEach(n => collect(n, found));
  else if (node && typeof node === "object") {
    if (node.lang === "en" && typeof node.title === "string" && !node.orig) found.push(node);
    for (const k of Object.keys(node)) if (node[k] && typeof node[k] === "object") collect(node[k], found);
  }
  return found;
}

// data(JSON 객체)를 고쳐서 돌려줘요. 원본 캐시를 건드리지 않게 복사본에 적용하세요.
async function localize(data, { budgetMs = BUDGET_MS } = {}) {
  if (!process.env.AI_BASE_URL) return data;
  const items = collect(data);
  const todo = [...new Set(items.map(n => n.title).filter(t => !CACHE.has(t)))];
  if (todo.length) {
    if (!inflight) {
      inflight = (async () => {
        for (let i = 0; i < todo.length; i += BATCH) {
          try { await translateBatch(todo.slice(i, i + BATCH)); }
          catch (e) { metrics.record("translate", e.message); break; }
        }
      })().finally(() => { inflight = null; });
    }
    await Promise.race([inflight, new Promise(r => setTimeout(r, budgetMs))]);
  }
  for (const n of items) {
    const ko = CACHE.get(n.title);
    if (ko) { n.orig = n.title; n.title = ko; }
  }
  return data;
}

module.exports = { localize, _cache: CACHE };
