"""릴스 대본 추출기 - 데스크톱 앱 (Windows/Mac 공용)

로컬에서 실행되는 tkinter GUI. 서버 없이 내 컴퓨터에서
yt-dlp(다운로드) -> ffmpeg(오디오 추출) -> faster-whisper(STT) 파이프라인을 직접 돌린다.
"""

import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog, ttk

import imageio_ffmpeg
import yt_dlp

MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small")
PARAGRAPH_GAP_SECONDS = 1.2
MAX_DOWNLOAD_MB = 300

# 추출 기록(내 컴퓨터에만 저장, 외부로 전송되지 않음)
HISTORY_DIR = Path.home() / "릴스대본추출기_기록"
HISTORY_INDEX = HISTORY_DIR / "index.json"


def load_history():
    if not HISTORY_INDEX.exists():
        return []
    try:
        return json.loads(HISTORY_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_history_entry(url: str, full_text: str):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    entries = load_history()
    entry_id = uuid.uuid4().hex[:8]
    entries.insert(0, {
        "id": entry_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "url": url,
        "preview": full_text[:60],
    })
    HISTORY_INDEX.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    (HISTORY_DIR / f"{entry_id}.txt").write_text(full_text, encoding="utf-8")

# 인스타그램 등이 로그인 안 된 요청을 막을 때, 브라우저에 로그인된 쿠키를 빌려 쓰기 위한 선택지
BROWSER_CHOICES = {
    "사용 안 함": None,
    "Chrome": "chrome",
    "Edge": "edge",
    "Whale": "whale",
    "Firefox": "firefox",
}

_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def download_video(url: str, out_dir: str, browser: str | None = None, cookie_file: str | None = None) -> str:
    ydl_opts = {
        "format": f"bestvideo[filesize<{MAX_DOWNLOAD_MB}M]+bestaudio/best[filesize<{MAX_DOWNLOAD_MB}M]/best",
        "outtmpl": os.path.join(out_dir, "input.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    # cookies.txt 파일이 있으면 그걸 우선 사용 (브라우저가 켜져 있을 때 쿠키 DB가 잠겨서
    # cookiesfrombrowser가 실패하는 경우가 많아, 파일 방식이 더 안정적임)
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    elif browser:
        ydl_opts["cookiesfrombrowser"] = (browser,)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    matches = list(Path(out_dir).glob("input.*"))
    if not matches:
        raise RuntimeError("다운로드된 파일을 찾을 수 없습니다.")
    return str(matches[0])


def extract_audio(video_path: str, audio_path: str) -> None:
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-loglevel", "error",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 추출 실패: {result.stderr}")


def group_into_paragraphs(segments, gap_seconds: float):
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


def transcribe_url(url: str, on_status, browser: str | None = None, cookie_file: str | None = None):
    with tempfile.TemporaryDirectory() as tmp_dir:
        on_status("영상 다운로드 중...")
        video_path = download_video(url, tmp_dir, browser, cookie_file)

        on_status("오디오 추출 중...")
        audio_path = os.path.join(tmp_dir, "audio.wav")
        extract_audio(video_path, audio_path)

        on_status("모델 로딩 중... (처음 실행 시 다소 걸릴 수 있어요)")
        model = get_model()

        on_status("음성 인식 중...")
        segments_gen, _info = model.transcribe(audio_path, language="ko", vad_filter=True)
        segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segments_gen]

        if not segments:
            return "", []

        paragraphs = group_into_paragraphs(segments, PARAGRAPH_GAP_SECONDS)
        full_text = " ".join(seg["text"].strip() for seg in segments)
        return full_text, paragraphs


class App(tk.Tk):
    BG = "#0f0f12"
    CARD = "#18181c"
    TEXT = "#f2f2f3"
    MUTED = "#9a9aa2"
    ACCENT = "#6c5ce7"

    def __init__(self):
        super().__init__()
        self.title("릴스 대본 추출기")
        self.geometry("640x580")
        self.configure(bg=self.BG)
        self.full_text = ""
        self._build_ui()

    def _build_ui(self):
        tk.Label(self, text="🎬 릴스 대본 추출기", font=("Segoe UI", 16, "bold"),
                 bg=self.BG, fg=self.TEXT).pack(anchor="w", padx=16, pady=(16, 0))
        tk.Label(self, text="릴스/쇼츠 링크를 붙여넣으면 대본(텍스트)으로 뽑아드려요.",
                 bg=self.BG, fg=self.MUTED).pack(anchor="w", padx=16, pady=(0, 12))

        self.url_entry = tk.Entry(self, font=("Segoe UI", 11))
        self.url_entry.pack(fill="x", padx=16, ipady=6)
        self.url_entry.bind("<Return>", lambda _e: self.on_submit())

        browser_frame = tk.Frame(self, bg=self.BG)
        browser_frame.pack(fill="x", padx=16, pady=(8, 0))
        tk.Label(browser_frame, text="브라우저 로그인 자동 사용(켜져 있으면 실패할 수 있음):",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.browser_var = tk.StringVar(value="사용 안 함")
        self.browser_combo = ttk.Combobox(browser_frame, textvariable=self.browser_var,
                                           values=list(BROWSER_CHOICES.keys()), state="readonly", width=10)
        self.browser_combo.pack(side="left", padx=(8, 0))

        cookie_frame = tk.Frame(self, bg=self.BG)
        cookie_frame.pack(fill="x", padx=16, pady=(4, 0))
        tk.Label(cookie_frame, text="또는 cookies.txt 파일(권장, 더 안정적):",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.cookie_file_var = tk.StringVar(value="")
        tk.Label(cookie_frame, textvariable=self.cookie_file_var, bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))
        tk.Button(cookie_frame, text="찾아보기", command=self.on_pick_cookie_file
                  ).pack(side="right")

        self.submit_btn = tk.Button(self, text="대본 추출하기", command=self.on_submit,
                                     bg=self.ACCENT, fg="white", relief="flat", cursor="hand2")
        self.submit_btn.pack(fill="x", padx=16, pady=8, ipady=8)

        self.status_label = tk.Label(self, text="", bg=self.BG, fg=self.MUTED)
        self.status_label.pack(anchor="w", padx=16)

        self.result_text = scrolledtext.ScrolledText(self, wrap="word", font=("Segoe UI", 11),
                                                       bg=self.CARD, fg=self.TEXT, relief="flat")
        self.result_text.pack(fill="both", expand=True, padx=16, pady=8)

        btn_frame = tk.Frame(self, bg=self.BG)
        btn_frame.pack(fill="x", padx=16, pady=(0, 16))
        tk.Button(btn_frame, text="복사하기", command=self.on_copy
                  ).pack(side="left", expand=True, fill="x", padx=(0, 4), ipady=4)
        tk.Button(btn_frame, text="txt로 저장", command=self.on_save
                  ).pack(side="left", expand=True, fill="x", padx=(4, 4), ipady=4)
        tk.Button(btn_frame, text="기록 보기", command=self.on_show_history
                  ).pack(side="left", expand=True, fill="x", padx=(4, 0), ipady=4)

    def on_submit(self):
        url = self.url_entry.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("알림", "올바른 링크를 입력해주세요.")
            return
        self.submit_btn.config(state="disabled")
        self.result_text.delete("1.0", "end")
        browser = BROWSER_CHOICES.get(self.browser_var.get())
        cookie_file = self.cookie_file_var.get().strip() or None
        threading.Thread(target=self._run_transcribe, args=(url, browser, cookie_file), daemon=True).start()

    def on_pick_cookie_file(self):
        path = filedialog.askopenfilename(filetypes=[("cookies.txt", "*.txt"), ("모든 파일", "*.*")])
        if path:
            self.cookie_file_var.set(path)

    def _run_transcribe(self, url, browser, cookie_file):
        def on_status(msg):
            self.after(0, lambda: self.status_label.config(text=msg))

        try:
            full_text, paragraphs = transcribe_url(url, on_status, browser, cookie_file)
            self.full_text = full_text
            if not paragraphs:
                self.after(0, lambda: self.status_label.config(text="음성을 인식하지 못했어요."))
            else:
                text = "\n\n".join(paragraphs)
                self.after(0, lambda: self._show_result(text))
                save_history_entry(url, full_text)
        except Exception as e:
            traceback.print_exc()
            msg = str(e)
            debug = f"[browser={browser!r} cookie_file={'있음' if cookie_file else '없음'}]"
            self.after(0, lambda: self.status_label.config(text=f"오류 {debug}: {msg}"))
        finally:
            self.after(0, lambda: self.submit_btn.config(state="normal"))

    def _show_result(self, text):
        self.result_text.insert("1.0", text)
        self.status_label.config(text="완료!")

    def on_copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.full_text)
        self.status_label.config(text="클립보드에 복사됨!")

    def on_save(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="script.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.full_text)

    def on_show_history(self):
        entries = load_history()
        win = tk.Toplevel(self)
        win.title("추출 기록")
        win.geometry("560x400")
        win.configure(bg=self.BG)

        if not entries:
            tk.Label(win, text="아직 저장된 기록이 없어요.", bg=self.BG, fg=self.MUTED
                      ).pack(padx=16, pady=16)
            return

        tk.Label(win, text="더블클릭하면 그 대본을 불러옵니다. (내 컴퓨터에만 저장된 기록)",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(12, 4))

        listbox = tk.Listbox(win, font=("Segoe UI", 10), bg=self.CARD, fg=self.TEXT,
                              selectbackground=self.ACCENT, relief="flat")
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for e in entries:
            listbox.insert("end", f"[{e['timestamp']}] {e['url']}  —  {e['preview']}")

        def on_double_click(_evt):
            sel = listbox.curselection()
            if not sel:
                return
            entry = entries[sel[0]]
            text_path = HISTORY_DIR / f"{entry['id']}.txt"
            if text_path.exists():
                self.full_text = text_path.read_text(encoding="utf-8")
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, entry["url"])
                self.result_text.delete("1.0", "end")
                self.result_text.insert("1.0", self.full_text)
                self.status_label.config(text=f"기록 불러옴 ({entry['timestamp']})")
                win.destroy()

        listbox.bind("<Double-Button-1>", on_double_click)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    App().mainloop()
