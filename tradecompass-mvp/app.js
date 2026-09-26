(() => {
const D = window.TC_DATA;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sectorById = Object.fromEntries(D.sectors.map(s => [s.id, s]));
const enDate = new Date(D.meta.date + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });

// ── 관심 섹터 (브라우저에만 저장) ──
const WKEY = "tc-watch";
const loadWatch = () => { try { return new Set(JSON.parse(localStorage.getItem(WKEY) || "[]")); } catch { return new Set(); } };
const saveWatch = set => { try { localStorage.setItem(WKEY, JSON.stringify([...set])); } catch {} };
let watch = loadWatch();

// ── 조각들 ──
const needleDeg = score => Math.max(-150, Math.min(150, (score - 50) * 3));
const compass = (score, cls = "compass") => `
  <svg class="${cls}" viewBox="0 0 38 38" aria-hidden="true">
    <circle cx="19" cy="19" r="17" fill="#FBF5EA" stroke="var(--brass)" stroke-width="1.5"/>
    <g class="n" data-deg="${needleDeg(score)}">
      <path d="M19 5 22 19 19 33 16 19Z" fill="var(--navy)"/>
      <path d="M19 19 22 19 19 33 16 19Z" fill="#8C97A8"/>
    </g>
    <circle cx="19" cy="19" r="1.6" fill="#FBF5EA"/>
  </svg>`;
const countUp = (to, dec = 0, pre = "", suf = "") =>
  `<span class="num" data-to="${to}" data-dec="${dec}" data-pre="${esc(pre)}" data-suf="${esc(suf)}">${pre}${Number(to).toFixed(dec)}${suf}</span>`;
const dirLabel = { up: "▲ 긍정", down: "▼ 부정", flat: "● 중립" };
const signed = (v, suf = "%") => `${v > 0 ? "+" : ""}${v}${suf}`;
const star = id => `<button class="star" data-star="${id}" aria-pressed="${watch.has(id)}" aria-label="${esc(sectorById[id].name)} 관심 섹터 ${watch.has(id) ? "해제" : "추가"}">★</button>`;

const sectorCard = (s, i) => `
  <a class="card lift sector rise" style="--i:${i}" href="#/sectors/${s.id}">
    ${compass(s.score)}
    <span class="name">${esc(s.name)}</span>
    <div class="score">${countUp(s.score, 2)}</div>
    <div class="state ${s.state}"><i></i>${s.state}</div>
    <div class="split"><span>▲ 강화 ${s.up}</span><span>▼ 완화 ${s.down}</span><span>● 중립 ${s.neutral}</span></div>
    <div class="meter"><b data-w="${s.score}"></b></div>
  </a>`;

const issueItem = (x, i) => `
  <li class="issue">
    <span class="rank">${String(i + 1).padStart(2, "0")}</span>
    <div>
      <h3>${esc(x.title)}</h3>
      <div class="src">${esc(x.source)} · ${esc(x.time)}</div>
      <span class="tag">${esc(x.tag)}</span>
      ${x.summary ? `<p class="sum">${esc(x.summary)}</p>` : ""}
    </div>
    <div class="impact ${x.impact >= 85 ? "hi" : ""}"><b class="num">${countUp(x.impact)}</b><small>영향도</small></div>
  </li>`;

const newsRow = n => `
  <li class="news-row">
    <div class="t"><b class="num">${esc(n.time)}</b><small>${esc(n.source)}</small></div>
    <div><h3><span class="tag">${esc(n.tag)}</span>${esc(n.title)}</h3><p>AI 요약 · ${esc(n.summary)}</p></div>
    <div class="links">${n.stocks.length ? n.stocks.map(s => `<span>${esc(s.name)} <em class="dir ${s.dir}">${dirLabel[s.dir]}</em></span>`).join("") : `<span class="muted">연관 종목 없음</span>`}</div>
  </li>`;

const riskItem = r => `
  <li class="risk ${r.level}">
    <span class="lv ${r.level}">${r.level}</span>
    <div><h3>${esc(r.title)}</h3><p>${esc(r.detail)}</p></div>
    <span class="eff">${esc(r.effect)}</span>
  </li>`;

const bars = (rows, label) => {
  const max = Math.max(...rows.map(r => r.value));
  return `<ul class="bars">${rows.map(r => `
    <li class="bar"><span>${esc(label(r))}</span>
      <span class="track"><span class="fill" data-w="${(r.value / max * 100).toFixed(1)}"></span></span>
      <span class="v num">$${r.value.toFixed(1)}B</span>
      <span class="y ${r.yoy >= 0 ? "pos" : "neg"}">${signed(r.yoy)}</span></li>`).join("")}</ul>`;
};

// 섹터 상세: 이 섹터의 Top10 이슈 + 대표 기사 + 종목 정렬 결과
const pct = (now, prev) => prev ? Math.round((now - prev) / prev * 100) : 0;
function sectorPage(s) {
  const list = D.issues.filter(x => x.sectors.includes(s.id)).sort((a, b) => b.score - a.score).slice(0, 10);
  return `
    <div class="sp">
      <a class="back-link rise" href="#/">← 홈으로</a>
      <div class="sp-head rise" style="--i:1">
        ${compass(s.score)}
        <div><h1>${esc(s.name)}</h1><div class="state ${s.state}"><i></i>${s.state} · 노출도 <b class="num">${s.score.toFixed(2)}</b></div></div>
        ${star(s.id)}
      </div>

      <h2 class="sp-h rise" style="--i:2">이 섹터의 Top10 이슈</h2>
      ${list.length ? `<ol class="top10">${list.map((x, i) => `
        <li class="rise" style="--i:${i + 3}">
          <span class="rank num">${i + 1}</span>
          <div class="t10">
            <a class="kw" href="#/news">${esc(x.keyword)}</a>
            <div class="chips-row">${x.sectors.map(id => sectorById[id] ? `<a class="schip${id === s.id ? " on" : ""}" href="#/sectors/${id}">${esc(sectorById[id].name)}</a>` : "").join("")}</div>
            <p class="meta">점수 ${x.score.toFixed(2)} · 최근 7일 보도 ${x.reports}건(직전 7일 ${x.prev}건) · ${signed(pct(x.reports, x.prev))}</p>
            <p class="meta">▲ 조치 강화 ${x.up}&nbsp;&nbsp;▼ 완화 ${x.down}&nbsp;&nbsp;● 중립 ${x.neutral}</p>
            <ul class="arts">${x.articles.map(a => `<li><span class="at">${esc(a.title)}</span><small>${esc(a.source)} · ${esc(a.at)}</small></li>`).join("")}</ul>
          </div>
        </li>`).join("")}</ol>` : `<p class="muted rise" style="--i:3">최근 7일 동안 이 섹터와 연결된 이슈가 없어요.</p>`}

      <section class="card rise sort-card" style="--i:${list.length + 3}">
        <h2>종목 정렬 결과</h2>
        <ul class="sort-list">${s.stocks.map(k => `<li><span>${esc(k.name)}</span><small class="num">${k.code}</small></li>`).join("")}</ul>
        <div class="pending">시세 데이터 준비 중</div>
      </section>
    </div>`;
}

// ── 화면 ──
const views = {
  home() {
    const b = D.bearing;
    const top = [...D.sectors].sort((a, b) => Math.abs(b.score - 50) - Math.abs(a.score - 50))[0];
    const t = D.trade;
    return `
      <header class="hero rise">
        <p class="kicker">Daily Brief · ${esc(enDate)}</p>
        <h1 class="display">Global Trade <em>Intelligence</em></h1>
        <p>무역 이슈가 국내 섹터와 종목에 주는 영향을 AI가 매일 정리합니다.</p>
      </header>

      <section class="bearing rise" style="--i:1">
        <svg class="rose" viewBox="0 0 300 300" aria-hidden="true">
          <g class="ring" fill="none" stroke="currentColor">
            <circle cx="150" cy="150" r="140" stroke-width="1"/>
            <circle cx="150" cy="150" r="112" stroke-width="1" stroke-dasharray="2 6"/>
            ${Array.from({length: 36}, (_, k) => `<line x1="150" y1="${k % 9 === 0 ? 4 : 10}" x2="150" y2="22" stroke-width="${k % 9 === 0 ? 2 : 1}" transform="rotate(${k * 10} 150 150)"/>`).join("")}
          </g>
          <g class="needle" style="--deg:${needleDeg(top.score)}deg">
            <path d="M150 50 162 150 150 250 138 150Z" fill="#2E4670"/>
            <path d="M150 50 162 150 138 150Z" fill="var(--brass)"/>
          </g>
        </svg>
        <div class="eyebrow">TODAY'S BEARING</div>
        <h2>${esc(b.headline)}</h2>
        <ul>${b.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>
      </section>

      <div class="sec-h rise" style="--i:2"><h2>섹터별 노출도</h2><span>최근 7일 뉴스 기준 · 50 = 중립</span></div>
      <div class="sectors">${D.sectors.map((s, i) => sectorCard(s, i + 3)).join("")}</div>
      <p class="note rise" style="--i:6">노출도는 최근 7일간 관련 뉴스의 보도량과 방향을 지수로 나타낸 참고 지표입니다. 실제 판단은 개별 정보를 함께 확인하신 뒤 직접 하시기 바랍니다.</p>

      <div class="grid g-2" style="margin-top:22px">
        <section class="card rise" style="--i:7">
          <div class="card-h"><div><h2>오늘의 핵심 무역 이슈</h2><p>영향도 · 확산 속도 기준</p></div><a class="more" href="#/news">전체 이슈 →</a></div>
          <ol class="issues">${D.issues.slice(0, 3).map(issueItem).join("")}</ol>
        </section>
        <section class="card rise" style="--i:8">
          <div class="card-h"><div><h2>공급망 리스크 신호</h2><p>${D.risks.length}개 신호 감시 중</p></div></div>
          <ul class="risks">${D.risks.map(riskItem).join("")}</ul>
        </section>
      </div>

      <section class="card rise" style="--i:9;margin-top:18px">
        <div class="card-h"><div><h2>뉴스 × 연관 종목 분석</h2><p>AI가 뉴스에서 종목 영향 경로를 추출했어요</p></div><a class="more" href="#/news">뉴스 피드 →</a></div>
        <ul class="news">${D.news.slice(0, 3).map(newsRow).join("")}</ul>
      </section>

      <section class="card rise" style="--i:10;margin-top:18px">
        <div class="card-h"><div><h2>국가·품목별 무역 흐름</h2><p>대한민국 월간 수출 · ${esc(t.period)}</p></div></div>
        <div class="kpis">
          <div class="kpi"><small>총 수출액</small><b>${countUp(t.total, 1, "$", "B")}</b></div>
          <div class="kpi"><small>전년 대비</small><b class="${t.yoy >= 0 ? "pos" : ""}">${countUp(t.yoy, 1, t.yoy >= 0 ? "+" : "", "%")}</b></div>
          <div class="kpi"><small>무역 수지</small><b>${countUp(t.balance, 1, "+$", "B")}</b></div>
        </div>
        <div class="grid g-2b">
          <div><p class="flow-h">주요 수출국</p>${bars(t.countries, r => r.name)}</div>
          <div><p class="flow-h">주요 수출 품목</p>${bars(t.items, r => r.name)}</div>
        </div>
      </section>`;
  },

  news(q) {
    const tags = ["전체", ...new Set(D.news.map(n => n.tag))];
    const cur = tags.includes(q) ? q : "전체";
    const list = D.news.filter(n => cur === "전체" || n.tag === cur);
    return `
      <div class="page-h rise"><p class="kicker">Newsroom</p><h1 class="display sm">Trade <em>News</em></h1><p>오늘 수집한 ${D.meta.sources.toLocaleString()}건 가운데 AI가 고른 핵심 뉴스와 이슈예요.</p></div>
      <div class="grid g-2">
        <section class="card rise" style="--i:1">
          <div class="card-h"><h2>뉴스 × 연관 종목</h2></div>
          <div class="filters">${tags.map(t => `<a class="chip" href="#/news/${encodeURIComponent(t)}" aria-pressed="${t === cur}">${esc(t)}</a>`).join("")}</div>
          <ul class="news">${list.map(newsRow).join("")}</ul>
        </section>
        <section class="card rise" style="--i:2">
          <div class="card-h"><h2>핵심 이슈 전체</h2></div>
          <ol class="issues">${D.issues.map(issueItem).join("")}</ol>
        </section>
      </div>`;
  },

  sectors(id) {
    if (id && sectorById[id]) return sectorPage(sectorById[id]);
    return `
      <div class="page-h rise"><p class="kicker">Sector Exposure</p><h1 class="display sm">Sector <em>Radar</em></h1><p>섹터를 누르면 관련 Top10 이슈와 기사를 볼 수 있어요.</p></div>
      <div class="grid">${D.sectors.map((s, i) => `
        <a class="card lift rise sector-row" style="--i:${i + 1}" href="#/sectors/${s.id}">
          ${compass(s.score)}
          <div>
            <h2>${esc(s.name)} <span class="state ${s.state}"><i></i>${s.state}</span></h2>
            <p>${esc(s.summary)}</p>
          </div>
          <div class="big">${countUp(s.score, 2)}</div>
        </a>`).join("")}</div>`;
  },

  watch() {
    const mine = D.sectors.filter(s => watch.has(s.id));
    return `
      <div class="page-h rise"><p class="kicker">Watchlist</p><h1 class="display sm">My <em>Sectors</em></h1><p>별을 눌러 관심 섹터를 고르면 대시보드처럼 모아 볼 수 있어요. (이 브라우저에만 저장돼요)</p></div>
      <section class="card rise" style="--i:1">
        ${D.sectors.map(s => `<div class="watch-row">${star(s.id)}<div><h3>${esc(s.name)}</h3><p>${esc(s.summary)}</p></div><b class="num">${s.score.toFixed(2)}</b></div>`).join("")}
      </section>
      <div class="sec-h rise" style="--i:2"><h2>내 관심 섹터</h2><span>${mine.length}개</span></div>
      ${mine.length ? `<div class="sectors">${mine.map((s, i) => sectorCard(s, i + 3)).join("")}</div>` : `<div class="card empty rise" style="--i:3">아직 고른 섹터가 없어요. 위 목록에서 ★을 눌러 보세요.</div>`}`;
  },

  hs(q) {
    const src = "../hs-code-finder/index.html" + (q ? "#q=" + encodeURIComponent(q) : "");
    return `
      <div class="page-h rise"><p class="kicker">Tools</p><h1 class="display sm">HS Code <em>&amp; Duty</em></h1><p>품목의 HS 코드를 찾고, 개인 직구·사업 수입 예상 세금을 계산해요.</p></div>
      <iframe class="tool-frame rise" style="--i:1" src="${src}" title="HS 코드 찾기와 세금 계산"></iframe>`;
  }
};

// ── 동작 붙이기 ──
function animate(root) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    root.querySelectorAll("[data-w]").forEach(el => el.style.width = el.dataset.w + "%");
    root.querySelectorAll(".compass .n").forEach((el, i) => setTimeout(() => el.style.setProperty("--deg", el.dataset.deg + "deg"), reduce ? 0 : 200 + i * 80));
  }));
  if (reduce) return;
  root.querySelectorAll(".num[data-to]").forEach(el => {
    const to = +el.dataset.to, dec = +el.dataset.dec, pre = el.dataset.pre, suf = el.dataset.suf;
    const t0 = performance.now(), dur = 1100;
    const tick = now => {
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = pre + (to * e).toFixed(dec) + suf;
      if (p < 1) requestAnimationFrame(tick);
    };
    el.textContent = pre + (0).toFixed(dec) + suf;
    requestAnimationFrame(tick);
  });
}

function route() {
  const [, name = "", arg] = location.hash.replace(/^#/, "").split("/");
  const key = views[name] ? name : "home";
  const main = $("#view");
  main.innerHTML = views[key](arg ? decodeURIComponent(arg) : undefined);
  main.classList.remove("enter"); void main.offsetWidth; main.classList.add("enter");
  document.querySelectorAll("nav a").forEach(a => a.dataset.view === key ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  animate(main);
  closeMenu();
  window.scrollTo(0, 0);
}

document.addEventListener("click", e => {
  const s = e.target.closest("[data-star]");
  if (!s) return;
  e.preventDefault();
  const id = s.dataset.star;
  watch.has(id) ? watch.delete(id) : watch.add(id);
  saveWatch(watch);
  if (location.hash.startsWith("#/watch")) route();
  else document.querySelectorAll(`[data-star="${id}"]`).forEach(b => b.setAttribute("aria-pressed", watch.has(id)));
});

$("#quick").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#quick-q").value.trim();
  location.hash = "#/hs" + (q ? "/" + encodeURIComponent(q) : "");
});

const side = $("#side"), scrim = $("#scrim"), menu = $("#menu");
function closeMenu() { side.classList.remove("open"); scrim.classList.remove("show"); menu.setAttribute("aria-expanded", "false"); }
menu.addEventListener("click", () => { const o = !side.classList.contains("open"); side.classList.toggle("open", o); scrim.classList.toggle("show", o); menu.setAttribute("aria-expanded", o); });
scrim.addEventListener("click", closeMenu);

// ── 머리글 ──
const d = new Date(D.meta.date + "T00:00:00");
$("#top-date").textContent = d.toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });
$("#upd").textContent = D.meta.updatedAt;
$("#sample-pill").hidden = !D.meta.sample;
$("#nav-news").textContent = D.news.length;

addEventListener("hashchange", route);
route();
})();
