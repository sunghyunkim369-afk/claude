# 글꼴 직접 제공용 파일 만들기 (GitHub Actions 의 fonts-build 작업에서 실행)
# - Google Fonts 원본 저장소(github.com/google/fonts)의 OFL 글꼴을 받아, 사이트에 쓰는 글자만 남겨 woff2 로 줄여요.
# - Noto Sans KR: 굵기 400·500·700 고정본. KS X 1001 한글 2,350자 + 사이트 파일·기사 제목에 나오는 글자(한자 포함)
# - Fraunces: 영문 제목용 가변 글꼴(굵기 400~700, opsz), 보통·기울임 2개. 라틴 문자만
# 실행: python scripts/fonts/build.py <원본 폴더> <출력 폴더>
import sys, os, json, glob, io
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools import subset

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def site_chars():
    chars = set()
    files = glob.glob(os.path.join(ROOT, "tradecompass-mvp", "*.js")) + glob.glob(os.path.join(ROOT, "tradecompass-mvp", "*.html")) \
          + glob.glob(os.path.join(ROOT, "hs-code-finder", "*.html")) + glob.glob(os.path.join(ROOT, "shared", "*.js"))
    for f in files:
        chars |= set(open(f, encoding="utf-8", errors="ignore").read())
    try:
        for it in json.load(open(os.path.join(ROOT, "data", "news", "archive.json"), encoding="utf-8"))["items"]:
            chars |= set(it.get("title", ""))
    except Exception as e:
        print("archive 읽기 건너뜀:", e)
    return chars

ks = set()
for cp in range(0xAC00, 0xD7A4):
    try: chr(cp).encode("euc-kr"); ks.add(chr(cp))
    except UnicodeEncodeError: pass
base = set(chr(c) for c in list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + list(range(0x2000, 0x2070))
           + list(range(0x2190, 0x2200)) + list(range(0x25A0, 0x2600)) + [0x2605, 0x2606, 0x2713, 0x2715, 0x20A9]
           + list(range(0x3000, 0x3040)) + list(range(0x3131, 0x318F)) + list(range(0xFF01, 0xFF5F)))
kr_chars = ks | base | site_chars()
print("Noto Sans KR 글자 수:", len(kr_chars), "(KS X 1001 한글", len(ks), ")")

def reload(font):
    # 가변 글꼴을 일부만 고정(instancer)한 뒤 바로 subset 하면 gvar 를 늦게 읽다가 KeyError 가 나요 → 한 번 저장했다가 다시 열어요
    buf = io.BytesIO(); font.save(buf); buf.seek(0)
    return TTFont(buf, lazy=False)

def save(font, text, path, keep_layout=True):
    font = reload(font)
    opts = subset.Options(); opts.flavor = "woff2"; opts.layout_features = ["*"] if keep_layout else []
    opts.name_IDs = ["*"]; opts.notdef_outline = True
    s = subset.Subsetter(opts); s.populate(text="".join(sorted(text))); s.subset(font)
    font.flavor = "woff2"; font.save(path)
    print(path, os.path.getsize(path) // 1024, "KB")

noto = os.path.join(src, "NotoSansKR[wght].ttf")
for w in (400, 500, 700):
    f = instancer.instantiateVariableFont(TTFont(noto), {"wght": w})
    save(f, kr_chars, os.path.join(out, f"NotoSansKR-{w}.woff2"))

latin = set(chr(c) for c in list(range(0x20, 0x7F)) + list(range(0xA0, 0x180)) + list(range(0x2000, 0x2070)) + [0x20A9])
for name, file in (("Fraunces", "Fraunces[SOFT,WONK,opsz,wght].ttf"), ("Fraunces-Italic", "Fraunces-Italic[SOFT,WONK,opsz,wght].ttf")):
    f = instancer.instantiateVariableFont(TTFont(os.path.join(src, file)), {"SOFT": 0, "WONK": 0, "wght": (400, 700)})
    save(f, latin, os.path.join(out, f"{name}.woff2"))
