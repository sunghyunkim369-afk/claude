// 페이지 공통 AI 호출 도우미: window.tcAI
// eyefeet(서버 함수가 있는 곳)와 로컬 테스트에서만 동작하고, 그 밖에서는 enabled=false 예요.
(() => {
  const host = location.hostname;
  const enabled = /^https?:$/.test(location.protocol) &&
    (host.endsWith("eyefeet.com") || host === "localhost" || host === "127.0.0.1");
  const LIVE_URL = "https://tcmvp.eyefeet.com";

  // task: "hs" | "sector" | "ask"
  async function ask(task, { query = "", context = "" } = {}) {
    if (!enabled) throw new Error("AI 분석은 eyefeet 사이트에서 이용할 수 있어요.");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 60_000);
    try {
      const r = await fetch("/api/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, query, context }),
        signal: ctrl.signal,
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "AI 응답을 받지 못했어요.");
      return j;
    } catch (e) {
      if (e.name === "AbortError") throw new Error("AI 응답이 1분 넘게 걸려 멈췄어요. 잠시 뒤 다시 시도해 주세요.");
      throw e;
    } finally { clearTimeout(timer); }
  }

  // 기다리는 동안 "AI가 분석 중이에요 · 12초" 처럼 경과 시간을 보여줘요
  function progress(el, label = "AI가 분석 중이에요") {
    const t0 = Date.now();
    const tick = () => { el.textContent = `${label} · ${Math.floor((Date.now() - t0) / 1000)}초 (보통 20~40초)`; };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }

  window.tcAI = { enabled, ask, progress, LIVE_URL };
})();
