// 최신 주간 데이터: GitHub 에 매주 자동으로 올라가는 data/news/latest.json 을 대신 받아 전달해요.
// 그래서 eyefeet 은 다시 배포하지 않아도 새 Top10 을 보여줄 수 있어요. (실패하면 페이지에 들어 있는 data.js 를 그대로 써요)
// 다른 저장소를 쓰려면 환경변수 NEWS_DATA_URL 로 바꿀 수 있어요.

const SOURCE = process.env.NEWS_DATA_URL ||
  "https://raw.githubusercontent.com/sunghyunkim369-afk/claude/main/data/news/latest.json";
const TTL = 10 * 60_000;     // 10분 동안은 받아 둔 것을 다시 써요
const MAX = 2_000_000;
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
      if (text.length > MAX) throw new Error("too large");
      const j = JSON.parse(text);
      if (!j.meta || !Array.isArray(j.issues) || !Array.isArray(j.sectors)) throw new Error("bad shape");
      cache = { at: Date.now(), body: text };
    } catch (e) {
      console.log(JSON.stringify({ tag: "[data-error]", time: new Date().toISOString(), message: String(e.message || e) }));
      if (!cache.body) { res.statusCode = 502; return res.end('{"error":"data unavailable"}'); }
    }
  }
  res.setHeader("Cache-Control", "public, max-age=300");
  res.statusCode = 200;
  res.end(cache.body);
};
