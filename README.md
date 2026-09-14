# 릴스 대본 추출기

릴스/쇼츠 링크를 붙여넣으면 (파일 다운로드 없이) 서버가 알아서 영상을 가져와서 음성을 텍스트로 변환해주는 웹앱.

- 영상 다운로드: [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — 인스타그램, 유튜브 쇼츠 등 링크 지원
- 오디오 추출: `ffmpeg`
- 음성 인식(STT): [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) — 오픈소스, 무료, 서버 CPU에서 실행
- 백엔드: FastAPI
- 프론트: 순수 HTML/JS (별도 프레임워크 없음)

## 1. 로컬에서 먼저 테스트해보기

```bash
# ffmpeg 설치 (mac)
brew install ffmpeg

# 파이썬 패키지 설치
pip install -r requirements.txt

# 서버 실행
uvicorn main:app --reload
```

브라우저에서 `http://localhost:8000` 접속 → 릴스 링크 붙여넣고 테스트.

처음 실행할 때 whisper 모델(small, 약 500MB)을 자동으로 다운로드해요. 시간이 좀 걸릴 수 있어요.

## 2. 인터넷에 배포하기 (추천: Railway)

혼자 쓰지만 어디서든 접속하고 싶다고 하셨으니, 이 방식이 제일 간단해요.

### Railway 사용 (추천 — Dockerfile 자동 인식, 카드 등록 후 매달 소액)

1. https://railway.app 가입 (GitHub 계정으로 로그인 가능)
2. "New Project" → "Deploy from GitHub repo" 선택
   - 이 폴더를 GitHub 레포로 먼저 올려두세요 (`git init` → `git add .` → `git commit` → GitHub에 push)
3. Railway가 `Dockerfile`을 자동으로 인식해서 빌드/배포함
4. 배포되면 `xxx.up.railway.app` 같은 주소가 생성됨 → 그 주소로 어디서든 접속 가능

**비용**: Railway는 사용한 만큼 과금(사용량 기반)이라, 개인이 가끔 쓰는 정도면 한 달 몇 천원 수준일 가능성이 높아요. 무료 크레딧도 제공하니 처음엔 체감 비용 거의 없을 거예요.

### 대안: Render.com

- 무료 티어 있음 (단, 15분 미사용 시 슬립 → 다시 켤 때 첫 요청이 느림)
- GitHub 레포 연결 → "New Web Service" → Dockerfile 자동 감지
- 개인 용도로 가끔 쓰는 거면 무료 티어로도 충분

## 3. 커스터마이징 포인트

- **모델 크기 조정**: `Dockerfile`의 `WHISPER_MODEL_SIZE` 값을 바꾸면 속도/정확도 조절 가능
  - `tiny` / `base` → 빠르지만 정확도 낮음
  - `small` (기본값) → 균형
  - `medium` → 더 정확하지만 CPU에서 꽤 느림 (무료 서버라면 비추천)
- **문단 나누는 기준**: `main.py`의 `PARAGRAPH_GAP_SECONDS` (기본 1.2초) — 말 사이 침묵이 이 값보다 길면 새 문단으로 나눔
- **업로드 용량 제한**: `MAX_UPLOAD_MB` (기본 300MB)

## 4. 주의사항

- 무료 CPU 기반이라 영상이 길수록(예: 10분 이상) 처리 시간이 꽤 걸려요. 릴스/쇼츠(1분 내외)는 보통 수십 초~1분 내로 끝나요.
- Railway/Render 무료·저가 플랜은 메모리 제한이 있어서, `small` 모델 기준으로는 문제없지만 `medium` 이상은 메모리 부족으로 죽을 수 있어요.
- **비공개 계정/비공개 게시물** 링크는 로그인이 필요해서 yt-dlp가 못 가져올 수 있어요. 공개 릴스 위주로 사용하세요.
- 인스타그램이 자동화된 요청을 감지하면 일시적으로 다운로드가 막힐 수 있어요 (너무 짧은 시간에 반복 요청하지 않는 게 좋아요). `yt-dlp`는 계속 업데이트되는 도구라, 안 될 경우 `pip install -U yt-dlp`로 최신 버전으로 올려보세요.
- 저작권이 있는 타인의 릴스를 대량으로 대본화해서 재배포하는 용도가 아니라, 본인 콘텐츠 벤치마킹/스크립트 참고용으로 쓰시는 걸 권장해요.

## 5. 서버 없이 무료로 계속 쓰기

### 데스크톱 앱 (Windows/Mac)

`desktop/` 폴더에 서버 없이 내 컴퓨터에서 바로 돌아가는 버전이 있어요. `.github/workflows/build-desktop.yml`이
GitHub Actions에서 Windows(.exe)/Mac 빌드를 자동으로 만들어 저장소의 Actions 탭 → 최신 실행 → Artifacts에서
내려받을 수 있어요. 직접 빌드하려면:

```bash
cd desktop
pip install -r requirements.txt
python desktop_app.py          # 실행만 해보기
pyinstaller --name "reels-transcript-extractor" --onedir --windowed --collect-submodules numpy --collect-data faster_whisper desktop_app.py   # 폴더+exe로 패키징
```

### 모바일 (PWA)

`static/`에 manifest.json / service-worker.js를 추가해서, 서버(Render 등)에 배포된 주소를 모바일 브라우저로 열고
"홈 화면에 추가"하면 앱처럼 아이콘이 생기고 실행돼요. (음성 인식 자체는 서버에서 처리하므로 서버는 계속 떠 있어야 함)
