// 관리자용 최근 서버 오류 목록. GET /api/admin-errors  (헤더 Authorization: Bearer <ADMIN_TOKEN>)
// ADMIN_TOKEN 환경변수가 없으면 이 주소는 꺼져 있어요(404). 오류 메시지 속 이메일·전화번호·긴 숫자는 가려져 있어요.
const crypto = require("crypto");
const metrics = require("./_metrics");

const same = (a, b) => { const x = Buffer.from(String(a)), y = Buffer.from(String(b)); return x.length === y.length && crypto.timingSafeEqual(x, y); };

module.exports = function handler(req, res) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  const token = process.env.ADMIN_TOKEN;
  if (!token) { res.statusCode = 404; return res.end('{"error":"not found"}'); }
  const given = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (!given || !same(given, token)) { res.statusCode = 401; return res.end('{"error":"관리자 토큰이 필요해요."}'); }
  res.statusCode = 200;
  res.end(JSON.stringify({ summary: metrics.summary(24), recent: metrics.recent(50) }));
};
