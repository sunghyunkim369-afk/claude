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
const decode = (s = "") => s
  .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
  .replace(/<[^>]+>/g, " ")
  .replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
  .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&amp;/g, "&")
  .replace(/\s+/g, " ").trim();
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
      desc: decode(tag(b, "description") || tag(b, "summary") || tag(b, "content")).slice(0, 400),
      source: src ? decode(src[2]) : "",
      sourceUrl: src ? src[1] : "",
    };
  }).filter(i => i.title && i.link);
}

// Google 뉴스 제목 끝의 " - 언론사" 를 떼어요
const stripOutlet = (title, source) => source && title.endsWith(` - ${source}`) ? title.slice(0, -(source.length + 3)) : title;
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
function findOutlet(name = "", url = "") {
  const h = hostOf(url) || (/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(name) ? name.replace(/^www\./, "") : "");
  return OUTLETS.find(o => o.match.some(m => name.includes(m)) || (h && o.domains.some(d => h === d || h.endsWith("." + d))));
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

function classify(item) {
  const text = lower(`${item.title} ${item.desc}`);
  const titleText = lower(item.title);
  // 제목에 나온 단어에 가중치 2, 요약에만 나오면 1
  const score = (words) => countHits(titleText, words) * 2 + countHits(text, words);
  // "관세"는 반덤핑 관세·관세청처럼 다른 주제 단어 안에도 들어 있어서, 동점이면 더 구체적인 주제를 앞에 둬요
  const topics = TOPICS.map(t => ({ id: t.id, s: score(t.kw) - (t.id === "tariff" ? 0.5 : 0) })).filter(t => t.s > 0).sort((a, b) => b.s - a.s);
  const sectors = SECTORS.map(s => ({ id: s.id, s: score(s.kw) })).filter(s => s.s > 0).sort((a, b) => b.s - a.s);
  const ctext = text.replace(/中企|中小/g, ""), ctitle = titleText.replace(/中企|中小/g, "");
  const cs = (words) => countHits(ctitle, words) * 2 + countHits(ctext, words);
  const countries = COUNTRIES.map(c => ({ id: c.id, s: cs(c.kw) })).filter(c => c.s > 0).sort((a, b) => b.s - a.s);
  const up = countHits(text, TIGHTEN), down = countHits(text, EASE);
  const direction = up > down ? "up" : down > up ? "down" : "neutral";
  const relevance = item.official || has(text, KOREA_WORDS) ? 1 : (item.lang === "en" ? 0.5 : 0.8);
  return {
    topic: topics[0] ? topics[0].id : "export_trend",
    topics: topics.slice(0, 2).map(t => t.id),
    sectors: sectors.slice(0, 2).map(s => s.id),
    country: countries[0] ? countries[0].id : "",
    direction, relevance,
  };
}

// 검색 주제(hint)와 단어 분류를 합쳐 최종 주제를 정해요. 무역 기사가 아니면 null (버림)
// - 단어 분류에 hint 주제가 있으면 hint 우선
// - 단어 분류가 다른 주제를 찾았으면 그 주제
// - 아무 주제 단어도 없으면: 수출·수입·무역·통상 단어가 있으면 "수출입 동향"으로, 없으면 버려요
//   (Google 뉴스 검색은 본문·비슷한 말로도 결과를 줘서 "세이프가드" 검색에 안전경영 기사가 섞여요)
function finalize(item, hint) {
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

module.exports = { fetchText, parseFeed, stripOutlet, outletTier, outletName, finalize, hostOf, isTrade, classify, shingles, jaccard, normTitle, sleep };
