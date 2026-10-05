// 서버 오류 집계 (관리용). 오류가 나면 record() 로 남기고, /api/health 는 건수만, /api/admin-errors 는 목록을 보여줘요.
// 메모리에만 두는 간단한 집계라 서버가 다시 시작되면 0부터 다시 세요. 오래 보관할 기록은 서버 로그(콘솔)에 같이 남겨요.
// 파일 이름이 "_" 로 시작하면 주소(/api/...)로 열리지 않는 내부 모듈이에요.
const MAX = 200;
const store = globalThis.__tcMetrics || (globalThis.__tcMetrics = { since: Date.now(), list: [] });

// 개인정보로 보일 만한 값(이메일, 전화번호, 긴 숫자)은 가려요
function mask(s, n = 200) {
  return String(s == null ? "" : s)
    .replace(/[^\s@]+@[^\s@]+\.[^\s@]+/g, "[email]")
    .replace(/\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b/g, "[phone]")
    .replace(/\d{6,}/g, "[number]")
    .slice(0, n);
}

// quiet: 이미 다른 형식으로 로그를 남긴 경우(브라우저 오류 [client-error]) 집계만 해요
function record(source, message, extra = {}, { quiet = false } = {}) {
  const item = { at: new Date().toISOString(), source: String(source).slice(0, 40), message: mask(message), ...extra };
  store.list.push(item);
  if (store.list.length > MAX) store.list.splice(0, store.list.length - MAX);
  if (!quiet) console.error("[server-error] " + JSON.stringify(item));
  return item;
}

function summary(hours = 24) {
  const from = Date.now() - hours * 3_600_000;
  const recent = store.list.filter(e => Date.parse(e.at) >= from);
  const bySource = {};
  for (const e of recent) bySource[e.source] = (bySource[e.source] || 0) + 1;
  return { last24h: recent.length, bySource, since: new Date(store.since).toISOString() };
}

const recent = (n = 50) => store.list.slice(-n).reverse();
const reset = () => { store.list = []; store.since = Date.now(); };

module.exports = { record, summary, recent, mask, reset };
