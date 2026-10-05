// 서버 함수(api/*.js)를 실제 HTTP 없이 부르는 테스트 도우미. 브라우저 한 개 = Jar 한 개(쿠키 보관).
let n = 0;
class Jar {
  // 브라우저마다 다른 IP (분당 시도 제한이 테스트끼리 섞이지 않게)
  constructor() { this.cookie = ""; this.last = ""; this.ip = `10.0.${Math.floor(++n / 250)}.${n % 250}`; }
  async call(handler, body, { origin = "https://tcmvp.eyefeet.com", ip = this.ip, method = "POST", headers = {} } = {}) {
    const req = { method, body, url: "/api", headers: { origin, host: "tcmvp.eyefeet.com", cookie: this.cookie, "x-forwarded-for": ip, ...headers } };
    const res = { statusCode: 200, headers: {}, setHeader(k, v) { this.headers[k.toLowerCase()] = v; }, end(b) { this.body = b; } };
    await handler(req, res);
    const sc = res.headers["set-cookie"];
    if (sc) { this.last = sc; this.cookie = /Max-Age=0/.test(sc) ? "" : sc.split(";")[0]; }
    let json = null; try { json = JSON.parse(res.body); } catch {}
    return { status: res.statusCode, body: json, setCookie: sc };
  }
}
module.exports = { Jar };
