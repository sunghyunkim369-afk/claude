// 직구·여행 통관 도우미: 여행자 면세 계산기 / 직구 반입 체커 / 통관 진행 조회
// app.js 가 window.TC_TOOLS.views 를 화면 목록에 합치고, 화면을 그린 뒤 mount() 를 불러요.
// 기준은 관세청 안내(2026-10 확인)이며 바뀔 수 있어요. 숫자를 바꿀 땐 아래 RULES 만 고치면 돼요.
(() => {
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const won = n => Math.round(n).toLocaleString("ko-KR") + "원";
const usd = n => "$" + (Math.round(n * 100) / 100).toLocaleString("en-US");
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };

const RULES = {
  checked: "2026-10",
  exempt: 800,                       // 기본 면세 (미화)
  liquor: { ml: 2000, usd: 400 },    // 술: 병 수 제한 없이 합계 2L·$400 이하 (2025-03-21 변경)
  perfumeMl: 100,
  tobacco: 200,                      // 궐련 200개비(1보루)
  selfReport: { rate: 0.3, max: 200000 },  // 자진신고: 세액 30% 감면, 최대 20만원
  penalty: 0.4, penaltyRepeat: 0.6,        // 미신고 적발 가산세 (2년 내 2회 이상 60%)
};
// 여행자 휴대품 간이세율 (관세·부가세 등을 합친 세율)
const CATS = [
  { id: "gen", name: "일반 물품 (전자제품·화장품·잡화 등)", rate: 0.15 },
  { id: "cloth", name: "의류·신발", rate: 0.18 },
  { id: "fur", name: "모피 제품", rate: 0.19 },
  { id: "deer", name: "녹용", rate: 0.21 },
  { id: "lux", name: "고가 보석·시계·가방 (개당 200만 원 초과)", rate: null },
];
const LIQ = [
  { id: "wine", name: "와인", rate: 0.68 },
  { id: "spirit", name: "위스키·브랜디·보드카", rate: 1.56 },
  { id: "kaoliang", name: "고량주", rate: 1.77 },
  { id: "other", name: "맥주·사케·기타", rate: null },
];
const SRC = {
  travel: "https://www.customs.go.kr/kcs/cm/cntnts/cntntsView.do?mi=2837&cntntsId=829",
  refund: "https://www.customs.go.kr/kcs/cm/cntnts/cntntsView.do?mi=2836&cntntsId=828",
  unipass: "https://unipass.customs.go.kr/csp/index.do",
  food: "https://www.foodsafetykorea.go.kr",
  easylaw: "https://easylaw.go.kr/CSP/CnpClsMain.laf?popMenu=ov&csmSeq=1504&ccfNo=2&cciNo=2&cnpClsNo=3",
};
const link = (url, text) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`;
const fxDefault = 1400;

// ═════════════ AI 공통 ═════════════
// AI는 "입력 정리·해석"만 해요. 세금 계산과 반입 판정은 위의 검증된 기준표로 해요.
const aiOn = () => !!(window.tcAI && tcAI.enabled);
const aiOffNote = () => `<p class="muted small">AI 기능은 ${link(window.tcAI ? tcAI.LIVE_URL : "#", "eyefeet 사이트")}에서 쓸 수 있어요.</p>`;
const aiHead = (title, i) => `<div class="ai-card-h"><h2>${title}</h2><span class="ai-badge">AI · 참고용</span></div>`;
async function runToolAI(card, task, payload, render) {
  const out = card.querySelector(".ai-result");
  const ctrls = card.querySelectorAll("button, textarea, input");
  ctrls.forEach(b => b.disabled = true);
  out.innerHTML = `<p class="muted ai-wait"></p>`;
  const stop = tcAI.progress(out.firstChild, "AI가 읽는 중이에요");
  try { out.innerHTML = render(await tcAI.ask(task, payload)); }
  catch (e) { out.innerHTML = `<p class="ai-err">${esc(e.message)}</p>`; }
  finally { stop(); ctrls.forEach(b => b.disabled = !aiOn()); }
}
const ulist = xs => xs && xs.length ? `<ul class="ai-list">${xs.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";

// ═════════════ 1. 여행자 면세 계산기 ═════════════
const TKEY = "tc-travel";
let T = store.get(TKEY, null) || {
  fx: fxDefault, adult: true, repeat: false,
  items: [{ cat: "gen", usd: "" }], liquor: [{ type: "spirit", ml: "", usd: "" }],
  perfume: { ml: "", usd: "" }, tobacco: "",
};
const saveT = () => store.set(TKEY, T);
const num = v => { const n = parseFloat(String(v).replace(/,/g, "")); return isFinite(n) && n > 0 ? n : 0; };

function calcTravel() {
  const warn = [], lines = [];
  const general = [];      // { rate, usd, label }
  let unknown = 0;
  T.items.forEach(it => {
    const c = CATS.find(c => c.id === it.cat) || CATS[0], v = num(it.usd);
    if (!v) return;
    if (c.rate === null) { unknown += v; return; }
    general.push({ rate: c.rate, usd: v, label: c.name });
  });
  // 향수: 100ml 이하는 따로 면세, 넘으면 일반 물품에 포함
  const pm = num(T.perfume.ml), pu = num(T.perfume.usd);
  if (pu && pm > RULES.perfumeMl) { general.push({ rate: 0.15, usd: pu, label: "향수(100ml 초과)" }); warn.push("향수가 100ml를 넘어서 일반 물품으로 계산했어요."); }

  // 기본 면세 $800 은 세율 높은 물품부터 빼요 (여행자에게 유리한 순서)
  const totalGen = general.reduce((s, g) => s + g.usd, 0) + unknown;
  let left = RULES.exempt, taxUsd = 0;
  [...general].sort((a, b) => b.rate - a.rate).forEach(g => {
    const off = Math.min(left, g.usd); left -= off;
    const t = (g.usd - off) * g.rate;
    if (t > 0) lines.push([`${g.label} ${usd(g.usd - off)} × ${Math.round(g.rate * 100)}%`, t]);
    taxUsd += t;
  });
  if (unknown) warn.push(`고가 보석·시계·가방(${usd(unknown)})은 관세·개별소비세·부가세를 따로 계산해서 이 추정에 넣지 않았어요. 세금이 크게 나올 수 있어요.`);

  // 술: 합계 2L·$400 이하면 면세, 넘으면 술 전체를 과세로 보수적으로 추정
  const lq = T.liquor.filter(l => num(l.usd) || num(l.ml));
  const lml = lq.reduce((s, l) => s + num(l.ml), 0), lusd = lq.reduce((s, l) => s + num(l.usd), 0);
  let liquorFree = true;
  if (lq.length) {
    if (!T.adult) { liquorFree = false; warn.push("만 19세 미만은 술·담배 면세가 없어요."); }
    else if (lml > RULES.liquor.ml || lusd > RULES.liquor.usd) liquorFree = false;
    if (!liquorFree) {
      lq.forEach(l => {
        const t = LIQ.find(x => x.id === l.type) || LIQ[0], v = num(l.usd);
        if (!v) return;
        if (t.rate === null) { warn.push(`${t.name}는 종류·도수마다 세율이 달라 계산에서 뺐어요. 세관에 신고하면 정확한 세금을 알려줘요.`); return; }
        const tx = v * t.rate; taxUsd += tx;
        lines.push([`${t.name} ${usd(v)} × ${Math.round(t.rate * 100)}%`, tx]);
      });
      if (T.adult) warn.push(`술이 면세 기준(합계 2L·$400)을 넘었어요 (지금 ${lml.toLocaleString()}ml · ${usd(lusd)}). 넘으면 술 전체가 과세될 수 있어 보수적으로 계산했어요.`);
    }
  }
  const tb = num(T.tobacco);
  if (tb && (!T.adult || tb > RULES.tobacco)) warn.push(`담배는 궐련 ${RULES.tobacco}개비(1보루)까지만 면세예요. 넘는 담배는 개별소비세 등이 붙어요(이 계산에는 미포함).`);

  const tax = taxUsd * num(T.fx || fxDefault);
  const cut = Math.min(tax * RULES.selfReport.rate, RULES.selfReport.max);
  const pen = tax * (T.repeat ? RULES.penaltyRepeat : RULES.penalty);
  return { totalGen, lml, lusd, liquorFree, lq: lq.length, tax, cut, pen, lines, warn, needReport: tax > 0 || unknown > 0 || (lq.length && !liquorFree) || (tb > RULES.tobacco) };
}

const opt = (list, cur) => list.map(c => `<option value="${c.id}" ${c.id === cur ? "selected" : ""}>${esc(c.name)}</option>`).join("");
function travelForm() {
  return `
    <section class="card tool rise" style="--i:1">
      <h2>1. 해외에서 산 물건</h2>
      <p class="muted">면세점에서 산 물건과 선물도 모두 넣어요. 가격은 산 가격(미화) 그대로.</p>
      <div class="rows" data-list="items">${T.items.map((it, i) => `
        <div class="row">
          <select data-f="items.${i}.cat" aria-label="품목 종류">${opt(CATS, it.cat)}</select>
          <label class="money"><span>$</span><input inputmode="decimal" data-f="items.${i}.usd" value="${esc(it.usd)}" placeholder="가격" aria-label="가격(미화)"></label>
          <button type="button" class="x" data-del="items.${i}" aria-label="이 줄 지우기">×</button>
        </div>`).join("")}</div>
      <button type="button" class="add" data-add="items">+ 물건 추가</button>

      <h2>2. 술 <small>병 수 제한 없이 합계 2L · $400까지 면세</small></h2>
      <div class="rows">${T.liquor.map((l, i) => `
        <div class="row">
          <select data-f="liquor.${i}.type" aria-label="술 종류">${opt(LIQ, l.type)}</select>
          <label class="money"><input inputmode="numeric" data-f="liquor.${i}.ml" value="${esc(l.ml)}" placeholder="용량" aria-label="용량(ml)"><span>ml</span></label>
          <label class="money"><span>$</span><input inputmode="decimal" data-f="liquor.${i}.usd" value="${esc(l.usd)}" placeholder="가격" aria-label="가격(미화)"></label>
          <button type="button" class="x" data-del="liquor.${i}" aria-label="이 줄 지우기">×</button>
        </div>`).join("")}</div>
      <button type="button" class="add" data-add="liquor">+ 술 추가</button>

      <h2>3. 향수 · 담배</h2>
      <div class="row">
        <span class="lbl">향수</span>
        <label class="money"><input inputmode="numeric" data-f="perfume.ml" value="${esc(T.perfume.ml)}" placeholder="합계 용량" aria-label="향수 용량(ml)"><span>ml</span></label>
        <label class="money"><span>$</span><input inputmode="decimal" data-f="perfume.usd" value="${esc(T.perfume.usd)}" placeholder="가격" aria-label="향수 가격(미화)"></label>
      </div>
      <div class="row">
        <span class="lbl">담배</span>
        <label class="money"><input inputmode="numeric" data-f="tobacco" value="${esc(T.tobacco)}" placeholder="개비 수" aria-label="담배 개비 수"><span>개비</span></label>
      </div>

      <h2>4. 기타</h2>
      <div class="row wrap">
        <label class="money"><span>환율 ₩</span><input inputmode="decimal" data-f="fx" value="${esc(T.fx)}" aria-label="환율(원/달러)"><span>/ $</span></label>
        <label class="chk"><input type="checkbox" data-f="adult" ${T.adult ? "checked" : ""}> 만 19세 이상</label>
        <label class="chk"><input type="checkbox" data-f="repeat" ${T.repeat ? "checked" : ""}> 최근 2년 안에 미신고로 걸린 적 있음</label>
      </div>
      <button type="button" class="reset" data-reset="travel">모두 지우기</button>
    </section>`;
}

function travelResult() {
  const r = calcTravel();
  const free = !r.needReport;
  return `
    <div class="verdict ${free ? "ok" : "warn"}">
      <b>${free ? "면세 범위 안이에요" : r.tax > 0 ? "세관에 신고해야 해요" : "신고 대상이에요"}</b>
      <span>일반 물품 합계 ${usd(r.totalGen)} / 기본 면세 $${RULES.exempt}${r.lq ? ` · 술 ${r.lml.toLocaleString()}ml ${usd(r.lusd)}` : ""}</span>
    </div>
    ${r.tax > 0 ? `
      <div class="cmp">
        <div class="opt good"><small>자진신고하면</small><b>${won(r.tax - r.cut)}</b><span>세금 ${won(r.tax)}에서 30% 감면(최대 20만 원)</span></div>
        <div class="opt bad"><small>신고 안 하고 걸리면</small><b>${won(r.tax + r.pen)}</b><span>세금 + 가산세 ${T.repeat ? "60" : "40"}%</span></div>
      </div>
      <p class="diff">신고하면 <b>${won(r.pen + r.cut)}</b> 아껴요.</p>
      <details class="calc"><summary>계산 내역 (추정)</summary><ul>${r.lines.map(([t, v]) => `<li><span>${esc(t)}</span><b>${usd(v)}</b></li>`).join("")}</ul>
        <p class="muted">환율 ${num(T.fx).toLocaleString()}원 기준. 세관은 신고일 과세환율과 실제 물품 정보로 다시 계산해요.</p></details>` : ""}
    ${r.warn.length ? `<ul class="warns">${r.warn.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
}

const FACTS = [
  { big: "−30%", sub: "최대 20만 원", t: "자진신고 감면", d: "면세 범위를 넘었으면 입국 때 신고하세요. 모바일로도 돼요.", tone: "good" },
  { big: "+40%", sub: "2년 내 재적발 60%", t: "미신고 가산세", d: "신고 안 하고 걸리면 세금에 가산세가 붙어요.", tone: "bad" },
  { big: "$800", sub: "1인 기준", t: "기본 면세", d: "가족 한도를 합쳐 비싼 물건 하나를 나눌 수는 없어요.", tone: "" },
  { big: "포함", sub: "국내 면세점", t: "면세점 물건도 합산", d: "출국 때 면세점에서 산 물건도 $800에 들어가요.", tone: "" },
  { big: "2L · $400", sub: "병 수 무관 · 2025.3~", t: "술", d: "둘 중 하나라도 넘으면 과세돼요.", tone: "" },
  { big: "100ml · 200개비", sub: "만 19세 이상", t: "향수 · 담배", d: "$800과 따로 면세. 19세 미만은 술·담배 면세가 없어요.", tone: "" },
];
// AI가 정리한 값을 계산기에 채워요. 달러가 아닌 금액은 대략 환율로 바꾸고 화면에 알려요.
const ROUGH_USD = { USD: 1, JPY: 0.0068, EUR: 1.08, CNY: 0.14, GBP: 1.27, THB: 0.029, VND: 0.00004, TWD: 0.031, HKD: 0.128, SGD: 0.74 };
let travelAINote = "";
function toUsd(price, cur, used) {
  if (!price) return 0;
  if (cur === "KRW") { used.add("KRW"); return price / (num(T.fx) || fxDefault); }
  if (cur !== "USD") used.add(cur);
  return price * (ROUGH_USD[cur] || 1);
}
const r2 = v => v ? String(Math.round(v * 100) / 100) : "";
function applyTravelAI(r) {
  const used = new Set();
  const items = r.items.map(i => ({ cat: i.cat, usd: r2(toUsd(i.price, i.currency, used)), name: i.name }));
  const liquor = r.liquor.map(l => ({ type: l.type, ml: l.ml ? String(l.ml) : "", usd: r2(toUsd(l.price, l.currency, used)), name: l.name }));
  T.items = items.length ? items : [{ cat: "gen", usd: "" }];
  T.liquor = liquor.length ? liquor : [{ type: "spirit", ml: "", usd: "" }];
  T.perfume = { ml: r.perfume.ml ? String(r.perfume.ml) : "", usd: r2(toUsd(r.perfume.price, r.perfume.currency, used)) };
  T.tobacco = r.tobacco ? String(r.tobacco) : "";
  saveT();
  const conv = [...used].filter(c => c !== "KRW").map(c => `1 ${c} ≈ $${ROUGH_USD[c]}`);
  const filled = [...items.map(i => i.name), ...liquor.map(l => l.name)].filter(Boolean);
  travelAINote = `<p class="ai-sum">${filled.length ? `${filled.length}개 항목을 채웠어요: ${esc(filled.join(", "))}` : "채울 항목을 찾지 못했어요. 물건 이름과 가격을 함께 적어 주세요."}</p>
    ${used.size ? `<p class="muted small">${used.has("KRW") ? `원화는 입력한 환율(${num(T.fx).toLocaleString()}원)로, ` : ""}${conv.length ? `${esc(conv.join(" · "))} 대략 환율로 바꿨어요. ` : ""}정확한 금액은 아래 칸에서 고쳐 주세요.</p>` : ""}
    ${ulist(r.notes)}
    <p class="ai-foot">AI가 글을 읽고 채운 값이에요. 칸마다 맞는지 확인하세요. 세금은 관세청 기준표로 계산해요.</p>`;
  rerenderTravel(true);
}
const travelAI = () => `
  <section class="card ai-card tool-ai rise" style="--i:1" data-ai-tool="travel">
    ${aiHead("AI로 한 번에 입력")}
    <p class="muted">산 물건을 평소 말하듯 적으면 AI가 아래 칸을 채워요. 엔화·유로·원화로 적어도 돼요.</p>
    <form class="ai-ask col" data-ai-travel>
      <label for="ai-travel-q" class="sr">쇼핑 목록</label>
      <textarea id="ai-travel-q" maxlength="600" rows="3" placeholder="예: 면세점 립스틱 2개 90달러, 위스키 700ml 2병 각 120달러, 일본 드럭스토어 화장품 2만 엔, 운동화 150달러, 담배 1보루" ${aiOn() ? "" : "disabled"}></textarea>
      <button class="ai-btn" ${aiOn() ? "" : "disabled"}>AI로 채우기</button>
    </form>
    <div class="ai-result" aria-live="polite">${aiOn() ? travelAINote : aiOffNote()}</div>
  </section>`;

const travelTips = () => `
  <section class="facts rise" style="--i:3">
    <div class="facts-h"><h2>모르면 손해 보는 6가지</h2><span>기준 ${RULES.checked} · ${link(SRC.travel, "관세청 안내")}</span></div>
    <div class="fact-grid">${FACTS.map(f => `
      <article class="fact ${f.tone}">
        <div class="fig"><b>${f.big}</b><small>${f.sub}</small></div>
        <h3>${f.t}</h3>
        <p>${f.d}</p>
      </article>`).join("")}</div>
    <p class="src">간이세율은 대표 품목 기준이라 실제 세액과 다를 수 있어요.</p>
  </section>`;

// ═════════════ 2. 직구 반입 체커 ═════════════
const ST = { ok: ["가능", "ok"], limit: ["조건부", "limit"], ban: ["불가", "ban"], check: ["확인 필요", "check"] };
const ITEMS = [
  { id: "supplement", k: "영양제 건강기능식품 비타민 오메가3 유산균 아이허브", name: "영양제·건강기능식품", st: "limit",
    say: "합계 6병까지, 가격 합계 $150 이하면 면세(미국도 $150).", know: "6병을 넘으면 판매용으로 보고 식품 수입 요건을 요구해요. 성분에 따라 아예 막히는 제품도 있어요." },
  { id: "melatonin", k: "멜라토닌 수면 보조제 melatonin", name: "멜라토닌", st: "ban",
    say: "국내에서는 전문의약품 성분이라 의사 소견서 없이 직구로 들여올 수 없어요.", know: "해외에선 영양제처럼 팔지만, 수량·가격과 상관없이 통관이 막혀요." },
  { id: "diet", k: "다이어트 보조제 체중 감량 근육 보충제 성기능", name: "다이어트·근육·성기능 보조제", st: "check",
    say: "식약처가 위해 성분(시부트라민 등)이 든 제품을 계속 차단 목록에 올려요.", know: `사기 전에 ${link(SRC.food, "식품안전나라 해외직구 위해식품 차단 목록")}에서 제품명을 검색해 보세요.` },
  { id: "medicine", k: "의약품 감기약 진통제 타이레놀 약 연고", name: "일반 의약품", st: "limit",
    say: "6병(또는 3개월 복용량) 이하. 처방이 필요한 약은 처방전·소견서가 있어야 해요.", know: "마약류·향정신성 성분이 든 약(일부 감기약·수면제 등)은 개인용이라도 들여올 수 없어요." },
  { id: "meat", k: "육포 소시지 햄 고기 육가공 스팸 사골", name: "육포·소시지·햄 등 육가공품", st: "ban",
    say: "가축전염병 예방 때문에 직구·우편·여행 휴대 모두 반입 금지예요.", know: "포장된 완제품이어도 막혀요. 여행 가방에 넣어 오다 걸리면 과태료를 물어요." },
  { id: "petfood", k: "반려동물 사료 간식 강아지 고양이 펫푸드", name: "반려동물 사료·간식", st: "check",
    say: "고기 등 동물성 원료가 든 사료는 검역 대상이라 개인 직구가 막히는 경우가 많아요.", know: "식물성 원료만 쓴 제품인지, 검역 증명서가 있는지 판매처에 먼저 확인하세요." },
  { id: "dairy", k: "치즈 유제품 버터 분유", name: "치즈·버터 등 유제품", st: "check",
    say: "품목과 생산국에 따라 검역 대상이 달라요.", know: "같은 치즈라도 나라·가공 방법에 따라 막히기도 해요. 비싼 제품은 판매처에 한국 배송 이력이 있는지 먼저 확인하세요." },
  { id: "seed", k: "씨앗 묘목 식물 흙 구근 꽃씨", name: "씨앗·묘목·흙", st: "ban",
    say: "흙은 반입 금지, 식물·씨앗은 식물검역을 받아야 해서 개인 직구는 대부분 폐기돼요.", know: "작은 씨앗 한 봉지도 검역 대상이에요." },
  { id: "radio", k: "스마트폰 이어폰 블루투스 드론 노트북 태블릿 전자제품 무선 공유기 스마트워치", name: "전자제품 (무선·전파 기기)", st: "limit",
    say: "같은 제품은 한 사람당 1대까지 전파인증(KC) 없이 들여올 수 있어요.", know: "같은 모델을 2대 이상 사면 인증을 요구받아요. 중고로 되팔려면 들여온 날부터 1년이 지나야 해요." },
  { id: "battery", k: "보조배터리 리튬 배터리 전동 킥보드", name: "보조배터리·리튬 배터리 단독", st: "check",
    say: "통관보다 운송이 문제예요. 항공 운송 제한 때문에 배송대행지·특송사가 거절하는 경우가 많아요.", know: "제품에 내장된 배터리는 대체로 괜찮지만, 배터리만 따로 보내는 건 어려워요." },
  { id: "cosmetic", k: "화장품 스킨케어 선크림 립스틱 향수", name: "화장품", st: "ok",
    say: "자가사용 수량이면 목록통관으로 들어와요. 면세 $150(미국 $200).", know: "같은 제품을 많이 사면 판매용으로 보고 일반 수입 요건을 요구할 수 있어요." },
  { id: "apparel", k: "옷 의류 신발 가방 잡화 운동화 시계", name: "의류·신발·잡화", st: "ok",
    say: "목록통관 대상. 면세 $150(미국에서 오면 $200).", know: "같은 날 들어온 물건은 합쳐서 면세 한도를 따져요(아래 합산과세 참고)." },
  { id: "fake", k: "짝퉁 레플리카 가품 위조 명품", name: "가품(레플리카)", st: "ban",
    say: "상표권 침해 물품이라 개인이 쓸 목적이어도 직구는 통관 보류·폐기 대상이에요.", know: "폐기되면 결제한 돈을 돌려받기 어려워요. 여행 때 들고 오는 소량만 예외로 봐 주는 경우가 있어요." },
  { id: "weapon", k: "모의총 에어소프트 비비탄총 도검 칼 전기충격기 석궁 가스총", name: "모의총포·도검·전기충격기", st: "ban",
    say: "경찰청 허가가 필요한 물품이라 개인 직구로 들어올 수 없어요.", know: "장난감처럼 보여도 실제 총과 비슷하면 모의총포로 막혀요." },
  { id: "liquor", k: "술 와인 위스키 주류 사케 맥주", name: "술", st: "limit",
    say: "직구는 가능하지만 소량(1병·1L, $150 이하)이어도 주세·교육세·부가세가 붙어요.", know: "관세만 면제되는 거라 생각보다 세금이 많이 나와요. 여행 귀국 때 들고 오는 게 대체로 유리해요." },
];

const checkView = (q = "") => `
  <div class="page-h rise"><p class="kicker">Customs Helper</p><h1 class="display sm">이거 <em>직구</em>해도 돼요?</h1>
    <p>자주 사는 품목의 반입 가능 여부와 사람들이 잘 모르는 함정을 정리했어요.</p></div>
  <section class="card tool rise" style="--i:1">
    <label class="search"><span class="sr">품목 검색</span><input type="search" data-check-q value="${esc(q)}" placeholder="예: 멜라토닌, 육포, 이어폰, 영양제" autocomplete="off"></label>
    <div class="legend">${Object.values(ST).map(([t, c]) => `<span class="st ${c}">${t}</span>`).join("")}</div>
    <ul class="checks" data-check-list>${checkList(q)}</ul>
    <div class="ai-card check-ai" data-ai-tool="customs">
      ${aiHead("목록에 없거나 헷갈리면 AI에게 물어보기")}
      <p class="muted">제품명이나 링크 속 상품 설명을 적으면, 위 기준표에서 해당하는 항목을 찾아 확인할 점을 알려 드려요.</p>
      <form class="ai-ask" data-ai-customs>
        <label for="ai-customs-q" class="sr">사려는 제품</label>
        <input id="ai-customs-q" maxlength="300" placeholder="예: 아이허브 마그네슘 + 멜라토닌 수면 젤리 2통" ${aiOn() ? "" : "disabled"}>
        <button class="ai-btn" ${aiOn() ? "" : "disabled"}>물어보기</button>
      </form>
      <div class="ai-result" aria-live="polite">${aiOn() ? "" : aiOffNote()}</div>
    </div>
    <p class="src">기준: 관세청 「수입통관 사무처리에 관한 고시」 자가사용 인정기준, 식약처·농림축산검역본부 안내 (${RULES.checked} 확인) · ${link(SRC.easylaw, "찾기쉬운 생활법령: 해외직구")}</p>
  </section>

  <div class="guides">
    <section class="card guide rise" style="--i:2">
      <span class="gi">1</span>
      <h2>합산과세 <small>나눠 사도 합쳐져요</small></h2>
      <p class="lead">같은 날 한국에 도착한 물건은 쇼핑몰이 달라도 <b>가격을 합쳐서</b> 면세 한도를 따져요.</p>
      <div class="eq" aria-label="예시: 100달러 두 건이 같은 날 도착하면 합계 200달러로 과세">
        <span class="box">$100<small>A 쇼핑몰</small></span><i>+</i><span class="box">$100<small>B 쇼핑몰</small></span><i>=</i><span class="box hot">$200<small>같은 날 도착</small></span>
      </div>
      <p class="note-s">미국 외 나라에서 오면 한도 $150을 넘어 과세 · 미국은 $200까지 면세</p>
      <p class="todo"><b>이렇게</b> 도착일이 며칠 벌어지게 나눠 받기. 단, 한 주문을 일부러 쪼개면 합쳐서 과세될 수 있어요.</p>
    </section>

    <section class="card guide rise" style="--i:3">
      <span class="gi">2</span>
      <h2>되팔기 <small>중고거래 전에 확인</small></h2>
      <p class="lead">면세로 들여온 직구품은 <b>내가 쓰는 조건</b>이라, 팔면 관세법 위반이 될 수 있어요.</p>
      <ul class="yn">
        <li class="n"><span>✕</span><p>직구한 옷·화장품 등을 바로 중고 판매</p></li>
        <li class="n"><span>✕</span><p>영양제·의약품 개인 간 판매</p></li>
        <li class="y"><span>✓</span><p>전자제품은 들여온 날부터 <b>1년 지나면</b> 1대 판매 가능</p></li>
      </ul>
    </section>

    <section class="card guide rise" style="--i:4">
      <span class="gi">3</span>
      <h2>반품 관세 환급 <small>세금도 돌려받기</small></h2>
      <p class="lead">세금 내고 들여온 물건을 <b>6개월 안에</b> 그대로 반품하면 관세·부가세를 돌려받아요. 판매처는 세금을 안 돌려줘요.</p>
      <ol class="steps">
        <li><b>반품</b>판매자에게 원래 상태로 반품</li>
        <li><b>서류</b>반품 송장 · 반품 확인서 · 환불 영수증</li>
        <li><b>신청</b>가까운 세관에 환급 신청 (200만 원 이하는 수출신고 없이)</li>
      </ol>
      <p class="more-l">${link(SRC.refund, "관세청 해외직구물품 관세환급 →")}</p>
    </section>

    <section class="card guide rise" style="--i:5">
      <span class="gi">4</span>
      <h2>개인통관고유부호 <small>도용 확인</small></h2>
      <p class="lead">여러 쇼핑몰에 입력하다 보니 유출이 잦아요. 남이 쓴 건이 내 면세 한도·합산과세에 섞일 수 있어요.</p>
      <ol class="steps">
        <li><b>조회</b>유니패스 → 개인통관고유부호 → 사용 내역</li>
        <li><b>재발급</b>모르는 통관이 있으면 바로 재발급 (기존 번호 정지)</li>
        <li><b>변경</b>쇼핑몰·배송대행지에 새 번호로 바꾸기 (이름·휴대폰이 다르면 통관 보류)</li>
      </ol>
      <p class="more-l">${link(SRC.unipass, "관세청 유니패스 →")}</p>
    </section>
  </div>`;

const plain = h => String(h).replace(/<[^>]+>/g, "");
const customsContext = () => ITEMS.map(x => `${x.id} | ${x.name} | ${ST[x.st][0]} | ${plain(x.say)} ${plain(x.know)}`).join("\n");
const itemCard = x => `
    <li class="ck ${ST[x.st][1]}">
      <span class="st ${ST[x.st][1]}">${ST[x.st][0]}</span>
      <div><h3>${esc(x.name)}</h3><p>${esc(x.say)}</p><p class="know"><b>잘 모르는 점</b> ${x.know}</p></div>
    </li>`;
function renderCustomsAI(r) {
  const hit = r.match.map(id => ITEMS.find(x => x.id === id)).filter(Boolean);
  // 기준표 항목을 찾았으면 판정은 기준표 값을 그대로 써요 (AI 판정보다 우선)
  const order = ["ban", "check", "limit", "ok"];
  const st = hit.length ? hit.map(x => x.st).sort((a, b) => order.indexOf(a) - order.indexOf(b))[0] : r.verdict;
  return `
    <div class="ai-verdict"><span class="st ${ST[st][1]}">${ST[st][0]}</span><p class="ai-sum">${esc(r.reason)}</p></div>
    ${r.ingredients.length ? `<p class="ings">확인할 성분 ${r.ingredients.map(i => `<span>${esc(i)}</span>`).join("")}</p>` : ""}
    ${r.checks.length ? `<h3 class="ai-h">사기 전에 확인할 일</h3>${ulist(r.checks)}` : ""}
    ${hit.length ? `<h3 class="ai-h">해당하는 기준표 항목</h3><ul class="checks">${hit.map(itemCard).join("")}</ul>`
      : `<p class="muted small">기준표에 딱 맞는 항목이 없어요. 관세청 고객지원센터(125)나 ${link(SRC.unipass, "유니패스 수입요건 조회")}로 확인하세요.</p>`}
    <p class="ai-foot">AI가 기준표에서 찾아 정리한 참고 안내예요. 판정은 기준표 항목을 따르고, 실제 통관은 세관이 결정해요.</p>`;
}

function checkList(q) {
  const t = q.trim().toLowerCase();
  const list = t ? ITEMS.filter(x => (x.k + " " + x.name).toLowerCase().includes(t)) : ITEMS;
  if (!list.length) return `<li class="none">"${esc(q)}"는 목록에 없어요. 의약품·식품·동식물·전파기기·무기류에 해당하면 확인이 필요해요. ${link(SRC.unipass, "유니패스")}의 수입요건 조회나 관세청 고객지원센터(국번 없이 125)에 문의하세요.</li>`;
  return list.map(itemCard).join("");
}

// ═════════════ 3. 통관 진행 조회 ═════════════
const STAGES = [
  ["입항", "배·비행기가 한국에 도착했어요.", "기다리면 돼요. 보통 하루 안에 다음 단계로 넘어가요."],
  ["하선·하기 신고", "화물을 내려 보세구역(세관 창고)으로 옮기는 중이에요.", "기다리면 돼요."],
  ["반입신고", "세관 창고에 들어왔어요. 이제 통관 심사를 받아요.", "목록통관은 대개 1~2일 안에 끝나요."],
  ["수입신고", "통관 심사가 시작됐어요. 목록통관(간단) 또는 일반통관(정식 신고)으로 처리돼요.", "일반통관이면 세금 고지가 나올 수 있어요. 배송사나 관세사 연락을 확인하세요."],
  ["검사 대상 선별", "X-ray나 개봉 검사 대상으로 뽑혔어요.", "문제가 없으면 하루 이틀 늦어질 뿐이에요. 연락이 오면 서류를 내면 돼요."],
  ["통관보류", "서류나 요건 문제로 통관이 멈췄어요.", "가만히 두면 안 풀려요. 보류 사유를 확인하고 세관이나 배송사에 연락하세요. 흔한 이유: 개인통관고유부호 이름·번호 불일치, 수량 초과, 수입요건 미비."],
  ["수입신고수리", "통관이 끝났어요. 세금이 있다면 이 단계 전후로 내요.", "세금을 안 냈다면 고지서(카카오톡·문자)를 확인하세요."],
  ["반출신고", "세관 창고에서 나와 국내 택배사로 넘어갔어요.", "이제 국내 택배 운송장으로 조회하세요."],
];
const trackView = () => `
  <div class="page-h rise"><p class="kicker">Customs Tracking</p><h1 class="display sm">내 택배 <em>통관</em> 어디까지?</h1>
    <p>번호를 넣으면 관세청 유니패스 조회로 바로 가요. 단계별로 무슨 뜻이고 뭘 해야 하는지도 알려 드려요.</p></div>
  <section class="card tool rise" style="--i:1">
    <form class="track" data-track>
      <label><span>조회 방법</span>
        <select data-track-kind><option value="cargo">화물관리번호</option><option value="bl">운송장(B/L) 번호</option></select></label>
      <label class="grow"><span>번호</span><input data-track-no placeholder="배송대행지·쇼핑몰 주문 내역의 운송장 또는 화물관리번호" autocomplete="off" inputmode="latin"></label>
      <label data-track-year-wrap hidden><span>입항 연도</span><select data-track-year>${[0, 1].map(d => { const y = new Date().getFullYear() - d; return `<option>${y}</option>`; }).join("")}</select></label>
      <button class="ai-btn">유니패스에서 조회</button>
    </form>
    <p class="muted small" data-track-msg>번호를 복사해 두고 유니패스 화면으로 이동해요. 유니패스 첫 화면의 <b>화물진행정보</b>에 붙여 넣으면 돼요. (외부 사이트는 자동 입력을 막아 두어 붙여 넣기가 필요해요)</p>
  </section>

  <section class="card ai-card track-ai rise" style="--i:2;margin-top:18px" data-ai-tool="track">
    ${aiHead("받은 문자·상태 문구 해석")}
    <p class="muted">유니패스 진행 상태나 배송사·세관 문자를 붙여 넣으면 무슨 뜻인지, 지금 뭘 하면 되는지 알려 드려요. 링크가 있으면 공식 주소인지도 바로 확인해요.</p>
    <form class="ai-ask col" data-ai-track>
      <label for="ai-track-q" class="sr">문자 또는 상태 문구</label>
      <textarea id="ai-track-q" maxlength="600" rows="3" placeholder="예: [관세청] 고객님의 물품이 통관보류되었습니다. 수입요건 미구비. 확인 바랍니다."></textarea>
      <button class="ai-btn">해석하기</button>
    </form>
    <div class="ai-result" aria-live="polite"></div>
  </section>

  <section class="card tool rise" style="--i:3;margin-top:18px">
    <h2>진행 단계 풀이</h2>
    <ol class="tl">${STAGES.map(([s, what, todo], i) => `
      <li class="${s === "통관보류" ? "hold" : ""}"><span class="dot">${i + 1}</span><div><h3>${s}</h3><p>${what}</p><p class="todo">→ ${todo}</p></div></li>`).join("")}</ol>
  </section>

  <div class="grid g-2" style="margin-top:18px">
    <section class="card tool tips rise" style="--i:3">
      <h2>목록통관 vs 일반통관</h2>
      <ul>
        <li><b>목록통관</b>: 옷·화장품 같은 일반 물품을 송장 목록만으로 빠르게 통관. 면세 $150(미국 $200) 이하.</li>
        <li><b>일반통관</b>: 영양제·의약품·식품 등은 금액이 작아도 정식 수입신고를 거쳐요. 하루 이틀 더 걸려요.</li>
        <li>면세 한도를 넘으면 어느 쪽이든 관세·부가세 고지가 나와요.</li>
      </ul>
    </section>
    <section class="card tool tips rise" style="--i:4">
      <h2>이럴 땐 이렇게</h2>
      <ul>
        <li><b>며칠째 같은 단계</b>: 반입신고 후 3일 넘게 그대로면 배송대행지·특송사에 먼저 문의하세요.</li>
        <li><b>세금 고지서</b>: 카카오톡·문자로 온 고지서는 금액과 화물관리번호가 맞는지 확인하고 내세요. 고지서를 사칭한 문자 링크는 누르지 마세요.</li>
        <li><b>문의</b>: 관세청 고객지원센터 국번 없이 <b>125</b>.</li>
      </ul>
    </section>
  </div>`;

// 링크 검사는 AI 없이도 바로 해요: 관세청·정부(go.kr) 주소가 아니면 주의
const OFFICIAL = /(^|\.)(customs\.go\.kr|go\.kr|korea\.kr)$/i;
function linkCheck(text) {
  const urls = String(text).match(/(https?:\/\/|www\.)[^\s)<>"']+|\b[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|net|kr|co|me|ly|io|xyz|top|site|info|link|cc|tk|shop)(\/[^\s]*)?/gi) || [];
  return [...new Set(urls)].map(u => {
    let host = u.replace(/^https?:\/\//i, "").replace(/^www\./i, "").split(/[/?#:]/)[0].toLowerCase();
    return { url: u, host, ok: OFFICIAL.test(host) };
  });
}
const linkCheckHtml = list => list.length ? `<ul class="links-chk">${list.map(l => `<li class="${l.ok ? "ok" : "bad"}"><span>${l.ok ? "공식 주소" : "주의"}</span><code>${esc(l.host)}</code>${l.ok ? "" : " — 관세청·정부(go.kr) 주소가 아니에요. 링크로 결제하지 말고 유니패스에서 직접 확인하세요."}</li>`).join("")}</ul>` : "";
const trackContext = () => STAGES.map(([s, w, t]) => `${s}: ${w} 할 일: ${t}`).join("\n");
function renderTrackAI(r, links) {
  const scam = r.scam === "suspect" || links.some(l => !l.ok);
  return `
    ${scam ? `<div class="scam"><b>사칭 문자일 수 있어요</b><span>${esc(r.scam_reason || "공식 주소가 아닌 링크가 있어요.")} 세금은 유니패스·은행 앱에서 직접 확인하고 내세요.</span></div>` : ""}
    ${linkCheckHtml(links)}
    <div class="ai-verdict"><span class="st check">${esc(r.stage || "모름")}</span><p class="ai-sum">${esc(r.meaning)}</p></div>
    ${r.actions.length ? `<h3 class="ai-h">지금 할 일</h3>${ulist(r.actions)}` : ""}
    <p class="ai-foot">AI가 문구를 풀어 쓴 참고 안내예요. 정확한 상태는 유니패스와 배송사에서 확인하세요.</p>`;
}

// ═════════════ 화면 묶기 ═════════════
const views = {
  travel() {
    return `
      <div class="page-h rise"><p class="kicker">Travel Duty-Free</p><h1 class="display sm">여행자 <em>면세</em> 계산기</h1>
        <p>귀국할 때 산 물건을 넣으면 면세인지, 세금이 얼마인지, 자진신고하면 얼마 아끼는지 알려 드려요.</p></div>
      ${travelAI()}
      <div class="tool-2">
        ${travelForm()}
        <section class="card tool result rise" style="--i:2" aria-live="polite"><div data-travel-result>${travelResult()}</div></section>
      </div>
      ${travelTips()}`;
  },
  check: (q) => checkView(q || ""),
  track: trackView,
};

// 입력 → 상태 반영 (전체를 다시 그리지 않고 결과만 갱신)
function setPath(obj, path, val) {
  const ks = path.split("."); let o = obj;
  ks.slice(0, -1).forEach(k => o = o[k]);
  o[ks[ks.length - 1]] = val;
}
function rerenderTravel(full) {
  const main = document.querySelector("#view");
  if (full) { const y = scrollY; main.innerHTML = views.travel(); main.querySelectorAll(".rise").forEach(e => e.classList.remove("rise")); scrollTo(0, y); return; }
  const box = main.querySelector("[data-travel-result]");
  if (box) box.innerHTML = travelResult();
}

document.addEventListener("input", e => {
  const f = e.target.closest("[data-f]");
  if (f && f.closest("#view") && location.hash.startsWith("#/travel")) {
    setPath(T, f.dataset.f, f.type === "checkbox" ? f.checked : f.value);
    saveT(); rerenderTravel(false);
  }
  const q = e.target.closest("[data-check-q]");
  if (q) { const ul = document.querySelector("[data-check-list]"); if (ul) ul.innerHTML = checkList(q.value); }
});
document.addEventListener("change", e => {
  const k = e.target.closest("[data-track-kind]");
  if (k) document.querySelector("[data-track-year-wrap]").hidden = k.value !== "bl";
});
document.addEventListener("click", e => {
  const add = e.target.closest("[data-add]");
  if (add) { T[add.dataset.add].push(add.dataset.add === "items" ? { cat: "gen", usd: "" } : { type: "spirit", ml: "", usd: "" }); saveT(); rerenderTravel(true); return; }
  const del = e.target.closest("[data-del]");
  if (del) { const [k, i] = del.dataset.del.split("."); T[k].splice(+i, 1); if (!T[k].length) T[k].push(k === "items" ? { cat: "gen", usd: "" } : { type: "spirit", ml: "", usd: "" }); saveT(); rerenderTravel(true); return; }
  if (e.target.closest("[data-reset='travel']")) { T = { fx: T.fx, adult: true, repeat: false, items: [{ cat: "gen", usd: "" }], liquor: [{ type: "spirit", ml: "", usd: "" }], perfume: { ml: "", usd: "" }, tobacco: "" }; saveT(); rerenderTravel(true); }
});
document.addEventListener("submit", e => {
  const tf = e.target.closest("[data-ai-travel]");
  if (tf) {
    e.preventDefault();
    const q = tf.querySelector("textarea").value.trim();
    const card = tf.closest(".ai-card");
    if (q.length < 4) { card.querySelector(".ai-result").innerHTML = `<p class="ai-err">산 물건과 가격을 적어 주세요.</p>`; return; }
    runToolAI(card, "travel", { query: q }, r => { setTimeout(() => applyTravelAI(r)); return `<p class="muted">채우는 중…</p>`; });
    return;
  }
  const cf = e.target.closest("[data-ai-customs]");
  if (cf) {
    e.preventDefault();
    const q = cf.querySelector("input").value.trim();
    const card = cf.closest(".ai-card");
    if (q.length < 2) { card.querySelector(".ai-result").innerHTML = `<p class="ai-err">제품 이름을 두 글자 이상 적어 주세요.</p>`; return; }
    runToolAI(card, "customs", { query: q, context: customsContext() }, renderCustomsAI);
    return;
  }
  const kf = e.target.closest("[data-ai-track]");
  if (kf) {
    e.preventDefault();
    const q = kf.querySelector("textarea").value.trim();
    const card = kf.closest(".ai-card"), out = card.querySelector(".ai-result");
    if (q.length < 4) { out.innerHTML = `<p class="ai-err">받은 문자나 상태 문구를 붙여 넣어 주세요.</p>`; return; }
    const links = linkCheck(q);
    if (!aiOn()) {   // AI가 없어도 링크 검사는 보여줘요
      out.innerHTML = (links.length ? linkCheckHtml(links) : `<p class="muted small">문자에 링크가 없어요.</p>`) + aiOffNote().replace("AI 기능은", "문구 해석(AI)은");
      return;
    }
    runToolAI(card, "track", { query: q, context: trackContext() }, r => renderTrackAI(r, links));
  }
});

document.addEventListener("submit", async e => {
  const f = e.target.closest("[data-track]");
  if (!f) return;
  e.preventDefault();
  const no = f.querySelector("[data-track-no]").value.trim().replace(/\s+/g, "");
  const msg = document.querySelector("[data-track-msg]");
  if (no.length < 6) { msg.innerHTML = `<span class="ai-err">번호를 6자리 이상 넣어 주세요.</span>`; return; }
  let copied = false;
  try { await navigator.clipboard.writeText(no); copied = true; } catch {}
  const kind = f.querySelector("[data-track-kind]").value;
  const year = f.querySelector("[data-track-year]").value;
  msg.innerHTML = `${copied ? `번호 <b>${esc(no)}</b>를 복사했어요.` : `번호 <b>${esc(no)}</b>를 복사해 주세요.`} 유니패스 첫 화면 <b>화물진행정보</b>에서 ${kind === "bl" ? `<b>B/L 번호</b>를 고르고 입항 연도 <b>${esc(year)}</b>를 선택한 뒤` : `<b>화물관리번호</b>를 고르고`} 붙여 넣으세요.`;
  window.open(SRC.unipass, "_blank", "noopener");
});

window.TC_TOOLS = { views };
})();
