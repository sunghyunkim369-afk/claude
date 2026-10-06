// 뉴스 파이프라인 공통 기능: RSS 읽기, 정리, 분류, 중복 묶기
const { OUTLETS, TOPICS, TRADE_WORDS, SECTORS, COUNTRIES, TIGHTEN, EASE, KOREA_WORDS, CORE_TRADE, PROMO, LEAD_MIN_RELEVANCE, TOPIC_EXCLUDE } = require("./config");

const UA = "Mozilla/5.0 (compatible; TradeCompassBot/1.0; +https://tcmvp.eyefeet.com)";
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function fetchText(url, tries = 2) {
  for (let i = 0; i < tries; i++) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 25_000);
      const r = await fetch(url, { headers: { "User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*" }, signal: ctrl.signal });
      clearTimeout(t);
      if (r.ok) return await r.text();
      if (r.status < 500) return null;
    } catch { /* 재시도 */ }
    await sleep(1500);
  }
  return null;
}

// ── RSS/Atom 읽기 (외부 패키지 없이 정규식으로 필요한 필드만) ──
// 엔티티를 먼저 풀고 태그를 지워요. (Google 뉴스 요약은 &lt;a href=...&gt; 처럼 태그가 한 번 더 감싸져 있어서,
// 순서를 거꾸로 하면 "<a href=https://news.google.com/..." 글자가 그대로 남아 화면 밖으로 삐져나와요)
const unescape = (s) => s
  .replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
  .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&amp;/g, "&");
const stripTags = (s) => s.replace(/<[^>]*>?/g, " ");
const decode = (s = "") => stripTags(unescape(stripTags(s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1"))))
  .replace(/\s+/g, " ").trim();

// 기사 요약 다듬기: 남은 태그·주소 제거, "(서울=연합뉴스) 홍길동 기자 =" 같은 머리말 제거,
// 제목을 그대로 되풀이하는 요약(Google 뉴스)은 버려요
function cleanDesc(desc = "", title = "") {
  let d = stripTags(unescape(desc)).replace(/https?:\/\/\S+/g, " ").replace(/\s+/g, " ").trim();
  d = d.replace(/^\([^)]{0,30}=[^)]{0,30}\)\s*([^=]{0,25}=\s*)?/, "").replace(/^\[[^\]]{0,30}\]\s*/, "");
  const norm = (x) => x.replace(/[^\p{L}\p{N}]/gu, "");
  if (d.length < 15 || norm(d).startsWith(norm(title).slice(0, 10))) return "";
  return d;
}
const tag = (block, name) => { const m = block.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`, "i")); return m ? m[1] : ""; };

function parseFeed(xml) {
  if (!xml) return [];
  const blocks = xml.match(/<item[\s>][\s\S]*?<\/item>/gi) || xml.match(/<entry[\s>][\s\S]*?<\/entry>/gi) || [];
  return blocks.map(b => {
    const linkTag = tag(b, "link");
    const href = (b.match(/<link[^>]*href="([^"]+)"/i) || [])[1];
    const src = b.match(/<source[^>]*url="([^"]*)"[^>]*>([\s\S]*?)<\/source>/i);
    return {
      title: decode(tag(b, "title")),
      link: decode(linkTag) || href || "",
      date: decode(tag(b, "pubDate") || tag(b, "updated") || tag(b, "published") || tag(b, "dc:date")),
      desc: decode(tag(b, "description") || tag(b, "summary") || tag(b, "content")).replace(/https?:\/\/\S+/g, " ").slice(0, 400),
      source: src ? decode(src[2]) : "",
      sourceUrl: src ? src[1] : "",
    };
  }).filter(i => i.title && i.link);
}

// Google 뉴스 제목 끝의 " - 언론사" 를 떼어요
// 출처 표기가 대소문자만 달라도("CHOSUNBIZ"), 끝에 붙은 이름이 신뢰 언론사면 떼요
function stripOutlet(title, source = "") {
  if (source && title.toLowerCase().endsWith(` - ${source}`.toLowerCase())) return title.slice(0, -(source.length + 3));
  const m = title.match(/\s[-|]\s([^-|]{2,30})$/);
  return m && outletTier(m[1].trim()) ? title.slice(0, m.index) : title;
}
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
// 스포츠·연예 매체는 같은 언론사 계열이어도 무역 뉴스 출처로 쓰지 않아요 (예: 스포츠동아의 연예인 관세 납부 기사)
const OUTLET_BLOCK = /스포츠|연예|스타뉴스|sports|entertain/i;
const BLOCK_HOST = /^(sports|star|enter|ent|stoo)\./;
function findOutlet(name = "", url = "") {
  const h = hostOf(url) || (/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(name) ? name.replace(/^www\./, "") : "");
  if (OUTLET_BLOCK.test(name) || (h && BLOCK_HOST.test(h))) return undefined;
  // 이름은 앞부분이 맞아야 인정해요. 예전처럼 "포함"만 보면 "스카이데일리" 안의 "이데일리",
  // "대한경제" 안의 "한경"이 화이트리스트로 통과했어요 (2026-10 출처 점검)
  const ln = name.toLowerCase().trim().replace(/^the\s+/, "");
  return OUTLETS.find(o => o.match.some(m => ln.startsWith(m.toLowerCase())) || (h && o.domains.some(d => h === d || h.endsWith("." + d))));
}
const outletTier = (name, url) => { const o = findOutlet(name, url); return o ? o.tier : 0; };
// "hankyung.com", "Chosunbiz" 처럼 제각각인 언론사 표기를 하나로 맞춰요 (그 언론사의 계열 매체 이름은 그대로)
const outletName = (name, url) => { const o = findOutlet(name, url); return o && (/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(name) || !name) ? o.match[0] : name; };

// ── 분류 ──
const lower = (s) => s.toLowerCase();
// 영문 단어는 단어 앞부분이 맞아야 인정 ("EU"가 "Reuters" 안에서 잡히지 않게). 한글은 그대로 포함 여부.
const reCache = new Map();
function hit(text, w) {
  const lw = lower(w);
  if (!/^[\x20-\x7e]+$/.test(w)) return text.includes(lw);
  let re = reCache.get(lw);
  // 2~4글자 영문 약어(IRA, LNG, FTA)는 단어 끝까지 맞아야 인정 ("IRA"가 "Iran" 안에서 잡히지 않게, 복수형 s 는 허용)
  const tail = /^[A-Z]{2,4}$/.test(w.trim()) ? "s?(?![a-z])" : "";
  if (!re) { re = new RegExp(`(^|[^a-z0-9])${lw.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}${tail}`); reCache.set(lw, re); }
  return re.test(text);
}
const has = (text, words) => words.some(w => hit(text, w));
const countHits = (text, words) => words.reduce((n, w) => n + (hit(text, w) ? 1 : 0), 0);

// 피드 기사 거르기: 주제 단어가 하나라도 있어야 무역 기사로 봐요 ("수입"만 있으면 소득 기사일 수 있어서)
function isTrade(text) { return TOPICS.some(t => has(text, t.kw)); }

// 지명·고유명사·다른 낱말 안에 우연히 들어간 단어는 지워요 (해운대 → "해운", 관세음 → "관세", 유통관리 → "통관")
const FALSE_HITS = /해운대|관세음|유통관/g;
function classify(item) {
  const text = lower(`${item.title} ${item.desc}`).replace(FALSE_HITS, "");
  const titleText = lower(item.title).replace(FALSE_HITS, "");
  // 제목에 나온 단어에 가중치 2, 요약에만 나오면 1
  const score = (words) => countHits(titleText, words) * 2 + countHits(text, words);
  // "관세"는 반덤핑 관세·관세청처럼 다른 주제 단어 안에도 들어 있어서, 동점이면 더 구체적인 주제를 앞에 둬요
  // 주제 단어가 다른 뜻으로 쓰인 제목(예: "마약 공급망")은 그 주제에서 빼요
  const excluded = (id) => (TOPIC_EXCLUDE[id] || []).some(w => titleText.includes(lower(w)));
  const topics = TOPICS.map(t => ({ id: t.id, s: excluded(t.id) ? 0 : score(t.kw) - (t.id === "tariff" ? 0.5 : 0) })).filter(t => t.s > 0).sort((a, b) => b.s - a.s);
  const sectors = SECTORS.map(s => ({ id: s.id, s: score(s.kw) })).filter(s => s.s > 0).sort((a, b) => b.s - a.s);
  const ctext = text.replace(/中企|中小/g, ""), ctitle = titleText.replace(/中企|中小/g, "");
  const cs = (words) => countHits(ctitle, words) * 2 + countHits(ctext, words);
  const countries = COUNTRIES.map(c => ({ id: c.id, s: cs(c.kw) })).filter(c => c.s > 0).sort((a, b) => b.s - a.s);
  const direction = MARKET_TITLE.test(item.title) ? "info" : actionDirection(text, topics[0] ? topics[0].id : "");
  // 우리 정부 발표는 1, 해외 정부 발표는 한국이 언급될 때만 1 (USTR·백악관 발표 전부가 한국 관련은 아니라서)
  const relevance = (item.official && item.lang !== "en") || has(text, KOREA_WORDS) ? 1 : (item.lang === "en" ? 0.5 : 0.8);
  return {
    topic: topics[0] ? topics[0].id : "export_trend",
    topics: topics.slice(0, 2).map(t => t.id),
    sectors: sectors.slice(0, 2).map(s => s.id),
    country: countries[0] ? countries[0].id : (item.countryHint || ""),
    direction, relevance,
  };
}

// ── 무역 조치 방향 ──
// 단어 하나씩만 세면 "제재 면제"가 제재(강화) 1 : 면제(완화) 1 로 비겨서 중립이 돼요.
// 그래서 "조치 대상 + 방향 동사" 짝(관세 인하, 제재 면제, 추가 관세, 관세 압박)을 먼저 찾아 2점을 줘요.
const MEASURE = "(관세|제재|규제|통제|제한|반덤핑|상계관세|세이프가드|쿼터|수입금지|tariffs?|sanctions?|duties|export controls?)";
// "0%"는 무관세를 뜻할 때만: 앞에 숫자가 붙은 "10%"·"300%"는 아니에요 (이전 버그: "관세 300%"가 완화로 분류됨)
const EASE_RE = new RegExp(`${MEASURE}[^.,…·]{0,12}?(면제|유예|완화|해제|철회|인하|폐지|철폐|연기|낮춰|낮춘|감면|상한|제외|(?<![\\d.])0\\s*%|exempt|waive|lift|cut|lower|pause|delay)|(관세|무역|통상)\\s*(합의|협정 타결)`, "i");
const TIGHT_RE = new RegExp([
  `(추가|보복|맞불|고율|폭탄|징벌적|new|higher|additional|retaliatory)\\s*${MEASURE}`,
  `${MEASURE}[^.,…·]{0,12}?(부과|인상|강화|확대|발동|폭탄|압박|위협|협박|발효|착수|상향|껑충|조사|가결|impose|hike|raise|expand|tighten)`,
  `(?<![\\d.])[1-9]\\d*\\s*%\\s*(까지\\s*)?(의\\s*)?(추가\\s*|보복\\s*|맞불\\s*)?(관세|tariff)`,   // "300% 관세", "50% 맞불관세"
  `반덤핑\\s*관세`,
].join("|"), "i");
// FTA·협정 발효·서명, 무관세 지위는 완화 쪽으로 봐요
const EASE_EXTRA = /(fta|협정)['’"”)\]\s]*(발효|서명|타결)|zero[- ]tariff|duty[- ]free|tariff[- ]free/i;
// "관세행정 지원대책 확대"처럼 지원·혜택을 늘리는 건 규제 강화가 아니에요
const SUPPORT_CTX = /(지원|혜택|특례|우대)[^.,…·]{0,8}(확대|강화)/;
// 정책 조치가 아닌 주제(환율·운임·수출 실적·원자재·공급망)는 방향이 없으면 "동향"으로 표시해요
const INFO_TOPICS = ["fx", "shipping", "export_trend", "energy", "supply_chain"];
// 무역 조치 맥락: 이런 단어가 하나도 없으면 "총반격"·"차단" 같은 단어가 있어도 무역 조치 방향을 붙이지 않아요
// (2026-10 사이트 검토: "예멘군 총반격" 기사가 '조치 강화'로 표시됨)
const TRADE_CTX = /관세|제재|규제|통제|수출|수입|무역|통상|반덤핑|상계|세이프가드|쿼터|협정|fta|엔티티|보조금|tariff|sanction|export|import|trade|dut(y|ies)|embargo|quota/i;
// 증시 시황 기사: 조치가 아니라 시장 반응이라 방향 대신 "동향"
const MARKET_TITLE = /증시|코스피|코스닥|나스닥|뉴욕증시|다우|주가|특징주|목표가|stocks? (fall|rise|slump|rally)|wall street/i;
function actionDirection(text, topic) {
  if (!TRADE_CTX.test(text)) return INFO_TOPICS.includes(topic) ? "info" : "neutral";
  const up = countHits(text, TIGHTEN) + (TIGHT_RE.test(text) && !SUPPORT_CTX.test(text) ? 2 : 0);
  const down = countHits(text, EASE) + (EASE_RE.test(text) || EASE_EXTRA.test(text) ? 2 : 0);
  if (up > down) return "up";
  if (down > up) return "down";
  return INFO_TOPICS.includes(topic) ? "info" : "neutral";
}

// 검색 주제(hint)와 단어 분류를 합쳐 최종 주제를 정해요. 무역 기사가 아니면 null (버림)
// - 단어 분류에 hint 주제가 있으면 hint 우선
// - 단어 분류가 다른 주제를 찾았으면 그 주제
// - 아무 주제 단어도 없으면: 수출·수입·무역·통상 단어가 있으면 "수출입 동향"으로, 없으면 버려요
//   (Google 뉴스 검색은 본문·비슷한 말로도 결과를 줘서 "세이프가드" 검색에 안전경영 기사가 섞여요)
// 게시판·인사·부고·사진 같은 단신은 무역 이슈가 아니라서 버려요
const SKIP_TITLE = /^\s*[\[【(]\s*(게시판|인사|부고|포토|사진|화보|운세|날씨|알림|모집|행사|표|그래픽)\s*[\]】)]/;
// 마약 단속은 관세청·세관 기사여도 무역 이슈가 아니라서 버려요 (2026-10: "마약 공급망 원천차단"이 통관 이슈 대표 기사로 나옴)
const NON_TRADE_TITLE = /마약/;
function finalize(item, hint) {
  if (SKIP_TITLE.test(item.title) || NON_TRADE_TITLE.test(item.title) || isPromo(item.title)) return null;
  const c = classify(item);
  if (hint && c.topics.includes(hint)) { c.topic = hint; c.topics = [hint, ...c.topics.filter(t => t !== hint)]; return c; }
  if (c.topics.length) return c;
  if (hint && has(lower(`${item.title} ${item.desc}`), TRADE_WORDS)) { c.topic = "export_trend"; c.topics = ["export_trend"]; return c; }
  return null;
}

// ── 제품 출시·행사 홍보 기사 거르기 ──
// 제목에 무역 핵심 단어(관세·수출·공급망·운임…)가 하나도 없는데 출시·개최·할인 같은 홍보성 단어가 있으면 true.
// 예) "HD현대사이트솔루션, 국내 최대 18t급 전동지게차 출시" → true (해운·물류 이슈에서 빠져요)
function isPromo(title = "") {
  const t = lower(title);
  return PROMO.some(w => t.includes(lower(w))) && !CORE_TRADE.some(w => hit(t, w));
}

// ── 의견·시황·홍보 문구 ──
// 사설·칼럼·기고·기자수첩, 증시 시황·특징주·목표가, 연설문 제목("~해 나가겠습니다")은 사실 보도가 아니라서
// 이슈 무게를 절반으로 하고, 대표 기사·속보로 쓰지 않아요
const OPINION_TITLE = /^\s*[\[【(<]\s*([^\]】)>]{0,6}(사설|칼럼|기고|시론|기자수첩|데스크|오피니언|논단|시평|특징주))\s*[\]】)>]|목표가|특징주|겠습니다["”’']?\s*$|^\s*(opinion|analysis|commentary|editorial|column)\s*[:|]/i;
const isOpinion = (title = "") => OPINION_TITLE.test(title) || MARKET_TITLE.test(title);

// ── 이슈 대표 기사 고르기 ──
// 관련도(0~1) = 주제 단어가 제목에 있으면 0.6 (요약에만 있으면 0.25)
//             + 상대국 이름이 제목에 있으면 0.3 (요약에만 0.1, 상대국 없는 이슈는 0.3)
//             + 무역 핵심 단어가 제목에 있으면 0.1
// 관련도가 기준(0.6) 이상인 기사 중 "무게 × (0.5 + 관련도)"가 가장 큰 기사를 대표로 써요. 기준을 넘는 기사가 없으면 관련도 1등.
function issueRelevance(a, topicId, countryId) {
  const t = lower(a.title || "").replace(FALSE_HITS, ""), d = lower(a.desc || "").replace(FALSE_HITS, "");
  const topic = TOPICS.find(x => x.id === topicId), country = COUNTRIES.find(x => x.id === countryId);
  let s = 0;
  if (topic && !(TOPIC_EXCLUDE[topicId] || []).some(w => t.includes(lower(w)))) s += has(t, topic.kw) ? 0.6 : has(d, topic.kw) ? 0.25 : 0;
  if (!country) s += 0.3;
  else {
    const ct = t.replace(/中企|中小/g, ""), cd = d.replace(/中企|中小/g, "");
    s += has(ct, country.kw) ? 0.3 : has(cd, country.kw) ? 0.1 : 0;
  }
  if (CORE_TRADE.some(w => hit(t, w))) s += 0.1;
  return Math.round(Math.min(1, s) * 100) / 100;
}
function rankForIssue(list, topicId, countryId, weightOf) {
  const scored = list.filter(a => !isPromo(a.title)).map(a => ({ a, rel: issueRelevance(a, topicId, countryId), w: weightOf(a) }));
  const pass = scored.filter(x => x.rel >= LEAD_MIN_RELEVANCE && !isOpinion(x.a.title)).sort((x, y) => y.w * (0.5 + y.rel) - x.w * (0.5 + x.rel));
  const rest = scored.filter(x => x.rel < LEAD_MIN_RELEVANCE || isOpinion(x.a.title)).sort((x, y) => y.rel - x.rel || y.w - x.w);
  return [...pass, ...rest];   // 앞쪽이 대표 기사 후보 (기준 통과 → 미달 순)
}

// ── 24시간 속보 ──
// 수집 시각 기준 최근 24시간(경계 포함) 안에 나온 신뢰 언론사 기사만, 최신순으로. 미래 시각(시계 오차 10분 초과)·홍보 기사는 빼요.
const BREAKING_HOURS = 24;
function breakingFrom(items, now = Date.now(), limit = 15) {
  const from = now - BREAKING_HOURS * 3_600_000, until = now + 10 * 60_000;
  return dedupeEvents(items
    .filter(a => { const t = Date.parse(a.date); return t >= from && t <= until; })
    .filter(a => (a.tier || outletTier(a.source)) >= 0.7 && !isPromo(a.title) && !isOpinion(a.title) && !NON_TRADE_TITLE.test(a.title))
    .sort((a, b) => Date.parse(b.date) - Date.parse(a.date)))
    .slice(0, limit);
}

// ── 화면용 같은 사건 합치기 ──
// 수집 때는 제목 유사도 0.55 이상만 같은 기사로 묶어요(다른 사건을 잘못 합치지 않게).
// 화면에서는 같은 이슈 안에서 3일 이내·유사도 0.35 이상이면 한 줄로 합치고 "N곳 보도"로 보여줘요.
// 예) "트럼프 '美에 공장 안 지으면 관세 300%'" 세 언론사 기사(유사도 0.45~0.53) → 한 줄
function dedupeEvents(list, threshold = 0.35) {
  const out = [];
  for (const a of list) {
    const sh = shingles(a.title), t = Date.parse(a.date);
    const same = out.find(x => Math.abs(x.t - t) < 3 * 86_400_000 && (x.a.topic || "") === (a.topic || "") && jaccard(x.sh, sh) >= threshold);
    if (same) { for (const o of a.outlets || [a.source]) if (!same.a.outlets.includes(o)) same.a.outlets.push(o); continue; }
    out.push({ a: { ...a, outlets: [...(a.outlets || [a.source])] }, sh, t });
  }
  return out.map(x => x.a);
}

// ── 중복 묶기 (제목 2글자 조각의 Jaccard 유사도, Broder 1997 shingling) ──
const normTitle = (t) => t.replace(/\[[^\]]*\]|\([^)]*\)|【[^】]*】/g, "").replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
function shingles(t) { const s = normTitle(t), set = new Set(); for (let i = 0; i < s.length - 1; i++) set.add(s.slice(i, i + 2)); return set; }
function jaccard(a, b) { let inter = 0; for (const x of a) if (b.has(x)) inter++; return inter / (a.size + b.size - inter || 1); }

module.exports = { dedupeEvents, isOpinion, findOutlet, breakingFrom, BREAKING_HOURS, isPromo, issueRelevance, rankForIssue, cleanDesc, actionDirection, fetchText, parseFeed, stripOutlet, outletTier, outletName, finalize, hostOf, isTrade, classify, shingles, jaccard, normTitle, sleep };
