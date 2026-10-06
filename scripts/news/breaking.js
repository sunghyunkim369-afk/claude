// 24시간 속보: 6시간마다 수집 직후 실행해서, 최근 24시간 신뢰 언론사 기사를 최신순으로 내보내요.
//   data/news/breaking.json        eyefeet /api/breaking 이 읽는 파일
//   tradecompass-mvp/breaking.js   GitHub Pages 등 정적 사이트가 바로 읽는 파일
// 실행: node scripts/news/breaking.js   (파일 위치는 NEWS_DATA_DIR·NEWS_WEB_DIR 로 바꿀 수 있어요, paths.js)
const fs = require("fs");
const { TOPICS, SECTORS } = require("./config");
const { breakingFrom, finalize, outletName, stripOutlet, BREAKING_HOURS } = require("./lib");

const { ARCHIVE, BREAKING, BREAKING_JS, write } = require("./paths");
const topicById = Object.fromEntries(TOPICS.map(t => [t.id, t]));
const sectorIds = new Set(SECTORS.map(s => s.id));

const now = Date.now();
const items = JSON.parse(fs.readFileSync(ARCHIVE, "utf8")).items.flatMap(a => {
  // 발행 때와 같은 규칙으로 다시 분류해요 (AI가 분류한 기사는 그대로)
  const base = { ...a, title: stripOutlet(a.title, a.source), source: outletName(a.source) };
  if (a.ai) return [base];
  const g = (a.feed || "").startsWith("gnews:") ? a.feed.slice(6) : "";
  const c = finalize(base, a.hint || (topicById[g] ? g : undefined));
  return c ? [{ ...base, ...c }] : [];
});
const list = breakingFrom(items, now).map(a => ({
  title: a.titleKo || a.title, orig: a.titleKo ? a.title : undefined, lang: a.lang === "en" ? "en" : undefined,
  link: a.link, source: a.source, outlets: (a.outlets || [a.source]).length,
  at: a.date, tag: topicById[a.topic]?.label || "무역", direction: a.direction || "neutral",
  sectors: (a.sectors || []).filter(id => sectorIds.has(id)),
}));
const out = { generated: new Date(now).toISOString(), hours: BREAKING_HOURS, items: list };
write(BREAKING, JSON.stringify(out, null, 1));
write(BREAKING_JS,
  `// 24시간 속보 — scripts/news/breaking.js 가 6시간마다 자동 생성해요. 직접 고치지 마세요.\nwindow.TC_BREAKING = ${JSON.stringify(out, null, 1)};\n`);
console.log(`속보 ${list.length}건 (최근 ${BREAKING_HOURS}시간, 생성 ${out.generated})`);
list.slice(0, 5).forEach(a => console.log(`- ${a.at.slice(5, 16)} ${a.source} · ${a.title}`));
