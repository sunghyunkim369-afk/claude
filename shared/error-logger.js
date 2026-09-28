// 브라우저 에러 수집기: 처리되지 않은 에러를 모아 /api/log 로 보내요.
// eyefeet(서버 함수가 있는 곳)에서만 전송하고, GitHub Pages·아티팩트·파일로 열었을 때는 보내지 않아요.
(() => {
  const ENDPOINT = "/api/log";
  const MAX_PER_PAGE = 10;           // 한 페이지에서 보낼 최대 에러 수
  const host = location.hostname;
  const enabled = /^https?:$/.test(location.protocol) && (host.endsWith("eyefeet.com") || host === "localhost" || host === "127.0.0.1");
  const seen = new Set();
  let sent = 0;

  function report(e) {
    const key = `${e.type}|${e.message}|${e.source}|${e.line}`;
    if (seen.has(key) || sent >= MAX_PER_PAGE) return;
    seen.add(key); sent += 1;
    const payload = { ...e, page: location.pathname + location.hash };
    if (!enabled) return;
    const body = JSON.stringify(payload);
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }))) return;
      fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
    } catch { /* 로깅 실패가 페이지를 깨뜨리지 않게 무시 */ }
  }

  addEventListener("error", ev => {
    // 이미지·스크립트 로드 실패도 잡아요 (ev.target이 요소인 경우)
    if (ev.target && ev.target !== window && ev.target.tagName) {
      report({ type: "resource", message: `${ev.target.tagName} 로드 실패`, source: ev.target.src || ev.target.href || "" });
      return;
    }
    report({ type: "error", message: ev.message, source: ev.filename, line: ev.lineno, col: ev.colno, stack: ev.error && ev.error.stack });
  }, true);

  addEventListener("unhandledrejection", ev => {
    const r = ev.reason || {};
    report({ type: "promise", message: String(r.message || r), stack: r.stack });
  });

  // 직접 기록할 때: window.tcLog("메시지", { 추가 정보 })
  window.tcLog = (message, extra) => report({ type: "manual", message: String(message), stack: extra ? JSON.stringify(extra).slice(0, 1000) : undefined });
})();
