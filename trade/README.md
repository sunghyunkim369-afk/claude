# 노트북 없이 대회 프로그램 돌리기 (GitHub Actions)

`trade/request.txt` 첫 단어를 바꿔서 이 브랜치에 올리면 GitHub 이 프로그램을 한 번 돌려요.
Claude 에게 "리밸런스" 라고 하면 Claude 가 올려요.

| 첫 단어 | 하는 일 |
|---|---|
| `now` | 지금 리밸런스 (순위 다시 계산, 교체, 1/3 맞추기) |
| `once` | 예약 실행과 같음 (리밸런스 날이면 리밸런스, 아니면 급락 확인만) |
| `journal` | 매매일지만 |

장중(09:05~15:20)에만 주문이 나가요. 결과는 `trade/last_run.txt`, 일지는 `journal/` 에 올라와요.

## 처음 한 번만: 비밀값 넣기
GitHub 저장소 → Settings → Secrets and variables → Actions → New repository secret 으로 3개:
`KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT` (노트북 trading/.env 와 같은 모의투자 값). 대화창에는 붙이지 마세요.

## 주의
- 노트북 예약 실행과 같이 쓰면 주문이 겹칠 수 있어요. 이걸 쓰는 동안 노트북은 `python -m trading unschedule`.
- 진행 상태는 `trade/state/contest-demo.json` 에 있어요. 다시 노트북으로 돌아가면 이 파일을 `trading/.cache/` 에 복사하세요.
