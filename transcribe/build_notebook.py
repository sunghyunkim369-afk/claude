"""run_cell.py 를 Colab 노트북(transcribe_ko.ipynb)으로 묶어요. 셀 코드를 고친 뒤 다시 실행하세요."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

INTRO = """# 🎙️ 한국어 녹음 전사 (무료 · Whisper large-v3)

아이폰 기본 받아쓰기보다 정확한 전사문을 만들어요. 구글 무료 GPU에서 돌아가서 비용이 없어요.

**처음 한 번만**
- 위 메뉴 **런타임 → 런타임 유형 변경 → T4 GPU** 선택 → 저장
- 사파리 아래 **공유(□↑) → 홈 화면에 추가** 하면 다음부터 아이콘으로 열려요

**쓰는 법**
1. 아이폰 음성 메모에서 녹음 → **공유 → '파일'에 저장**
2. 아래 셀 왼쪽 **▶** → 구글 드라이브 **허용** → **파일 선택**에서 녹음 고르기
3. 끝나면 `_전사.txt`(문단 + 시각)와 `_자막.srt`가 내려받아지고, 구글 드라이브 **전사문** 폴더에도 저장돼요
4. 요약이 필요하면 [받아쓰기 요약](https://claude.ai/artifact/KWUbqdVfGP4Mwnnz7ryzgG)에서 드라이브의 `_전사.txt`를 고르세요

**폰에서 쓸 때**: 도는 동안 화면이 꺼지면 연결이 끊길 수 있어요. 결과는 드라이브에 남지만, 긴 녹음이면 **설정 → 디스플레이 및 밝기 → 자동 잠금 → 안 함**으로 잠깐 바꿔 두세요.

걸리는 시간(T4 GPU, large-v3): 1시간 녹음 → 약 10~15분. 급하면 모델을 `large-v3-turbo`로 바꾸세요.
"""


def lines(text):
    parts = text.splitlines(keepends=True)
    return parts


def main():
    with open(os.path.join(HERE, "run_cell.py"), encoding="utf-8") as f:
        code = f.read().rstrip("\n")
    nb = {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "colab": {"provenance": [], "gpuType": "T4", "name": "transcribe_ko.ipynb"},
            "accelerator": "GPU",
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": lines(INTRO)},
            {
                "cell_type": "code",
                "metadata": {"cellView": "form"},
                "execution_count": None,
                "outputs": [],
                "source": lines(code),
            },
        ],
    }
    with open(os.path.join(HERE, "transcribe_ko.ipynb"), "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
