// 점수식 비교 (검증용, 사이트에는 쓰이지 않아요): 기존식(v1)과 제안식(v2)으로 같은 주의 이슈 후보 전체를 다시 매겨 비교해요.
// scripts/news/publish.js 의 점수 계산은 건드리지 않고, 같은 규칙을 여기서 그대로 다시 계산해요.
// 처음에 v1 결과가 발행된 latest.json 과 같은지 확인하고, 다르면 경고를 띄워요.
//
// 실행 예 (2026-10-11 발행 주와 같은 기사로):
//   git show 92366c5:data/news/archive.json > /tmp/archive.json
//   node scripts/score_compare.js --archive /tmp/archive.json --now 2026-10-11T21:59:25.647Z
// 옵션: --archive 기사 보관 파일 (기본 data/news/archive.json) · --now 기준 시각 (기본 latest.json 의 생성 시각)
//       --latest 비교할 발행본 (기본 data/news/latest.json) · --human 사람 순위 (기본 tests/fixtures/human_rank.json)
//       --json 결과를 JSON 으로 저장할 파일 · --write-human-template 후보 20개로 human_rank.json 양식을 새로 써요
// Node 기본 모듈만 써요.
const fs = require("fs");
const path = require("path");
const { TOPICS, COUNTRIES, SCORE, LEAD_MIN_RELEVANCE } = require("./news/config");
const { finalize, outletName, stripOutlet, rankForIssue, isPromo, isOpinion } = require("./news/lib");

const ROOT = path.join(__dirname, "..");
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const flag = (k) => process.argv.includes(`--${k}`);
const LATEST = arg("latest", path.join(ROOT, "data", "news", "latest.json"));
const ARCHIVE = arg("archive", path.join(ROOT, "data", "news", "archive.json"));
const HUMAN = arg("human", path.join(ROOT, "tests", "fixtures", "human_rank.json"));
const latest = fs.existsSync(LATEST) ? JSON.parse(fs.readFileSync(LATEST, "utf8")) : null;
const now = Date.parse(arg("now", latest ? latest.meta.generated : new Date().toISOString()));
const DAY = 86_400_000;

// ── v2 설정 (초기값: 문헌 설계에서 가져온 값이고 실험으로 정한 값이 아니에요) ──
const V2 = { wR: 0.5, wV: 0.3, wD: 0.2, minOutlets: 4, thinPenalty: 0.5 };

const topicById = Object.fromEntries(TOPICS.map(t => [t.id, t]));
const countryById = Object.fromEntries(COUNTRIES.map(c => [c.id, c]));
const round = (x, d = 2) => Math.round(x * 10 ** d) / 10 ** d;
const issueKey = (a) => `${a.topic}|${a.country || ""}`;
const issueName = (key) => { const [t, c] = key.split("|"); return `${c && countryById[c] ? countryById[c].name + " " : ""}${topicById[t] ? topicById[t].label : t}`; };
const weekOf = (a) => Math.floor((now - Date.parse(a.date)) / (SCORE.windowDays * DAY));
const count = (items, keyFn) => { const m = new Map(); for (const a of items) { const k = keyFn(a); if (k) m.set(k, (m.get(k) || 0) + 1); } return m; };
const sumBy = (items, keyFn, valFn) => { const m = new Map(); for (const a of items) { const k = keyFn(a); m.set(k, (m.get(k) || 0) + valFn(a)); } return m; };

// ── publish.js 와 같은 계산 (v1) ──
function weight(a) {
  const age = Math.max(0, (now - Date.parse(a.date)) / DAY);
  const decay = Math.pow(0.5, age / SCORE.halfLifeDays);
  const breadth = Math.min(2, 1 + 0.25 * ((a.outlets || [a.source]).length - 1));
  const rel = SCORE.relevanceFloor + (1 - SCORE.relevanceFloor) * (a.relevance ?? 0.8);
  return a.tier * rel * decay * breadth * (isOpinion(a.title) ? 0.5 : 1);
}
function volume(list) {
  const seen = {};
  return [...list].sort((a, b) => weight(b) - weight(a))
    .reduce((s, a) => { const k = (seen[a.source] = (seen[a.source] || 0) + 1); return s + weight(a) / Math.pow(k, SCORE.outletDecay); }, 0);
}
function shareGrowth(nNow, totalNow, nBase, totalBase, kinds) {
  if (!totalBase) return 0.5;
  const a = SCORE.smoothing;
  return 0.5 + 0.5 * Math.tanh(Math.log(((nNow + a) / (totalNow + a * kinds)) / ((nBase + a) / (totalBase + a * kinds))));
}
function similarity(x, y) {
  const [tx, cx] = x.key.split("|"), [ty, cy] = y.key.split("|");
  const sOverlap = x.sectors.filter(s => y.sectors.includes(s)).length / Math.max(1, Math.min(x.sectors.length, y.sectors.length));
  return 0.5 * (tx === ty) + 0.3 * (cx && cx === cy) + 0.2 * sOverlap;
}
function pickTop(cands, n, scoreOf) {
  const picked = [], perSector = {}, pool = [...cands].sort((a, b) => scoreOf(b) - scoreOf(a));
  const add = (c) => { picked.push(c); if (c.mainSector) perSector[c.mainSector] = (perSector[c.mainSector] || 0) + 1; };
  pool.splice(0, Math.min(SCORE.mmrKeep, n)).forEach(add);
  while (picked.length < n && pool.length) {
    let best = -Infinity, bi = -1;
    pool.forEach((c, i) => {
      if (c.mainSector && (perSector[c.mainSector] || 0) >= SCORE.maxPerSector) return;
      const red = picked.length ? Math.max(...picked.map(p => similarity(c, p))) : 0;
      const v = SCORE.mmrLambda * (scoreOf(c) / 100) - (1 - SCORE.mmrLambda) * red;
      if (v > best) { best = v; bi = i; }
    });
    if (bi < 0) break;
    add(pool.splice(bi, 1)[0]);
  }
  return picked.sort((a, b) => scoreOf(b) - scoreOf(a));
}

// ── 기사 불러오기 (publish.js 와 같은 재분류) ──
const all = JSON.parse(fs.readFileSync(ARCHIVE, "utf8")).items.filter(a => Date.parse(a.date) <= now + DAY).flatMap(a => {
  a.title = stripOutlet(a.title, a.source);
  a.source = outletName(a.source);
  a.outlets = [...new Set((a.outlets || [a.source]).map(o => outletName(o)))];
  if (a.ai) return isPromo(a.title) ? [] : [a];
  const g = (a.feed || "").startsWith("gnews:") ? a.feed.slice(6) : "";
  const c = finalize(a, a.hint || (topicById[g] ? g : undefined));
  return c ? [{ ...a, ...c }] : [];
});
const week = all.filter(a => weekOf(a) === 0);
const base = all.filter(a => { const w = weekOf(a); return w >= 1 && w <= SCORE.baselineWeeks; });
const feedsIn = (w) => new Set(all.filter(a => weekOf(a) === w).map(a => a.feed || ""));
const baseFeeds = Array.from({ length: SCORE.baselineWeeks }, (_, i) => feedsIn(i + 1));
const stable = new Set([...baseFeeds[0]].filter(f => baseFeeds.every(s => s.has(f))));
let comparable = (a) => stable.has(a.feed || "");
if (week.filter(comparable).length < 10) comparable = () => true;
const weekC = week.filter(comparable), baseC = base.filter(comparable);
const kinds = new Set(all.map(issueKey)).size;
const nBase = count(baseC, issueKey), nNowC = count(weekC, issueKey);
// v2 의 최신성 가중 건수: 기사마다 0.5^(나이/반감기) (v1 의 반감기 3.5일과 같은 값)
const recency = (a) => Math.pow(0.5, Math.max(0, (now - Date.parse(a.date)) / DAY) / SCORE.halfLifeDays);
const wNowC = sumBy(weekC, issueKey, recency), wTotC = weekC.reduce((s, a) => s + recency(a), 0);

const groups = new Map();
for (const a of week) { const k = issueKey(a); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(a); }

const raw = [...groups].map(([key, list]) => {
  const nC = nNowC.get(key) || 0;
  const M0 = shareGrowth(nC, weekC.length, nBase.get(key) || 0, baseC.length, kinds);
  const outlets = new Set(list.flatMap(a => a.outlets || [a.source]));
  return {
    key, list, nC,
    V1: volume(list),
    K: list.reduce((s, a) => s + (a.relevance ?? 0.8), 0) / list.length,
    M: 0.5 + (M0 - 0.5) * nC / (nC + SCORE.momentumPrior),
    // v2 R: 신뢰도 가중 기사 수 ÷ 이번 주 전체 기사 수 (같은 주 안에서는 분모가 같아서 순위에는 영향 없음)
    Rshare: list.reduce((s, a) => s + a.tier, 0) / week.length,
    // v2 V: 최신성 가중 점유율 / 직전 4주 점유율 → 0~1 (같은 출처 기준, v1 과 같은 평활)
    Vg: shareGrowth(wNowC.get(key) || 0, wTotC, nBase.get(key) || 0, baseC.length, kinds),
    outlets: outlets.size,
  };
});
const vMax = Math.max(...raw.map(r => r.V1));
const cand = raw.filter(r => r.list.length >= SCORE.minReports || r.list.some(a => a.official && a.lang !== "en"));
const rMax = Math.max(...cand.map(r => r.Rshare)), dMax = Math.max(...cand.map(r => Math.sqrt(r.outlets)));

function v2Score(r, w = V2, opt = {}) {
  const R = r.Rshare / rMax;
  const Vraw = opt.shrinkV ? 0.5 + (r.Vg - 0.5) * r.nC / (r.nC + SCORE.momentumPrior) : r.Vg;
  const D = Math.sqrt(r.outlets) / dMax * (r.outlets < w.minOutlets ? w.thinPenalty : 1);
  return { score: 100 * (w.wR * R + w.wV * Vraw + w.wD * D) * (0.6 + 0.4 * r.K), R, V: Vraw, D };
}

const cands = cand.map(r => {
  const Vn = Math.log(1 + r.V1) / Math.log(1 + vMax);
  const s1 = 100 * (SCORE.wVolume * Vn + SCORE.wMomentum * r.M) * (0.6 + 0.4 * r.K);
  const v2 = v2Score(r);
  const [topic, country] = r.key.split("|");
  const ranked = rankForIssue(r.list, topic, country, weight);
  const lead = ranked[0] ? ranked[0].a : r.list[0];
  const sectorsCount = count(r.list.flatMap(a => (a.sectors || []).map(s => ({ s }))), x => x.s);
  const sectors = [...sectorsCount].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([id]) => id);
  const mainN = sectors[0] ? r.list.filter(a => (a.sectors || []).includes(sectors[0])).length : 0;
  return {
    key: r.key, keyword: issueName(r.key), reports: r.list.length, outlets: r.outlets, sectors,
    mainSector: mainN / r.list.length >= SCORE.mainSectorShare ? sectors[0] : null,
    v1: round(s1), v1parts: { volume: round(Vn, 3), momentum: round(r.M, 3), relevance: round(r.K, 3) },
    v2: round(v2.score), v2parts: { R: round(v2.R, 3), V: round(v2.V, 3), D: round(v2.D, 3), K: round(r.K, 3) },
    raw: r,
    lead: { title: lead.titleKo || lead.title, source: lead.source, rel: ranked[0] ? ranked[0].rel : 0 },
    leadRelevance: ranked[0] ? ranked[0].rel : 0,
  };
});

// ── 순위·상관 ──
const rankMap = (list, f) => { const s = [...list].sort((a, b) => f(b) - f(a)); return new Map(s.map((x, i) => [x.key, i + 1])); };
function avgRanks(vals) {   // 동점은 평균 순위
  const idx = vals.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]), r = new Array(vals.length);
  for (let i = 0; i < idx.length;) { let j = i; while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++; for (let k = i; k <= j; k++) r[idx[k][1]] = (i + j) / 2 + 1; i = j + 1; }
  return r;
}
function spearman(x, y) {   // 동점 보정: 순위의 피어슨 상관
  const rx = avgRanks(x), ry = avgRanks(y), n = x.length, mx = rx.reduce((a, b) => a + b) / n, my = ry.reduce((a, b) => a + b) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { sxy += (rx[i] - mx) * (ry[i] - my); sxx += (rx[i] - mx) ** 2; syy += (ry[i] - my) ** 2; }
  return sxy / Math.sqrt(sxx * syy);
}
const overlap = (a, b) => a.filter(k => b.includes(k)).length;

const eligible = cands.filter(x => x.leadRelevance >= LEAD_MIN_RELEVANCE);
const top = (f) => [...cands].sort((a, b) => f(b) - f(a)).slice(0, 10);
const site = (f) => pickTop(eligible, 10, f);
const T = {
  v1score: top(x => x.v1), v2score: top(x => x.v2),
  v1site: site(x => x.v1), v2site: site(x => x.v2),
};
const r1 = rankMap(cands, x => x.v1), r2 = rankMap(cands, x => x.v2);
const rho = spearman(cands.map(x => x.v1), cands.map(x => x.v2));

// 민감도: 가중치(초기값)를 흔들거나 V 에 v1 과 같은 축소를 넣으면 결과가 얼마나 달라지나
const variants = [
  ["v2 기본 (0.5/0.3/0.2)", V2, {}],
  ["v2 + V 축소 n/(n+5)", V2, { shrinkV: true }],
  ["v2 가중 0.4/0.4/0.2", { ...V2, wR: 0.4, wV: 0.4 }, {}],
  ["v2 가중 0.6/0.2/0.2", { ...V2, wR: 0.6, wV: 0.2 }, {}],
  ["v2 가중 0.5/0.2/0.3", { ...V2, wV: 0.2, wD: 0.3 }, {}],
  ["v2 최소 출처 벌점 없음", { ...V2, thinPenalty: 1 }, {}],
].map(([name, w, opt]) => {
  const s = new Map(cands.map(x => [x.key, v2Score(x.raw, w, opt).score]));
  const t = [...cands].sort((a, b) => s.get(b.key) - s.get(a.key)).slice(0, 10).map(x => x.key);
  return { name, rho: spearman(cands.map(x => x.v1), cands.map(x => s.get(x.key))), overlapV1: overlap(t, T.v1score.map(x => x.key)), top3: t.slice(0, 3).map(issueName) };
});

// ── 사람 순위 ──
let human = null;
if (fs.existsSync(HUMAN)) human = JSON.parse(fs.readFileSync(HUMAN, "utf8"));
const humanFilled = human && Array.isArray(human.ranking) && human.ranking.filter(Boolean).length >= 10;
function humanMetrics(scoreOf) {
  if (!humanFilled) return "미측정";
  const keys = human.ranking.filter(k => cands.some(x => x.keyword === k));
  const unknown = human.ranking.filter(k => k && !keys.includes(k));
  if (unknown.length) console.error(`⚠ human_rank.json 에서 후보에 없는 이름은 빼고 계산해요: ${unknown.join(", ")}`);
  const sub = keys.map(k => cands.find(x => x.keyword === k));
  const hScore = sub.map((_, i) => keys.length - i);    // 사람이 앞에 적을수록 큰 값
  const mTop = [...sub].sort((a, b) => scoreOf(b) - scoreOf(a)).slice(0, 10).map(x => x.keyword);
  return { n: keys.length, spearman: round(spearman(hScore, sub.map(scoreOf)), 3), top10overlap: overlap(keys.slice(0, 10), mTop) };
}

// ── 출력 ──
const out = [];
const p = (s = "") => out.push(s);
const sameAsPublished = latest && latest.meta.generated === new Date(now).toISOString()
  ? JSON.stringify(T.v1site.map(x => [x.keyword, x.v1])) === JSON.stringify(latest.issues.map(x => [x.keyword, x.score])) : null;
p(`# 점수식 비교 (기준 ${new Date(now).toISOString()})`);
p(`이번 주 기사 ${week.length}건 (같은 출처 비교용 ${weekC.length}건) · 직전 4주 ${base.length}건 · 이슈 묶음 ${raw.length}개 · 후보(기사 ${SCORE.minReports}건 이상 또는 정부 발표) ${cands.length}개 · 대표 기사 기준 통과 ${eligible.length}개`);
p(sameAsPublished === null ? "v1 재현 확인: 발행본과 기준 시각이 달라 비교하지 않았어요" : sameAsPublished ? "v1 재현 확인: 발행된 Top 10·점수와 똑같아요 ✓" : "⚠ v1 재현 확인: 발행된 Top 10 과 달라요 (보관 파일이나 분류 규칙이 발행 때와 다를 수 있음)");
p();
const table = (title, a, b, fa, fb) => {
  p(`## ${title}`);
  p("| 순위 | v1 이슈 | v1 점수 | v2 이슈 | v2 점수 | v1→v2 순위 변화 |");
  p("|---|---|---|---|---|---|");
  const bk = b.map(x => x.key), ak = a.map(x => x.key);
  for (let i = 0; i < 10; i++) {
    const x = a[i], y = b[i];
    const j = y ? ak.indexOf(y.key) : -1;
    const mv = !y ? "" : j < 0 ? "새로 진입" : j === i ? "–" : j > i ? `▲${j - i}` : `▼${i - j}`;
    p(`| ${i + 1} | ${x ? x.keyword : ""} | ${x ? fa(x) : ""} | ${y ? y.keyword : ""} | ${y ? fb(y) : ""} | ${mv} |`);
  }
  p(`- 겹침 ${overlap(ak, bk)}/10 · v2에서 새로 들어온 이슈: ${b.filter(x => !ak.includes(x.key)).map(x => x.keyword).join(", ") || "없음"} · 빠진 이슈: ${a.filter(x => !bk.includes(x.key)).map(x => x.keyword).join(", ") || "없음"}`);
  p();
};
table("점수만으로 매긴 Top 10 (후보 전체, 다양성 규칙 없음)", T.v1score, T.v2score, x => x.v1, x => x.v2);
table("사이트 선정 방식 Top 10 (대표 기사 기준 + MMR·섹터 제한, 발행과 같은 규칙)", T.v1site, T.v2site, x => x.v1, x => x.v2);
p(`## 순위 상관`);
p(`- Spearman ρ (후보 ${cands.length}개 전체, 동점 평균 순위): **${round(rho, 3)}**`);
p(`- Top 10 겹침: 점수 순 ${overlap(T.v1score.map(x => x.key), T.v2score.map(x => x.key))}/10 · 사이트 선정 ${overlap(T.v1site.map(x => x.key), T.v2site.map(x => x.key))}/10`);
p();
p(`## 사람 판단과 비교 (${path.relative(ROOT, HUMAN)})`);
const h1 = humanMetrics(x => x.v1), h2 = humanMetrics(x => x.v2);
if (!humanFilled) p("- v1: 미측정 · v2: 미측정 (ranking 이 비어 있어요. 10개 이상 적으면 계산해요)");
else p(`- v1: Spearman ${h1.spearman}, Top10 겹침 ${h1.top10overlap}/10 (n=${h1.n}) · v2: Spearman ${h2.spearman}, Top10 겹침 ${h2.top10overlap}/10`);
p();
p("## 민감도 (v2 초기값을 바꾸면)");
p("| 변형 | v1 과 Spearman | v1 Top10 과 겹침 | Top 3 |");
p("|---|---|---|---|");
variants.forEach(v => p(`| ${v.name} | ${round(v.rho, 3)} | ${v.overlapV1}/10 | ${v.top3.join(", ")} |`));
p();
p("## 후보 전체");
p("| 이슈 | 기사 | 독립 출처 | v1 (V·M·K) | v1 순위 | v2 (R·V·D·K) | v2 순위 | 대표 기사 관련도 | 대표 기사 |");
p("|---|---|---|---|---|---|---|---|---|");
[...cands].sort((a, b) => r1.get(a.key) - r1.get(b.key)).forEach(x => {
  const a = x.v1parts, b = x.v2parts;
  p(`| ${x.keyword} | ${x.reports} | ${x.outlets} | ${x.v1} (${a.volume}·${a.momentum}·${a.relevance}) | ${r1.get(x.key)} | ${x.v2} (${b.R}·${b.V}·${b.D}·${b.K}) | ${r2.get(x.key)} | ${x.leadRelevance}${x.leadRelevance < LEAD_MIN_RELEVANCE ? " ⚠" : ""} | ${x.lead.title.replace(/\|/g, "/").slice(0, 60)} (${x.lead.source}) |`);
});
console.log(out.join("\n"));

if (arg("json")) fs.writeFileSync(arg("json"), JSON.stringify({ now: new Date(now).toISOString(), rho, top: Object.fromEntries(Object.entries(T).map(([k, v]) => [k, v.map(x => x.keyword)])), cands: cands.map(({ raw, ...x }) => x), variants, human: { v1: h1, v2: h2 } }, null, 1));
if (flag("write-human-template")) {
  // 후보 20개: 두 식 어느 쪽에서든 순위가 높은 것부터 (v1·v2 순위 중 높은 쪽 기준)
  const pick = [...cands].sort((a, b) => Math.min(r1.get(a.key), r2.get(a.key)) - Math.min(r1.get(b.key), r2.get(b.key)) || r1.get(a.key) - r1.get(b.key)).slice(0, 20);
  // 사람이 순서에 끌려가지 않게 이름순(가나다)으로 적어요
  const list = pick.map(x => ({ keyword: x.keyword, reports: x.reports, titles: rankForIssue(x.raw.list, ...x.key.split("|"), weight).slice(0, 4).map(r => r.a.titleKo || r.a.title) })).sort((a, b) => a.keyword.localeCompare(b.keyword, "ko"));
  fs.mkdirSync(path.dirname(HUMAN), { recursive: true });
  fs.writeFileSync(HUMAN, JSON.stringify({
    week: latest && latest.meta.generated === new Date(now).toISOString() ? latest.meta.period : new Date(now).toISOString().slice(0, 10),
    generated_at: new Date(now).toISOString(),
    how_to: "candidates 의 keyword 를 중요하다고 생각하는 순서대로 ranking 에 옮겨 적어 주세요 (1위가 맨 앞). 10개 이상 적으면 scripts/score_compare.js 가 사람 기준 지표를 계산해요. titles 는 그 이슈로 묶인 기사 제목 예시(최대 4개)예요. 점수는 보지 말고 제목만 보고 판단해 주세요. 무역과 상관없는 기사가 섞인 이슈는 그만큼 낮게 매기면 돼요.",
    rater: "",
    ranking: [],
    candidates: list,
  }, null, 1) + "\n");
  console.error(`사람 순위 양식을 썼어요: ${path.relative(ROOT, HUMAN)} (후보 ${list.length}개)`);
}
