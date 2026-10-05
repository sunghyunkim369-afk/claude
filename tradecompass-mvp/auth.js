// 로그인 · 회원가입 창 (window.tcAuth)
// 서버(/api/auth)가 있는 eyefeet 에서만 실제로 가입·로그인돼요. 다른 곳(GitHub Pages 등)에서는 창은 보이되 안내만 해요.
// 로그인하면 관심 섹터가 계정에 저장돼서 다른 기기에서도 같아져요. (app.js 가 "tc-auth" 이벤트를 받아 합쳐요)
(() => {
const host = location.hostname;
const SERVER = /^https?:$/.test(location.protocol) && (host.endsWith("eyefeet.com") || host === "localhost" || host === "127.0.0.1");
const LIVE_URL = "https://tcmvp.eyefeet.com";
const SECTORS = [["semi", "반도체"], ["auto", "자동차"], ["battery", "2차전지"], ["steel", "철강·금속"], ["chem", "석유화학·에너지"], ["ship", "조선·해운물류"], ["machinery", "기계·전자"], ["consumer", "소비재·농식품"]];
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let user = null, mode = "login", unavailable = !SERVER, lastFocus = null;

async function api(body) {
  const r = await fetch("/api/auth", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (r.status === 503 && j.code === "not_configured") unavailable = true;
  if (!r.ok) { const e = new Error(j.error || "잠시 문제가 생겼어요."); e.field = j.field; e.status = r.status; throw e; }
  return j;
}
const emit = () => document.dispatchEvent(new CustomEvent("tc-auth", { detail: user }));

// ── 머리글 버튼 ──
const slot = document.createElement("div");
slot.className = "auth-slot";
document.querySelector(".top-badges")?.after(slot);
function renderSlot() {
  slot.innerHTML = user
    ? `<button type="button" class="me-btn" data-auth="menu" aria-haspopup="true" aria-expanded="false"><span class="av">${esc(user.name.slice(0, 1))}</span><span class="nm">${esc(user.name)}</span></button>
       <div class="me-menu" hidden role="menu">
         <p><b>${esc(user.name)}</b><small>${esc(user.email)}</small></p>
         <a href="#/watch" role="menuitem">관심 섹터 <em>${user.watch.length}</em></a>
         <button type="button" role="menuitem" data-auth="logout">로그아웃</button>
       </div>`
    : `<button type="button" class="ghost-btn" data-auth="open-login">로그인</button><button type="button" class="join-btn" data-auth="open-signup">회원가입</button>`;
}

// ── 창 ──
const dlg = document.createElement("dialog");
dlg.className = "auth";
dlg.setAttribute("aria-labelledby", "auth-title");
document.body.appendChild(dlg);

const field = (id, label, type, extra = "") => `
  <label class="f" for="${id}"><span>${label}</span>
    <span class="in">${`<input id="${id}" name="${id}" type="${type}" ${extra}>`}${type === "password" ? `<button type="button" class="eye" data-auth="eye" aria-label="비밀번호 보기">보기</button>` : ""}</span>
    <em class="msg" data-msg="${id}"></em></label>`;

function renderDialog() {
  const signup = mode === "signup";
  dlg.innerHTML = `
    <div class="auth-wrap">
      <aside class="auth-side">
        <svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M21.5 10.5 17.8 17.8 10.5 21.5 14.2 14.2Z" fill="currentColor"/></svg>
        <h3>Trade<em>Compass</em></h3>
        <p>회원이 되면</p>
        <ul>
          <li><b>관심 섹터 동기화</b>휴대폰·PC 어디서나 같은 관심 섹터</li>
          <li><b>내 섹터 먼저</b>대시보드에서 관심 섹터를 바로 확인</li>
          <li class="soon"><b>주간 브리핑 메일</b>매주 월요일 Top 10 요약 (준비 중)</li>
        </ul>
      </aside>
      <div class="auth-main">
        <button type="button" class="x-close" data-auth="close" aria-label="닫기">×</button>
        <div class="tabs" role="tablist">
          <button type="button" role="tab" aria-selected="${!signup}" data-auth="tab-login">로그인</button>
          <button type="button" role="tab" aria-selected="${signup}" data-auth="tab-signup">회원가입</button>
        </div>
        <h2 id="auth-title">${signup ? "무역나침반 계정 만들기" : "다시 오셨네요"}</h2>
        <p class="sub">${signup ? "이메일로 1분이면 가입할 수 있어요." : "이메일과 비밀번호로 로그인하세요."}</p>
        ${unavailable ? `<div class="notice">${SERVER ? "회원 기능을 준비하고 있어요. 서버 설정이 끝나면 바로 이용할 수 있어요." : `회원 기능은 <a href="${LIVE_URL}" target="_blank" rel="noopener">eyefeet 사이트</a>에서 이용할 수 있어요. 여기서는 화면만 미리 볼 수 있어요.`}</div>` : ""}
        <form novalidate data-auth-form>
          ${signup ? field("au-name", "이름", "text", 'maxlength="20" autocomplete="nickname" placeholder="표시할 이름 (20자까지)" required') : ""}
          ${field("au-email", "이메일", "email", 'autocomplete="email" inputmode="email" placeholder="name@example.com" required')}
          ${field("au-pw", "비밀번호", "password", `autocomplete="${signup ? "new-password" : "current-password"}" maxlength="72" placeholder="${signup ? "영문·숫자 포함 8자 이상" : "비밀번호"}" required`)}
          ${signup ? `<ul class="rules" aria-live="polite"><li data-rule="len">8자 이상</li><li data-rule="mix">영문과 숫자 포함</li><li data-rule="same">비밀번호 확인 일치</li></ul>
            ${field("au-pw2", "비밀번호 확인", "password", 'autocomplete="new-password" maxlength="72" placeholder="한 번 더 입력" required')}
            <fieldset class="pick"><legend>관심 섹터 <small>선택 · 나중에 바꿀 수 있어요</small></legend>
              ${SECTORS.map(([id, n]) => `<label><input type="checkbox" name="watch" value="${id}"><span>${n}</span></label>`).join("")}
            </fieldset>
            <label class="agree"><input type="checkbox" id="au-agree"> <span><b>[필수]</b> 개인정보 수집·이용에 동의해요</span></label>
            <details class="terms"><summary>내용 보기</summary>
              <p>수집 항목: 이메일, 이름, 관심 섹터 · 목적: 로그인과 관심 섹터 저장 · 보관: 탈퇴할 때까지(탈퇴 요청 시 바로 삭제) · 비밀번호는 복원할 수 없는 방식(암호화 해시)으로만 저장해요. 동의하지 않으면 가입할 수 없지만, 로그인 없이도 모든 기능을 쓸 수 있어요.</p>
            </details>
            <em class="msg" data-msg="au-agree"></em>`
          : `<label class="agree"><input type="checkbox" id="au-remember"> <span>로그인 상태 유지 (30일)</span></label>`}
          <p class="form-err" data-msg="form" role="alert"></p>
          <button class="submit" ${unavailable ? "disabled" : ""}>${signup ? "가입하고 시작하기" : "로그인"}</button>
        </form>
        <p class="switch">${signup ? `이미 계정이 있나요? <button type="button" data-auth="tab-login">로그인</button>` : `처음이신가요? <button type="button" data-auth="tab-signup">회원가입</button>`}</p>
      </div>
    </div>`;
}

function open(m) {
  mode = m; lastFocus = document.activeElement;
  renderDialog();
  if (!dlg.open) dlg.showModal();
  document.documentElement.classList.add("modal-open");
  setTimeout(() => dlg.querySelector(mode === "signup" ? "#au-name" : "#au-email")?.focus(), 30);
}
function close() { if (dlg.open) dlg.close(); }
dlg.addEventListener("close", () => { document.documentElement.classList.remove("modal-open"); lastFocus?.focus?.(); });
dlg.addEventListener("click", e => { if (e.target === dlg) close(); });   // 바깥(배경) 누르면 닫기

// ── 입력 확인 ──
const $d = s => dlg.querySelector(s);
const setMsg = (k, t) => { const el = $d(`[data-msg="${k}"]`); if (el) el.textContent = t || ""; const inp = $d("#" + k); if (inp) inp.setAttribute("aria-invalid", t ? "true" : "false"); };
const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;
function rules() {
  const pw = $d("#au-pw")?.value || "", pw2 = $d("#au-pw2")?.value || "";
  const r = { len: pw.length >= 8, mix: /[A-Za-z]/.test(pw) && /\d/.test(pw), same: !!pw && pw === pw2 };
  Object.entries(r).forEach(([k, ok]) => $d(`[data-rule="${k}"]`)?.classList.toggle("ok", ok));
  return r;
}
function validate() {
  let ok = true;
  const bad = (k, t) => { setMsg(k, t); if (t) ok = false; };
  const email = $d("#au-email").value.trim();
  bad("au-email", !email ? "이메일을 입력해 주세요." : !EMAIL.test(email) ? "이메일 형식을 확인해 주세요." : "");
  if (mode === "signup") {
    const name = $d("#au-name").value.trim();
    bad("au-name", name ? "" : "이름을 입력해 주세요.");
    const r = rules();
    bad("au-pw", !r.len ? "8자 이상 입력해 주세요." : !r.mix ? "영문과 숫자를 함께 넣어 주세요." : "");
    bad("au-pw2", r.same ? "" : "비밀번호가 서로 달라요.");
    bad("au-agree", $d("#au-agree").checked ? "" : "필수 동의에 체크해 주세요.");
  } else bad("au-pw", $d("#au-pw").value ? "" : "비밀번호를 입력해 주세요.");
  return ok;
}

dlg.addEventListener("input", e => {
  if (mode === "signup" && /au-pw/.test(e.target.id)) rules();
  if (e.target.id) setMsg(e.target.id, "");
  setMsg("form", "");
});

dlg.addEventListener("submit", async e => {
  e.preventDefault();
  if (unavailable || !validate()) { dlg.querySelector('[aria-invalid="true"]')?.focus(); return; }
  const btn = $d(".submit"), label = btn.textContent;
  btn.disabled = true; btn.textContent = "잠시만요…";
  try {
    const body = mode === "signup"
      ? { action: "signup", name: $d("#au-name").value.trim(), email: $d("#au-email").value.trim(), password: $d("#au-pw").value,
          agree: $d("#au-agree").checked, watch: [...dlg.querySelectorAll('input[name="watch"]:checked')].map(i => i.value) }
      : { action: "login", email: $d("#au-email").value.trim(), password: $d("#au-pw").value, remember: $d("#au-remember").checked };
    user = (await api(body)).user;
    renderSlot(); close(); emit();
    toast(mode === "signup" ? `${user.name}님, 가입을 환영해요!` : `${user.name}님, 반가워요.`);
  } catch (err) {
    if (unavailable) { renderDialog(); return; }
    if (err.field) setMsg(err.field === "password" ? "au-pw" : err.field === "agree" ? "au-agree" : "au-" + err.field, err.message);
    else setMsg("form", err.message);
    btn.disabled = false; btn.textContent = label;
  }
});

document.addEventListener("click", async e => {
  const a = e.target.closest("[data-auth]");
  const menu = slot.querySelector(".me-menu");
  if (menu && !menu.hidden && !e.target.closest(".me-menu, [data-auth='menu']")) toggleMenu(false);
  if (!a) return;
  const act = a.dataset.auth;
  if (act === "open-login" || act === "tab-login") open("login");
  else if (act === "open-signup" || act === "tab-signup") open("signup");
  else if (act === "close") close();
  else if (act === "eye") {
    const inp = a.previousElementSibling, show = inp.type === "password";
    inp.type = show ? "text" : "password"; a.textContent = show ? "숨기기" : "보기"; a.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 보기");
  } else if (act === "menu") toggleMenu(menu.hidden);
  else if (act === "logout") {
    try { await api({ action: "logout" }); } catch {}
    user = null; renderSlot(); emit(); toast("로그아웃했어요.");
  }
});
function toggleMenu(show) {
  const menu = slot.querySelector(".me-menu"), btn = slot.querySelector(".me-btn");
  if (!menu) return;
  menu.hidden = !show; btn.setAttribute("aria-expanded", show);
}
document.addEventListener("keydown", e => { if (e.key === "Escape") toggleMenu(false); });

// ── 알림 ──
function toast(t) {
  const el = document.createElement("div");
  el.className = "toast"; el.setAttribute("role", "status"); el.textContent = t;
  document.body.appendChild(el);
  setTimeout(() => el.classList.add("out"), 2400);
  setTimeout(() => el.remove(), 2900);
}

// ── 관심 섹터 저장 (app.js 가 불러요) ──
let saveTimer = null;
function syncWatch(list) {
  if (!user) return;
  user.watch = list; renderSlot();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api({ action: "watch", watch: list }).catch(() => {}), 600);
}

renderSlot();
// 이미 로그인돼 있으면 내 정보를 불러와요
if (SERVER) api({ action: "me" }).then(j => { user = j.user; renderSlot(); emit(); }).catch(() => {});

window.tcAuth = { open, get user() { return user; }, syncWatch };
})();
