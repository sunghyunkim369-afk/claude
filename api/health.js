// 가동 상태 확인: 모니터링(GitHub Actions)이 매시간 이 주소를 불러요.
// 환경변수는 값이 아니라 "설정됐는지"만 알려줘요. 값은 절대 응답에 넣지 않아요.

const EXPECTED = ["AI_BASE_URL", "AI_API_KEY", "ANTHROPIC_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "DATABASE_URL", "SESSION_SECRET"];

module.exports = function handler(req, res) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.statusCode = 200;
  res.end(JSON.stringify({
    ok: true,
    time: new Date().toISOString(),
    env: Object.fromEntries(EXPECTED.map(k => [k, Boolean(process.env[k])])),
  }));
};
