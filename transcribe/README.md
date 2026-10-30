# 녹음 전사

아이폰 기본 받아쓰기보다 정확한 한국어 전사문을 무료로 만들어요.

| 파일 | 내용 |
|---|---|
| `transcribe_ko.ipynb` | Colab 노트북. 무료 T4 GPU에서 Whisper large-v3(faster-whisper)로 전사 → `_전사.txt`(문단·시각), `_자막.srt` |
| `run_cell.py` | 노트북 셀 코드 원본. 고친 뒤 `python3 build_notebook.py` 로 노트북을 다시 만들어요 |
| `summary.html` | [받아쓰기 요약](https://claude.ai/artifact/KWUbqdVfGP4Mwnnz7ryzgG) 페이지 원본. 전사문을 올리면 Claude가 요약·결정·할 일로 정리 |

폰에서 쓰기: 요약 페이지를 사파리에서 열고 **공유 → 홈 화면에 추가** (페이지 맨 아래에 안내 있음). 전사 결과는 구글 드라이브 `전사문` 폴더에도 저장돼서, 요약 페이지 파일 선택 창에서 바로 고를 수 있어요.

노트북: https://colab.research.google.com/github/sunghyunkim369-afk/claude/blob/claude/elegant-heisenberg-s2a9ar/transcribe/transcribe_ko.ipynb

정확도를 위해 하는 것: 한국어 고정, beam 5, 음성 구간만 인식(VAD), 앞 문장에 끌려가는 반복 막기, 작은 목소리 보정(ffmpeg), 자주 나오는 이름·용어 힌트, 조용한 구간의 Whisper 헛소리("시청해주셔서 감사합니다" 등)와 반복 줄 걸러내기.
