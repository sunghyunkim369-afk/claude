// 이미지 기능 미리보기 (한 번 돌려 보는 점검용, 화면에는 아직 쓰지 않아요)
// 1) 공식 방송사 유튜브 채널의 최신 영상(RSS, 키 불필요)을 받아 이번 주 Top10 이슈와 제목 유사도로 짝지어 봐요
// 2) Openverse(무료 라이선스 이미지 검색, 키 불필요)에서 주제·섹터별 사진 후보를 받아요 (CC0·공공영역·CC BY 만)
// 결과: data/media-preview/ 아래 youtube.json, photos.json, 썸네일 이미지
const fs = require("fs");
const path = require("path");
const { parseFeed, shingles, jaccard } = require("../news/lib");

const OUT = path.join(__dirname, "..", "..", "data", "media-preview");
const UA = { "User-Agent": "TradeCompass/1.0 (student project; media preview)" };
// 채널 ID 는 핸들 페이지(@...)에서 찾아요. 후보 핸들을 여러 개 두고 처음 찾은 것을 써요.
const CHANNELS = [
  ["연합뉴스TV", ["yonhapnewstv23", "yonhapnewstv"]], ["YTN", ["ytnnews24", "YTN"]], ["KBS News", ["newskbs", "KBSNEWS"]],
  ["SBS 뉴스", ["sbsnews8", "SBSNEWS"]], ["MBCNEWS", ["MBCNEWS11", "MBCNEWS"]], ["JTBC News", ["jtbc_news", "JTBCNEWS"]],
  ["한국경제TV", ["wowtv", "hkwowtv"]], ["SBS Biz", ["SBSBiz", "sbsbiz"]],
];
async function channelId(handles) {
  for (const h of handles) {
    try {
      const html = await get(`https://www.youtube.com/@${h}`);
      const m = html.match(/"externalId":"(UC[\w-]{22})"/) || html.match(/channel\/(UC[\w-]{22})/);
      if (m) return { handle: h, id: m[1] };
    } catch (e) { console.log(`  @${h}: ${e.message}`); }
  }
  return null;
}
const PHOTO_QUERIES = {
  tariff: "container port cranes", export_control: "semiconductor wafer", trade_remedy: "steel coils",
  shipping: "container ship sea", supply_chain: "cargo terminal", energy: "oil tanker", fx: "currency exchange",
  sanctions: "oil tanker sea", agreement: "flags conference", customs: "customs cargo inspection",
  semi: "semiconductor chip", auto: "car carrier ship", battery: "lithium battery cells", steel: "steel mill",
  chem: "petrochemical plant", ship: "shipyard", machinery: "electronics factory", consumer: "food market",
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function get(url, asBuf) {
  const r = await fetch(url, { headers: UA, redirect: "follow" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return asBuf ? Buffer.from(await r.arrayBuffer()) : r.text();
}

async function youtube() {
  const vids = [];
  for (const [name, handles] of CHANNELS) {
    try {
      const ch = await channelId(handles);
      if (!ch) throw new Error("채널 ID 못 찾음");
      let xml;
      try { xml = await get(`https://www.youtube.com/feeds/videos.xml?channel_id=${ch.id}`); }
      catch (e) { xml = await get(`https://www.youtube.com/feeds/videos.xml?playlist_id=UU${ch.id.slice(2)}`); }   // 업로드 목록 피드로 다시
      console.log(`  ${name}: @${ch.handle} → ${ch.id}`);
      const title = (xml.match(/<title>([^<]*)<\/title>/) || [])[1];
      const entries = xml.match(/<entry>[\s\S]*?<\/entry>/g) || [];
      for (const e of entries) {
        const vid = (e.match(/<yt:videoId>([^<]+)/) || [])[1];
        const t = (e.match(/<title>([^<]*)<\/title>/) || [])[1] || "";
        const at = (e.match(/<published>([^<]+)/) || [])[1];
        if (vid) vids.push({ channel: name, id: vid, title: t.replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&#39;/g, "'"), at });
      }
      console.log(`youtube ${name}: OK "${title}" ${entries.length}개`);
    } catch (e) { console.log(`youtube ${name}: 실패 ${e.message}`); }
    await sleep(500);
  }
  // 이번 주 Top10 이슈와 짝짓기: 이슈 대표 기사·관련 기사 제목과의 최대 유사도
  const latest = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "data", "news", "latest.json"), "utf8"));
  const matches = latest.issues.map(x => {
    const refs = [x.title, ...(x.articles || []).map(a => a.title)].map(shingles);
    const scored = vids.map(v => ({ v, s: Math.max(...refs.map(r => jaccard(r, shingles(v.title)))) })).sort((a, b) => b.s - a.s);
    return { issue: x.keyword, lead: x.title, best: scored.slice(0, 3).map(({ v, s }) => ({ ...v, sim: Math.round(s * 100) / 100 })) };
  });
  fs.mkdirSync(path.join(OUT, "yt"), { recursive: true });
  for (const m of matches) for (const v of m.best.slice(0, 1)) {
    try { fs.writeFileSync(path.join(OUT, "yt", `${v.id}.jpg`), await get(`https://i.ytimg.com/vi/${v.id}/hqdefault.jpg`, true)); } catch {}
  }
  fs.writeFileSync(path.join(OUT, "youtube.json"), JSON.stringify({ videos: vids.length, matches }, null, 1));
  matches.forEach(m => console.log(`- [${m.issue}] ${m.lead.slice(0, 40)}\n    → ${m.best[0] ? `${m.best[0].sim} ${m.best[0].channel} · ${m.best[0].title.slice(0, 50)}` : "없음"}`));
}

// Wikimedia Commons: 키 없이 검색되고 라이선스 정보(extmetadata)를 같이 줘요. CC0·공공영역·CC BY·CC BY-SA 만, 가로 사진만.
const OK_LICENSE = /^(cc0|public domain|pd|cc by(-sa)? [1-4]\.0)/i;
async function photos() {
  const out = {};
  fs.mkdirSync(path.join(OUT, "photos"), { recursive: true });
  for (const [key, q] of Object.entries(PHOTO_QUERIES)) {
    try {
      const api = `https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=20&gsrsearch=${encodeURIComponent(q + " filetype:bitmap")}&prop=imageinfo&iiprop=url|size|extmetadata&iiurlwidth=960`;
      const pages = Object.values(JSON.parse(await get(api)).query?.pages || {}).sort((a, b) => a.index - b.index);
      out[key] = [];
      for (const pg of pages) {
        const ii = (pg.imageinfo || [])[0]; if (!ii) continue;
        const md = ii.extmetadata || {}, lic = (md.LicenseShortName?.value || "").trim();
        if (!OK_LICENSE.test(lic) || ii.width < ii.height * 1.2 || ii.width < 900) continue;
        const file = `${key}-${out[key].length}.jpg`;
        try { fs.writeFileSync(path.join(OUT, "photos", file), await get(ii.thumburl, true)); } catch { continue; }
        out[key].push({ file, title: pg.title.replace(/^File:/, ""), creator: (md.Artist?.value || "").replace(/<[^>]+>/g, "").trim().slice(0, 80), license: lic, source: ii.descriptionurl });
        await sleep(300);
        if (out[key].length >= 4) break;
      }
      console.log(`photo ${key} (${q}): 후보 ${pages.length} → ${out[key].length}장`);
    } catch (e) { console.log(`photo ${key}: 실패 ${e.message}`); }
    await sleep(600);
  }
  fs.writeFileSync(path.join(OUT, "photos.json"), JSON.stringify(out, null, 1));
}

(async () => { await youtube(); await photos(); })();
