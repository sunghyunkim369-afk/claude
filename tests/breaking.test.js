// 24시간 속보 경계 시각 테스트: node --test
const test = require("node:test");
const assert = require("node:assert/strict");
const { breakingFrom } = require("../scripts/news/lib");

const NOW = Date.parse("2026-10-05T09:00:00Z");
const H = 3_600_000, M = 60_000;
const at = (ms) => new Date(NOW - ms).toISOString();
const item = (title, ago, extra = {}) => ({ title, date: at(ago), source: "연합뉴스", tier: 0.9, ...extra });

test("24시간 경계: 23시간 59분 전은 포함, 24시간 1분 전은 제외", () => {
  const out = breakingFrom([
    item("A 관세 23h59m", 23 * H + 59 * M),
    item("B 관세 24h01m", 24 * H + 1 * M),
    item("C 관세 정확히 24h", 24 * H),
  ], NOW).map(a => a.title);
  assert.deepEqual(out, ["A 관세 23h59m", "C 관세 정확히 24h"]);
});

test("미래 시각(10분 넘게)·신뢰 언론사가 아닌 기사·홍보 기사는 제외", () => {
  const out = breakingFrom([
    item("미래 관세", -2 * H),
    item("시계 오차 5분 관세", -5 * M),
    item("블로그 관세", 1 * H, { source: "어느 블로그", tier: 0 }),
    item("신제품 전동지게차 출시", 1 * H),
  ], NOW).map(a => a.title);
  assert.deepEqual(out, ["시계 오차 5분 관세"]);
});

test("최신순 정렬과 최대 개수", () => {
  const list = Array.from({ length: 20 }, (_, i) => item(`관세 ${i}`, (i + 1) * H));
  const out = breakingFrom(list, NOW, 5);
  assert.equal(out.length, 5);
  assert.deepEqual(out.map(a => a.title), ["관세 0", "관세 1", "관세 2", "관세 3", "관세 4"]);
});

test("시세표·게시판 같은 단신은 속보에서 빼요", () => {
  const { finalize } = require("../scripts/news/lib");
  assert.equal(finalize({ title: "[표] 외국환율고시표", desc: "환율", lang: "ko" }, "fx"), null);
  assert.equal(finalize({ title: "[게시판] 관세청 설명회", desc: "", lang: "ko" }, "customs"), null);
  assert.notEqual(finalize({ title: "원/달러 환율 1,350원대 하락…수출기업 부담", desc: "", lang: "ko" }, "fx"), null);
});
