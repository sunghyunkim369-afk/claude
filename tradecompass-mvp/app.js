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
// up/down: 기사 속 무역 조치가 강화·완화 / neutral: 조치 기사지만 방향 없음 / info: 환율·운임·실적 같은 동향 기사
const actLabel = { up: ["▲ 조치 강화", "up"], down: ["▼ 조치 완화", "down"], neutral: ["● 중립", "flat"], info: ["● 동향", "flat"] };
const act = d => actLabel[d] || actLabel.neutral;
const signed = (v, suf = "%") => `${v > 0 ? "+" : ""}${v}${suf}`;
const star = id => `<button class="star" data-star="${id}" aria-pressed="${watch.has(id)}" aria-label="${esc(sectorById[id].name)} 관심 섹터 ${watch.has(id) ? "해제" : "추가"}">★</button>`;

const sectorCard = (s, i) => `
  <a class="card lift sector tilt rise" style="--i:${i}" href="#/sectors/${s.id}">
    ${compass(s.score)}
    <span class="name">${esc(s.name)}</span>
    <div class="score">${countUp(s.score, 0)}</div>
    ${D.meta.sample ? "" : sectorChange(s)}
    <div class="state ${s.state}" title="기사 속 무역 조치 방향"><i></i>${stateLabel(s.state)}</div>
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
// ── 사진 (Wikimedia Commons 자유 라이선스, photos.js) ──
// 주제·섹터마다 사진 2~3장. 같은 이슈는 그 주 동안 같은 사진이고, 주(기간)가 바뀌면 자동으로 다른 사진으로 돌아가요.
const PH = window.TC_PHOTOS || {};
const TOPIC_BY_LABEL = { "관세": "tariff", "수출통제": "export_control", "반덤핑·무역구제": "trade_remedy", "FTA·통상협정": "agreement",
  "경제제재": "sanctions", "해운·물류": "shipping", "공급망·핵심광물": "supply_chain", "환율": "fx", "유가·원자재": "energy",
  "보조금·산업정책": "subsidy", "통관·원산지": "customs", "수출입 동향": "export_trend" };
const hashStr = s => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
function photoFor(key, seed, shift = 0) {
  const list = PH[key];
  return list && list.length ? list[(hashStr(`${seed}|${D.meta.period || ""}`) + shift) % list.length] : null;
}
// '순위 산정 방식' 페이지의 사진 출처 전체 목록 (같은 사진은 한 번만)
function photoCredits() {
  const all = [...new Map(Object.values(PH).flat().map(p => [p.f, p])).values()];
  if (!all.length) return "";
  return `<h2>사진 출처</h2><p class="muted">기사 사진이 아니라 주제를 보여주는 참고 사진이에요. 모두 Wikimedia Commons 의 자유 라이선스 사진이며, 이름을 누르면 원본과 라이선스를 볼 수 있어요.</p>
    <ul class="credits">${all.map(p => `<li><a href="${esc(p.src)}" target="_blank" rel="noopener noreferrer">${esc(p.t.replace(/\.(jpe?g|png)$/i, ""))}</a> — ${esc(p.by)} · ${esc(p.lic)}</li>`).join("")}</ul>`;
}
// 큰 사진: 오른쪽 아래에 작은 출처 표시(누르면 원본 페이지)
const photoFig = (p, cls, alt) => p ? `<figure class="ph ${cls}"><img src="${esc(p.f)}" alt="${esc(alt || "")}" loading="lazy" decoding="async"><a class="ph-cr" href="${esc(p.src)}" target="_blank" rel="noopener noreferrer" title="${esc(p.t)} · ${esc(p.by)} · ${esc(p.lic)}">ⓒ ${esc(p.by.slice(0, 28))} · ${esc(p.lic)}</a></figure>` : "";
// 작은 썸네일: 출처는 마우스를 올리면 보이고, 전체 목록은 '순위 산정 방식' 페이지 맨 아래에 있어요
const photoThumb = p => p ? `<img class="ph-th" src="${esc(p.f)}" alt="" loading="lazy" decoding="async" title="사진: ${esc(p.by)} · ${esc(p.lic)} (Wikimedia Commons)">` : "";

// 영문 기사 표시: EN 배지 + (AI 번역이 있으면) 원문 제목
const enBadge = n => n.lang === "en" ? `<span class="en" title="영문 기사${n.orig ? " · 제목은 AI 번역" : ""}">EN</span>` : "";
const origLine = n => n.orig ? `<small class="orig">원문: ${esc(n.orig)}</small>` : "";
// 지난주 대비 순위 변화: 숫자 = 오른 칸 수, "new" = 새로 진입
const moveBadge = x => x.move == null ? "" : x.move === "new" ? `<span class="mv new" title="지난주 Top 10에 없던 이슈">NEW</span>`
  : x.move > 0 ? `<span class="mv up" title="지난주보다 ${x.move}계단 상승">▲${x.move}</span>` : x.move < 0 ? `<span class="mv down" title="지난주보다 ${-x.move}계단 하락">▼${-x.move}</span>` : `<span class="mv same" title="지난주와 같은 순위">–</span>`;
// 섹터 조치 상태 표시: "보합"은 주식 용어라 "중립"으로 보여줘요 (데이터 값은 그대로)
const stateLabel = st => st === "보합" ? "조치 중립" : st ? `조치 ${st}` : "";
// 이슈 보도 변화: 같은 출처 기준으로 계산한 값(change, ratio)을 써요. 2배 이상이면 "N배"가 읽기 쉬워요
const changeText = x => x.change == null ? (x.prev ? "" : "새 이슈")
  : x.ratio >= 2 ? `지난주의 ${x.ratio}배` : `지난주 대비 ${x.change >= 0 ? "+" : ""}${x.change}%`;
// 섹터 노출도 옆 한 줄: 평소(직전 4주 주평균)보다 기사가 얼마나 늘었나 + 기사가 적으면 경고
const sectorChange = s => s.thin ? `<span class="thin" title="이번 주 기사 ${s.articles}건뿐이라 점수가 크게 흔들릴 수 있어요">기사 적음 · ${s.articles}건</span>`
  : s.change != null ? `<span class="chg">평소 대비 ${s.change >= 0 ? "+" : ""}${s.change}%</span>` : "";
const sectorChips = ids => (ids || []).map(id => sectorById[id] ? `<a class="sc" href="#/sectors/${id}">${esc(sectorById[id].name)}</a>` : "").join("");

// 홈 "주요 무역 뉴스": 머리기사 1건 크게 + 헤드라인 6건 (중요도 순)
function newsFront(list) {
  // 머리기사는 1위 이슈가 아닌 기사에서 골라요. 1위 이슈는 이번 주 요약·핵심 이슈 1위에 이미 나와서
  // 같은 기사가 홈에 세 번 반복되지 않게 해요. 1위 이슈의 대표 기사는 헤드라인에서도 빼요.
  const ranked = [...list].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const top1 = D.meta.sample ? null : D.issues[0];
  const pool = top1 ? ranked.filter(n => n.link !== top1.link) : ranked;
  // 머리기사 = 2위 이슈의 대표 기사 (뉴스 목록에 있으면 그 기사, 없으면 이슈 정보로 만들어요)
  const second = D.meta.sample ? null : D.issues[1];
  const fromIssue = x => ({ tag: x.tag, title: x.title, link: x.link, source: x.source, time: x.time, why: `${x.keyword} 이슈 · 이번 주 ${x.reports}건 보도`,
    lang: x.lang, orig: x.orig, issue: x.keyword, issueReports: x.reports, trend: x.trend });
  const lead = (second && (pool.find(n => n.link === second.link) || fromIssue(second)))
    || (top1 && pool.find(n => n.issue !== top1.keyword)) || pool[0];
  const rest = pool.filter(n => n !== lead && n.link !== lead.link);
  if (!lead) return `<p class="muted">이번 주 주요 뉴스가 없어요.</p>`;
  const heads = rest.slice(0, 6);
  return `
    <article class="lead">
      <div class="lead-main">
        ${photoFig(photoFor(TOPIC_BY_LABEL[lead.tag], lead.issue || lead.title, 1), "lead-ph", lead.tag)}
        <div class="kick"><span class="tag">${esc(lead.tag)}</span>${lead.direction ? `<em class="dir ${act(lead.direction)[1]}">${act(lead.direction)[0]}</em>` : ""}${reach(lead)}${enBadge(lead)}</div>
        <h3>${ext(lead.link, esc(lead.title))}</h3>${origLine(lead)}
        ${lead.why ? `<p>${esc(lead.why)}</p>` : ""}
        <div class="meta">${esc(lead.source)} · ${esc(ago(lead.at) || lead.time)}${sectorChips(lead.sectors)}</div>
      </div>
      ${lead.trend ? `<aside class="lead-trend"><small>${esc(lead.issue)} 이슈 · 최근 5주 보도</small>${spark(lead.trend, { w: 150, h: 54, label: `${lead.issue} 보도 추이` })}<b>${lead.issueReports}<span>건 이번 주</span></b></aside>` : ""}
    </article>
    <ol class="heads">${heads.map(n => `
      <li>
        <div class="kick"><span class="tag">${esc(n.tag)}</span>${reach(n)}${enBadge(n)}</div>
        <h4>${ext(n.link, esc(n.title))}</h4>
        ${n.why ? `<p>${esc(n.why)}</p>` : ""}
        <small>${esc(n.source)} · ${esc(ago(n.at) || n.time)}</small>
      </li>`).join("")}</ol>`;
}

const issueItem = (x, i) => `
  <li class="issue">
    <span class="rank">${String(i + 1).padStart(2, "0")}${moveBadge(x)}</span>
    <div>
      ${photoThumb(photoFor(x.topic || TOPIC_BY_LABEL[x.tag], x.keyword))}
      <h3>${ext(x.link, esc(x.title))}</h3>
      <div class="src">${x.keyword ? `<b>${esc(x.keyword)}</b> · ` : ""}${esc(x.source)} · ${esc(x.time)}${x.reports ? ` · 보도 ${x.reports}건` : ""}</div>
      <span class="tag">${esc(x.tag)}</span>
    </div>
    <div class="impact ${x.impact >= 80 ? "hi" : ""}"><b class="num">${countUp(x.impact)}</b><small>${D.meta.sample ? "영향도" : "이슈 점수"}</small>${x.trend ? spark(x.trend, { w: 64, h: 20, label: `${x.keyword} 보도 추이` }) : ""}</div>
  </li>`;

// 기사별 관련 섹터와 무역 조치 방향 (뉴스와 종목을 잇는 표시는 하지 않아요)
const newsLinks = n => n.sectors
  ? `<span><em class="dir ${act(n.direction)[1]}">${act(n.direction)[0]}</em></span>${n.sectors.map(id => sectorById[id] ? `<a href="#/sectors/${id}">${esc(sectorById[id].name)}</a>` : "").join("")}${n.trend ? spark(n.trend, { w: 64, h: 18, label: `${n.issue} 보도 추이` }) : ""}`
  : "";
const newsRow = n => `
  <li class="news-row">
    <div class="t"><b class="num">${esc(n.time)}</b><small>${esc(n.source)}${n.outlets > 1 ? ` 외 ${n.outlets - 1}곳` : ""}</small>${n.at ? `<small>${esc(ago(n.at))}</small>` : ""}</div>
    <div><h3><span class="tag">${esc(n.tag)}</span>${enBadge(n)}${ext(n.link, esc(n.title))}</h3>${origLine(n)}${n.why ? `<p class="why">${esc(n.why)}</p>` : ""}</div>
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

// 섹터 상세: 이 섹터의 Top10 이슈 + 대표 기사
const pct = (now, prev) => prev ? Math.round((now - prev) / prev * 100) : 0;
// 섹터 뉴스: 발행 데이터의 섹터별 기사(없으면 전체 뉴스에서 이 섹터 기사)
const sectorNews = s => s.news || D.news.filter(n => (n.sectors || []).includes(s.id));
const newsMeta = n => `${esc(n.source)}${n.outlets > 1 ? ` 외 ${n.outlets - 1}곳` : ""} · ${esc(n.time)}${n.clock ? " " + esc(n.clock) : ""}`;
const newsBadges = n => `<span class="tag">${esc(n.tag)}</span>${n.direction ? `<em class="dir ${act(n.direction)[1]}">${act(n.direction)[0]}</em>` : ""}${enBadge(n)}`;
function sectorNewsBlock(s, i) {
  const ns = sectorNews(s);
  if (!ns.length) return `<section class="sp-news rise" style="--i:${i}"><h2 class="sp-h">이번 주 ${esc(s.name)} 뉴스</h2><p class="muted">이번 주 이 섹터와 연결된 무역 기사가 없어요.</p></section>`;
  const [f, ...rest] = ns;
  return `
    <section class="sp-news rise" style="--i:${i}">
      <div class="sp-news-h"><h2 class="sp-h">이번 주 ${esc(s.name)} 뉴스</h2><span>${s.articles ? `관련 기사 ${s.articles}건 중 ` : ""}영향 큰 순 · 제목을 누르면 원문</span></div>
      <article class="card lift nf">
        <div class="nb">${newsBadges(f)}</div>
        <h3>${ext(f.link, esc(f.title))}</h3>${origLine(f)}
        ${f.why ? `<p>${esc(f.why)}</p>` : ""}
        <small>${newsMeta(f)}</small>
      </article>
      ${rest.length ? `<ul class="nl">${rest.map(n => `
        <li class="card lift">
          <div class="nb">${newsBadges(n)}</div>
          <h3>${ext(n.link, esc(n.title))}</h3>
          ${n.why ? `<p>${esc(n.why)}</p>` : ""}
          <small>${newsMeta(n)}</small>
        </li>`).join("")}</ul>` : ""}
    </section>`;
}

function sectorPage(s) {
  const list = D.issues.filter(x => x.sectors.includes(s.id)).sort((a, b) => b.score - a.score).slice(0, 10);
  return `
    <div class="sp">
      <a class="back-link rise" href="#/">← 홈으로</a>
      ${photoFig(photoFor(s.id, s.id), "sp-ph rise", s.name)}
      <div class="sp-head rise" style="--i:1">
        ${compass(s.score)}
        <div><h1>${esc(s.name)}</h1><div class="state ${s.state}"><i></i>노출도 <b class="num">${Math.round(s.score)}</b> · ${stateLabel(s.state)}</div>
          ${D.meta.sample ? "" : `<p class="sp-base">이번 주 기사 ${s.articles}건 · 평소 주 ${s.baseAvg ?? "-"}건 ${sectorChange(s)}</p>`}</div>
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
            <p class="meta">점수 ${Math.round(x.score)} · 이번 주 보도 ${x.reports}건 · ${changeText(x)}</p>
            <p class="meta">▲ 조치 강화 ${x.up}&nbsp;&nbsp;▼ 완화 ${x.down}&nbsp;&nbsp;● ${D.meta.sample ? "중립" : "중립·동향"} ${x.neutral}</p>
            <ul class="arts">${x.articles.map(a => `<li><span class="at">${enBadge(a)}${ext(a.link, esc(a.title))}</span><small>${esc(a.source)}${a.outlets > 1 ? ` 외 ${a.outlets - 1}곳` : ""} · ${esc(a.at)}</small></li>`).join("")}</ul>
          </div>
        </li>`).join("")}</ol>` : `<p class="muted rise" style="--i:3">최근 7일 동안 이 섹터와 연결된 이슈가 없어요.</p>`}

      ${aiCard("sector", s.id, list.length + 3)}

    </div>`;
}

// ── AI 분석 (Eyefeet AI) ──
// 화면에 있는 데이터를 글로 정리해 /api/ai 로 보내고, 돌아온 분석을 카드에 그려요.
// 섹터 'AI 영향 분석'·대시보드 'AI에게 묻기'는 통합 단계에서 숨겨 둬요 (false). 통관 도구 AI 4개는 tools.js 에서 그대로 써요.
const NEWS_AI = false;
const AI_EXAMPLES = ["반도체 수출기업은 지금 무엇을 확인해야 하나요?", "홍해 리스크가 유럽 수출 물류비에 주는 영향은?", "이번 주 가장 주의할 섹터와 이유는?"];
// 섹터 AI: 이슈별 보도량 방향과 무역 조치 방향만 보여줘요 (기업·업종에 긍정/부정 평가를 붙이지 않아요)
const TREND = { up: "보도 ▲ 늘어남", down: "보도 ▼ 줄어듦", flat: "보도 – 비슷" };
const MEASURE = { tighten: ["조치 강화", "up"], ease: ["조치 완화", "down"], none: ["조치 방향 없음", "flat"] };

function aiCard(kind, id, i) {
  if (!NEWS_AI) return "";
  const sector = kind === "sector";
  const off = !window.tcAI || !tcAI.enabled;
  return `<section class="card rise ai-card" style="--i:${i}" data-ai="${kind}" data-id="${id}">
    <div class="ai-card-h"><h2>${sector ? "AI 영향 분석" : "이번 주 브리핑에 대해 AI에게 묻기"}</h2><span class="ai-badge">AI · 참고용</span></div>
    <p class="muted">${sector ? "이 섹터와 관련된 무역 뉴스의 보도 흐름과 조치 방향(강화·완화)을 정리해요. 기업·업종에 대한 평가는 하지 않아요." : "이번 주 정리된 이슈·섹터 자료만 근거로 답해요."}${D.meta.sample ? " 지금은 샘플 데이터라 분석도 예시예요." : ""}</p>
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
    `관련 이슈:`,
    ...iss.map(x => `- ${x.keyword}: ${x.title} (점수 ${x.score}, 최근 7일 보도 ${x.reports}건·직전 ${x.prev}건, 강화 ${x.up}·완화 ${x.down})`),
  ].join("\n");
}

function briefContext() {
  const t = D.trade;
  return [
    `기준 기간 ${D.meta.period || D.meta.date}`,
    `이번 주 요약: ${D.bearing.headline}`, ...D.bearing.points.map(p => `- ${p}`),
    `섹터 노출도(50=평소 수준):`, ...D.sectors.map(s => `- ${s.name} ${s.score} (${s.state}): ${s.summary}`),
    `핵심 이슈:`, ...D.issues.map(x => `- ${x.keyword ? x.keyword + ": " : ""}${x.title} (점수 ${x.impact}, 보도 ${x.reports}건·직전 주 ${x.prev}건)`),
    `공급망 리스크:`, ...D.risks.map(r => `- [${r.level}] ${r.title}: ${r.detail}, ${r.effect}`),
    t ? `무역 흐름(${t.period}): 수출 $${t.total}B, 전년 대비 ${t.yoy}%, 수지 +$${t.balance}B` : "",
  ].filter(Boolean).join("\n").slice(0, 5800);
}

const li = (xs) => xs.length ? `<ul class="ai-list">${xs.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
function renderSectorAI(r) {
  return `<p class="ai-sum">${esc(r.summary)}</p>
    ${r.flows.length ? `<ul class="ai-impacts">${r.flows.map(f => `<li><b>${esc(f.issue)}</b> <span class="ai-flow">${TREND[f.trend]}</span><em class="dir ${MEASURE[f.measure][1]}">${MEASURE[f.measure][0]}</em><br>${esc(f.note)}</li>`).join("")}</ul>` : ""}
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
    out.innerHTML = (r.notice ? `<p class="ai-notice">${esc(r.notice)}</p>` : "") + (task === "sector" ? renderSectorAI(r) : renderAskAI(r))
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

// ── 속보 · 최근 24시간 ──
// 6시간마다 수집할 때 만든 breaking.js (eyefeet 은 /api/breaking 으로 최신본) 에서, 지금 시각 기준 24시간 안의 기사만 보여줘요.
let BR = window.TC_BREAKING || null;
const H24 = 24 * 3600 * 1000;
const within24 = iso => { const t = Date.parse(iso), n = Date.now(); return t >= n - H24 && t <= n + 600000; };
const hm = iso => { const d = new Date(iso); return isNaN(d) ? "" : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
function breakingBlock(i, full) {
  if (!BR) return "";
  const items = (BR.items || []).filter(a => within24(a.at)).slice(0, full ? 15 : 6);
  return `
    <section class="card breaking rise" id="breaking" style="--i:${i}">
      <div class="card-h"><div><h2><span class="live"></span>속보 <small>최근 24시간</small></h2><p>신뢰 언론사 기사 최신순 · 6시간마다 수집 · 마지막 수집 ${esc(hm(BR.generated))}</p></div>${full ? "" : `<a class="more" href="#/news">전체 →</a>`}</div>
      ${items.length ? `<ol class="br-list${full ? " full" : ""}">${items.map(a => `
        <li>
          <span class="br-time${Date.now() - Date.parse(a.at) < 3 * 3600 * 1000 ? " new" : ""}">${esc(ago(a.at))}</span>
          <div class="br-body">
            <h3>${ext(a.link, esc(a.title))}</h3>${origLine(a)}
            <p><span class="tag">${esc(a.tag)}</span>${enBadge(a)}<em class="dir ${act(a.direction)[1]}">${act(a.direction)[0]}</em><span class="src">${esc(a.source)}${a.outlets > 1 ? ` 외 ${a.outlets - 1}곳` : ""}</span></p>
          </div>
        </li>`).join("")}</ol>`
      : `<p class="muted">최근 24시간 동안 새로 들어온 무역 기사가 없어요. 다음 수집 때 다시 확인해 주세요.</p>`}
    </section>`;
}
const refreshBreaking = () => { const el = document.getElementById("breaking"); if (el) { const full = location.hash.startsWith("#/news"); el.outerHTML = breakingBlock(1, full); } };

// ── 홈: 내 관심 섹터 ──
// 관심 섹터를 고르면(로그인 없이도 이 브라우저에 저장) 홈 위쪽에 점수·상태·대표 기사를 모아 보여줘요.
function myBlock() {
  const u = window.tcAuth && tcAuth.user;
  const mine = D.sectors.filter(s => watch.has(s.id));
  const who = u ? `${esc(u.name)}님의 관심 섹터` : "내 관심 섹터";
  if (!mine.length) return `
    <section class="my rise" id="my-sectors" style="--i:2">
      <div class="my-h"><h2>나만의 화면 만들기</h2><span>섹터 1~3개를 고르면 이 자리에 내 섹터의 속보·이슈가 먼저 나와요</span></div>
      <div class="my-empty">
        <p>우리 회사·관심 분야와 가까운 섹터를 눌러 주세요.${u ? "" : ` <button type="button" class="linkish" data-auth="open-signup">회원가입</button>하면 휴대폰·PC 어디서나 같아요.`}</p>
        <div class="my-picks">${D.sectors.map(s => `<button type="button" class="pick-chip" data-star="${s.id}" aria-pressed="false">+ ${esc(s.name)}</button>`).join("")}</div>
      </div>
    </section>`;
  return `
    <section class="my rise" id="my-sectors" style="--i:2">
      <div class="my-h"><h2>${who}</h2><a class="more" href="#/watch">편집 →</a></div>
      <div class="my-grid">${mine.map(s => {
        const n = (s.news || [])[0];
        return `<article class="my-card">
          <a class="my-top" href="#/sectors/${s.id}">${compass(s.score, "compass sm")}<span class="nm">${esc(s.name)}</span><b class="num">${Math.round(s.score)}</b><span class="state ${s.state}"><i></i>${stateLabel(s.state)}</span></a>
          ${n ? `<p class="my-news"><span class="tag">${esc(n.tag)}</span>${enBadge(n)}${ext(n.link, esc(n.title))}</p>` : `<p class="my-news muted">이번 주 관련 기사가 거의 없어요.</p>`}
          <small>기사 ${s.articles ?? (s.up + s.down + s.neutral)}건 · ▲${s.up} ▼${s.down}${D.meta.sample ? "" : " · " + sectorChange(s)}</small>
        </article>`; }).join("")}</div>
      ${myFeed(mine)}
    </section>`;
}
// 내 섹터의 Top10 이슈와 24시간 속보를 한곳에 (로그인·관심 섹터의 이유가 되는 "내 화면")
function myFeed(mine) {
  const ids = new Set(mine.map(s => s.id));
  const iss = D.issues.filter(x => x.sectors.some(id => ids.has(id))).slice(0, 3);
  const br = BR ? (BR.items || []).filter(a => within24(a.at) && (a.sectors || []).some(id => ids.has(id))).slice(0, 4) : [];
  if (!iss.length && !br.length) return "";
  return `<div class="my-feed">
    ${br.length ? `<div><h3>내 섹터 속보 <small>최근 24시간</small></h3><ul>${br.map(a => `<li><span class="br-time">${esc(ago(a.at))}</span>${enBadge(a)}${ext(a.link, esc(a.title))} <small>${esc(a.source)}</small></li>`).join("")}</ul></div>` : ""}
    ${iss.length ? `<div><h3>내 섹터 Top 10 이슈</h3><ol>${iss.map(x => `<li><b class="num">${D.issues.indexOf(x) + 1}위</b>${moveBadge(x)} ${esc(x.keyword)} — ${ext(x.link, esc(x.title))}</li>`).join("")}</ol></div>` : ""}
  </div>`;
}

const TOOLS = [
  ["hs", "HS 코드 · 통관 계산", "품목의 HS 코드를 찾고 관세·부가세와 FTA 협정을 확인해요.", "HS"],
  ["travel", "여행자 면세 계산기", "귀국할 때 산 물건이 면세인지, 자진신고하면 얼마 아끼는지.", "$800"],
  ["check", "직구 반입 체커", "멜라토닌·육포·전자제품… 직구해도 되는지와 잘 모르는 함정.", "OK?"],
  ["track", "통관 진행 조회", "유니패스 단계 풀이와 받은 문자가 사칭인지 확인.", "B/L"],
];
// 첫 화면 "나는 누구?" 바로가기: 수출입 실무·직구/여행·공부 목적에 맞는 메뉴로 바로 보내요. 고른 것은 이 브라우저에 기억해요.
const PKEY = "tc-persona";
const PERSONAS = [
  ["biz", "수출입 기업", "Top 10 이슈·섹터 노출도를 먼저"],
  ["life", "직구·해외여행", "면세 계산·직구 반입·통관 조회를 먼저"],
  ["study", "공부·리서치", "이번 주 흐름과 순위 산정 방법을 먼저"],
];
const getPersona = () => { try { return localStorage.getItem(PKEY) || ""; } catch { return ""; } };
function personaBar() {
  if (D.meta.sample) return "";
  const cur = getPersona();
  return `<nav class="persona rise" aria-label="목적에 맞게 홈 순서 바꾸기"><span>무엇이 필요하세요? <small>고르면 홈 순서가 바뀌어요</small></span>${PERSONAS.map(([id, t, d]) =>
    `<button type="button" data-persona="${id}" aria-pressed="${cur === id}" class="${cur === id ? "on" : ""}"><b>${t}</b><small>${esc(d)}</small></button>`).join("")}</nav>`;
}
// 같은 버튼을 다시 누르면 기본 순서로 돌아가요
document.addEventListener("click", e => {
  const p = e.target.closest("[data-persona]");
  if (!p) return;
  const next = getPersona() === p.dataset.persona ? "" : p.dataset.persona;
  try { next ? localStorage.setItem(PKEY, next) : localStorage.removeItem(PKEY); } catch {}
  const y = window.scrollY; route(); window.scrollTo(0, y);
});
// 홈 블록 순서: 모바일은 Top 10 이슈를 속보보다 먼저(가장 중요한 정보가 첫 두 화면 안에 들어오게)
const MOBILE = window.matchMedia ? window.matchMedia("(max-width:640px)") : { matches: false };
function homeOrder() {
  const p = getPersona(), m = MOBILE.matches;
  if (p === "biz") return ["hero", "persona", "my", "issues", "sectors", "breaking", "bearing", "news", "ai", "trade"];
  if (p === "life") return ["hero", "persona", "tools", "breaking", "bearing", "news", "issues", "my", "sectors", "ai", "trade"];
  if (p === "study") return ["hero", "persona", "bearing", "method", "issues", "sectors", "news", "breaking", "my", "ai", "trade"];
  return m ? ["hero", "persona", "my", "bearing", "issues", "breaking", "news", "sectors", "ai", "trade"]
           : ["hero", "persona", "my", "bearing", "breaking", "issues", "news", "sectors", "ai", "trade"];
}
// 화면 폭이 모바일 경계를 넘으면 홈 순서를 다시 맞춰요
if (MOBILE.addEventListener) MOBILE.addEventListener("change", () => { if (!location.hash || location.hash === "#/") route(); });

// 목적별 블록: 생활 통관 도구 바로가기, 순위 산정 방법 요약
function toolsQuick() {
  return `<section class="card rise quick-tools" style="--i:2"><div class="card-h"><div><h2>생활 통관 도구</h2><p>여행·직구할 때 바로 쓰는 계산기와 체커예요</p></div><a class="more" href="#/tools">전체 →</a></div>
    <div class="qt-grid">${TOOLS.map(([id, t, d, mark]) => `<a class="qt" href="#/${id}"><span class="hub-mark">${mark}</span><b>${t}</b><small>${d}</small></a>`).join("")}</div></section>`;
}
function methodQuick() {
  return `<section class="card rise" style="--i:2"><div class="card-h"><div><h2>이 순위는 어떻게 만들어지나요?</h2><p>공부·발표 자료로 쓸 때 알아 두면 좋은 기준</p></div><a class="more" href="#/method">자세히 →</a></div>
    <ul class="mq">
      <li><b>출처</b> 정부·국제기구, 통신사, 주요 경제지·방송, 해외 정부·경제지 ${D.meta.outlets}곳의 기사 ${D.meta.sources.toLocaleString()}건 (목록에 없는 매체는 쓰지 않아요)</li>
      <li><b>점수</b> = 100 × (0.6 × 보도량 + 0.4 × 평소 대비 추세) × (0.6 + 0.4 × 한국 관련도)</li>
      <li><b>한계</b> 조치 방향은 단어 규칙이라 틀릴 수 있어요(표본 점검 88% 일치). 판단은 원문으로 확인하세요.</li>
    </ul></section>`;
}
const refreshMy = () => { const el = document.getElementById("my-sectors"); if (el) { el.outerHTML = myBlock(); animate(document.getElementById("my-sectors")); } };

// ── 화면 ──
const views = {
  home() {
    const b = D.bearing;
    const top = [...D.sectors].sort((a, b) => Math.abs(b.score - 50) - Math.abs(a.score - 50))[0];
    const t = D.trade;
    // 홈은 블록을 이어 붙여 만들어요. 순서는 기기(모바일)와 "무엇이 필요하세요?"에서 고른 목적에 따라 달라져요.
    const B = {
      hero: `
      <header class="hero rise">
        <p class="kicker">Weekly Brief · ${esc(enDate())}</p>
        <h1 class="display">Global Trade <em>Intelligence</em></h1>
        <p>${D.meta.sample ? "무역 뉴스 흐름을 섹터별로 정리합니다." : `${esc(D.meta.period)} 동안 신뢰할 수 있는 언론·기관 ${D.meta.outlets}곳의 무역 기사 ${D.meta.sources.toLocaleString()}건을 분석했어요.`}</p>
      </header>`,
      persona: personaBar(),
      my: myBlock(),
      bearing: `
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
      </section>`,
      breaking: breakingBlock(2, false),
      issues: `
      <div class="grid g-2" style="margin-top:22px">
        <section class="card rise" style="--i:7">
          <div class="card-h"><div><h2>이번 주 핵심 무역 이슈</h2><p>보도량 · 증가 추세 · 한국 관련도 기준</p></div><a class="more" href="#/news">Top 10 →</a></div>
          <ol class="issues">${D.issues.slice(0, 4).map(issueItem).join("")}</ol>
        </section>
        <section class="card rise" style="--i:8">
          <div class="card-h"><div><h2>공급망 리스크 신호</h2><p>${D.risks.length ? `수출통제·제재·해운·공급망·원자재 이슈 ${D.risks.length}개` : "이번 주 두드러진 신호 없음"}</p></div></div>
          <ul class="risks">${D.risks.map(riskItem).join("")}</ul>
        </section>
      </div>`,
      news: `
      <section class="card rise" style="--i:9;margin-top:18px">
        <div class="card-h"><div><h2>주요 무역 뉴스</h2><p>${D.meta.sample ? "샘플 데이터예요" : "이번 주 중요도 순 · 제목을 누르면 원문으로 이동해요"}</p></div><a class="more" href="#/news">뉴스 피드 →</a></div>
        ${D.meta.sample ? `<ul class="news">${D.news.slice(0, 3).map(newsRow).join("")}</ul>` : `<div class="front">${newsFront(D.news)}</div>`}
      </section>`,
      sectors: `
      <div class="sec-h rise" style="--i:10;margin-top:28px"><h2>섹터별 노출도</h2><span>${D.meta.sample ? "최근 7일 뉴스 기준 · 50 = 중립" : "직전 4주 대비 뉴스 비중 · 50 = 평소 수준"}</span></div>
      <div class="sectors">${D.sectors.map((s, i) => sectorCard(s, i + 3)).join("")}</div>
      <p class="note rise" style="--i:6">노출도는 이번 주 무역 기사 중 그 섹터 기사의 비중이 직전 4주 평균보다 얼마나 높은지를 나타낸 지표예요(50 = 평소 수준). 강화·완화는 기사 속 무역 조치 방향입니다. 실제 판단은 원문을 함께 확인하신 뒤 직접 하시기 바랍니다. <a href="#/method">계산 방법 보기</a></p>`,
      ai: aiCard("ask", "", 11),
      trade: t ? `<section class="card rise" style="--i:10;margin-top:18px">
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
      </section>` : "",
      tools: toolsQuick(),
      method: methodQuick(),
    };
    return homeOrder().map(k => B[k] || "").join("\n");
  },

  news(q) {
    const tags = ["전체", ...new Set(D.news.map(n => n.tag))];
    const cur = tags.includes(q) ? q : "전체";
    const list = D.news.filter(n => cur === "전체" || n.tag === cur);
    return `
      <div class="page-h rise"><p class="kicker">Newsroom</p><h1 class="display sm">Trade <em>News</em></h1><p>${D.meta.sample ? `오늘 수집한 ${D.meta.sources.toLocaleString()}건 가운데 고른 핵심 뉴스와 이슈예요.` : `${esc(D.meta.period)} 수집한 무역 기사 ${D.meta.sources.toLocaleString()}건에서 고른 Top 10 이슈와 주요 뉴스예요. <a href="#/method">순위는 어떻게 정하나요?</a>`}</p></div>
      ${breakingBlock(1, true)}
      <div class="grid g-2">
        <section class="card rise" style="--i:1">
          <div class="card-h"><h2>주요 뉴스</h2></div>
          <div class="filters">${tags.map(t => `<a class="chip" href="#/news/${encodeURIComponent(t)}" aria-pressed="${t === cur}">${esc(t)}</a>`).join("")}</div>
          <ul class="news">${list.map(newsRow).join("")}</ul>
        </section>
        <section class="card rise" style="--i:2">
          <div class="card-h"><div><h2>${D.meta.sample ? "핵심 이슈 전체" : "이번 주 Top 10 이슈"}</h2>${D.meta.lastWeek ? `<p>▲▼ 지난주(${esc(D.meta.lastWeek)} 주) 대비 순위${D.meta.dropped && D.meta.dropped.length ? ` · 빠진 이슈: ${D.meta.dropped.map(esc).join(", ")}` : ""}</p>` : ""}</div></div>
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
            <h2>${esc(s.name)} <span class="state ${s.state}"><i></i>${stateLabel(s.state)}</span></h2>
            <p>${esc(s.summary)}</p>
          </div>
          <div class="big">${countUp(s.score, 0)}</div>
        </a>`).join("")}</div>`;
  },

  watch() {
    const mine = D.sectors.filter(s => watch.has(s.id));
    return `
      <div class="page-h rise"><p class="kicker">Watchlist</p><h1 class="display sm">My <em>Sectors</em></h1><p>별을 눌러 관심 섹터를 고르면 대시보드처럼 모아 볼 수 있어요. (이 브라우저에만 저장돼요)</p></div>
      <section class="card rise" style="--i:1">
        ${D.sectors.map(s => `<div class="watch-row">${star(s.id)}<div><h3>${esc(s.name)}</h3><p>${esc(s.summary)}</p></div><b class="num">${Math.round(s.score)}</b></div>`).join("")}
      </section>
      <div class="sec-h rise" style="--i:2"><h2>내 관심 섹터</h2><span>${mine.length}개</span></div>
      ${mine.length ? `<div class="sectors">${mine.map((s, i) => sectorCard(s, i + 3)).join("")}</div>` : `<div class="card empty rise" style="--i:3">아직 고른 섹터가 없어요. 위 목록에서 ★을 눌러 보세요.</div>`}`;
  },

  tools() {
    const T = TOOLS;
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
          <li><b>언론사 RSS</b>: 연합뉴스(경제·산업·국제), 매일경제(경제). 한국경제는 이용약관 확인 전까지 꺼 두었어요.</li>
          <li><b>해외 1차 출처</b>: 미국 무역대표부(USTR)·백악관 대통령 조치·미 관보(한국 관련 반덤핑·수출통제 결정)·EU 집행위원회 발표, Nikkei Asia·SCMP, 해운 전문지(gCaptain·The Loadstar)</li>
          <li><b>수집 원칙</b>: 각 출처의 robots.txt·이용약관을 확인한 RSS 만 써요. 같은 곳에는 2초 이상 간격을 두고 요청해요. Google 뉴스 검색(robots.txt 가 허용하지 않음)과 WTO(전체 금지)는 2026-10-06부터 쓰지 않아요. 기사 본문은 저장·게시하지 않고 제목·언론사·시각·원문 링크만 보여 줘요.</li>
          <li>6시간마다 수집하고, 같은 사건을 다룬 기사는 제목 유사도로 하나로 묶어 몇 곳이 보도했는지만 셉니다.</li>
        </ul>
        <h2>2. 이슈 점수</h2>
        <p class="formula">점수 = 100 × (0.6 × 보도량 + 0.4 × 추세) × (0.6 + 0.4 × 한국 관련도)</p>
        <ul>
          <li><b>이슈</b> = 주제 × 상대국 (예: 미국 관세, 중국 수출통제)</li>
          <li><b>보도량</b> = 기사마다 출처 신뢰도 × 최신성(3.5일마다 절반) × 보도 언론사 수를 더한 뒤 로그로 0~1 정규화. 같은 언론사가 속보·2보·종합으로 여러 번 쓰면 두 번째부터는 덜 셉니다(1/√k).</li>
          <li><b>추세</b> = 이번 주 무역 기사 중 이 이슈의 비중을 직전 4주 비중과 비교 (같으면 0.5, 늘수록 1에 가깝게). 기사가 적은 이슈는 우연일 수 있어 평소 쪽으로 줄여요. 새로 붙인 출처 때문에 늘어 보이지 않도록 4주 내내 있던 출처의 기사끼리만 비교해요.</li>
          <li><b>한국 관련도</b> = 한국·국내 기업이 직접 언급되면 1, 해외 기사는 0.5</li>
          <li>Top 10은 점수 순으로 고르되 같은 주제·같은 나라 이슈가 반복되지 않도록 조정해요(한 섹터 최대 3개). 단, 점수 상위 3개는 항상 들어가요.</li>
          <li><b>대표 기사</b> = 이슈 주제 단어가 제목에 있고(상대국이 있으면 나라 이름까지) 관련도 기준을 넘는 기사 중 가장 중요한 기사예요. 제품 출시·행사 홍보 기사는 이슈에서 빼요.</li>
        </ul>
        <h2>3. 섹터 노출도</h2>
        <p>이번 주 무역 기사 중 그 섹터 기사의 비중을 직전 4주 평균과 비교해 0~100으로 나타내요. 50은 평소 수준, 높을수록 평소보다 뉴스에 많이 노출됐다는 뜻이에요. 강화·완화·보합은 기사 속 무역 조치(부과·통제 vs 인하·유예)의 방향이에요.</p>
        <h2>4. 언제 바뀌나요?</h2>
        <p>기사는 6시간마다 쌓이고, 순위는 <b>매주 월요일 아침</b> 새로 계산돼요.${m.sample ? "" : ` 지금 화면: ${esc(m.period)} · 기사 ${m.sources.toLocaleString()}건 · 언론사·기관 ${m.outlets}곳.`}</p>
        <h2>5. 출처는 어디이고, 틀리면 어떻게 하나요?</h2>
        <ul>
          <li><b>출처 종류</b>: 정부·국제기구 발표(1.0) · 통신사(0.9) · 주요 경제지·일간지·방송·해외 경제지(0.8) · 전문지(0.7). 숫자는 점수에 곱하는 신뢰도예요. 목록에 없는 매체와 스포츠·연예 매체는 쓰지 않아요.</li>
          <li><b>덜 세는 기사</b>: 사설·칼럼·기고, 증시 시황·증권사 리포트, 연설문 제목("~해 나가겠습니다")은 사실 보도가 아니라서 무게를 절반으로 하고 대표 기사·속보에서 빼요.</li>
          <li><b>같은 사건</b>은 한 줄로 합치고 "N곳 보도"로 보여줘요. 영문 기사는 <span class="en">EN</span> 표시가 붙어요.</li>
          <li><b>조치 방향</b>(강화·완화)은 관세·제재·수출입 같은 무역 맥락이 있는 기사에만 붙여요. 단어 규칙이라 틀릴 수 있어요. 2026-10 점검에서 표본 75건 중 88%가 사람 판단과 같았어요.</li>
          <li><b>정정</b>: 분류·대표 기사가 틀렸다면 원문 링크와 함께 알려 주세요. 규칙을 고쳐 다음 수집(6시간 이내)부터 반영하고, 고친 내용은 계산 방법 문서에 남겨요.</li>
        </ul>
        ${photoCredits()}
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
  // 저장소에 들어 있는 data.js·breaking.js 로 그리는 동안은 '샘플 데이터' 표시를 켜 둬요. 서버(/api/data·/api/breaking)에서 둘 다 받아오면 꺼요.
  const pill = $("#sample-pill");
  pill.hidden = !D.meta.sample && LIVE.data && LIVE.breaking;
  pill.title = D.meta.sample ? "예시로 만든 데이터예요" : "사이트에 함께 들어 있는 데이터로 보여주고 있어요. 서버에서 최신 데이터를 받으면 사라져요.";
  $("#nav-news").textContent = D.issues.length;
}
const LIVE = { data: false, breaking: false };
header();

// eyefeet 에서는 6시간마다 갱신되는 속보도 다시 배포 없이 받아와요
if (window.tcAI && tcAI.enabled) {
  fetch("/api/breaking", { headers: { Accept: "application/json" } })
    .then(r => r.ok ? r.json() : null)
    .then(n => {
      if (!n || !Array.isArray(n.items) || (BR && (n.generated || "") < (BR.generated || ""))) return;
      BR = n; LIVE.breaking = true; header(); refreshBreaking();
    })
    .catch(() => {});
}

// eyefeet 에서는 다시 배포하지 않아도 최신 주간 데이터를 받아와요 (/api/data → GitHub 에 매주 올라가는 latest.json)
if (window.tcAI && tcAI.enabled) {
  fetch("/api/data", { headers: { Accept: "application/json" } })
    .then(r => r.ok ? r.json() : null)
    .then(n => {
      if (!n || !n.meta || !Array.isArray(n.sectors) || !Array.isArray(n.issues)) return;
      if (!D.meta.sample && (n.meta.generated || "") < (D.meta.generated || "")) return;
      D = window.TC_DATA = n; LIVE.data = true;
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
