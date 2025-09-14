# src/new_video.py
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional, Tuple

import httpx
import streamlit as st
import yaml
from faster_whisper import WhisperModel

# ---------- Page ----------
st.set_page_config(page_title="Video → Transcript → Summary", page_icon="🎬", layout="centered")
st.title("🎬 Video → Transcript → Summary")
st.write(
    "Upload a **video or audio** file. I’ll extract audio with **ffmpeg**, "
    "transcribe locally with **faster-whisper**, then summarize via your model server."
)
st.write("✅ App loaded")  # sanity-breadcrumb so blank pages are obvious

# ---------- Config ----------
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

cfg = load_config()

# ---------- FFmpeg Resolver ----------
def resolve_ffmpeg(config: dict) -> str:
    # 1) config override
    cfg_path = (config or {}).get("ffmpeg_path")
    if cfg_path and os.path.isfile(cfg_path):
        return cfg_path
    # 2) env vars
    for env_var in ("IMAGEIO_FFMPEG_EXE", "FFMPEG_BIN"):
        p = os.environ.get(env_var)
        if p and os.path.isfile(p):
            return p
    # 3) PATH
    p = shutil.which("ffmpeg")
    if p:
        return p
    # 4) Common locations
    candidates = []
    if os.name == "nt":
        candidates += [
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        ]
    else:
        candidates += ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""

FFMPEG_EXE = resolve_ffmpeg(cfg)

# ---------- Sidebar ----------
with st.sidebar:
    st.subheader("Settings")
    st.caption(f"Workspace: `{cfg.get('workspace_slug', '')}`")
    st.caption(f"Endpoint base: `{cfg.get('model_server_base_url', '')}`")
    # We keep the stream flag visible, but default to blocking for reliability
    st.toggle("Streaming (from config)", value=bool(cfg.get("stream", True)), disabled=True)
    st.markdown("---")
    model_size = st.selectbox("Whisper model size", ["tiny", "base", "small"], index=1)
    compute_type = st.selectbox("Compute type", ["int8", "int8_float16", "float16", "float32"], index=0)
    lang_hint = st.text_input("Language hint (optional, e.g., en, es, ta)", value="")
    transcripts_dir = Path(st.text_input("Transcripts folder", value="transcripts")).resolve()

with st.expander("Diagnostics", expanded=True):
    safe_cfg = dict(cfg)
    if "api_key" in safe_cfg:
        safe_cfg["api_key"] = "*****" + safe_cfg["api_key"][-4:]
    st.write("**Config (redacted key)**")
    st.code(safe_cfg, language="yaml")

    st.write("**Resolved ffmpeg path**")
    st.write(FFMPEG_EXE or "NOT FOUND")
    st.write("**Transcripts folder**")
    st.write(str(transcripts_dir))

    if not FFMPEG_EXE:
        st.info('Set `ffmpeg_path:` in config.yaml if ffmpeg is not on PATH.')

# ---------- Model Server Client ----------
class ModelClient:
    def __init__(self, config: dict):
        self.api_key = config["api_key"]
        self.base_url = config["model_server_base_url"].rstrip("/")
        self.stream = bool(config.get("stream", True))
        self.stream_timeout = int(config.get("stream_timeout", 120))
        self.workspace_slug = config["workspace_slug"]
        self.stream_url = f"{self.base_url}/workspace/{self.workspace_slug}/stream-chat"
        self.block_url = f"{self.base_url}/workspace/{self.workspace_slug}/chat"
        self.headers = {
            "accept": "application/json, text/plain;q=0.5, */*;q=0.1",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }

    def _build_prompt(self, transcript: str) -> str:
        return (
            "You are a concise meeting/video summarizer.\n\n"
            "Given the transcript below, produce a short, executive summary with:\n"
            "• 5–7 bullet points of key takeaways\n"
            "• Participants or speakers (if clear)\n"
            "• Concrete decisions and action items (with owners if clear)\n"
            "• 1–2 notable quotes if salient\n\n"
            "Be crisp. Avoid filler. Keep it under ~180 words.\n\n"
            f"TRANSCRIPT START\n{transcript}\nTRANSCRIPT END"
        )

    def summarize_blocking(self, transcript: str) -> tuple[str, dict]:
        """Return (summary_text, debug_info)."""
        data = {
            "message": self._build_prompt(transcript),
            "mode": "chat",
            "sessionId": "video-summary-ui",
            "attachments": [],
        }
        debug = {}
        try:
            resp = httpx.post(self.block_url, headers=self.headers, json=data, timeout=self.stream_timeout)
            debug = {
                "status": resp.status_code,
                "content_type": (resp.headers.get("content-type") or "").lower(),
                "text_snippet": (resp.text or "")[:500],
            }
        except Exception as e:
            return (f"_Request failed: {e}_", {"exception": str(e)})

        if resp.status_code >= 400:
            return (f"_Server error {resp.status_code}: {resp.text[:500]}_", debug)

        ct = debug["content_type"]
        if "application/json" in ct:
            try:
                return ((resp.json().get("textResponse") or "").strip(), debug)
            except Exception as e:
                return (f"_JSON parse error: {e}. Raw:_\n\n{resp.text[:800]}", debug)
        # plain text fallback
        return (resp.text.strip(), debug)

    async def summarize_streaming(self, transcript: str) -> Generator[str, None, None]:
        data = {
            "message": self._build_prompt(transcript),
            "mode": "chat",
            "sessionId": "video-summary-ui",
            "attachments": [],
        }
        buffer = ""
        async with httpx.AsyncClient(timeout=self.stream_timeout) as client:
            async with client.stream("POST", self.stream_url, headers=self.headers, json=data) as response:
                async for chunk in response.aiter_text():
                    if not chunk:
                        continue
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.startswith("data: "):
                            line = line[len("data: "):]
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line)
                            piece = parsed.get("textResponse", "")
                            if piece:
                                yield piece
                            if parsed.get("close", False):
                                return
                        except json.JSONDecodeError:
                            continue

# ---------- Helpers ----------
@st.cache_resource(show_spinner=False)
def load_whisper_model(model_size: str = "base", compute_type: str = "int8") -> WhisperModel:
    return WhisperModel(model_size, device="cpu", compute_type=compute_type)

def extract_audio_to_wav(video_bytes: bytes, filename: str) -> str:
    """Extract audio to 16kHz mono PCM WAV using ffmpeg; return path to WAV."""
    if not FFMPEG_EXE or not os.path.isfile(FFMPEG_EXE):
        raise RuntimeError("ffmpeg not found. Set 'ffmpeg_path' in config.yaml or ensure it's on PATH.")
    tmp_dir = tempfile.mkdtemp(prefix="vs_")
    in_path = os.path.join(tmp_dir, filename)
    with open(in_path, "wb") as f:
        f.write(video_bytes)
    out_wav = os.path.join(tmp_dir, "audio_16k.wav")
    cmd = [FFMPEG_EXE, "-y", "-i", in_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_wav]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if not os.path.exists(out_wav):
        raise RuntimeError("ffmpeg did not produce the expected WAV file.")
    return out_wav

def transcribe_wav(model: WhisperModel, wav_path: str, language: Optional[str] = None) -> Tuple[str, float]:
    segments, info = model.transcribe(
        wav_path,
        language=language,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    parts = [seg.text.strip() for seg in segments if seg.text]
    transcript = " ".join(parts).strip()
    return transcript, float(info.duration or 0.0)

# ---------- UI Controls ----------
uploaded = st.file_uploader(
    "Video/Audio file",
    type=["mp4", "mov", "mkv", "webm", "avi", "mp3", "wav", "m4a", "aac", "flac", "ogg"],
    accept_multiple_files=False,
)
go = st.button("Process & Summarize", type="primary", disabled=(uploaded is None))

# ---------- Pipeline ----------
if go and uploaded:
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    base_name = Path(uploaded.name).stem
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{base_name}_{stamp}"

    st.info("1) Extracting audio… 2) Transcribing (local)… 3) Summarizing…")

    # 1) Extract
    try:
        with st.spinner("Extracting audio with ffmpeg…"):
            wav_path = extract_audio_to_wav(uploaded.read(), uploaded.name)
    except Exception as e:
        st.exception(e)
        wav_path = ""

    # 2) Transcribe
    transcript_text = ""
    duration = 0.0
    if wav_path:
        try:
            with st.spinner(f"Transcribing with faster-whisper ({model_size}, {compute_type})…"):
                model = load_whisper_model(model_size=model_size, compute_type=compute_type)
                transcript_text, duration = transcribe_wav(model, wav_path, language=(lang_hint or None))
        except Exception as e:
            st.exception(e)

    if transcript_text:
        # ALWAYS save transcript as .txt
        txt_out = transcripts_dir / f"{base}.txt"
        txt_out.write_text(transcript_text, encoding="utf-8")
        st.success(f"Transcribed ~{int(duration)} seconds. Saved transcript: `{txt_out.name}`")

        with st.expander("Preview transcript", expanded=False):
            st.text_area("Transcript", value=transcript_text, height=240)

        st.download_button(
            "Download transcript (.txt)",
            data=transcript_text.encode("utf-8"),
            file_name=f"{base}.txt",
            mime="text/plain",
        )

        # 3) Summarize — use blocking for reliability; stream can be flaky depending on server
        st.subheader("Summary")
        client = ModelClient(cfg)
        summary_text = ""
        debug_info = {}

        try:
            with st.spinner("Generating summary…"):
                summary_text, debug_info = client.summarize_blocking(transcript_text)
        except Exception as e:
            st.exception(e)

        with st.expander("🔎 Server response debug", expanded=False):
            st.write(debug_info or "(no debug info)")

        if summary_text:
            md_out = transcripts_dir / f"{base}.summary.md"
            md_out.write_text(summary_text, encoding="utf-8")
            st.markdown(summary_text)
            st.download_button(
                "Download summary (.md)",
                data=summary_text.encode("utf-8"),
                file_name=f"{base}.summary.md",
                mime="text/markdown",
            )
            st.success(f"Saved summary: `{md_out.name}`")
        else:
            st.warning("No summary generated (server returned empty).")
    else:
        st.warning("Transcription produced empty text.")

# ---------- Processed summaries (manual/optional auto-refresh) ----------
st.markdown("---")
st.subheader("📚 Processed recordings")

col1, col2, col3 = st.columns([1, 2, 2])
with col1:
    auto = st.checkbox("Auto-refresh", value=False)  # default OFF to avoid blank loops
with col2:
    interval = st.slider("Refresh every (seconds)", 3, 30, 5)
with col3:
    if st.button("Refresh now"):
        try:
            st.rerun()
        except Exception:
            st.experimental_rerun()

transcripts_dir.mkdir(exist_ok=True)
summary_files = sorted(transcripts_dir.glob("*.summary.md"), key=lambda p: p.stat().st_mtime, reverse=True)

if not summary_files:
    st.caption("No processed summaries yet. When the monitor/worker finishes a file, it will appear here.")
else:
    names = [f.stem for f in summary_files]
    choice = st.selectbox("Pick a summary", names, index=0)
    chosen = transcripts_dir / f"{choice}.summary.md"
    transcript_path = transcripts_dir / f"{choice}.txt"

    st.write(f"**Summary file:** `{chosen.name}`")
    if transcript_path.exists():
        with st.expander("Show transcript (.txt)"):
            st.text_area("Transcript", transcript_path.read_text(encoding="utf-8"), height=240)

    st.markdown(chosen.read_text(encoding="utf-8"))

# Optional auto-refresh (kept safe by default OFF)
if auto:
    time.sleep(interval)
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()

st.caption(
    "Requires: `ffmpeg`, `faster-whisper`, `httpx`, `PyYAML`, `streamlit`.\n"
    "Set `ffmpeg_path` in config.yaml if ffmpeg is not on PATH.\n"
    "Run: `streamlit run src\\new_video.py`"
)
