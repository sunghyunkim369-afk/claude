// 주간 발행: data/news/archive.json 의 실제 기사로 이번 주 Top10 이슈·섹터 노출도·리스크·뉴스 피드를 계산해
// tradecompass-mvp/data.js (사이트가 바로 읽는 파일) 와 data/news/latest.json (eyefeet /api/data 가 읽는 파일) 을 만들어요.
// 실행: node scripts/news/publish.js   (매주 월요일 아침 자동 실행 · 계산 근거는 docs/news-algorithm.md)
// 파일 위치는 환경변수 NEWS_DATA_DIR·NEWS_WEB_DIR 로 바꿀 수 있어요 (paths.js)
const fs = require("fs");
const { TOPICS, SECTORS, COUNTRIES, SCORE } = require("./config");
const { weeklyBrief } = require("./ai");
const { finalize, outletName, stripOutlet, rankForIssue, isPromo, isOpinion, issueRelevance, dedupeEvents } = require("./lib");
const { LEAD_MIN_RELEVANCE } = require("./config");

// HISTORY: 주마다 발행한 Top10 기록 (지난주 대비 순위 변화를 보여주려고 남겨요)
const { ARCHIVE, LATEST: OUT_JSON, DATA_JS: OUT_JS, HISTORY, write } = require("./paths");
const DAY = 86_400_000;

const topicById = Object.fromEntries(TOPICS.map(t => [t.id, t]));
const sectorById = Object.fromEntries(SECTORS.map(s => [s.id, s]));
const countryById = Object.fromEntries(COUNTRIES.map(c => [c.id, c]));
const round = (x, d = 2) => Math.round(x * 10 ** d) / 10 ** d;
const kst = (t) => new Date(t + 9 * 3600_000);                       // 한국 시간으로 표시
const fmtDate = (t) => kst(t).toISOString().slice(0, 10);
const fmtAt = (t) => kst(t).toISOString().slice(0, 16).replace("T", " ");
const fmtHM = (t) => kst(t).toISOString().slice(11, 16);
const fmtMD = (t) => kst(t).toISOString().slice(5, 10).replace("-", ".");
const signed = (v) => `${v > 0 ? "+" : ""}${v}%`;

function ago(t, now) {
  const m = Math.max(1, Math.round((now - t) / 60_000));
  if (m < 60) return `${m}분 전`;
  if (m < 60 * 24) return `${Math.round(m / 60)}시간 전`;
  return `${Math.round(m / 1440)}일 전`;
}

// ── 기사 한 건의 무게 ───────────────────────────────────────────────
// 신뢰도(tier) × 한국 관련도 × 최신성(반감기 3.5일) × 보도 범위(같은 사건을 다룬 언론사 수, 최대 2배) × 의견·증권 단신 0.5
function weight(a, now) {
  const age = Math.max(0, (now - Date.parse(a.date)) / DAY);
  const decay = Math.pow(0.5, age / SCORE.halfLifeDays);
  const breadth = Math.min(2, 1 + 0.25 * ((a.outlets || [a.source]).length - 1));
  const rel = SCORE.relevanceFloor + (1 - SCORE.relevanceFloor) * (a.relevance ?? 0.8);
  return a.tier * rel * decay * breadth * (isOpinion(a.title) ? 0.5 : 1);
}

// 이슈 보도량 V: 기사 무게의 합. 단, 같은 언론사의 k번째 기사는 1/√k 만 더해요.
// "얼마나 많은 언론이 다뤘나"를 재려는 것이라, 한 통신사가 속보·2보·종합으로 여러 번 쓴 것이 보도량을 키우지 않게 해요.
function volume(list, now) {
  const seen = {};
  return [...list].sort((a, b) => weight(b, now) - weight(a, now))
    .reduce((s, a) => { const k = (seen[a.source] = (seen[a.source] || 0) + 1); return s + weight(a, now) / Math.pow(k, SCORE.outletDecay); }, 0);
}

// 이슈 = (주제 × 상대국). 예: "미국 관세", "중국 수출통제", 상대국이 없으면 "수출입 동향"
const issueKey = (a) => `${a.topic}|${a.country || ""}`;
const issueName = (key) => {
  const [t, c] = key.split("|");
  return `${c && countryById[c] ? countryById[c].name + " " : ""}${topicById[t] ? topicById[t].label : t}`;
};

// 주(週) 구간별 기사 묶기: w=0 이번 주, w=1 직전 주, ...
function weekOf(a, now) { return Math.floor((now - Date.parse(a.date)) / (SCORE.windowDays * DAY)); }

function count(items, keyFn) {
  const m = new Map();
  for (const a of items) for (const k of [].concat(keyFn(a))) if (k) m.set(k, (m.get(k) || 0) + 1);
  return m;
}

// ── 추세(M): 무역 기사 전체에서 이 이슈가 차지하는 비중이 평소보다 얼마나 늘었나 ──
// Caldara et al.(2020) 무역정책 불확실성 지수처럼 "건수"가 아니라 "비중"을 써서
// 수집 출처가 늘거나 뉴스가 많은 주에 모든 이슈가 같이 올라가는 문제를 없애요.
function momentum(nNow, totalNow, nBase, totalBase, kinds) {
  if (!totalBase) return 0.5;                                        // 비교할 과거가 없으면 중립
  const a = SCORE.smoothing;
  const shareNow = (nNow + a) / (totalNow + a * kinds);
  const shareBase = (nBase + a) / (totalBase + a * kinds);
  return 0.5 + 0.5 * Math.tanh(Math.log(shareNow / shareBase));       // 0~1, 평소와 같으면 0.5
}

// ── Top10 고르기: MMR(Carbonell & Goldstein 1998)로 점수 높은 순 + 비슷한 이슈 반복 줄이기 ──
function similarity(x, y) {
  const [tx, cx] = x.key.split("|"), [ty, cy] = y.key.split("|");
  const sOverlap = x.sectors.filter(s => y.sectors.includes(s)).length / Math.max(1, Math.min(x.sectors.length, y.sectors.length));
  return 0.5 * (tx === ty) + 0.3 * (cx && cx === cy) + 0.2 * sOverlap;
}
function pickTop(cands, n) {
  const picked = [], perSector = {};
  const pool = [...cands];
  // 점수 상위 몇 개는 다양성 규칙 때문에 빠지지 않게 먼저 넣어요 (2026-10: 점수 3위 이슈가
  // 같은 섹터의 더 낮은 이슈들에 밀려 Top10에서 빠지는 일이 있었어요)
  for (const c of pool.splice(0, Math.min(SCORE.mmrKeep, n))) {
    picked.push(c);
    if (c.mainSector) perSector[c.mainSector] = (perSector[c.mainSector] || 0) + 1;
  }
  while (picked.length < n && pool.length) {
    let best = -Infinity, bi = -1;
    pool.forEach((c, i) => {
      const main = c.mainSector;
      if (main && (perSector[main] || 0) >= SCORE.maxPerSector) return;
      const red = picked.length ? Math.max(...picked.map(p => similarity(c, p))) : 0;
      const v = SCORE.mmrLambda * (c.score / 100) - (1 - SCORE.mmrLambda) * red;
      if (v > best) { best = v; bi = i; }
    });
    if (bi < 0) break;
    const [c] = pool.splice(bi, 1);
    picked.push(c);
    if (c.mainSector) perSector[c.mainSector] = (perSector[c.mainSector] || 0) + 1;
  }
  return picked;
}

const dirCount = (list) => ({
  up: list.filter(a => a.direction === "up").length,
  down: list.filter(a => a.direction === "down").length,
  neutral: list.filter(a => a.direction !== "up" && a.direction !== "down").length,
});
const topSectors = (list, k = 3) => [...count(list, a => a.sectors || [])].sort((a, b) => b[1] - a[1]).slice(0, k).map(([id]) => id);
// 화면·공개 데이터에는 기사 본문에서 잘라 온 글(발췌)과 AI 한 줄 요약을 넣지 않아요 (팀 규칙 C5, 2026-10-06).
// 제목·언론사·시각·원문 링크와, 우리 데이터로 만든 설명(why: "○○ 이슈 · 이번 주 N건 보도")만 써요.
// 보관 파일(archive.json)의 desc 는 분류용으로만 남겨요.

// 이슈별 보도 추이·이번 주 건수 (main 에서 채워요)
const TREND_WEEKS = 5;   // 보관 42일 중 수집 시점 차이로 잘리는 가장 오래된 주는 빼요
let CTX = { week: new Map(), trend: () => [] };

// 화면에 싣는 기사 한 건
// why: 요약이 없는 기사(Google 뉴스 등)도 "왜 중요한지" 한 줄을 보여주려고 데이터로 만든 설명
const toNews = (a, rank) => {
  const key = issueKey(a), sectors = (a.sectors || []).filter(id => sectorById[id]);
  const n = CTX.week.get(key) || 0;
  return {
    time: fmtMD(Date.parse(a.date)), clock: fmtHM(Date.parse(a.date)), at: a.date, rank, source: a.source,
    outlets: (a.outlets || []).length, tag: topicById[a.topic]?.label || "무역", title: a.titleKo || a.title, link: a.link,
    lang: a.lang === "en" ? "en" : undefined, orig: a.titleKo ? a.title : undefined,
    direction: a.direction || "neutral", sectors,
    issue: issueName(key), issueReports: n, trend: CTX.trend(key),
    why: `${issueName(key)} 이슈 · 이번 주 ${n}건 보도${sectors.length ? ` · ${sectors.map(id => sectorById[id].name).join("·")} 영향권` : ""}`,
  };
};
// 중요도 순으로 고르되 같은 이슈 기사는 cap 건까지, 같은 사건은 한 줄로 합쳐요
// 중요도 = 무게 × (0.5 + 이슈 관련도): 제목에 무역 단어가 없는 기사("가축질병 진단능력 평가")가 최신이라는 이유로 위에 오지 않게
function pickNews(list, n, cap, now) {
  const per = {};
  const imp = (a) => weight(a, now) * (0.5 + issueRelevance(a, a.topic, a.country)) * (isOpinion(a.title) ? 0.5 : 1);
  return dedupeEvents([...list].sort((a, b) => imp(b) - imp(a)))
    .filter(a => { const k = issueKey(a); per[k] = (per[k] || 0) + 1; return per[k] <= cap; }).slice(0, n);
}

async function main() {
  const now = process.env.TC_NOW ? Date.parse(process.env.TC_NOW) : Date.now();   // TC_NOW: 지난주 기준으로 다시 계산할 때 (history 초기화용)
  // 분류 규칙이 바뀌어도 지난 기사까지 같은 규칙으로 계산하도록, AI가 분류하지 않은 기사는 여기서 다시 분류해요
  const all = JSON.parse(fs.readFileSync(ARCHIVE, "utf8")).items.filter(a => Date.parse(a.date) <= now + DAY).flatMap(a => {
    a.title = stripOutlet(a.title, a.source);
    a.source = outletName(a.source);
    a.outlets = [...new Set((a.outlets || [a.source]).map(o => outletName(o)))];
    if (a.ai) return isPromo(a.title) ? [] : [a];
    const g = (a.feed || "").startsWith("gnews:") ? a.feed.slice(6) : "";
    const c = finalize(a, a.hint || (topicById[g] ? g : undefined));
    return c ? [{ ...a, ...c }] : [];
  });
  const week = all.filter(a => weekOf(a, now) === 0);
  const base = all.filter(a => { const w = weekOf(a, now); return w >= 1 && w <= SCORE.baselineWeeks; });
  // ── 같은 출처끼리 비교 (2026-10) ──
  // 추세·노출도·보도 추이는 "직전 4주 내내 있던 출처(피드)"의 기사로만 비교해요.
  // 새 출처(언론사 RSS, 해외 피드)를 붙인 주에는 그 출처가 많이 다루는 이슈가 "급증"처럼 보이기 때문이에요.
  // 새 출처도 4주가 지나면 자동으로 비교에 들어가요. 보도량(V)과 화면의 "이번 주 N건"은 모든 출처를 써요.
  const feedsIn = (w) => new Set(all.filter(a => weekOf(a, now) === w).map(a => a.feed || ""));
  const baseFeeds = Array.from({ length: SCORE.baselineWeeks }, (_, i) => feedsIn(i + 1));
  const stable = new Set([...baseFeeds[0]].filter(f => baseFeeds.every(s => s.has(f))));
  let comparable = (a) => stable.has(a.feed || "");
  if (all.filter(a => weekOf(a, now) === 0 && comparable(a)).length < 10) comparable = () => true;   // 과거 데이터가 부족하면 전부 사용
  const weekC = week.filter(comparable), baseC = base.filter(comparable);
  const prevWeek = all.filter(a => weekOf(a, now) === 1 && comparable(a));
  // 이슈별 최근 5주 보도 건수 (오래된 주 → 이번 주, 같은 출처끼리)
  const byWeek = Array.from({ length: TREND_WEEKS }, (_, i) => count(all.filter(a => weekOf(a, now) === TREND_WEEKS - 1 - i && comparable(a)), issueKey));
  CTX = { week: count(week, issueKey), trend: (key) => byWeek.map(m => m.get(key) || 0) };
  const trendWeeks = Array.from({ length: TREND_WEEKS }, (_, i) => fmtMD(now - (TREND_WEEKS - i) * SCORE.windowDays * DAY + DAY));
  if (week.length < 10) throw new Error(`이번 주 기사가 ${week.length}건뿐이라 발행하지 않아요 (수집 확인 필요)`);

  // ── 이슈 점수 ─────────────────────────────────────────────────────
  const groups = new Map();
  for (const a of week) { const k = issueKey(a); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(a); }
  const nPrev = count(prevWeek, issueKey), nBase = count(baseC, issueKey), nNowC = count(weekC, issueKey);
  const kinds = new Set([...all.map(issueKey)]).size;

  const raw = [...groups].map(([key, list]) => {
    const V = volume(list, now);
    const R = list.reduce((s, a) => s + (a.relevance ?? 0.8), 0) / list.length;
    // 추세는 기사가 적을수록 평소(0.5) 쪽으로 당겨요: n/(n+k). 3건짜리 신규 이슈가 추세 만점(1.0)을 받아 Top 10에 들어가는 것을 막아요.
    const nC = nNowC.get(key) || 0;
    const M0 = momentum(nC, weekC.length, nBase.get(key) || 0, baseC.length, kinds);
    const M = 0.5 + (M0 - 0.5) * nC / (nC + SCORE.momentumPrior);
    return { key, list, V, R, M };
  });
  const vMax = Math.max(...raw.map(r => r.V));

  const cands = raw
    .filter(r => r.list.length >= SCORE.minReports || r.list.some(a => a.official && a.lang !== "en"))
    .map(r => {
      // 점수 = 100 × (0.6·보도량 + 0.4·추세) × (0.6 + 0.4·한국 관련도)
      const Vn = Math.log(1 + r.V) / Math.log(1 + vMax);
      const score = 100 * (SCORE.wVolume * Vn + SCORE.wMomentum * r.M) * (0.6 + 0.4 * r.R);
      // 대표 기사: 이슈 주제·상대국과 관련도가 기준을 넘는 기사 중 무게 순 (lib.rankForIssue)
      const [topic, country] = r.key.split("|");
      const ranked = rankForIssue(r.list, topic, country, a => weight(a, now));
      const sorted = ranked.map(x => x.a);
      const lead = sorted[0] || r.list[0];
      const leadRel = ranked[0] ? ranked[0].rel : 0;
      const sectors = topSectors(r.list, 3);
      // 섹터당 3개 제한은 그 섹터 기사가 이슈의 40% 이상일 때만 셉니다.
      // ("미국 관세"처럼 여러 섹터에 걸친 이슈가 기사 몇 건 때문에 석유화학 이슈로 세어져 다른 이슈를 막지 않게)
      const mainN = sectors[0] ? r.list.filter(a => (a.sectors || []).includes(sectors[0])).length : 0;
      const mainSector = mainN / r.list.length >= SCORE.mainSectorShare ? sectors[0] : null;
      return {
        key: r.key, mainSector, keyword: issueName(r.key), title: lead.titleKo || lead.title, link: lead.link, source: lead.source,
        lang: lead.lang === "en" ? "en" : undefined, orig: lead.titleKo ? lead.title : undefined,
        time: ago(Date.parse(lead.date), now), impact: Math.round(score), tag: topicById[topic]?.label || topic,
        topic, risk: !!topicById[topic]?.risk, sectors, score: round(score), reports: r.list.length, prev: nPrev.get(r.key) || 0, reportsC: nNowC.get(r.key) || 0,
        // 지난주 대비 (같은 출처 기준): 증감률과 배수. 직전 주가 0건이면 null(새 이슈)
        ...(() => { const p = nPrev.get(r.key) || 0, c = nNowC.get(r.key) || 0; return p ? { change: Math.round((c - p) / p * 100), ratio: round(c / p, 1) } : { change: null, ratio: null }; })(),
        ...dirCount(r.list),
        parts: { volume: round(Vn, 3), momentum: round(r.M, 3), relevance: round(r.R, 3) },
        trend: CTX.trend(r.key), leadRelevance: leadRel,
        articles: dedupeEvents(sorted).slice(0, 4).map(a => ({ title: a.titleKo || a.title, lang: a.lang === "en" ? "en" : undefined, source: a.source, outlets: a.outlets.length, at: fmtAt(Date.parse(a.date)), link: a.link })),
      };
    })
    .sort((a, b) => b.score - a.score);

  // 대표로 쓸 만한 기사(관련도 기준 통과)가 하나도 없는 이슈는 Top 10 후보에서 빼요
  const eligible = cands.filter(x => x.leadRelevance >= LEAD_MIN_RELEVANCE);
  // MMR 로 고른 10개를 화면에는 점수 순으로 보여줘요
  const issues = pickTop(eligible, 10).sort((a, b) => b.score - a.score);
  // ── 지난주 대비 순위 변화 ──
  // history.json 에 주(기간 시작일)별 Top10 이슈 이름을 남기고, 이번 주와 다른 가장 최근 주와 비교해요.
  // 같은 주에 여러 번 발행해도 그 주 기록만 덮어써서 비교 기준이 흔들리지 않아요.
  const weekId = fmtDate(now - SCORE.windowDays * DAY);
  let history = [];
  try { history = JSON.parse(fs.readFileSync(HISTORY, "utf8")); } catch { history = []; }
  const last = history.filter(h => h.week !== weekId).slice(-1)[0];
  issues.forEach((x, i) => {
    const before = last ? last.top.indexOf(x.keyword) : -1;
    x.move = !last ? null : before < 0 ? "new" : before - i;     // 양수 = 올라감, "new" = 새로 진입
  });
  const dropped = last ? last.top.filter(k => !issues.some(x => x.keyword === k)) : [];
  history = [...history.filter(h => h.week !== weekId), { week: weekId, top: issues.map(x => x.keyword) }].slice(-12);
  if (process.env.DEBUG_CANDS) eligible.slice(0, 15).forEach(x => console.log("cand", x.score.toFixed(1), x.keyword, x.reports, x.sectors.join(",")));

  // ── 섹터 노출도: 이번 주 그 섹터 기사 비중이 평소(직전 4주)보다 높으면 50 위로 ──
  const sCountNow = count(weekC, a => a.sectors || []), sCountBase = count(baseC, a => a.sectors || []);
  const sectors = SECTORS.map(s => {
    const list = week.filter(a => (a.sectors || []).includes(s.id));
    const nC = sCountNow.get(s.id) || 0;
    const M = momentum(nC, weekC.length, sCountBase.get(s.id) || 0, baseC.length, SECTORS.length);
    // 기사가 적을수록 50(평소) 쪽으로 당겨요: n/(n+k). 기사 4건짜리 섹터가 100 가까이 튀지 않게
    const shrink = nC / (nC + SCORE.sectorPrior);
    const score = 50 + (M - 0.5) * 100 * shrink;
    const d = dirCount(list);
    // 상태는 방향이 있는 기사(강화·완화)끼리만 비교해요. 환율·실적 같은 동향 기사까지 분모에 넣으면
    // 거의 모든 섹터가 "보합"이 돼서 정보가 없어지기 때문이에요. 방향 기사가 2건 미만이면 보합.
    const directional = d.up + d.down;
    const tone = directional >= 2 ? (d.up - d.down) / directional : 0;
    const state = tone >= 0.34 ? "강화" : tone <= -0.34 ? "완화" : "보합";
    const its = cands.filter(x => x.sectors.includes(s.id)).slice(0, 2).map(x => x.keyword);
    const summary = list.length
      ? `이번 주 관련 기사 ${list.length}건(직전 4주 주평균 ${round((sCountBase.get(s.id) || 0) / SCORE.baselineWeeks, 1)}건)${its.length ? ` · 주요 이슈: ${its.join(", ")}` : ""}`
      : "이번 주 관련 무역 기사가 거의 없어요.";
    // 평소 대비: 같은 출처 기준 이번 주 건수 vs 직전 4주 주평균. 기사 10건 미만이면 화면에 "기사 적음" 표시
    const baseAvg = (sCountBase.get(s.id) || 0) / SCORE.baselineWeeks;
    const change = baseAvg >= 1 ? Math.round((nC - baseAvg) / baseAvg * 100) : null;
    return { id: s.id, name: s.name, score: round(score), state, ...d, articles: list.length, baseAvg: round(baseAvg, 1), change, thin: list.length < 10, summary,
      news: pickNews(list, 6, 2, now).map((a, i) => toNews(a, i)) };
  }).sort((a, b) => b.score - a.score);

  // ── 공급망 리스크: 리스크 주제(수출통제·제재·해운·공급망·원자재) 이슈 ──
  const risks = cands.filter(x => x.risk).slice(0, 5).map(x => ({
    level: x.score >= 70 ? "심각" : x.score >= 55 ? "주의" : "관찰",
    title: x.keyword, detail: x.title,
    effect: `보도 ${x.reports}건 (${x.change == null ? "신규" : x.ratio >= 2 ? `${x.ratio}배` : signed(x.change)})`,
    link: x.link,
  }));

  // ── 뉴스 피드: 무게 순 + 같은 이슈 기사는 3건까지 ──
  // rank: 중요도(무게) 순위 — 홈의 머리기사·헤드라인은 rank 순, 뉴스 피드는 최신순
  const news = pickNews(week, SCORE.newsCount, 3, now).map((a, i) => toNews(a, i)).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));

  // ── 이번 주 요약 (AI 키가 있으면 AI, 없으면 규칙) ──
  const top = issues[0];
  // 증감은 같은 출처끼리 비교한 값. 2배 이상이면 "지난주의 N배"가 읽기 쉬워요
  const pctTxt = (x) => x.change == null ? "새 이슈" : x.ratio >= 2 ? `지난주의 ${x.ratio}배` : `지난주 대비 ${signed(x.change)}`;
  const bearing = (await weeklyBrief(issues)) || {
    headline: `이번 주 최대 무역 이슈는 '${top.keyword}'입니다`,
    points: issues.slice(0, 3).map(x => `${x.keyword}: 보도 ${x.reports}건(${pctTxt(x)})${x.sectors.length ? ` · ${x.sectors.map(id => sectorById[id].name).join("·")} 영향권` : ""}`),
  };

  const from = now - SCORE.windowDays * DAY;
  const data = {
    meta: {
      date: fmtDate(now), updatedAt: fmtHM(now), generated: new Date(now).toISOString(),
      period: `${fmtDate(from)} ~ ${fmtDate(now)}`, cadence: "매주 월요일",
      sources: week.length, outlets: new Set(week.flatMap(a => a.outlets || [a.source])).size,
      archive: all.length, sample: false, trendWeeks, lastWeek: last ? last.week : null, dropped,
    },
    bearing, sectors, issues: issues.map(({ key, risk, reportsC, mainSector, ...x }) => x), news, risks,
    trade: null,   // 월간 수출입 통계는 관세청 공공데이터 API 키가 생기면 붙일 자리
  };

  write(OUT_JSON, JSON.stringify(data, null, 1));
  write(HISTORY, JSON.stringify(history, null, 1) + "\n");
  write(OUT_JS, `// 무역나침반 데이터 — scripts/news/publish.js 가 매주 실제 뉴스로 자동 생성해요. 직접 고치지 마세요.\n// 기간: ${data.meta.period} · 기사 ${week.length}건 · 생성 ${data.meta.generated}\nwindow.TC_DATA = ${JSON.stringify(data, null, 1)};\n`);

  console.log(`기간 ${data.meta.period} · 이번 주 기사 ${week.length}건(비교 가능 출처 ${weekC.length}건) · 직전 4주 ${base.length}건 · 이슈 후보 ${cands.length}개`);
  // 대표 기사 점검표: 이슈 ↔ 대표 기사 제목 ↔ 관련도 (기준 미만이면 ⚠)
  console.log(`\n대표 기사 점검 (관련도 기준 ${LEAD_MIN_RELEVANCE})`);
  issues.forEach((x, i) => console.log(`${String(i + 1).padStart(2)}. ${x.leadRelevance >= LEAD_MIN_RELEVANCE ? "✓" : "⚠"} ${x.leadRelevance.toFixed(2)}  [${x.keyword}] ${x.title}`));
  console.log("");
  issues.forEach((x, i) => console.log(`${String(i + 1).padStart(2)}. ${x.score.toFixed(2)}  ${x.keyword}  (보도 ${x.reports}/직전 ${x.prev}, V ${x.parts.volume} M ${x.parts.momentum} R ${x.parts.relevance})  ${x.title}`));
  console.log(sectors.map(s => `${s.name} ${s.score} ${s.state} (${s.articles}건)`).join(" | "));
}

main().catch(e => { console.error(e.message); process.exit(1); });
