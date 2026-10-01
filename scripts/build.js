// eyefeet(vercel 런타임)는 빌드 결과 폴더만 정적 파일로 보여줘요.
// 사이트 파일을 dist/ 로 복사해요. eyefeet는 /app/dist 를 정적 폴더로 봐요. (api/ 는 서버 함수라 복사하지 않아요)
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const OUT = path.join(ROOT, "dist");
const ITEMS = ["index.html", "tradecompass-mvp", "hs-code-finder", "shared"];

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
for (const item of ITEMS) {
  fs.cpSync(path.join(ROOT, item), path.join(OUT, item), { recursive: true });
}
console.log(`dist/ 에 ${ITEMS.join(", ")} 복사 완료`);
