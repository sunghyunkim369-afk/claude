// 뉴스 수집: 공식·유명 출처에서 무역 관련 기사를 모아 data/news/archive.json 에 쌓아요.
// 실행: node scripts/news/collect.js   (6시간마다 자동 실행)
// 출처는 config.js FEEDS 중 enabled 인 RSS 만. Google 뉴스 검색과 과거 채우기(--backfill)는 2026-10-06 삭제 (docs/sources.md)
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { FEEDS, SCORE } = require("./config");
const { fetchText, parseFeed, isTrade, finalize, shingles, jaccard, sleep } = require("./lib");
const { enrich } = require("./ai");

const ARCHIVE = path.join(__dirname, "..", "..", "data", "news", "archive.json");
const DAY = 86_400_000;

const load = () => { try { return JSON.parse(fs.readFileSync(ARCHIVE, "utf8")); } catch { return { items: [] }; } };
const toISO = (d) => { const t = new Date(d); return isNaN(t) ? new Date().toISOString() : t.toISOString(); };
async function fromFeeds() {
  const out = [], report = [];
  for (const f of FEEDS) {
    if (f.enabled === false) { report.push(`${f.id}: 꺼져 있음 (건너뜀)`); continue; }
    const items = parseFeed(await fetchText(f.url));
    const kept = items.filter(i => !(f.skip && f.skip.test(i.title)) && (f.all || isTrade(`${i.title} ${i.desc}`.toLowerCase())));
    report.push(`${f.id}: ${items.length}건 중 무역 관련 ${kept.length}건`);
    for (const i of kept) out.push({ ...i, source: f.name, tier: f.tier, official: !!f.official, lang: f.lang || "ko", feed: f.id, countryHint: f.country });
    await sleep(2000);   // 같은 곳에 보내는 요청 사이 2초 이상 (팀 규칙 C4)
  }
  return { out, report };
}

async function main() {
  const archive = load();
  const items = archive.items;
  const byLink = new Set(items.map(i => i.link));
  const recent = items.filter(i => Date.now() - Date.parse(i.date) < 45 * DAY).map(i => ({ i, sh: shingles(i.title) }));

  const a = await fromFeeds();
  let added = 0, merged = 0;
  for (const raw of a.out) {
    if (byLink.has(raw.link)) continue;
    byLink.add(raw.link);
    const date = toISO(raw.date);
    const sh = shingles(raw.title);
    // 같은 사건을 다룬 기사(제목 유사 + 3일 이내)는 새로 넣지 않고 보도 언론사만 추가해요
    const dup = recent.find(r => Math.abs(Date.parse(r.i.date) - Date.parse(date)) < 3 * DAY && jaccard(r.sh, sh) >= SCORE.dupJaccard);
    if (dup) {
      const o = dup.i.outlets || (dup.i.outlets = [dup.i.source]);
      if (!o.includes(raw.source)) o.push(raw.source);
      if (raw.tier > dup.i.tier) dup.i.tier = raw.tier;
      merged++; continue;
    }
    const c = finalize(raw, raw.hint);
    if (!c) continue;                              // 무역 기사가 아니면 버려요
    const item = {
      id: crypto.createHash("sha1").update(raw.link).digest("hex").slice(0, 12),
      title: raw.title.slice(0, 200), link: raw.link, source: raw.source, outlets: [raw.source],
      tier: raw.tier, official: raw.official, lang: raw.lang, date, desc: raw.desc.slice(0, 200), feed: raw.feed, hint: raw.hint,
      ...(raw.countryHint ? { countryHint: raw.countryHint } : {}),
      ...c, collectedAt: new Date().toISOString(),
    };
    items.push(item); recent.push({ i: item, sh }); added++;
  }

  // AI 키가 있으면 아직 AI가 보지 않은 기사를 다시 분류해요 (없으면 단어 규칙 분류만 사용)
  const ai = await enrich(items.filter(i => !i.ai && Date.now() - Date.parse(i.date) < 9 * DAY));

  const keep = items.filter(i => Date.now() - Date.parse(i.date) < SCORE.archiveDays * DAY)
    .sort((x, y) => Date.parse(y.date) - Date.parse(x.date));
  fs.mkdirSync(path.dirname(ARCHIVE), { recursive: true });
  // 한 줄에 기사 하나: 용량을 줄이면서 git diff 는 읽을 수 있게
  fs.writeFileSync(ARCHIVE, `{"updated":${JSON.stringify(new Date().toISOString())},"items":[\n${keep.map(i => JSON.stringify(i)).join(",\n")}\n]}\n`);

  console.log(a.report.join("\n"));
  console.log(`새 기사 ${added}건 · 중복으로 묶음 ${merged}건 · AI 분류 ${ai}건 · 보관 ${keep.length}건`);
}

main().catch(e => { console.error(e); process.exit(1); });
