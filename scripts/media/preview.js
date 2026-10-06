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
// 키(주제·섹터)마다 검색어 여러 개 → 후보 최대 6장
const PHOTO_QUERIES = {
  tariff: ["Busan port container terminal", "container cranes port"], export_trend: ["Busan New Port", "Incheon port container"],
  export_control: ["semiconductor wafer", "semiconductor cleanroom"], trade_remedy: ["steel coils", "steel plates warehouse"],
  sanctions: ["oil tanker sea", "tanker ship strait"], shipping: ["container ship sea", "HMM container ship"],
  supply_chain: ["container terminal aerial", "rare earth minerals"], energy: ["LNG carrier", "oil refinery night"],
  fx: ["Korean won banknotes", "United States dollar banknotes"], agreement: ["flags South Korea United States", "trade agreement signing ceremony"],
  customs: ["container x-ray scanner", "customs inspection container"], subsidy: ["battery factory production line", "electric vehicle factory"],
  semi: ["semiconductor wafer", "semiconductor chip macro"], auto: ["car carrier ship", "Hyundai Glovis"],
  battery: ["lithium ion battery cells", "battery factory"], steel: ["steel mill", "POSCO Pohang"],
  chem: ["Ulsan petrochemical", "oil refinery"], ship: ["Hyundai Heavy Industries shipyard", "Okpo shipyard", "shipyard crane ship construction"],
  machinery: ["industrial robot factory", "electronics factory"], consumer: ["supermarket shelves", "Korean food market", "cosmetics store"],
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
// 429(요청 너무 많음)·5xx 면 잠깐 쉬었다가 다시 (최대 4번)
async function get(url, asBuf) {
  for (let i = 0; ; i++) {
    const r = await fetch(url, { headers: UA, redirect: "follow" });
    if (r.ok) return asBuf ? Buffer.from(await r.arrayBuffer()) : r.text();
    if (i >= 3 || !(r.status === 429 || r.status >= 500)) throw new Error(`HTTP ${r.status}`);
    await sleep(5000 * (i + 1));
  }
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
    await sleep(2000);
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
  // 이미 받은 키는 그대로 두고 4장 미만인 키만 다시 받아요
  let out = {};
  try { out = JSON.parse(fs.readFileSync(path.join(OUT, "photos.json"), "utf8")); } catch {}
  fs.mkdirSync(path.join(OUT, "photos"), { recursive: true });
  for (const [key, qs] of Object.entries(PHOTO_QUERIES)) {
   if ((out[key] || []).length >= 4) continue;
   out[key] = [];
   for (const q of qs) {
    try {
      const api = `https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search&gsrnamespace=6&gsrlimit=20&gsrsearch=${encodeURIComponent(q + " filetype:bitmap")}&prop=imageinfo&iiprop=url|size|extmetadata&iiurlwidth=960`;
      const pages = Object.values(JSON.parse(await get(api)).query?.pages || {}).sort((a, b) => a.index - b.index);
      for (const pg of pages) {
        const ii = (pg.imageinfo || [])[0]; if (!ii) continue;
        const md = ii.extmetadata || {}, lic = (md.LicenseShortName?.value || "").trim();
        if (!OK_LICENSE.test(lic) || ii.width < ii.height * 1.2 || ii.width < 900) continue;
        const file = `${key}-${out[key].length}.jpg`;
        try { fs.writeFileSync(path.join(OUT, "photos", file), await get(ii.thumburl, true)); } catch { continue; }
        out[key].push({ file, title: pg.title.replace(/^File:/, ""), creator: (md.Artist?.value || "").replace(/<[^>]+>/g, "").trim().slice(0, 80), license: lic, source: ii.descriptionurl });
        await sleep(2000);
        if (out[key].length >= Math.ceil(6 * (qs.indexOf(q) + 1) / qs.length)) break;
      }
      console.log(`photo ${key} (${q}): 후보 ${pages.length} → 누적 ${out[key].length}장`);
    } catch (e) { console.log(`photo ${key}: 실패 ${e.message}`); }
    await sleep(2500);
   }
  }
  fs.writeFileSync(path.join(OUT, "photos.json"), JSON.stringify(out, null, 1));
}

(async () => { await photos(); })();   // 유튜브는 GitHub 서버에서 막혀 있어 API 키가 생기면 다시 켜요
