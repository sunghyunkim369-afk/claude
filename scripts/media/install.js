// 고른 사진을 사이트에 넣어요: data/media-preview/photos → tradecompass-mvp/img/photos/*.webp (가로 1200px, 약 100KB)
// 고르는 목록: scripts/media/photos.config.json  {"tariff": ["tariff-0", "tariff-4"], ...}
// 출처 정보는 tradecompass-mvp/photos.js (window.TC_PHOTOS) 로 만들어 화면에서 작게 표시해요.
// 후보 원본(data/media-preview/photos)은 저장소에 두지 않아요(용량). 사진을 다시 고를 때는 먼저 Media preview 작업으로 후보를 받아요.
// 실행: NODE_PATH=$(npm root -g) node scripts/media/install.js   (사진 크기 조정에 Playwright 의 Chromium 을 써요)
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const ROOT = path.join(__dirname, "..", "..");
const SRC = path.join(ROOT, "data", "media-preview");
const DEST = path.join(ROOT, "tradecompass-mvp", "img", "photos");
const pick = JSON.parse(fs.readFileSync(path.join(__dirname, "photos.config.json"), "utf8"));
const meta = Object.values(JSON.parse(fs.readFileSync(path.join(SRC, "photos.json"), "utf8"))).flat();

(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM || "/opt/pw-browsers/chromium" });
  const p = await b.newPage();
  fs.rmSync(DEST, { recursive: true, force: true });
  fs.mkdirSync(DEST, { recursive: true });
  const out = {};
  let total = 0;
  for (const [key, files] of Object.entries(pick)) {
    out[key] = [];
    for (const name of files) {
      const m = meta.find(x => x.file === `${name}.jpg`);
      if (!m) { console.log(`없음: ${name}`); continue; }
      const file = `${name}.webp`;          // 여러 주제에 같은 사진을 쓰면 파일은 하나만
      if (fs.existsSync(path.join(DEST, file))) { out[key].push({ f: `img/photos/${file}`, by: m.creator || "Wikimedia Commons", lic: m.license, src: m.source, t: m.title }); continue; }
      const dataUrl = "data:image/jpeg;base64," + fs.readFileSync(path.join(SRC, "photos", m.file)).toString("base64");
      // data: URL 은 캔버스가 오염되지 않아서 다시 저장할 수 있어요
      const webp = await p.evaluate(async (src) => {
        const img = new Image(); img.src = src; await img.decode();
        const w = Math.min(1200, img.naturalWidth), h = Math.round(img.naturalHeight * w / img.naturalWidth);
        const c = document.createElement("canvas"); c.width = w; c.height = h;
        c.getContext("2d").drawImage(img, 0, 0, w, h);
        return c.toDataURL("image/webp", 0.72);
      }, dataUrl);
      const buf = Buffer.from(webp.split(",")[1], "base64");
      fs.writeFileSync(path.join(DEST, file), buf);
      total += buf.length;
      out[key].push({ f: `img/photos/${file}`, by: m.creator || "Wikimedia Commons", lic: m.license, src: m.source, t: m.title });
    }
  }
  await b.close();
  fs.writeFileSync(path.join(ROOT, "tradecompass-mvp", "photos.js"),
    `// 주제·섹터별 사진 — scripts/media/install.js 가 만들어요. 모두 Wikimedia Commons 의 자유 라이선스(CC0·공공영역·CC BY·CC BY-SA) 사진이에요.\n` +
    `window.TC_PHOTOS = ${JSON.stringify(out, null, 1)};\n`);
  console.log(`사진 ${Object.values(out).flat().length}장 · ${(total / 1024).toFixed(0)}KB`);
})();
