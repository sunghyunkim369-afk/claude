// 뉴스 스크립트 파일 위치: 환경변수가 없으면 지금 위치, 있으면 그 폴더 (paths.js)
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const { execFileSync } = require("child_process");
const ROOT = path.join(__dirname, "..");

const paths = (env) => JSON.parse(execFileSync(process.execPath, ["-e", "console.log(JSON.stringify(require('./scripts/news/paths')))"],
  { cwd: ROOT, env: { PATH: process.env.PATH, ...env } }).toString());

test("환경변수가 없으면 data/news 와 tradecompass-mvp 를 써요", () => {
  const p = paths({});
  assert.equal(p.ARCHIVE, path.join(ROOT, "data", "news", "archive.json"));
  assert.equal(p.LATEST, path.join(ROOT, "data", "news", "latest.json"));
  assert.equal(p.DATA_JS, path.join(ROOT, "tradecompass-mvp", "data.js"));
  assert.equal(p.BREAKING_JS, path.join(ROOT, "tradecompass-mvp", "breaking.js"));
});

test("NEWS_DATA_DIR·NEWS_WEB_DIR 로 바꾸고, NEWS_WEB_DIR=off 면 화면용 파일을 쓰지 않아요", () => {
  const p = paths({ NEWS_DATA_DIR: "/srv/news", NEWS_WEB_DIR: "/srv/web" });
  assert.equal(p.HISTORY, "/srv/news/history.json");
  assert.equal(p.BREAKING, "/srv/news/breaking.json");
  assert.equal(p.DATA_JS, "/srv/web/data.js");
  const off = paths({ NEWS_DATA_DIR: "/srv/news", NEWS_WEB_DIR: "off" });
  assert.equal(off.DATA_JS, null);
  assert.equal(off.BREAKING_JS, null);
});
