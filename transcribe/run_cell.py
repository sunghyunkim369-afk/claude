#@title ▶ 실행 — 녹음 파일 고르기 → 전사문 받기
#@markdown **모델**: `large-v3` 가장 정확 · `large-v3-turbo` 2~3배 빠르고 약간 덜 정확
MODEL = "large-v3"  #@param ["large-v3", "large-v3-turbo"]
#@markdown **자주 나오는 이름·용어** (쉼표로 구분, 비워도 돼요). 예: `김성현, 트레이드컴퍼스, HS코드`
KEYWORDS = ""  #@param {type:"string"}
#@markdown **음질 보정**: 작은 목소리 키우기, 웅웅거리는 저음 줄이기
CLEAN_AUDIO = True  #@param {type:"boolean"}
#@markdown **문단 나누기**: 이 초 이상 말이 끊기면 새 문단
PARAGRAPH_GAP = 2.0  #@param {type:"number"}
#@markdown **구글 드라이브 '전사문' 폴더에도 저장**: 폰 화면이 꺼져 연결이 끊겨도 결과가 남고, 요약 페이지에서 바로 골라 쓸 수 있어요
SAVE_TO_DRIVE = True  #@param {type:"boolean"}

import ctypes
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
import time

SUMMARY_PAGE = "https://claude.ai/artifact/KWUbqdVfGP4Mwnnz7ryzgG"
WORK_DIR = "/content/transcribe"
DRIVE_DIR = "/content/drive/MyDrive/전사문"

# 유튜브 자막에서 배운 Whisper 단골 헛소리. 녹음에 없는데 조용한 구간에서 튀어나와요.
HALLUCINATIONS = [
    r"시청\s*(해|하여)?\s*주셔서\s*감사",
    r"구독\s*(과|,)?\s*좋아요",
    r"좋아요\s*(와|,)?\s*구독",
    r"알림\s*설정",
    r"다음\s*(영상|시간)에서\s*(만나요|뵙겠습니다)",
    r"끝까지\s*시청",
    r"자막\s*(제공|제작|by)",
    r"(MBC|KBS|SBS|YTN)\s*뉴스\s*\S+입니다",
    r"한글\s*자막",
]
HALLUCINATION_RE = re.compile("|".join(HALLUCINATIONS), re.IGNORECASE)
LOOP_RE = re.compile(r"(\S.{0,19}?)(?:\s*\1){3,}")


def fmt_time(sec, srt=False, hours=False):
    sec = max(0.0, sec)
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if srt:
        ms = int(round((sec - int(sec)) * 1000))
        if ms == 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{h}:{m:02d}:{s:02d}" if h or hours else f"{m:02d}:{s:02d}"


def clean_segments(segments):
    """Whisper 헛소리·반복을 걸러 (start, end, text) 목록으로 돌려줘요."""
    out = []
    for seg in segments:
        text = LOOP_RE.sub(r"\1", seg.text.strip())
        if not text:
            continue
        if len(text) < 40 and HALLUCINATION_RE.search(text):
            continue
        if out and len(text) >= 6 and text == out[-1][2]:
            continue
        out.append((seg.start, seg.end, text))
    return out


def to_paragraphs(rows, gap):
    paras = []
    for start, end, text in rows:
        if paras:
            last = paras[-1]
            long_enough = len(last["text"]) > 400 and last["text"][-1:] in ".?!"
            if start - last["end"] < gap and not long_enough:
                last["text"] += " " + text
                last["end"] = end
                continue
        paras.append({"start": start, "end": end, "text": text})
    return paras


def write_outputs(base, rows, duration, model_name):
    paras = to_paragraphs(rows, PARAGRAPH_GAP)
    txt_path = os.path.join(WORK_DIR, f"{base}_전사.txt")
    srt_path = os.path.join(WORK_DIR, f"{base}_자막.srt")
    today = datetime.date.today().isoformat()
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"파일: {base}\n길이: {fmt_time(duration)}\n모델: {model_name} · {today}\n\n")
        for p in paras:
            f.write(f"[{fmt_time(p['start'], hours=duration >= 3600)}] {p['text']}\n\n")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(rows, 1):
            f.write(f"{i}\n{fmt_time(start, True)} --> {fmt_time(end, True)}\n{text}\n\n")
    return txt_path, srt_path, paras


def has_gpu():
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and "GPU" in r.stdout


def preload_cudnn():
    # faster-whisper(CTranslate2)가 Colab의 cuDNN을 못 찾는 경우가 있어 미리 올려 둬요.
    try:
        import torch
        if torch.cuda.is_available():
            torch.backends.cudnn.version()
    except Exception:
        pass
    for lib_dir in glob.glob("/usr/local/lib/python3*/dist-packages/nvidia/cudnn/lib"):
        for so in sorted(glob.glob(os.path.join(lib_dir, "libcudnn*.so*"))):
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


def prepare_audio(src, dst):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000"]
    if CLEAN_AUDIO:
        cmd += ["-af", "highpass=f=70,dynaudnorm=f=250:g=15:m=10"]
    subprocess.run(cmd + [dst], check=True)


def load_model(gpu):
    from faster_whisper import WhisperModel
    if gpu:
        try:
            return WhisperModel(MODEL, device="cuda", compute_type="float16"), "GPU"
        except Exception as e:
            print(f"GPU로 모델을 못 띄워서 CPU로 바꿔요: {e}")
    return WhisperModel(MODEL, device="cpu", compute_type="int8"), "CPU"


def transcribe(model, wav, keywords):
    words = [w.strip() for w in re.split(r"[,，\n]", keywords) if w.strip()]
    prompt = "다음은 한국어 대화를 받아 적은 글입니다. 문장 부호를 정확히 씁니다."
    if words:
        prompt += " 등장하는 말: " + ", ".join(words) + "."
    segments, info = model.transcribe(
        wav,
        language="ko",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
        condition_on_previous_text=False,
        initial_prompt=prompt,
        hotwords=", ".join(words) or None,
    )
    raw, next_mark, started = [], 0.05, time.time()
    for seg in segments:
        raw.append(seg)
        done = seg.end / info.duration if info.duration else 1
        if done >= next_mark:
            print(f"  {done:4.0%}  {fmt_time(seg.end)} / {fmt_time(info.duration)}  ({time.time() - started:.0f}초 걸림)")
            next_mark = done + 0.05
    return raw, info.duration


def mount_drive():
    from google.colab import drive
    print("구글 드라이브 연결: 뜨는 창에서 계정을 고르고 '허용'을 눌러 주세요.")
    try:
        drive.mount("/content/drive")
        os.makedirs(DRIVE_DIR, exist_ok=True)
        return DRIVE_DIR
    except Exception as e:
        print(f"드라이브에 연결하지 못했어요 ({e}). 내려받기로만 저장할게요.\n")
        return None


def main():
    from google.colab import files

    os.makedirs(WORK_DIR, exist_ok=True)
    gpu = has_gpu()
    if not gpu:
        print("⚠️ GPU가 꺼져 있어요. 위 메뉴 [런타임 → 런타임 유형 변경 → T4 GPU]를 고르고 다시 ▶ 누르면 10배 이상 빨라요.")
        print("   지금은 CPU로 해요 (1시간 녹음이면 1시간 넘게 걸릴 수 있어요).\n")

    drive_dir = mount_drive() if SAVE_TO_DRIVE else None

    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("프로그램 설치 중 (처음 한 번, 1분쯤)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "faster-whisper"], check=True)

    print("녹음 파일을 골라 주세요 (m4a·mp3·wav·mp4 등, 여러 개 가능)")
    uploaded = files.upload()
    if not uploaded:
        print("고른 파일이 없어요. 다시 ▶ 눌러 주세요.")
        return

    if gpu:
        preload_cudnn()
    print(f"\n{MODEL} 모델 준비 중 (처음엔 3GB 내려받느라 1~2분)...")
    model, device = load_model(gpu)
    print(f"준비 완료 ({device})\n")

    for name in uploaded:
        base = os.path.splitext(os.path.basename(name))[0]
        src = os.path.join(WORK_DIR, os.path.basename(name))
        os.replace(name, src)
        wav = os.path.join(WORK_DIR, base + "_16k.wav")
        print(f"▶ {name}")
        try:
            prepare_audio(src, wav)
        except subprocess.CalledProcessError:
            print("  이 파일은 소리를 못 읽었어요. 녹음 파일이 맞는지 확인해 주세요.\n")
            continue
        raw, duration = transcribe(model, wav, KEYWORDS)
        rows = clean_segments(raw)
        txt_path, srt_path, paras = write_outputs(base, rows, duration, MODEL)
        print(f"  완료: {len(paras)}문단 · 걸러낸 헛소리/반복 {len(raw) - len(rows)}줄\n")
        print("─" * 40)
        for p in paras:
            print(f"[{fmt_time(p['start'], hours=duration >= 3600)}] {p['text']}\n")
        print("─" * 40)
        if drive_dir:
            for path in (txt_path, srt_path):
                shutil.copy(path, drive_dir)
            print(f"  드라이브 저장: 내 드라이브/전사문/{os.path.basename(txt_path)}")
        try:
            files.download(txt_path)
            files.download(srt_path)
        except Exception:
            print("  내려받기 창을 못 띄웠어요. 위 글을 복사하거나 드라이브에서 열어 주세요.")

    print("\n✅ 끝. 전사문(_전사.txt)을 내려받았어요." + (" 구글 드라이브 '전사문' 폴더에도 있어요." if drive_dir else ""))
    print(f"요약이 필요하면 이 페이지에 전사문 파일을 올리세요: {SUMMARY_PAGE}")


main()
