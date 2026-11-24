#@title ▶ 실행 — 녹음 파일 고르기 → 전사문 받기
#@markdown **모델**: `large-v3` 가장 정확 · `large-v3-turbo` 2~3배 빠르고 약간 덜 정확
MODEL = "large-v3"  #@param ["large-v3", "large-v3-turbo"]
#@markdown **자주 나오는 이름·용어** (쉼표로 구분). 기본값은 관세·물류 강의용이에요. 다른 주제면 바꾸거나 지우세요
KEYWORDS = "보세화물, 적재화물목록, 하우스B/L, B/L, AEO, 포워더, 관세청장, 법규수행도, 과태료, 매니페스트, 현대글로비스, 산식, 가중치"  #@param {type:"string"}
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
CUDA12_DIR = "/content/cuda12"
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".mp4", ".aac", ".flac", ".ogg", ".opus", ".qta", ".caf", ".mov", ".webm", ".amr", ".3gp"}

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
# 말하는 사람이 강조할 때 쓰는 말. 이 말이 든 줄에 ⭐를 붙여요.
EMPHASIS_RE = re.compile(
    r"중요(?:합니다|해요|하다|한|하고|하죠|하거든)|핵심|치명적이|심각(?:합니다|해요|하다|한)|중대(?:한|하다|합니다)"
    r"|반드시|꼭\s|시험에\s*(?:나|잘)|기억하세요|기억해|명심|주의(?:하세요|해야|하셔야|할)|조심|잊지\s*마|강조"
)


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


def split_keywords(keywords):
    return [w.strip() for w in re.split(r"[,，\n]", keywords) if w.strip()]


def clean_segments(segments, words=()):
    """Whisper 헛소리·반복을 걸러 (start, end, text) 목록으로 돌려줘요."""
    out = []
    for seg in segments:
        text = LOOP_RE.sub(r"\1", seg.text.strip())
        if not text:
            continue
        if len(text) < 40 and HALLUCINATION_RE.search(text):
            continue
        # 힌트 단어를 그대로 읊은 줄 (조용한 구간에서 힌트 목록이 튀어나옴)
        hits = [w for w in words if w in text]
        if len(hits) >= 3 and len(text) < 2 * sum(len(w) for w in hits):
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


def emphasized(rows):
    """강조하는 말이 든 줄을 (시작 시각, 문장) 목록으로 돌려줘요."""
    return [(start, text) for start, _, text in rows if EMPHASIS_RE.search(text)]


def write_outputs(base, rows, duration, model_name):
    paras = to_paragraphs(rows, PARAGRAPH_GAP)
    marks = emphasized(rows)
    long = duration >= 3600
    txt_path = os.path.join(WORK_DIR, f"{base}_전사.txt")
    srt_path = os.path.join(WORK_DIR, f"{base}_자막.srt")
    today = datetime.date.today().isoformat()
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"파일: {base}\n길이: {fmt_time(duration)}\n모델: {model_name} · {today}\n\n")
        if marks:
            f.write(f"⭐ 중요하다고 말한 부분 ({len(marks)}곳, '중요·치명적·반드시·시험에' 같은 말로 자동으로 찾았어요)\n")
            for start, text in marks:
                f.write(f"  [{fmt_time(start, hours=long)}] {text}\n")
            f.write("\n" + "─" * 30 + "\n\n")
        for p in paras:
            star = "⭐ " if EMPHASIS_RE.search(p["text"]) else ""
            f.write(f"{star}[{fmt_time(p['start'], hours=long)}] {p['text']}\n\n")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(rows, 1):
            f.write(f"{i}\n{fmt_time(start, True)} --> {fmt_time(end, True)}\n{text}\n\n")
    return txt_path, srt_path, paras, marks


def has_gpu():
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and "GPU" in r.stdout


def ensure_cuda12_libs():
    """faster-whisper(CTranslate2)는 CUDA 12용 cuBLAS·cuDNN을 찾아요.
    Colab이 CUDA 13으로 바뀌어 없으면 따로 깔고, 같은 이름으로 먼저 올려 둬서 그걸 쓰게 해요."""
    try:
        ctypes.CDLL("libcublas.so.12")
        ctypes.CDLL("libcudnn_ops.so.9")
        return
    except OSError:
        pass
    libs = sorted(glob.glob(os.path.join(CUDA12_DIR, "nvidia", "*", "lib", "*.so*")))
    if not any(os.path.basename(p) == "libcublas.so.12" for p in libs):
        print("GPU용 라이브러리(CUDA 12) 설치 중 (처음 한 번, 1~2분)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", CUDA12_DIR,
                        "nvidia-cublas-cu12", "nvidia-cudnn-cu12"], check=True)
        libs = sorted(glob.glob(os.path.join(CUDA12_DIR, "nvidia", "*", "lib", "*.so*")))
    # 서로 기대는 라이브러리가 있어서, 더 올라가지 않을 때까지 몇 번 돌려요.
    pending = [p for p in libs if "nvblas" not in p]
    for _ in range(5):
        left = []
        for so in pending:
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                left.append(so)
        if len(left) == len(pending):
            break
        pending = left


def prepare_audio(src, dst):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000"]
    if CLEAN_AUDIO:
        cmd += ["-af", "highpass=f=70,dynaudnorm=f=250:g=15:m=10"]
    subprocess.run(cmd + [dst], check=True)


def read_wav(path):
    # faster-whisper의 파일 읽기(PyAV)가 최신 PyAV와 안 맞아서, 16kHz wav를 직접 읽어 넘겨요.
    import wave
    import numpy as np
    with wave.open(path, "rb") as w:
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def load_model(gpu):
    import numpy as np
    from faster_whisper import WhisperModel
    if gpu:
        try:
            model = WhisperModel(MODEL, device="cuda", compute_type="float16")
            # 라이브러리 문제는 모델을 띄울 때가 아니라 처음 계산할 때 터져서, 1초짜리 무음으로 미리 시험해요.
            segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="ko", vad_filter=False)
            list(segments)
            return model, "GPU"
        except Exception as e:
            print(f"⚠️ GPU를 쓰지 못해 CPU로 바꿔요 (아주 느려요): {e}")
    return WhisperModel(MODEL, device="cpu", compute_type="int8"), "CPU"


def already_done(base, drive_dir):
    """전사문이 이미 있는 녹음. 런타임이 초기화돼 다시 올려도 드라이브에 있는 건 건너뛰어요."""
    folders = [WORK_DIR] + ([drive_dir] if drive_dir else [])
    return any(os.path.exists(os.path.join(d, f"{base}_전사.txt")) for d in folders)


def leftover_audio(drive_dir):
    """앞서 올렸는데 아직 전사문이 없는 녹음. 중간에 멈췄을 때 다시 올리지 않고 이어서 해요."""
    found = []
    for folder in ("/content", WORK_DIR):
        for path in sorted(glob.glob(os.path.join(folder, "*"))):
            base, ext = os.path.splitext(os.path.basename(path))
            if ext.lower() not in AUDIO_EXTS or base.endswith("_16k"):
                continue
            if not already_done(base, drive_dir):
                found.append(path)
    return found


def transcribe(model, wav, words):
    # initial_prompt는 쓰지 않아요: 힌트를 넣은 안내 문장을 녹음 첫머리에 그대로 적어 버리는 일이 있었어요.
    segments, info = model.transcribe(
        read_wav(wav),
        language="ko",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
        condition_on_previous_text=False,
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

    audio = leftover_audio(drive_dir)
    if audio:
        print(f"지난번에 올린 녹음 {len(audio)}개가 아직 남아 있어서 이어서 할게요:")
        for path in audio:
            print("  -", os.path.basename(path))
    else:
        print("녹음 파일을 골라 주세요 (m4a·mp3·wav·mp4 등, 여러 개 가능)")
        audio = [os.path.abspath(name) for name in files.upload()]
        if not audio:
            print("고른 파일이 없어요. 다시 ▶ 눌러 주세요.")
            return
        done = [p for p in audio if already_done(os.path.splitext(os.path.basename(p))[0], drive_dir)]
        for path in done:
            print(f"  건너뜀: {os.path.basename(path)} (드라이브 '전사문' 폴더에 전사문이 이미 있어요)")
        audio = [p for p in audio if p not in done]
        if not audio:
            print("올린 녹음은 모두 전사가 끝나 있어요.")
            return

    if gpu:
        ensure_cuda12_libs()
    print(f"\n{MODEL} 모델 준비 중 (처음엔 3GB 내려받느라 1~2분)...")
    model, device = load_model(gpu)
    print(f"준비 완료 ({device})\n")

    for path in audio:
        name = os.path.basename(path)
        base = os.path.splitext(name)[0]
        src = os.path.join(WORK_DIR, name)
        if path != src:
            os.replace(path, src)
        wav = os.path.join(WORK_DIR, base + "_16k.wav")
        print(f"▶ {name}")
        try:
            prepare_audio(src, wav)
        except subprocess.CalledProcessError:
            print("  이 파일은 소리를 못 읽었어요. 녹음 파일이 맞는지 확인해 주세요.\n")
            continue
        words = split_keywords(KEYWORDS)
        raw, duration = transcribe(model, wav, words)
        rows = clean_segments(raw, words)
        txt_path, srt_path, paras, marks = write_outputs(base, rows, duration, MODEL)
        print(f"  완료: {len(paras)}문단 · 걸러낸 헛소리/반복 {len(raw) - len(rows)}줄 · ⭐ 강조 {len(marks)}곳\n")
        if marks:
            print("⭐ 중요하다고 말한 부분")
            for start, text in marks:
                print(f"  [{fmt_time(start, hours=duration >= 3600)}] {text}")
        print("─" * 40)
        for p in paras:
            star = "⭐ " if EMPHASIS_RE.search(p["text"]) else ""
            print(f"{star}[{fmt_time(p['start'], hours=duration >= 3600)}] {p['text']}\n")
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
