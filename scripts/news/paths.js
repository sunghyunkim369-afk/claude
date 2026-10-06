// 뉴스 데이터 파일 위치 — 환경변수로 바꿀 수 있어요 (없으면 지금 위치 그대로).
//   NEWS_DATA_DIR : archive.json · latest.json · history.json · breaking.json 을 두는 폴더 (기본 data/news)
//   NEWS_WEB_DIR  : 화면이 바로 읽는 data.js · breaking.js 를 쓰는 폴더 (기본 tradecompass-mvp). "off" 면 쓰지 않아요.
// 상대 경로는 명령을 실행한 폴더 기준이에요. Node 기본 모듈만 써요.
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "..");
const env = (k) => (process.env[k] || "").trim();

const DATA_DIR = env("NEWS_DATA_DIR") ? path.resolve(env("NEWS_DATA_DIR")) : path.join(ROOT, "data", "news");
const WEB_OFF = env("NEWS_WEB_DIR").toLowerCase() === "off";
const WEB_DIR = WEB_OFF ? null : env("NEWS_WEB_DIR") ? path.resolve(env("NEWS_WEB_DIR")) : path.join(ROOT, "tradecompass-mvp");

const data = (f) => path.join(DATA_DIR, f);
const web = (f) => (WEB_DIR ? path.join(WEB_DIR, f) : null);

// 파일을 쓰기 전에 폴더를 만들어 둬요. 웹 폴더가 꺼져 있으면(null) 아무것도 하지 않아요.
function write(file, text) {
  if (!file) return false;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
  return true;
}

module.exports = {
  DATA_DIR, WEB_DIR, write,
  ARCHIVE: data("archive.json"),
  LATEST: data("latest.json"),
  HISTORY: data("history.json"),
  BREAKING: data("breaking.json"),
  DATA_JS: web("data.js"),
  BREAKING_JS: web("breaking.js"),
};
