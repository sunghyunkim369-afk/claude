// 가동 상태 확인: 모니터링(GitHub Actions)이 매시간 이 주소를 불러요. (/health 로도 열려요)
// 환경변수는 값이 아니라 "설정됐는지"만, 오류는 내용 없이 "최근 24시간 건수"만 알려줘요.
// 오류 내용은 관리자용 /api/admin-errors (ADMIN_TOKEN 필요) 에서 봐요.
const metrics = require("./_metrics");

const EXPECTED = ["AI_BASE_URL", "AI_API_KEY", "ANTHROPIC_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "DATABASE_URL", "SESSION_SECRET", "ADMIN_TOKEN"];

module.exports = function handler(req, res) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.statusCode = 200;
  res.end(JSON.stringify({
    ok: true,
    time: new Date().toISOString(),
    uptimeSec: Math.round(process.uptime()),
    env: Object.fromEntries(EXPECTED.map(k => [k, Boolean(process.env[k])])),
    auth: process.env.AUTH_DISABLED === "1" ? "off" : process.env.DATABASE_URL ? "postgres" : "file",
    errors: metrics.summary(24),
  }));
};
