// 뉴스 파이프라인 공통 기능: RSS 읽기, 정리, 분류, 중복 묶기
const { OUTLETS, TOPICS, TRADE_WORDS, SECTORS, COUNTRIES, TIGHTEN, EASE, KOREA_WORDS } = require("./config");

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
function findOutlet(name = "", url = "") {
  const h = hostOf(url) || (/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(name) ? name.replace(/^www\./, "") : "");
  const ln = name.toLowerCase();
  return OUTLETS.find(o => o.match.some(m => ln.includes(m.toLowerCase())) || (h && o.domains.some(d => h === d || h.endsWith("." + d))));
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
  if (!re) { re = new RegExp(`(^|[^a-z0-9])${lw.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`); reCache.set(lw, re); }
  return re.test(text);
}
const has = (text, words) => words.some(w => hit(text, w));
const countHits = (text, words) => words.reduce((n, w) => n + (hit(text, w) ? 1 : 0), 0);

// 피드 기사 거르기: 주제 단어가 하나라도 있어야 무역 기사로 봐요 ("수입"만 있으면 소득 기사일 수 있어서)
function isTrade(text) { return TOPICS.some(t => has(text, t.kw)); }

// 지명·고유명사 안에 우연히 들어간 단어는 지워요 (해운대 → "해운", 관세음 → "관세")
const FALSE_HITS = /해운대|관세음/g;
function classify(item) {
  const text = lower(`${item.title} ${item.desc}`).replace(FALSE_HITS, "");
  const titleText = lower(item.title).replace(FALSE_HITS, "");
  // 제목에 나온 단어에 가중치 2, 요약에만 나오면 1
  const score = (words) => countHits(titleText, words) * 2 + countHits(text, words);
  // "관세"는 반덤핑 관세·관세청처럼 다른 주제 단어 안에도 들어 있어서, 동점이면 더 구체적인 주제를 앞에 둬요
  const topics = TOPICS.map(t => ({ id: t.id, s: score(t.kw) - (t.id === "tariff" ? 0.5 : 0) })).filter(t => t.s > 0).sort((a, b) => b.s - a.s);
  const sectors = SECTORS.map(s => ({ id: s.id, s: score(s.kw) })).filter(s => s.s > 0).sort((a, b) => b.s - a.s);
  const ctext = text.replace(/中企|中小/g, ""), ctitle = titleText.replace(/中企|中小/g, "");
  const cs = (words) => countHits(ctitle, words) * 2 + countHits(ctext, words);
  const countries = COUNTRIES.map(c => ({ id: c.id, s: cs(c.kw) })).filter(c => c.s > 0).sort((a, b) => b.s - a.s);
  const direction = actionDirection(text, topics[0] ? topics[0].id : "");
  const relevance = item.official || has(text, KOREA_WORDS) ? 1 : (item.lang === "en" ? 0.5 : 0.8);
  return {
    topic: topics[0] ? topics[0].id : "export_trend",
    topics: topics.slice(0, 2).map(t => t.id),
    sectors: sectors.slice(0, 2).map(s => s.id),
    country: countries[0] ? countries[0].id : "",
    direction, relevance,
  };
}

// ── 무역 조치 방향 ──
// 단어 하나씩만 세면 "제재 면제"가 제재(강화) 1 : 면제(완화) 1 로 비겨서 중립이 돼요.
// 그래서 "조치 대상 + 방향 동사" 짝(관세 인하, 제재 면제, 추가 관세, 관세 압박)을 먼저 찾아 2점을 줘요.
const MEASURE = "(관세|제재|규제|통제|제한|반덤핑|상계관세|세이프가드|쿼터|수입금지|tariffs?|sanctions?|duties|export controls?)";
const EASE_RE = new RegExp(`${MEASURE}[^.,…·]{0,12}?(면제|유예|완화|해제|철회|인하|폐지|철폐|연기|낮춰|낮춘|감면|상한|제외|0%|exempt|waive|lift|cut|lower|pause|delay)`, "i");
const TIGHT_RE = new RegExp(`(추가|보복|고율|폭탄|징벌적|new|higher|additional)\\s*${MEASURE}|${MEASURE}[^.,…·]{0,12}?(부과|인상|강화|확대|발동|폭탄|압박|위협|협박|발효|착수|상향|껑충|impose|hike|raise|expand|tighten)`, "i");
// 정책 조치가 아닌 주제(환율·운임·수출 실적·원자재·공급망)는 방향이 없으면 "동향"으로 표시해요
const INFO_TOPICS = ["fx", "shipping", "export_trend", "energy", "supply_chain"];
function actionDirection(text, topic) {
  const up = countHits(text, TIGHTEN) + (TIGHT_RE.test(text) ? 2 : 0);
  const down = countHits(text, EASE) + (EASE_RE.test(text) ? 2 : 0);
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
const SKIP_TITLE = /^\s*[\[【(]\s*(게시판|인사|부고|포토|사진|화보|운세|날씨|알림|모집|행사)\s*[\]】)]/;
function finalize(item, hint) {
  if (SKIP_TITLE.test(item.title)) return null;
  const c = classify(item);
  if (hint && c.topics.includes(hint)) { c.topic = hint; c.topics = [hint, ...c.topics.filter(t => t !== hint)]; return c; }
  if (c.topics.length) return c;
  if (hint && has(lower(`${item.title} ${item.desc}`), TRADE_WORDS)) { c.topic = "export_trend"; c.topics = ["export_trend"]; return c; }
  return null;
}

// ── 중복 묶기 (제목 2글자 조각의 Jaccard 유사도, Broder 1997 shingling) ──
const normTitle = (t) => t.replace(/\[[^\]]*\]|\([^)]*\)|【[^】]*】/g, "").replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
function shingles(t) { const s = normTitle(t), set = new Set(); for (let i = 0; i < s.length - 1; i++) set.add(s.slice(i, i + 2)); return set; }
function jaccard(a, b) { let inter = 0; for (const x of a) if (b.has(x)) inter++; return inter / (a.size + b.size - inter || 1); }

module.exports = { cleanDesc, actionDirection, fetchText, parseFeed, stripOutlet, outletTier, outletName, finalize, hostOf, isTrade, classify, shingles, jaccard, normTitle, sleep };
