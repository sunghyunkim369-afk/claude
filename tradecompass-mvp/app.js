(() => {
let D = window.TC_DATA;
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
let sectorById = Object.fromEntries(D.sectors.map(s => [s.id, s]));
const enDate = () => new Date(D.meta.date + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
// 원문 기사 링크 (http/https 만 허용)
const ext = (url, text) => /^https?:\/\//.test(url || "") ? `<a class="ext" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;

// ── 관심 섹터 (브라우저에만 저장) ──
const WKEY = "tc-watch";
const loadWatch = () => { try { return new Set(JSON.parse(localStorage.getItem(WKEY) || "[]")); } catch { return new Set(); } };
const saveWatch = set => {
  try { localStorage.setItem(WKEY, JSON.stringify([...set])); } catch {}
  if (window.tcAuth) tcAuth.syncWatch([...set]);   // 로그인 중이면 계정에도 저장
};
let watch = loadWatch();
// 로그인하면 이 기기의 관심 섹터와 계정의 관심 섹터를 합쳐요
document.addEventListener("tc-auth", e => {
  const u = e.detail;
  setTimeout(refreshMy);
  if (!u) return;
  const merged = new Set([...watch, ...u.watch]);
  const changed = merged.size !== u.watch.length || merged.size !== watch.size;
  watch = merged;
  try { localStorage.setItem(WKEY, JSON.stringify([...watch])); } catch {}
  if (changed && merged.size !== u.watch.length) tcAuth.syncWatch([...watch]);
  document.querySelectorAll("[data-star]").forEach(b => b.setAttribute("aria-pressed", watch.has(b.dataset.star)));
  if (changed && location.hash.startsWith("#/watch")) route();
});

// ── 조각들 ──
const needleDeg = score => Math.max(-150, Math.min(150, (score - 50) * 3));
const compass = (score, cls = "compass") => `
  <svg class="${cls}" viewBox="0 0 38 38" aria-hidden="true">
    <circle cx="19" cy="19" r="17" fill="url(#tc-face)" stroke="url(#tc-rim)" stroke-width="1.8"/>
    <circle cx="19" cy="19" r="13.5" fill="none" stroke="rgba(184,135,59,.25)" stroke-width=".6"/>
    <g class="n" data-deg="${needleDeg(score)}">
      <path d="M19 5 22 19 19 33 16 19Z" fill="var(--navy-bg)"/>
      <path d="M19 19 22 19 19 33 16 19Z" fill="#8C97A8"/>
    </g>
    <circle cx="19" cy="19" r="1.6" fill="#FBF5EA"/>
  </svg>`;
const countUp = (to, dec = 0, pre = "", suf = "") =>
  `<span class="num" data-to="${to}" data-dec="${dec}" data-pre="${esc(pre)}" data-suf="${esc(suf)}">${pre}${Number(to).toFixed(dec)}${suf}</span>`;
const dirLabel = { up: "▲ 긍정", down: "▼ 부정", flat: "● 중립" };
// up/down: 기사 속 무역 조치가 강화·완화 / neutral: 조치 기사지만 방향 없음 / info: 환율·운임·실적 같은 동향 기사
const actLabel = { up: ["▲ 조치 강화", "up"], down: ["▼ 조치 완화", "down"], neutral: ["● 중립", "flat"], info: ["● 동향", "flat"] };
const act = d => actLabel[d] || actLabel.neutral;
const signed = (v, suf = "%") => `${v > 0 ? "+" : ""}${v}${suf}`;
const star = id => `<button class="star" data-star="${id}" aria-pressed="${watch.has(id)}" aria-label="${esc(sectorById[id].name)} 관심 섹터 ${watch.has(id) ? "해제" : "추가"}">★</button>`;

const sectorCard = (s, i) => `
  <a class="card lift sector tilt rise" style="--i:${i}" href="#/sectors/${s.id}">
    ${compass(s.score)}
    <span class="name">${esc(s.name)}</span>
    <div class="score">${countUp(s.score, 2)}</div>
    <div class="state ${s.state}"><i></i>${s.state}</div>
    <div class="split"><span>▲ 강화 ${s.up}</span><span>▼ 완화 ${s.down}</span><span>● ${D.meta.sample ? "중립" : "중립·동향"} ${s.neutral}</span></div>
    <div class="meter"><b data-w="${s.score}"></b></div>
  </a>`;

// ── 보도 추이 미니 그래프 (최근 5주, 오래된 주 → 이번 주) ──
// 한 가지 값만 그리는 작은 선 그래프라 범례 없이 제목·라벨로 설명해요. 점마다 주·건수 툴팁.
function spark(trend, { w = 92, h = 26, label = "보도 추이" } = {}) {
  if (!Array.isArray(trend) || trend.length < 2) return "";
  const weeks = D.meta.trendWeeks || trend.map((_, i) => `${trend.length - i}주 전`);
  const max = Math.max(...trend, 1), pad = 4, step = (w - pad * 2) / (trend.length - 1);
  const pts = trend.map((v, i) => [pad + i * step, h - pad - (v / max) * (h - pad * 2)]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${pad},${h - pad} ${line} ${(w - pad).toFixed(1)},${h - pad}`;
  const [lx, ly] = pts[pts.length - 1];
  const desc = `${label}: ${trend.map((v, i) => `${weeks[i] || ""}주 ${v}건`).join(", ")}`;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(desc)}">
    <polygon points="${area}" class="sa"/><polyline points="${line}" class="sl"/>
    ${pts.map(([x, y], i) => `<g class="sp"><rect x="${(x - step / 2).toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${h}"/><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6"/><title>${esc(weeks[i] || "")}주 · ${trend[i]}건</title></g>`).join("")}
    <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="3" class="last"/>
  </svg>`;
}
// "3시간 전" 같은 상대 시간 (기사 시각 기준)
function ago(iso) {
  const t = Date.parse(iso); if (!t) return "";
  const m = Math.max(1, Math.round((Date.now() - t) / 60000));
  return m < 60 ? `${m}분 전` : m < 1440 ? `${Math.round(m / 60)}시간 전` : `${Math.round(m / 1440)}일 전`;
}
const reach = n => n.outlets > 1 ? `<span class="reach">${n.outlets}곳 보도</span>` : "";
const sectorChips = ids => (ids || []).map(id => sectorById[id] ? `<a class="sc" href="#/sectors/${id}">${esc(sectorById[id].name)}</a>` : "").join("");

// 홈 "주요 무역 뉴스": 머리기사 1건 크게 + 헤드라인 6건 (중요도 순)
function newsFront(list) {
  const ranked = [...list].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const [lead, ...rest] = ranked;
  if (!lead) return `<p class="muted">이번 주 주요 뉴스가 없어요.</p>`;
  const heads = rest.slice(0, 6);
  return `
    <article class="lead">
      <div class="lead-main">
        <div class="kick"><span class="tag">${esc(lead.tag)}</span><em class="dir ${act(lead.direction)[1]}">${act(lead.direction)[0]}</em>${reach(lead)}</div>
        <h3>${ext(lead.link, esc(lead.title))}</h3>
        <p>${esc(lead.summary || lead.why || "")}</p>
        <div class="meta">${esc(lead.source)} · ${esc(ago(lead.at) || lead.time)}${sectorChips(lead.sectors)}</div>
      </div>
      ${lead.trend ? `<aside class="lead-trend"><small>${esc(lead.issue)} 이슈 · 최근 5주 보도</small>${spark(lead.trend, { w: 150, h: 54, label: `${lead.issue} 보도 추이` })}<b>${lead.issueReports}<span>건 이번 주</span></b></aside>` : ""}
    </article>
    <ol class="heads">${heads.map(n => `
      <li>
        <div class="kick"><span class="tag">${esc(n.tag)}</span>${reach(n)}</div>
        <h4>${ext(n.link, esc(n.title))}</h4>
        <p>${esc(n.summary && n.summary.length < 90 ? n.summary : (n.why || n.summary || ""))}</p>
        <small>${esc(n.source)} · ${esc(ago(n.at) || n.time)}</small>
      </li>`).join("")}</ol>`;
}

const issueItem = (x, i) => `
  <li class="issue">
    <span class="rank">${String(i + 1).padStart(2, "0")}</span>
    <div>
      <h3>${ext(x.link, esc(x.title))}</h3>
      <div class="src">${x.keyword ? `<b>${esc(x.keyword)}</b> · ` : ""}${esc(x.source)} · ${esc(x.time)}${x.reports ? ` · 보도 ${x.reports}건` : ""}</div>
      <span class="tag">${esc(x.tag)}</span>
      ${x.summary ? `<p class="sum">${esc(x.summary)}</p>` : ""}
    </div>
    <div class="impact ${x.impact >= 80 ? "hi" : ""}"><b class="num">${countUp(x.impact)}</b><small>${D.meta.sample ? "영향도" : "이슈 점수"}</small>${x.trend ? spark(x.trend, { w: 64, h: 20, label: `${x.keyword} 보도 추이` }) : ""}</div>
  </li>`;

// 실제 데이터: 기사별 관련 섹터와 무역 조치 방향 / 예전 샘플: 종목별 방향
const newsLinks = n => n.sectors
  ? `<span><em class="dir ${act(n.direction)[1]}">${act(n.direction)[0]}</em></span>${n.sectors.map(id => sectorById[id] ? `<a href="#/sectors/${id}">${esc(sectorById[id].name)}</a>` : "").join("")}${n.trend ? spark(n.trend, { w: 64, h: 18, label: `${n.issue} 보도 추이` }) : ""}`
  : (n.stocks.length ? n.stocks.map(s => `<span>${esc(s.name)} <em class="dir ${s.dir}">${dirLabel[s.dir]}</em></span>`).join("") : `<span class="muted">연관 종목 없음</span>`);
const newsRow = n => `
  <li class="news-row">
    <div class="t"><b class="num">${esc(n.time)}</b><small>${esc(n.source)}${n.outlets > 1 ? ` 외 ${n.outlets - 1}곳` : ""}</small>${n.at ? `<small>${esc(ago(n.at))}</small>` : ""}</div>
    <div><h3><span class="tag">${esc(n.tag)}</span>${ext(n.link, esc(n.title))}</h3>${n.summary ? `<p>${D.meta.sample ? "AI 요약 · " : ""}${esc(n.summary)}</p>` : ""}${n.why ? `<p class="why">${esc(n.why)}</p>` : ""}</div>
    <div class="links">${newsLinks(n)}</div>
  </li>`;

const riskItem = r => `
  <li class="risk ${r.level}">
    <span class="lv ${r.level}">${r.level}</span>
    <div><h3>${esc(r.title)}</h3><p>${ext(r.link, esc(r.detail))}</p></div>
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
// 섹터 뉴스: 발행 데이터의 섹터별 기사(없으면 전체 뉴스에서 이 섹터 기사)
const sectorNews = s => s.news || D.news.filter(n => (n.sectors || []).includes(s.id));
const newsMeta = n => `${esc(n.source)}${n.outlets > 1 ? ` 외 ${n.outlets - 1}곳` : ""} · ${esc(n.time)}${n.clock ? " " + esc(n.clock) : ""}`;
const newsBadges = n => `<span class="tag">${esc(n.tag)}</span>${n.direction ? `<em class="dir ${act(n.direction)[1]}">${act(n.direction)[0]}</em>` : ""}`;
function sectorNewsBlock(s, i) {
  const ns = sectorNews(s);
  if (!ns.length) return `<section class="sp-news rise" style="--i:${i}"><h2 class="sp-h">이번 주 ${esc(s.name)} 뉴스</h2><p class="muted">이번 주 이 섹터와 연결된 무역 기사가 없어요.</p></section>`;
  const [f, ...rest] = ns;
  return `
    <section class="sp-news rise" style="--i:${i}">
      <div class="sp-news-h"><h2 class="sp-h">이번 주 ${esc(s.name)} 뉴스</h2><span>${s.articles ? `관련 기사 ${s.articles}건 중 ` : ""}영향 큰 순 · 제목을 누르면 원문</span></div>
      <article class="card lift nf">
        <div class="nb">${newsBadges(f)}</div>
        <h3>${ext(f.link, esc(f.title))}</h3>
        ${f.summary ? `<p>${esc(f.summary)}</p>` : ""}
        <small>${newsMeta(f)}</small>
      </article>
      ${rest.length ? `<ul class="nl">${rest.map(n => `
        <li class="card lift">
          <div class="nb">${newsBadges(n)}</div>
          <h3>${ext(n.link, esc(n.title))}</h3>
          ${n.summary ? `<p>${esc(n.summary)}</p>` : ""}
          <small>${newsMeta(n)}</small>
        </li>`).join("")}</ul>` : ""}
    </section>`;
}

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
      ${s.summary ? `<p class="sp-sum rise" style="--i:1">${esc(s.summary)}</p>` : ""}

      ${sectorNewsBlock(s, 2)}

      <h2 class="sp-h rise" style="--i:2">이 섹터의 Top10 이슈</h2>
      ${list.length ? `<ol class="top10">${list.map((x, i) => `
        <li class="rise" style="--i:${i + 3}">
          <span class="rank num">${i + 1}</span>
          <div class="t10">
            <a class="kw" href="#/news">${esc(x.keyword)}</a>
            <div class="chips-row">${x.sectors.map(id => sectorById[id] ? `<a class="schip${id === s.id ? " on" : ""}" href="#/sectors/${id}">${esc(sectorById[id].name)}</a>` : "").join("")}</div>
            <p class="meta">점수 ${x.score.toFixed(2)} · 최근 7일 보도 ${x.reports}건(직전 7일 ${x.prev}건) · ${x.prev ? signed(pct(x.reports, x.prev)) : "신규"}</p>
            <p class="meta">▲ 조치 강화 ${x.up}&nbsp;&nbsp;▼ 완화 ${x.down}&nbsp;&nbsp;● ${D.meta.sample ? "중립" : "중립·동향"} ${x.neutral}</p>
            <ul class="arts">${x.articles.map(a => `<li><span class="at">${ext(a.link, esc(a.title))}</span><small>${esc(a.source)} · ${esc(a.at)}</small></li>`).join("")}</ul>
          </div>
        </li>`).join("")}</ol>` : `<p class="muted rise" style="--i:3">최근 7일 동안 이 섹터와 연결된 이슈가 없어요.</p>`}

      ${aiCard("sector", s.id, list.length + 3)}

      <section class="card rise sort-card" style="--i:${list.length + 4}">
        <h2>종목 정렬 결과</h2>
        <ul class="sort-list">${s.stocks.map(k => `<li><span>${esc(k.name)}</span><small class="num">${k.code}</small></li>`).join("")}</ul>
        <div class="pending">시세 데이터 준비 중</div>
      </section>
    </div>`;
}

// ── AI 분석 (Eyefeet AI) ──
// 화면에 있는 데이터를 글로 정리해 /api/ai 로 보내고, 돌아온 분석을 카드에 그려요.
const AI_EXAMPLES = ["반도체 수출기업은 지금 무엇을 확인해야 하나요?", "홍해 리스크가 유럽 수출 물류비에 주는 영향은?", "이번 주 가장 주의할 섹터와 이유는?"];
const DIR = { positive: ["긍정", "pos"], negative: ["부정", "neg"], mixed: ["혼재", "mix"] };

function aiCard(kind, id, i) {
  const sector = kind === "sector";
  const off = !window.tcAI || !tcAI.enabled;
  return `<section class="card rise ai-card" style="--i:${i}" data-ai="${kind}" data-id="${id}">
    <div class="ai-card-h"><h2>${sector ? "AI 영향 분석" : "이번 주 브리핑에 대해 AI에게 묻기"}</h2><span class="ai-badge">AI · 참고용</span></div>
    <p class="muted">${sector ? "이 섹터의 노출도와 관련 이슈를 바탕으로 국내 기업이 받을 영향을 정리해요." : "이번 주 정리된 이슈·섹터 자료만 근거로 답해요."}${D.meta.sample ? " 지금은 샘플 데이터라 분석도 예시예요." : ""}</p>
    ${sector
      ? `<button type="button" class="ai-btn" data-ai-run ${off ? "disabled" : ""}>AI로 분석하기</button>`
      : `<form class="ai-ask" data-ai-ask><label for="ask-q" class="sr">질문</label>
           <input id="ask-q" maxlength="400" placeholder="예: ${esc(AI_EXAMPLES[0])}" ${off ? "disabled" : ""}>
           <button class="ai-btn" ${off ? "disabled" : ""}>묻기</button></form>
         <div class="ai-examples">${AI_EXAMPLES.map(q => `<button type="button" class="ai-chip" data-ai-example="${esc(q)}" ${off ? "disabled" : ""}>${esc(q)}</button>`).join("")}</div>`}
    <div class="ai-result" aria-live="polite">${off ? `<p class="muted">AI 분석은 <a href="${window.tcAI ? tcAI.LIVE_URL : "#"}" target="_blank" rel="noopener">eyefeet 사이트</a>에서 이용할 수 있어요.</p>` : ""}</div>
  </section>`;
}

function sectorContext(s) {
  const iss = D.issues.filter(x => x.sectors.includes(s.id));
  return [
    `섹터: ${s.name} / 노출도 ${s.score} (${s.state}, 50=중립) / 조치 강화 ${s.up} · 완화 ${s.down} · 중립 ${s.neutral}`,
    `요약: ${s.summary}`,
    `관련 종목: ${s.stocks.map(k => k.name).join(", ")}`,
    `관련 이슈:`,
    ...iss.map(x => `- ${x.keyword}: ${x.title} (점수 ${x.score}, 최근 7일 보도 ${x.reports}건·직전 ${x.prev}건, 강화 ${x.up}·완화 ${x.down}) ${x.summary}`),
  ].join("\n");
}

function briefContext() {
  const t = D.trade;
  return [
    `기준 기간 ${D.meta.period || D.meta.date}`,
    `이번 주 요약: ${D.bearing.headline}`, ...D.bearing.points.map(p => `- ${p}`),
    `섹터 노출도(50=평소 수준):`, ...D.sectors.map(s => `- ${s.name} ${s.score} (${s.state}): ${s.summary}`),
    `핵심 이슈:`, ...D.issues.map(x => `- ${x.keyword ? x.keyword + ": " : ""}${x.title} (점수 ${x.impact}, 보도 ${x.reports}건·직전 주 ${x.prev}건)${x.summary ? " " + x.summary : ""}`),
    `공급망 리스크:`, ...D.risks.map(r => `- [${r.level}] ${r.title}: ${r.detail}, ${r.effect}`),
    t ? `무역 흐름(${t.period}): 수출 $${t.total}B, 전년 대비 ${t.yoy}%, 수지 +$${t.balance}B` : "",
  ].filter(Boolean).join("\n").slice(0, 5800);
}

const li = (xs) => xs.length ? `<ul class="ai-list">${xs.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
function renderSectorAI(r) {
  return `<p class="ai-sum">${esc(r.summary)}</p>
    ${r.impacts.length ? `<ul class="ai-impacts">${r.impacts.map(i => `<li><span class="ai-dir ${DIR[i.direction][1]}">${DIR[i.direction][0]}</span><b>${esc(i.who)}</b> ${esc(i.effect)}</li>`).join("")}</ul>` : ""}
    ${r.watch.length ? `<h3 class="ai-h">지켜볼 점</h3>${li(r.watch)}` : ""}
    ${r.actions.length ? `<h3 class="ai-h">지금 확인할 일</h3>${li(r.actions)}` : ""}`;
}
function renderAskAI(r) {
  return `<p class="ai-sum">${esc(r.answer)}</p>
    ${r.points.length ? `<h3 class="ai-h">근거로 쓴 자료</h3>${li(r.points)}` : ""}
    ${r.next.length ? `<h3 class="ai-h">더 확인하면 좋은 것</h3>${li(r.next)}` : ""}`;
}

async function runAI(card, task, payload) {
  const out = card.querySelector(".ai-result");
  const btns = card.querySelectorAll("button, input");
  btns.forEach(b => b.disabled = true);
  out.innerHTML = `<p class="muted ai-wait"></p>`;
  const stop = tcAI.progress(out.firstChild);
  try {
    const r = await tcAI.ask(task, payload);
    out.innerHTML = (task === "sector" ? renderSectorAI(r) : renderAskAI(r))
      + `<p class="ai-foot">AI가 화면의 자료로 만든 참고 분석이에요. 투자 권유가 아니며, 중요한 판단은 원문을 확인하세요.</p>`;
  } catch (e) {
    out.innerHTML = `<p class="ai-err">${esc(e.message)}</p>`;
  } finally { stop(); btns.forEach(b => b.disabled = false); }
}

document.addEventListener("click", e => {
  const run = e.target.closest("[data-ai-run]");
  if (run) { const card = run.closest(".ai-card"); return runAI(card, "sector", { context: sectorContext(sectorById[card.dataset.id]) }); }
  const ex = e.target.closest("[data-ai-example]");
  if (ex) { const card = ex.closest(".ai-card"); card.querySelector("#ask-q").value = ex.dataset.aiExample; card.querySelector("form").requestSubmit(); }
});
document.addEventListener("submit", e => {
  const f = e.target.closest("[data-ai-ask]");
  if (!f) return;
  e.preventDefault();
  const q = f.querySelector("#ask-q").value.trim();
  const card = f.closest(".ai-card");
  if (q.length < 2) { card.querySelector(".ai-result").innerHTML = `<p class="ai-err">질문을 두 글자 이상 적어 주세요.</p>`; return; }
  runAI(card, "ask", { query: q, context: briefContext() });
});

// ── 홈: 내 관심 섹터 ──
// 관심 섹터를 고르면(로그인 없이도 이 브라우저에 저장) 홈 위쪽에 점수·상태·대표 기사를 모아 보여줘요.
function myBlock() {
  const u = window.tcAuth && tcAuth.user;
  const mine = D.sectors.filter(s => watch.has(s.id));
  const who = u ? `${esc(u.name)}님의 관심 섹터` : "내 관심 섹터";
  if (!mine.length) return `
    <section class="my rise" id="my-sectors" style="--i:2">
      <div class="my-h"><h2>${who}</h2><span>고르면 여기에 모아 보여드려요</span></div>
      <div class="my-empty">
        <p>관심 있는 섹터를 눌러 추가하세요.${u ? "" : ` <button type="button" class="linkish" data-auth="open-signup">회원가입</button>하면 휴대폰·PC 어디서나 같아요.`}</p>
        <div class="my-picks">${D.sectors.map(s => `<button type="button" class="pick-chip" data-star="${s.id}" aria-pressed="false">+ ${esc(s.name)}</button>`).join("")}</div>
      </div>
    </section>`;
  return `
    <section class="my rise" id="my-sectors" style="--i:2">
      <div class="my-h"><h2>${who}</h2><a class="more" href="#/watch">편집 →</a></div>
      <div class="my-grid">${mine.map(s => {
        const n = (s.news || [])[0];
        return `<article class="my-card">
          <a class="my-top" href="#/sectors/${s.id}">${compass(s.score, "compass sm")}<span class="nm">${esc(s.name)}</span><b class="num">${s.score.toFixed(1)}</b><span class="state ${s.state}"><i></i>${s.state}</span></a>
          ${n ? `<p class="my-news"><span class="tag">${esc(n.tag)}</span>${ext(n.link, esc(n.title))}</p>` : `<p class="my-news muted">이번 주 관련 기사가 거의 없어요.</p>`}
          <small>기사 ${s.articles ?? (s.up + s.down + s.neutral)}건 · ▲${s.up} ▼${s.down}</small>
        </article>`; }).join("")}</div>
    </section>`;
}
const refreshMy = () => { const el = document.getElementById("my-sectors"); if (el) { el.outerHTML = myBlock(); animate(document.getElementById("my-sectors")); } };

// ── 화면 ──
const views = {
  home() {
    const b = D.bearing;
    const top = [...D.sectors].sort((a, b) => Math.abs(b.score - 50) - Math.abs(a.score - 50))[0];
    const t = D.trade;
    return `
      <header class="hero rise">
        <p class="kicker">Weekly Brief · ${esc(enDate())}</p>
        <h1 class="display">Global Trade <em>Intelligence</em></h1>
        <p>${D.meta.sample ? "무역 이슈가 국내 섹터와 종목에 주는 영향을 정리합니다." : `${esc(D.meta.period)} 동안 신뢰할 수 있는 언론·기관 ${D.meta.outlets}곳의 무역 기사 ${D.meta.sources.toLocaleString()}건을 분석했어요.`}</p>
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
        <div class="eyebrow">THIS WEEK'S BEARING</div>
        <h2>${esc(b.headline)}</h2>
        <ul>${b.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>
      </section>

      ${myBlock()}

      <div class="grid g-2" style="margin-top:22px">
        <section class="card rise" style="--i:7">
          <div class="card-h"><div><h2>이번 주 핵심 무역 이슈</h2><p>보도량 · 증가 추세 · 한국 관련도 기준</p></div><a class="more" href="#/news">Top 10 →</a></div>
          <ol class="issues">${D.issues.slice(0, 4).map(issueItem).join("")}</ol>
        </section>
        <section class="card rise" style="--i:8">
          <div class="card-h"><div><h2>공급망 리스크 신호</h2><p>${D.risks.length ? `수출통제·제재·해운·공급망·원자재 이슈 ${D.risks.length}개` : "이번 주 두드러진 신호 없음"}</p></div></div>
          <ul class="risks">${D.risks.map(riskItem).join("")}</ul>
        </section>
      </div>

      <section class="card rise" style="--i:9;margin-top:18px">
        <div class="card-h"><div><h2>${D.meta.sample ? "뉴스 × 연관 종목 분석" : "주요 무역 뉴스"}</h2><p>${D.meta.sample ? "AI가 뉴스에서 종목 영향 경로를 추출했어요" : "이번 주 중요도 순 · 제목을 누르면 원문으로 이동해요"}</p></div><a class="more" href="#/news">뉴스 피드 →</a></div>
        ${D.meta.sample ? `<ul class="news">${D.news.slice(0, 3).map(newsRow).join("")}</ul>` : `<div class="front">${newsFront(D.news)}</div>`}
      </section>


      <div class="sec-h rise" style="--i:10;margin-top:28px"><h2>섹터별 노출도</h2><span>${D.meta.sample ? "최근 7일 뉴스 기준 · 50 = 중립" : "직전 4주 대비 뉴스 비중 · 50 = 평소 수준"}</span></div>
      <div class="sectors">${D.sectors.map((s, i) => sectorCard(s, i + 3)).join("")}</div>
      <p class="note rise" style="--i:6">노출도는 이번 주 무역 기사 중 그 섹터 기사의 비중이 직전 4주 평균보다 얼마나 높은지를 나타낸 지표예요(50 = 평소 수준). 강화·완화는 기사 속 무역 조치 방향입니다. 실제 판단은 원문을 함께 확인하신 뒤 직접 하시기 바랍니다. <a href="#/method">계산 방법 보기</a></p>


      ${aiCard("ask", "", 11)}

      ${t ? `<section class="card rise" style="--i:10;margin-top:18px">
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
      </section>` : ""}`;
  },

  news(q) {
    const tags = ["전체", ...new Set(D.news.map(n => n.tag))];
    const cur = tags.includes(q) ? q : "전체";
    const list = D.news.filter(n => cur === "전체" || n.tag === cur);
    return `
      <div class="page-h rise"><p class="kicker">Newsroom</p><h1 class="display sm">Trade <em>News</em></h1><p>${D.meta.sample ? `오늘 수집한 ${D.meta.sources.toLocaleString()}건 가운데 고른 핵심 뉴스와 이슈예요.` : `${esc(D.meta.period)} 수집한 무역 기사 ${D.meta.sources.toLocaleString()}건에서 고른 Top 10 이슈와 주요 뉴스예요. <a href="#/method">순위는 어떻게 정하나요?</a>`}</p></div>
      <div class="grid g-2">
        <section class="card rise" style="--i:1">
          <div class="card-h"><h2>${D.meta.sample ? "뉴스 × 연관 종목" : "주요 뉴스"}</h2></div>
          <div class="filters">${tags.map(t => `<a class="chip" href="#/news/${encodeURIComponent(t)}" aria-pressed="${t === cur}">${esc(t)}</a>`).join("")}</div>
          <ul class="news">${list.map(newsRow).join("")}</ul>
        </section>
        <section class="card rise" style="--i:2">
          <div class="card-h"><h2>${D.meta.sample ? "핵심 이슈 전체" : "이번 주 Top 10 이슈"}</h2></div>
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

  tools() {
    const T = [
      ["hs", "HS 코드 · 통관 계산", "품목의 HS 코드를 찾고 관세·부가세와 FTA 협정을 확인해요.", "HS"],
      ["travel", "여행자 면세 계산기", "귀국할 때 산 물건이 면세인지, 자진신고하면 얼마 아끼는지.", "$800"],
      ["check", "직구 반입 체커", "멜라토닌·육포·전자제품… 직구해도 되는지와 잘 모르는 함정.", "OK?"],
      ["track", "통관 진행 조회", "유니패스 단계 풀이와 받은 문자가 사칭인지 확인.", "B/L"],
    ];
    return `
      <div class="page-h rise"><p class="kicker">Tools</p><h1 class="display sm">Trade <em>Tools</em></h1><p>무역·직구·여행 통관에 바로 쓰는 도구 모음이에요.</p></div>
      <div class="tool-hub">${T.map(([id, t, d, mark], i) => `
        <a class="card lift hub rise" style="--i:${i + 1}" href="#/${id}"><span class="hub-mark">${mark}</span><h2>${t}</h2><p>${d}</p><span class="go">열기 →</span></a>`).join("")}</div>`;
  },

  method() {
    const m = D.meta;
    return `
      <div class="page-h rise"><p class="kicker">Methodology</p><h1 class="display sm">How we <em>rank</em></h1><p>Top 10 이슈와 섹터 노출도를 정하는 방법이에요. 모든 숫자는 실제 기사 수로 계산해요.</p></div>
      <section class="card rise method" style="--i:1">
        <h2>1. 어떤 뉴스를 모으나요?</h2>
        <ul>
          <li><b>통신사·경제지 RSS</b>: 연합뉴스(경제·산업·국제), 한국경제(경제·국제), 매일경제(경제), WTO 공식 뉴스</li>
          <li><b>Google 뉴스 검색</b>: 관세·수출통제·반덤핑·FTA·제재·해운·공급망·환율·원자재·보조금·통관·수출입 동향 12개 주제와 정책브리핑(정부 발표), 해외 통신사 영문 기사. 검색 결과 중 <b>신뢰 언론사 목록</b>(통신사, 주요 경제지·일간지·방송, Reuters·Bloomberg 등 30여 곳)에 있는 기사만 남겨요.</li>
          <li>6시간마다 수집하고, 같은 사건을 다룬 기사는 제목 유사도로 하나로 묶어 몇 곳이 보도했는지만 셉니다.</li>
        </ul>
        <h2>2. 이슈 점수</h2>
        <p class="formula">점수 = 100 × (0.6 × 보도량 + 0.4 × 추세) × (0.6 + 0.4 × 한국 관련도)</p>
        <ul>
          <li><b>이슈</b> = 주제 × 상대국 (예: 미국 관세, 중국 수출통제)</li>
          <li><b>보도량</b> = 기사마다 출처 신뢰도 × 최신성(3.5일마다 절반) × 보도 언론사 수를 더한 뒤 로그로 0~1 정규화</li>
          <li><b>추세</b> = 이번 주 무역 기사 중 이 이슈의 비중을 직전 4주 비중과 비교 (같으면 0.5, 늘수록 1에 가깝게)</li>
          <li><b>한국 관련도</b> = 한국·국내 기업이 직접 언급되면 1, 해외 기사는 0.5</li>
          <li>Top 10은 점수 순으로 고르되 같은 주제·같은 나라 이슈가 반복되지 않도록 조정해요(한 섹터 최대 3개).</li>
        </ul>
        <h2>3. 섹터 노출도</h2>
        <p>이번 주 무역 기사 중 그 섹터 기사의 비중을 직전 4주 평균과 비교해 0~100으로 나타내요. 50은 평소 수준, 높을수록 평소보다 뉴스에 많이 노출됐다는 뜻이에요. 강화·완화·보합은 기사 속 무역 조치(부과·통제 vs 인하·유예)의 방향이에요.</p>
        <h2>4. 언제 바뀌나요?</h2>
        <p>기사는 6시간마다 쌓이고, 순위는 <b>매주 월요일 아침</b> 새로 계산돼요.${m.sample ? "" : ` 지금 화면: ${esc(m.period)} · 기사 ${m.sources.toLocaleString()}건 · 언론사·기관 ${m.outlets}곳.`}</p>
        <p class="muted">근거 논문: Baker·Bloom·Davis(2016) 경제정책 불확실성 지수, Caldara 외(2020) 무역정책 불확실성 지수, Carbonell·Goldstein(1998) MMR, Broder(1997) 문서 유사도.</p>
      </section>`;
  },

  hs(q) {
    const src = "../hs-code-finder/index.html" + (q ? "#q=" + encodeURIComponent(q) : "");
    return `
      <div class="page-h rise"><p class="kicker">Tools</p><h1 class="display sm">HS Code <em>&amp; Duty</em></h1><p>품목의 HS 코드를 찾고, 개인 직구·사업 수입·수출 관세와 한국의 FTA 협정을 확인해요.</p></div>
      <iframe class="tool-frame rise" style="--i:1" src="${src}" title="HS 코드 찾기와 세금 계산"></iframe>`;
  }
};

// 직구·여행 통관 도우미 화면 (tools.js)
Object.assign(views, (window.TC_TOOLS || {}).views || {});

// ── 동작 붙이기 ──
// 숫자·막대 애니메이션. 화면 프레임(requestAnimationFrame)이 안 도는 환경(숨은 탭, 미리보기, 캡처, 일부 앱 내 브라우저)에서도
// 숫자가 0에 멈추지 않도록: 처음부터 최종값을 그려 두고, 프레임이 실제로 돌 때만 0부터 올라가게 하고, 끝나면 최종값으로 확정해요.
const DUR = 1100;
const finalText = el => el.dataset.pre + (+el.dataset.to).toFixed(+el.dataset.dec) + el.dataset.suf;
function settle(root) {
  root.querySelectorAll("[data-w]").forEach(el => el.style.width = el.dataset.w + "%");
  root.querySelectorAll(".compass .n").forEach(el => el.style.setProperty("--deg", el.dataset.deg + "deg"));
  root.querySelectorAll(".num[data-to]").forEach(el => el.textContent = finalText(el));
}
function animate(root) {
  if (reduce || document.hidden) return settle(root);
  requestAnimationFrame(t0 => {
    root.querySelectorAll(".num[data-to]").forEach(el => {
      const to = +el.dataset.to, dec = +el.dataset.dec, pre = el.dataset.pre, suf = el.dataset.suf;
      const tick = now => {
        const p = Math.min(1, (now - t0) / DUR), e = 1 - Math.pow(1 - p, 3);
        el.textContent = pre + (to * e).toFixed(dec) + suf;
        if (p < 1) requestAnimationFrame(tick);
      };
      tick(t0);
    });
    requestAnimationFrame(() => {
      root.querySelectorAll("[data-w]").forEach(el => el.style.width = el.dataset.w + "%");
      root.querySelectorAll(".compass .n").forEach((el, i) => setTimeout(() => el.style.setProperty("--deg", el.dataset.deg + "deg"), 200 + i * 80));
    });
  });
  // 프레임이 안 돌았어도, 돌았어도 끝나는 시점에 최종값으로 확정해요
  setTimeout(() => settle(root), DUR + 100);
}

function route() {
  const [, name = "", arg] = location.hash.replace(/^#/, "").split("/");
  const key = views[name] ? name : "home";
  const main = $("#view");
  main.innerHTML = views[key](arg ? decodeURIComponent(arg) : undefined);
  main.classList.remove("enter"); void main.offsetWidth; main.classList.add("enter");
  document.querySelectorAll(".side nav a").forEach(a => a.dataset.view === key ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  const tab = { home: "home", news: "news", sectors: "sectors", watch: "me", method: "news", tools: "tools", hs: "tools", travel: "tools", check: "tools", track: "tools" }[key];
  document.querySelectorAll(".tabbar [data-tab]").forEach(a => a.dataset.tab === tab ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
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
  if (location.hash.startsWith("#/watch")) return route();
  document.querySelectorAll(`[data-star="${id}"]`).forEach(b => b.setAttribute("aria-pressed", watch.has(id)));
  refreshMy();
});

$("#quick").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#quick-q").value.trim();
  location.hash = "#/hs" + (q ? "/" + encodeURIComponent(q) : "");
});

// ── 다크 모드: 기기 설정을 따르다가, 버튼을 누르면 그 선택을 기억해요 ──
const TKEY = "tc-theme";
const isDark = () => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
function paintThemeBtn() {
  const b = $("#theme-btn"); if (!b) return;
  const d = isDark();
  b.setAttribute("aria-label", d ? "밝은 화면으로" : "어두운 화면으로"); b.title = b.getAttribute("aria-label");
  b.innerHTML = d
    ? `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5" fill="currentColor"/><g stroke="currentColor" stroke-width="1.8" stroke-linecap="round">${[0,45,90,135,180,225,270,315].map(r => `<line x1="12" y1="2.5" x2="12" y2="4.8" transform="rotate(${r} 12 12)"/>`).join("")}</g></svg>`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" fill="currentColor"/></svg>`;
}
$("#theme-btn")?.addEventListener("click", () => {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(TKEY, next); } catch {}
  paintThemeBtn();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", paintThemeBtn);
paintThemeBtn();

// 하단 탭바의 '내 정보': 로그인했으면 관심 섹터, 아니면 로그인 창
document.addEventListener("click", e => {
  const me = e.target.closest('.tabbar [data-tab="me"]');
  if (!me || (window.tcAuth && tcAuth.user)) return;
  if (window.tcAuth) { e.preventDefault(); tcAuth.open("login"); }
});

const side = $("#side"), scrim = $("#scrim"), menu = $("#menu");
function closeMenu() { side.classList.remove("open"); scrim.classList.remove("show"); menu.setAttribute("aria-expanded", "false"); }
menu.addEventListener("click", () => { const o = !side.classList.contains("open"); side.classList.toggle("open", o); scrim.classList.toggle("show", o); menu.setAttribute("aria-expanded", o); });
scrim.addEventListener("click", closeMenu);

// ── 머리글 ──
function header() {
  const d = new Date(D.meta.date + "T00:00:00");
  $("#top-date").textContent = D.meta.period ? `${D.meta.period} 기준` : d.toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });
  $("#upd").textContent = D.meta.sample ? `AI 요약 · 매일 ${D.meta.updatedAt} 갱신` : `실제 뉴스 · ${D.meta.cadence || "매주"} 갱신 (${d.getMonth() + 1}/${d.getDate()})`;
  $("#sample-pill").hidden = !D.meta.sample;
  $("#nav-news").textContent = D.issues.length;
}
header();

// eyefeet 에서는 다시 배포하지 않아도 최신 주간 데이터를 받아와요 (/api/data → GitHub 에 매주 올라가는 latest.json)
if (window.tcAI && tcAI.enabled) {
  fetch("/api/data", { headers: { Accept: "application/json" } })
    .then(r => r.ok ? r.json() : null)
    .then(n => {
      if (!n || !n.meta || !Array.isArray(n.sectors) || !Array.isArray(n.issues)) return;
      if (!D.meta.sample && (n.meta.generated || "") <= (D.meta.generated || "")) return;
      D = window.TC_DATA = n;
      sectorById = Object.fromEntries(D.sectors.map(s => [s.id, s]));
      header(); route();
    })
    .catch(() => {});
}

// ── 입체 효과: 카드 기울기, 스크롤 그림자 ──
if (matchMedia("(hover: hover) and (prefers-reduced-motion: no-preference)").matches) {
  document.addEventListener("pointermove", e => {
    const card = e.target.closest(".tilt");
    document.querySelectorAll(".tilt.tilting").forEach(c => c !== card && c.classList.remove("tilting"));
    if (!card) return;
    const r = card.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    card.style.setProperty("--ry", ((x - .5) * 14).toFixed(2) + "deg");
    card.style.setProperty("--rx", ((.5 - y) * 12).toFixed(2) + "deg");
    card.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
    card.style.setProperty("--my", (y * 100).toFixed(1) + "%");
    card.classList.add("tilting");
  });
  document.documentElement.addEventListener("pointerleave", () =>
    document.querySelectorAll(".tilt.tilting").forEach(c => c.classList.remove("tilting")));
}
const topBar = $(".top");
addEventListener("scroll", () => topBar.classList.toggle("scrolled", scrollY > 4), { passive: true });

addEventListener("hashchange", route);
route();
})();
