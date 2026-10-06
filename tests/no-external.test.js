// 팀 규칙: 글꼴은 직접 제공해요. 페이지가 외부 주소에서 글꼴·스타일·스크립트를 받지 않아야 해요.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..");

for (const f of ["index.html", "tradecompass-mvp/index.html", "hs-code-finder/index.html"]) {
  test(`${f}: 외부 글꼴·스타일·스크립트가 없고 CSP 가 font-src 'self'`, () => {
    const s = fs.readFileSync(path.join(ROOT, f), "utf8");
    assert.doesNotMatch(s, /<(link|script)[^>]+(href|src)="https?:/);
    assert.doesNotMatch(s, /fonts\.googleapis|fonts\.gstatic/);
    assert.match(s, /font-src 'self'/);
  });
}

test("shared/fonts 에 woff2 와 OFL 라이선스가 있어요", () => {
  const dir = path.join(ROOT, "shared", "fonts");
  const css = fs.readFileSync(path.join(dir, "fonts.css"), "utf8");
  for (const m of css.matchAll(/url\("([^"]+)"\)/g)) assert.ok(fs.existsSync(path.join(dir, m[1])), m[1]);
  assert.match(css, /font-display:swap/);
  assert.ok(fs.existsSync(path.join(dir, "OFL-NotoSansKR.txt")) && fs.existsSync(path.join(dir, "OFL-Fraunces.txt")));
});
