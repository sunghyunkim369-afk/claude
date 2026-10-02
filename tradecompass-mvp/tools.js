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

const travelTips = () => `
  <section class="card tool tips rise" style="--i:3">
    <h2>모르면 손해 보는 것</h2>
    <ul>
      <li><b>자진신고 감면</b> — 면세 범위를 넘으면 입국 때 신고(모바일 신고 가능)하면 세금의 30%를 최대 20만 원까지 깎아줘요. 신고 안 하고 걸리면 반대로 40% 가산세(2년 내 두 번째부터 60%)가 붙어요.</li>
      <li><b>$800은 한 사람 기준</b> — 가족 면세 한도를 합쳐 비싼 물건 하나를 나눌 수는 없어요. 물건마다 실제 산 사람 기준이에요.</li>
      <li><b>면세점 물건도 포함</b> — 출국할 때 국내 면세점에서 산 물건도 들고 들어오면 $800에 포함돼요.</li>
      <li><b>술은 병 수 제한이 없어졌어요</b> — 2025년 3월부터 합계 2L·$400 이하면 몇 병이든 면세예요. 둘 중 하나라도 넘으면 과세돼요.</li>
      <li><b>향수 100ml, 담배 200개비</b>는 $800과 따로 면세돼요. 만 19세 미만은 술·담배 면세가 없어요.</li>
    </ul>
    <p class="src">기준: 관세청 여행자 휴대품 통관 안내 (${RULES.checked} 확인) · ${link(SRC.travel, "관세청 안내 보기")} · 간이세율은 대표 품목 기준이라 실제 세액과 다를 수 있어요.</p>
  </section>`;

// ═════════════ 2. 직구 반입 체커 ═════════════
const ST = { ok: ["가능", "ok"], limit: ["조건부", "limit"], ban: ["불가", "ban"], check: ["확인 필요", "check"] };
const ITEMS = [
  { k: "영양제 건강기능식품 비타민 오메가3 유산균 아이허브", name: "영양제·건강기능식품", st: "limit",
    say: "합계 6병까지, 가격 합계 $150 이하면 면세(미국도 $150).", know: "6병을 넘으면 판매용으로 보고 식품 수입 요건을 요구해요. 성분에 따라 아예 막히는 제품도 있어요." },
  { k: "멜라토닌 수면 보조제 melatonin", name: "멜라토닌", st: "ban",
    say: "국내에서는 전문의약품 성분이라 의사 소견서 없이 직구로 들여올 수 없어요.", know: "해외에선 영양제처럼 팔지만, 수량·가격과 상관없이 통관이 막혀요." },
  { k: "다이어트 보조제 체중 감량 근육 보충제 성기능", name: "다이어트·근육·성기능 보조제", st: "check",
    say: "식약처가 위해 성분(시부트라민 등)이 든 제품을 계속 차단 목록에 올려요.", know: `사기 전에 ${link(SRC.food, "식품안전나라 해외직구 위해식품 차단 목록")}에서 제품명을 검색해 보세요.` },
  { k: "의약품 감기약 진통제 타이레놀 약 연고", name: "일반 의약품", st: "limit",
    say: "6병(또는 3개월 복용량) 이하. 처방이 필요한 약은 처방전·소견서가 있어야 해요.", know: "마약류·향정신성 성분이 든 약(일부 감기약·수면제 등)은 개인용이라도 들여올 수 없어요." },
  { k: "육포 소시지 햄 고기 육가공 스팸 사골", name: "육포·소시지·햄 등 육가공품", st: "ban",
    say: "가축전염병 예방 때문에 직구·우편·여행 휴대 모두 반입 금지예요.", know: "포장된 완제품이어도 막혀요. 여행 가방에 넣어 오다 걸리면 과태료를 물어요." },
  { k: "반려동물 사료 간식 강아지 고양이 펫푸드", name: "반려동물 사료·간식", st: "check",
    say: "고기 등 동물성 원료가 든 사료는 검역 대상이라 개인 직구가 막히는 경우가 많아요.", know: "식물성 원료만 쓴 제품인지, 검역 증명서가 있는지 판매처에 먼저 확인하세요." },
  { k: "치즈 유제품 버터 분유", name: "치즈·버터 등 유제품", st: "check",
    say: "품목과 생산국에 따라 검역 대상이 달라요.", know: "같은 치즈라도 나라·가공 방법에 따라 막히기도 해요. 비싼 제품은 판매처에 한국 배송 이력이 있는지 먼저 확인하세요." },
  { k: "씨앗 묘목 식물 흙 구근 꽃씨", name: "씨앗·묘목·흙", st: "ban",
    say: "흙은 반입 금지, 식물·씨앗은 식물검역을 받아야 해서 개인 직구는 대부분 폐기돼요.", know: "작은 씨앗 한 봉지도 검역 대상이에요." },
  { k: "스마트폰 이어폰 블루투스 드론 노트북 태블릿 전자제품 무선 공유기 스마트워치", name: "전자제품 (무선·전파 기기)", st: "limit",
    say: "같은 제품은 한 사람당 1대까지 전파인증(KC) 없이 들여올 수 있어요.", know: "같은 모델을 2대 이상 사면 인증을 요구받아요. 중고로 되팔려면 들여온 날부터 1년이 지나야 해요." },
  { k: "보조배터리 리튬 배터리 전동 킥보드", name: "보조배터리·리튬 배터리 단독", st: "check",
    say: "통관보다 운송이 문제예요. 항공 운송 제한 때문에 배송대행지·특송사가 거절하는 경우가 많아요.", know: "제품에 내장된 배터리는 대체로 괜찮지만, 배터리만 따로 보내는 건 어려워요." },
  { k: "화장품 스킨케어 선크림 립스틱 향수", name: "화장품", st: "ok",
    say: "자가사용 수량이면 목록통관으로 들어와요. 면세 $150(미국 $200).", know: "같은 제품을 많이 사면 판매용으로 보고 일반 수입 요건을 요구할 수 있어요." },
  { k: "옷 의류 신발 가방 잡화 운동화 시계", name: "의류·신발·잡화", st: "ok",
    say: "목록통관 대상. 면세 $150(미국에서 오면 $200).", know: "같은 날 들어온 물건은 합쳐서 면세 한도를 따져요(아래 합산과세 참고)." },
  { k: "짝퉁 레플리카 가품 위조 명품", name: "가품(레플리카)", st: "ban",
    say: "상표권 침해 물품이라 개인이 쓸 목적이어도 직구는 통관 보류·폐기 대상이에요.", know: "폐기되면 결제한 돈을 돌려받기 어려워요. 여행 때 들고 오는 소량만 예외로 봐 주는 경우가 있어요." },
  { k: "모의총 에어소프트 비비탄총 도검 칼 전기충격기 석궁 가스총", name: "모의총포·도검·전기충격기", st: "ban",
    say: "경찰청 허가가 필요한 물품이라 개인 직구로 들어올 수 없어요.", know: "장난감처럼 보여도 실제 총과 비슷하면 모의총포로 막혀요." },
  { k: "술 와인 위스키 주류 사케 맥주", name: "술", st: "limit",
    say: "직구는 가능하지만 소량(1병·1L, $150 이하)이어도 주세·교육세·부가세가 붙어요.", know: "관세만 면제되는 거라 생각보다 세금이 많이 나와요. 여행 귀국 때 들고 오는 게 대체로 유리해요." },
];

const checkView = (q = "") => `
  <div class="page-h rise"><p class="kicker">Customs Helper</p><h1 class="display sm">이거 <em>직구</em>해도 돼요?</h1>
    <p>자주 사는 품목의 반입 가능 여부와 사람들이 잘 모르는 함정을 정리했어요.</p></div>
  <section class="card tool rise" style="--i:1">
    <label class="search"><span class="sr">품목 검색</span><input type="search" data-check-q value="${esc(q)}" placeholder="예: 멜라토닌, 육포, 이어폰, 영양제" autocomplete="off"></label>
    <div class="legend">${Object.values(ST).map(([t, c]) => `<span class="st ${c}">${t}</span>`).join("")}</div>
    <ul class="checks" data-check-list>${checkList(q)}</ul>
    <p class="src">기준: 관세청 「수입통관 사무처리에 관한 고시」 자가사용 인정기준, 식약처·농림축산검역본부 안내 (${RULES.checked} 확인) · ${link(SRC.easylaw, "찾기쉬운 생활법령: 해외직구")}</p>
  </section>

  <div class="grid g-2" style="margin-top:18px">
    <section class="card tool tips rise" style="--i:2">
      <h2>합산과세: 나눠 사도 합쳐져요</h2>
      <ul>
        <li>같은 날 한국에 도착한 물건은 쇼핑몰이 달라도 <b>가격을 합쳐서</b> 면세 한도($150, 미국 $200)를 따져요.</li>
        <li>각각 $100짜리 두 건이 같은 날 들어오면 합계 $200 → 미국 외 나라면 과세돼요.</li>
        <li>며칠 간격으로 나눠 들어오게 하는 게 안전해요. 다만 한 주문을 일부러 쪼개면 합쳐서 과세할 수 있어요.</li>
      </ul>
    </section>
    <section class="card tool tips rise" style="--i:3">
      <h2>되팔기 전에 꼭 확인</h2>
      <ul>
        <li><b>면세로 들여온 직구품을 팔면</b> 자가사용 목적이 아니게 돼서 관세법 위반(밀수입 등)이 될 수 있어요. 중고거래 앱에 바로 올리는 게 흔한 실수예요.</li>
        <li><b>전자제품</b>은 예외적으로 들여온 날(수입신고수리일)부터 <b>1년이 지나면</b> 1대에 한해 중고로 팔 수 있어요.</li>
        <li>건강기능식품·의약품은 개인 간 판매 자체가 제한돼요.</li>
      </ul>
    </section>
    <section class="card tool tips rise" style="--i:4">
      <h2>반품하면 관세를 돌려받아요</h2>
      <ul>
        <li>세금을 내고 들여온 직구품을 <b>수입신고수리일부터 6개월 안에</b> 그대로 반품하면 낸 관세·부가세를 환급받을 수 있어요.</li>
        <li>200만 원 이하 물품은 수출신고 없이도 <b>반품 송장·반품 확인서·환불 영수증</b>으로 신청할 수 있어요.</li>
        <li>판매처가 상품값만 환불해 주고 세금은 안 돌려줘요. 직접 세관에 신청해야 해요. ${link(SRC.refund, "관세청 해외직구물품 관세환급")}</li>
      </ul>
    </section>
    <section class="card tool tips rise" style="--i:5">
      <h2>개인통관고유부호, 도용됐는지 확인</h2>
      <ul>
        <li>여러 쇼핑몰에 부호를 입력하다 보니 유출·도용 사례가 있어요. 남이 쓴 건이 내 면세 한도와 합산과세에 섞일 수 있어요.</li>
        <li>${link(SRC.unipass, "관세청 유니패스")} → 개인통관고유부호 메뉴에서 <b>사용 내역</b>을 보고, 모르는 통관이 있으면 바로 <b>재발급</b>(기존 번호 정지)하세요.</li>
        <li>부호의 이름·휴대폰 번호와 주문서 정보가 다르면 통관이 보류되니, 번호를 바꾸면 쇼핑몰 정보도 같이 바꿔 주세요.</li>
      </ul>
    </section>
  </div>`;

function checkList(q) {
  const t = q.trim().toLowerCase();
  const list = t ? ITEMS.filter(x => (x.k + " " + x.name).toLowerCase().includes(t)) : ITEMS;
  if (!list.length) return `<li class="none">"${esc(q)}"는 목록에 없어요. 의약품·식품·동식물·전파기기·무기류에 해당하면 확인이 필요해요. ${link(SRC.unipass, "유니패스")}의 수입요건 조회나 관세청 고객지원센터(국번 없이 125)에 문의하세요.</li>`;
  return list.map(x => `
    <li class="ck ${ST[x.st][1]}">
      <span class="st ${ST[x.st][1]}">${ST[x.st][0]}</span>
      <div><h3>${esc(x.name)}</h3><p>${esc(x.say)}</p><p class="know"><b>잘 모르는 점</b> ${x.know}</p></div>
    </li>`).join("");
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

  <section class="card tool rise" style="--i:2;margin-top:18px">
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

// ═════════════ 화면 묶기 ═════════════
const views = {
  travel() {
    return `
      <div class="page-h rise"><p class="kicker">Travel Duty-Free</p><h1 class="display sm">여행자 <em>면세</em> 계산기</h1>
        <p>귀국할 때 산 물건을 넣으면 면세인지, 세금이 얼마인지, 자진신고하면 얼마 아끼는지 알려 드려요.</p></div>
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
