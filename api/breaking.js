// 24시간 속보: 6시간마다 GitHub 에 올라가는 data/news/breaking.json 을 대신 받아 전달해요 (10분 캐시).
// 실패하면 페이지에 들어 있는 breaking.js 를 그대로 써요. 다른 저장소를 쓰려면 BREAKING_DATA_URL 로 바꿀 수 있어요.
const metrics = require("./_metrics");
const { localize } = require("./_translate");
const SOURCE = process.env.BREAKING_DATA_URL ||
  "https://raw.githubusercontent.com/sunghyunkim369-afk/claude/main/data/news/breaking.json";
const TTL = 10 * 60_000;
let cache = { at: 0, body: null };

module.exports = async function handler(req, res) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  if (req.method !== "GET") { res.statusCode = 405; return res.end('{"error":"GET only"}'); }
  if (!cache.body || Date.now() - cache.at > TTL) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch(SOURCE, { signal: ctrl.signal, headers: { "User-Agent": "TradeCompass" } });
      clearTimeout(t);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const text = await r.text();
      if (text.length > 500_000) throw new Error("too large");
      const j = JSON.parse(text);
      if (!Array.isArray(j.items) || !j.generated) throw new Error("bad shape");
      cache = { at: Date.now(), body: text };
    } catch (e) {
      metrics.record("breaking", e.message);
      if (!cache.body) { res.statusCode = 502; return res.end('{"error":"data unavailable"}'); }
    }
  }
  // 영문 기사 제목은 한국어로 번역해서 보내요 (AI 가 없거나 느리면 원문 그대로)
  let body = cache.body;
  try { body = JSON.stringify(await localize(JSON.parse(cache.body))); } catch (e) { metrics.record("translate", e.message); }
  res.setHeader("Cache-Control", "public, max-age=300");
  res.statusCode = 200;
  res.end(body);
};
