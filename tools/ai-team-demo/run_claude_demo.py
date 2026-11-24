# AI 무역팀 'Claude만' 모드 실측: USB-C 65W 충전기 모듈 미국 OEM 사례(가상). 회의 2번 + 관세사에게 묻기 + 바이어 메일.
# 쓰는 법: TC_ROOT=/home/user/tradecompass python3 run_claude_demo.py out.json   (ANTHROPIC_API_KEY 환경변수 필요, 값은 출력하지 않음)
import json, os, secrets, sys, time
ROOT = os.environ.get("TC_ROOT", "/home/user/tradecompass")
sys.path.insert(0, ROOT + "/backend"); sys.path.insert(0, ROOT + "/backend/tests")
os.environ.setdefault("JWT_SECRET_KEY", "local-dev-only-" + "x" * 40)
os.environ["COOKIE_SECURE"] = "false"
os.environ["TEAM_DATA_KEY"] = secrets.token_urlsafe(40)
os.environ["TEAM_CLAUDE_MODEL"] = os.environ.get("TEAM_CLAUDE_MODEL") or "claude-opus-5-5,claude-sonnet-5-5"
assert os.environ.get("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY 환경변수가 필요합니다"
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from summary_support import SessionDb
from app.bootstrap import SEED_DIR, run_startup_init
from app.main import create_app

OUT = sys.argv[1]
import tempfile; engine = create_engine("sqlite+pysqlite:///" + tempfile.mktemp(suffix=".db", dir=os.environ.get("TC_TMP") or None), connect_args={"check_same_thread": False, "timeout": 30})
@event.listens_for(engine, "connect")
def fk(c, _): c.execute("PRAGMA foreign_keys=ON")
run_startup_init(engine, SEED_DIR)
app = create_app(db=SessionDb(engine), validate_env=False, static_dir=None, password_hasher=PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
c = TestClient(app)
c.post("/api/auth/register", json={"email": "demo@example.test", "password": "Passw0rd!", "agree": True})
h = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": "demo@example.test", "password": "Passw0rd!"}).json()["access_token"]}
SECRETS = ["브라이트볼트", "BrightVolt", "Miller", "brightvolt", "USD 6.90", "4.10달러", "555-0188"]
p = c.post("/api/team/projects", headers=h, json={
    "title": "USB-C 65W 충전기 모듈 첫 미국 OEM 공급", "security": "normal", "ai_mode": "claude",
    "charter": {"goal": "미국 소형 가전 브랜드에 USB-C 65W GaN 충전기 모듈 5,000개를 OEM으로 첫 공급해 2027년 3월 15일까지 LA 창고에 도착시킨다",
                "deadline": "2027-03-15",
                "constraints": ["첫 거래는 대금 회수 수단을 확보한다", "인증 비용은 2,000만 원 안", "미국 판매 중 리콜·통관 보류가 없게 한다"],
                "item": "USB-C PD 65W GaN 충전기 모듈(케이스 없는 PCB 어셈블리, HS 8504.40 예상·확인 필요)", "market": "미국(로스앤젤레스)",
                "terms": {"qty": "5,000개", "unit_price": "개당 USD 6.90", "total": "USD 34,500", "incoterms": "바이어 제안 FOB 부산",
                          "payment": "바이어 제안: 30% 선금, 70% 선적 후 30일", "ship_from": "부산", "ship_to": "LA 창고",
                          "hs": "8504.40 예상(확인 필요)", "ship_by": "2027-03-15까지 도착", "other": "바이어가 최종 제품에 넣어 아마존에서 판매"}},
    "masks": [{"term": "브라이트볼트", "label": "바이어"}, {"term": "BrightVolt", "label": "바이어(영)"}, {"term": "Miller", "label": "바이어담당(영)"},
              {"term": "개당 USD 6.90", "label": "단가"}, {"term": "USD 6.90", "label": "단가(영)"}, {"term": "4.10달러", "label": "원가"}]}).json()["project"]
pid = p["id"]
c.post("/api/team/knowledge", headers=h, json={"kind": "company", "text": "우리 회사는 직원 25명의 전원 모듈 제조사이고, 국내 KC 안전·EMC 인증 경험이 있다"})
c.post("/api/team/knowledge", headers=h, json={"kind": "product", "text": "GaN 전력 소자와 PD 컨트롤러 IC는 대만·미국산, 변압기와 PCB는 중국에서 조달해 한국 공장에서 실장·검사한다"})
for kind, text, extra in [
    ("fact", "미국 브랜드 브라이트볼트가 샘플 20개 평가 뒤 첫 OEM 주문을 제안했다", {}),
    ("fact", "모듈은 바이어의 최종 제품(멀티 충전 스테이션) 안에 들어간다", {}),
    ("fact", "원가는 개당 4.10달러", {"private": True}),
    ("issue", "미국 안전 인증(UL 등)과 FCC를 누가 받는지 정하지 않았다", {}),
]:
    c.post(f"/api/team/projects/{pid}/ledger", headers=h, json={"kind": kind, "text": text, **extra})


def relogin():  # 회의가 길어져 로그인 토큰(30분)이 끝나면 다시 받는다
    h["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"email": "demo@example.test", "password": "Passw0rd!"}).json()["access_token"]


def get_json(path):
    r = c.get(path, headers=h)
    if r.status_code == 401:
        relogin()
        r = c.get(path, headers=h)
    return r.json()


def meet(topic, want="", materials="", project=None):
    t0 = time.time()
    body = {"topic": topic, **({"want": want} if want else {}), **({"materials": materials} if materials else {})}
    mid = c.post(f"/api/team/projects/{project or pid}/meetings", headers=h, json=body).json()["meeting"]["id"]
    seen, resumed = 0, 0
    while True:
        time.sleep(8)
        m = get_json(f"/api/team/meetings/{mid}")["meeting"]
        for x in m["progress"][seen:]:
            print("CALL", x["step"], x["role"], x["model"], "ok" if x["ok"] else "FAIL", x["error"], round(x["ms"] / 1000), "s", flush=True)
        seen = len(m["progress"])
        if m["status"] == "done":
            break
        if m["status"] == "failed":
            print("FAILED", m["error"], flush=True)
            if resumed >= 3:
                break
            resumed += 1
            time.sleep(60)
            print("RESUME", c.post(f"/api/team/meetings/{mid}/resume", headers=h).status_code, flush=True)
    print("MEETING_DONE", mid, int(time.time() - t0), "s", flush=True)
    return mid


def ask(mid, role, q):
    c.post(f"/api/team/meetings/{mid}/ask", headers=h, json={"question": q, "role": role})
    while True:
        time.sleep(4)
        items = get_json(f"/api/team/meetings/{mid}")["meeting"]["qa"]
        if items[-1]["status"] in ("done", "failed"):
            print("ANSWER", role, items[-1]["status"], flush=True)
            return


m1 = meet("브라이트볼트 밀러 씨가 샘플 평가가 좋았다고 5,000개 OEM 주문을 하고 싶대요. 근데 모듈 단위로 UL 인증이랑 FCC를 우리가 받아 오래요. "
          "우리는 미국 인증이 처음이고 비용이 얼마나 들지 몰라요. 그리고 변압기랑 PCB가 중국산인데 미국 관세 문제가 생기는지도 걱정돼요. "
          "결제는 30% 선금, 나머지는 선적 후 30일이래요. 3월 중순까지 LA에 도착해야 한대요.",
          want="인증을 누가 어떻게 받을지, 원산지·관세 위험, 조건 수락 여부",
          materials="From: K. Miller <k.miller@brightvolt.example>\nSubject: OEM order - 65W USB-C module\n"
                    "Hi, evaluation passed. We'd like 5,000 pcs at USD 6.90/pc FOB Busan.\n"
                    "Requirements: module-level UL 62368-1 certification (or recognized component), FCC Part 15 test report, country of origin marking.\n"
                    "Payment: 30% deposit, 70% net 30 after B/L. Need goods at our LA warehouse by March 15, 2027.\n"
                    "Thanks, Kevin Miller (+1 310-555-0188)")
mt1 = get_json(f"/api/team/meetings/{m1}?sent=1")["meeting"]
relogin()
for e in mt1["proposals"]:
    c.post(f"/api/team/ledger/{e['id']}/review", headers=h, json={"approve": True})
for k in c.get("/api/team/knowledge", headers=h).json()["knowledge"]:
    if k["status"] == "pending":
        c.post(f"/api/team/knowledge/{k['id']}/review", headers=h, json={"approve": True})
m2 = meet("시험소에 물어보니 UL 인정 부품(Recognized Component) 받는 데 10~12주, 비용 1,800만 원이래요. 밀러 씨는 최종 제품 인증은 자기들이 하니까 "
          "우리는 모듈만 인정받으면 된대요. 일정이 3월 중순 도착에 빠듯한데 어떻게 하죠?", want="인증 일정과 선적 계획")
relogin()
ask(m2, "broker", "중국산 변압기·PCB가 들어가도 원산지를 한국으로 표시할 수 있어요? 미국 관세에 영향이 있어요?")
d = c.post(f"/api/team/projects/{pid}/documents", headers=h, json={"kind": "buyer_mail", "meeting_id": m2, "notes": "2차 회의 결론대로 일정과 인증 계획을 회신"})
did = d.json()["document"]["id"]
while True:
    time.sleep(5)
    doc = get_json(f"/api/team/documents/{did}?sent=1")["document"]
    if doc["status"] in ("done", "failed"):
        break
mt2 = get_json(f"/api/team/meetings/{m2}?sent=1")["meeting"]
know = c.get("/api/team/knowledge", headers=h).json()["knowledge"]
texts = [s["text"] for mt in (mt1, mt2) for s in mt.get("sent", [])] + [q.get("sent", "") for q in mt2["qa"]] + [s["text"] for s in doc.get("sent", [])]
leak = [w for w in SECRETS if any(w in t for t in texts)]
json.dump({"m1": mt1, "m2": mt2, "doc": doc, "knowledge": know, "leak": leak}, open(OUT, "w"), ensure_ascii=False, indent=1)
print("DEMO_DONE leak=", leak, flush=True)


# 사람이 읽는 요약(out.json 옆에 .md로)
def _summary(d):
    L = []
    for label, mt in (("1차", d["m1"]), ("2차", d["m2"])):
        mi = mt.get("minutes") or {}
        a = mi.get("agenda") or {}
        dec = mi.get("decision") or {}
        L += [f"## {label} 회의: {a.get('title', '')}", f"- 정할 것: {', '.join(a.get('decide', []))}",
              f"- 참석: {', '.join(x['role'] + '(' + x['seat'] + ')' for x in mi.get('attendees', []))}"]
        for o in mi.get("options", []):
            L.append(f"- 안 {o['option']} {o['title']}: " + " / ".join(f"{r['role'][:6]} {r['round1']}→{r['final']}{'·거부권' if r['veto'] else ''}" for r in o["roles"]))
        L += [f"- 결론: {dec.get('option')} {dec.get('title', '')} — {dec.get('summary', '')}"]
        L += [f"  - 반대·조건({x['role']}): {x['point']}" for x in dec.get("dissent", []) if x.get("on_pick", True)]
        L += [f"  - 사람이 정할 것: {h['question']}" for h in dec.get("human_decisions", [])]
        L += [f"  - 할 일: {x['task']} ({x['owner']}, {x['due'] or '기한 미정'})" for x in dec.get("actions", [])]
        L += [f"- 점검: {f}" for f in dec.get("flags", [])] + [f"- 지표: {mi.get('metrics')}", ""]
    for q in d["m2"].get("qa", []):
        L += [f"## 묻기({q['role_name']}): {q['question']}", q.get("answer", ""), ""]
    L += [f"## 바이어 메일: {d['doc'].get('title', '')}", "```", d["doc"].get("body", ""), "```", f"- 점검: {d['doc'].get('checks')}", f"- 유출 점검: {d['leak'] or '없음'}"]
    return "\n".join(L)


open(OUT.rsplit(".", 1)[0] + ".md", "w").write(_summary(json.load(open(OUT))))
print("SUMMARY", OUT.rsplit(".", 1)[0] + ".md", flush=True)
