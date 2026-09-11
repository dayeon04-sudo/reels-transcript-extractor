"""
릴스 대본 추출 웹앱 - FastAPI 백엔드

흐름:
1. 사용자가 릴스/영상 링크(URL)를 입력
2. yt-dlp로 해당 링크의 영상을 서버에 다운로드
3. ffmpeg로 오디오만 추출 (wav, 16kHz mono - whisper 권장 포맷)
4. faster-whisper로 음성 -> 텍스트 변환 (한국어)
5. 문단 단위로 정리해서 JSON으로 반환
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
# 모델 크기: tiny < base < small < medium < large-v3
# CPU에서 무료로 돌릴 거면 base~small을 추천 (속도/정확도 균형)
# 환경변수로 바꿀 수 있게 해둠 (Railway/Render 설정에서 조정 가능)
MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")  # int8이 CPU에서 제일 빠름

# 문단을 나누는 기준: 이 초 이상 침묵(간격)이 있으면 새 문단으로 취급
PARAGRAPH_GAP_SECONDS = float(os.environ.get("PARAGRAPH_GAP_SECONDS", "1.2"))

MAX_DOWNLOAD_MB = int(os.environ.get("MAX_DOWNLOAD_MB", "300"))  # 다운로드 용량 제한
DOWNLOAD_TIMEOUT_SEC = int(os.environ.get("DOWNLOAD_TIMEOUT_SEC", "120"))

app = FastAPI(title="릴스 대본 추출기")

print(f"[startup] Whisper 모델 로딩 중... size={MODEL_SIZE} device={DEVICE}")
_model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print("[startup] 모델 로딩 완료")


def download_video(url: str, out_path_no_ext: str) -> str:
    """yt-dlp로 URL의 영상을 다운로드. 실제로 저장된 파일 경로를 반환."""
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[filesize<{0}M]+bestaudio/best[filesize<{0}M]/best".format(MAX_DOWNLOAD_MB),
        "--max-filesize", f"{MAX_DOWNLOAD_MB}M",
        "--no-playlist",
        "-o", f"{out_path_no_ext}.%(ext)s",
        url,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT_SEC
    )
    if result.returncode != 0:
        raise RuntimeError(f"영상 다운로드 실패: {result.stderr[-800:]}")

    # yt-dlp가 실제로 저장한 파일 찾기 (확장자를 알 수 없으므로)
    parent = Path(out_path_no_ext).parent
    stem = Path(out_path_no_ext).name
    matches = list(parent.glob(f"{stem}.*"))
    if not matches:
        raise RuntimeError("다운로드된 파일을 찾을 수 없습니다.")
    return str(matches[0])


def extract_audio(video_path: str, audio_path: str) -> None:
    """ffmpeg로 영상에서 오디오만 뽑아 16kHz mono wav로 저장"""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn",                 # 비디오 스트림 제외
        "-ac", "1",             # mono
        "-ar", "16000",         # 16kHz (whisper 권장)
        "-loglevel", "error",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 추출 실패: {result.stderr}")


def group_into_paragraphs(segments, gap_seconds: float):
    """세그먼트들을 침묵 간격 기준으로 문단으로 묶기"""
    paragraphs = []
    current = []
    prev_end = None

    for seg in segments:
        if prev_end is not None and (seg["start"] - prev_end) > gap_seconds and current:
            paragraphs.append(" ".join(current).strip())
            current = []
        current.append(seg["text"].strip())
        prev_end = seg["end"]

    if current:
        paragraphs.append(" ".join(current).strip())

    return paragraphs


@app.get("/")
def serve_index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/manifest.json")
def serve_manifest():
    return FileResponse(Path(__file__).parent / "static" / "manifest.json")


@app.get("/service-worker.js")
def serve_service_worker():
    # 정적 파일이지만 scope가 "/" 전체를 덮도록 /static/ 밖(루트)에서 서빙
    return FileResponse(Path(__file__).parent / "static" / "service-worker.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    return {"status": "ok", "model_size": MODEL_SIZE}


class TranscribeRequest(BaseModel):
    url: str


@app.post("/api/transcribe")
async def transcribe(req: TranscribeRequest):
    url = req.url.strip()
    if not url or not url.startswith("http"):
        raise HTTPException(400, "올바른 링크를 입력해주세요.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        video_stem = os.path.join(tmp_dir, "input")
        audio_path = os.path.join(tmp_dir, "audio.wav")

        # 0) 링크에서 영상 다운로드
        try:
            video_path = download_video(url, video_stem)
        except subprocess.TimeoutExpired:
            raise HTTPException(408, "다운로드 시간이 너무 오래 걸려 중단했어요.")
        except RuntimeError as e:
            raise HTTPException(400, str(e))

        # 1) 오디오 추출
        try:
            extract_audio(video_path, audio_path)
        except RuntimeError as e:
            raise HTTPException(400, str(e))

        # 2) 음성 인식
        start_time = time.time()
        segments_gen, info = _model.transcribe(
            audio_path,
            language="ko",
            vad_filter=True,  # 무음 구간 자동 스킵 -> 속도/정확도 향상
        )

        segments = [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in segments_gen
        ]
        elapsed = time.time() - start_time

        if not segments:
            return JSONResponse({
                "full_text": "",
                "paragraphs": [],
                "segments": [],
                "warning": "음성이 감지되지 않았습니다.",
            })

        paragraphs = group_into_paragraphs(segments, PARAGRAPH_GAP_SECONDS)
        full_text = " ".join(seg["text"].strip() for seg in segments)

        return JSONResponse({
            "full_text": full_text,
            "paragraphs": paragraphs,
            "segments": segments,
            "duration_sec": info.duration,
            "processing_sec": round(elapsed, 1),
        })


# 정적 파일(프론트엔드) 서빙
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
