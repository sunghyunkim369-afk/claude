// (선택) Eyefeet AI로 기사 분류를 다듬어요. GitHub Secrets 에 AI_API_KEY 가 없으면 아무것도 하지 않아요.
// 단어 규칙 분류보다 정확하게: 주제, 관련 섹터, 조치 방향, 한국 관련도, 한 줄 요약
const { TOPICS, SECTORS } = require("./config");

const BASE = process.env.AI_BASE_URL || "https://www.eyefeetai.com/api/chat/completions";
const MODEL = process.env.AI_MODEL || "qwen3-30b-a3b";
const TOPIC_IDS = TOPICS.map(t => t.id), SECTOR_IDS = SECTORS.map(s => s.id);

const SYSTEM = `당신은 한국 수출입 기업을 위한 무역 뉴스 분류기입니다. 각 기사에 대해 아래 값을 고르세요.
- topic: ${TOPICS.map(t => `${t.id}(${t.label})`).join(", ")} 중 하나
- sectors: ${SECTORS.map(s => `${s.id}(${s.name})`).join(", ")} 중 0~2개
- direction: 무역 조치가 강화되면 "up", 완화되면 "down", 조치와 무관하거나 판단 어려우면 "neutral"
- relevance: 한국 기업·수출입에 직접 관련 있으면 1, 간접이면 0.5, 거의 없으면 0
- summary: 한국 수출입 기업 관점의 한 문장 요약 (60자 이내, 기사에 없는 내용 금지)
반드시 JSON만 출력: {"items":[{"i":0,"topic":"...","sectors":["..."],"direction":"up|down|neutral","relevance":1,"summary":"..."}]}`;

async function call(items) {
  const user = items.map((it, i) => `${i}. [${it.source}] ${it.title}${it.desc ? ` — ${it.desc.slice(0, 160)}` : ""}`).join("\n");
  const r = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.AI_API_KEY}` },
    body: JSON.stringify({ model: MODEL, stream: false, temperature: 0.1, max_tokens: 1800,
      messages: [{ role: "system", content: SYSTEM }, { role: "user", content: `${user} /no_think` }] }),
  });
  if (!r.ok) throw new Error(`AI HTTP ${r.status}`);
  const text = (await r.json()).choices?.[0]?.message?.content || "";
  const m = text.replace(/<think>[\s\S]*?<\/think>/gi, "").match(/\{[\s\S]*\}/);
  return m ? JSON.parse(m[0]).items || [] : [];
}

// 반환: AI가 분류한 기사 수
async function enrich(items, batch = 10) {
  if (!process.env.AI_API_KEY || !items.length) return 0;
  let done = 0;
  for (let k = 0; k < items.length && k < 300; k += batch) {
    const chunk = items.slice(k, k + batch);
    try {
      for (const res of await call(chunk)) {
        const it = chunk[res.i];
        if (!it) continue;
        if (TOPIC_IDS.includes(res.topic)) { it.topic = res.topic; it.topics = [res.topic, ...it.topics.filter(t => t !== res.topic)].slice(0, 2); }
        if (Array.isArray(res.sectors)) it.sectors = res.sectors.filter(s => SECTOR_IDS.includes(s)).slice(0, 2);
        if (["up", "down", "neutral"].includes(res.direction)) it.direction = res.direction;
        if (typeof res.relevance === "number") it.relevance = Math.max(0, Math.min(1, res.relevance));
        if (typeof res.summary === "string" && res.summary.trim()) it.summary = res.summary.trim().slice(0, 120);
        it.ai = true; done++;
      }
    } catch (e) { console.log(`AI 분류 건너뜀 (${e.message})`); }
  }
  return done;
}

// 이번 주 요약 한 문단 (없으면 null → 규칙으로 만든 요약 사용)
async function weeklyBrief(issues) {
  if (!process.env.AI_API_KEY || !issues.length) return null;
  const sys = `당신은 한국 수출입 기업을 위한 무역 애널리스트입니다. 주어진 이번 주 이슈만 근거로 쓰세요. 없는 사실·수치는 만들지 마세요.
반드시 JSON만 출력: {"headline":"이번 주 핵심 한 문장(40자 이내)","points":["핵심 포인트 한 문장","...","..."]}`;
  const user = issues.slice(0, 6).map(x => `- ${x.keyword} (점수 ${x.score}, 보도 ${x.reports}건, 직전 ${x.prev}건): ${x.title}`).join("\n");
  try {
    const r = await fetch(BASE, { method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.AI_API_KEY}` },
      body: JSON.stringify({ model: MODEL, stream: false, temperature: 0.2, max_tokens: 500,
        messages: [{ role: "system", content: sys }, { role: "user", content: `${user} /no_think` }] }) });
    const text = (await r.json()).choices?.[0]?.message?.content || "";
    const j = JSON.parse(text.replace(/<think>[\s\S]*?<\/think>/gi, "").match(/\{[\s\S]*\}/)[0]);
    if (typeof j.headline === "string" && Array.isArray(j.points)) return { headline: j.headline.slice(0, 80), points: j.points.slice(0, 3).map(p => String(p).slice(0, 120)) };
  } catch (e) { console.log(`AI 요약 건너뜀 (${e.message})`); }
  return null;
}

module.exports = { enrich, weeklyBrief };
