import asyncio
import json
import os
import shutil
import subprocess
import tempfile
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

# ---------- Config ----------
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

cfg = load_config()

# ---------- FFmpeg Resolver ----------
def resolve_ffmpeg(config: dict) -> str:
    """
    Resolve ffmpeg executable path from (in order):
    1) config['ffmpeg_path'] (if provided)
    2) env IMAGEIO_FFMPEG_EXE or FFMPEG_BIN
    3) shutil.which('ffmpeg')
    4) Common locations on Windows/macOS/Linux
    """
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
        candidates += [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",  # macOS Apple Silicon (brew)
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    return ""  # not found

FFMPEG_EXE = resolve_ffmpeg(cfg)

with st.sidebar:
    st.subheader("Settings")
    st.caption(f"Workspace: `{cfg.get('workspace_slug', '')}`")
    st.caption(f"Endpoint base: `{cfg.get('model_server_base_url', '')}`")
    st.toggle("Streaming (from config)", value=bool(cfg.get("stream", True)), disabled=True)
    st.markdown("---")
    model_size = st.selectbox("Whisper model size", ["tiny", "base", "small"], index=1)
    compute_type = st.selectbox("Compute type", ["int8", "int8_float16", "float16", "float32"], index=0)
    lang_hint = st.text_input("Language hint (optional, e.g., en, es, ta)", value="")

with st.expander("Diagnostics", expanded=True):
    st.write("**Config (redacted key)**")
    safe_cfg = dict(cfg)
    if "api_key" in safe_cfg:
        safe_cfg["api_key"] = "*****" + safe_cfg["api_key"][-4:]
    st.code(safe_cfg, language="yaml")

    st.write("**Resolved ffmpeg path**")
    st.write(FFMPEG_EXE or "NOT FOUND")

    st.write("**PATH (first 2 entries)**")
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    st.code("\n".join(path_entries[:2]) or "(empty)")

    if not FFMPEG_EXE:
        st.info(
            "ffmpeg not resolved. You can add a key to config.yaml, e.g.:\n\n"
            "ffmpeg_path: \"C:\\\\ProgramData\\\\chocolatey\\\\bin\\\\ffmpeg.exe\""
        )

# ---------- Model Server Client ----------
class ModelClient:
    def __init__(self, config: dict):
        self.api_key = config["api_key"]
        self.base_url = config["model_server_base_url"].rstrip("/")
        self.stream = bool(config.get("stream", True))
        self.stream_timeout = config.get("stream_timeout", 120)
        self.workspace_slug = config["workspace_slug"]
        self.chat_url = (
            f"{self.base_url}/workspace/{self.workspace_slug}/stream-chat"
            if self.stream else
            f"{self.base_url}/workspace/{self.workspace_slug}/chat"
        )
        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key
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

    def summarize_blocking(self, transcript: str) -> str:
        data = {
            "message": self._build_prompt(transcript),
            "mode": "chat",
            "sessionId": "video-summary-ui",
            "attachments": []
        }
        resp = httpx.post(self.chat_url, headers=self.headers, json=data, timeout=self.stream_timeout)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("textResponse", "").strip()

    async def summarize_streaming(self, transcript: str) -> Generator[str, None, None]:
        data = {
            "message": self._build_prompt(transcript),
            "mode": "chat",
            "sessionId": "video-summary-ui",
            "attachments": []
        }
        buffer = ""
        async with httpx.AsyncClient(timeout=self.stream_timeout) as client:
            async with client.stream("POST", self.chat_url, headers=self.headers, json=data) as response:
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
        raise RuntimeError(
            "ffmpeg not found. Set 'ffmpeg_path' in config.yaml or ensure it's on PATH."
        )
    tmp_dir = tempfile.mkdtemp(prefix="vs_")
    in_path = os.path.join(tmp_dir, filename)
    with open(in_path, "wb") as f:
        f.write(video_bytes)
    out_wav = os.path.join(tmp_dir, "audio_16k.wav")
    cmd = [
        FFMPEG_EXE, "-y",
        "-i", in_path,
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        out_wav
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if not os.path.exists(out_wav):
        raise RuntimeError("ffmpeg did not produce the expected WAV file.")
    return out_wav

def transcribe_wav(model: WhisperModel, wav_path: str, language: Optional[str] = None) -> Tuple[str, float]:
    """Transcribe WAV to text; returns (transcript, duration_seconds)."""
    segments, info = model.transcribe(
        wav_path,
        language=language,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False
    )
    parts = [seg.text.strip() for seg in segments if seg.text]
    transcript = " ".join(parts).strip()
    return transcript, info.duration

# ---------- UI Controls ----------
uploaded = st.file_uploader(
    "Video/Audio file",
    type=["mp4", "mov", "mkv", "webm", "avi", "mp3", "wav", "m4a", "aac", "flac", "ogg"],
    accept_multiple_files=False
)

go = st.button("Process & Summarize", type="primary", disabled=(uploaded is None))

# ---------- Pipeline ----------
if go and uploaded:
    st.info("1) Extracting audio… 2) Transcribing (local)… 3) Summarizing…")

    # 1) Extract
    with st.spinner("Extracting audio with ffmpeg…"):
        try:
            wav_path = extract_audio_to_wav(uploaded.read(), uploaded.name)
        except subprocess.CalledProcessError as e:
            st.error("ffmpeg failed while extracting audio. Ensure the file format is supported.")
            st.code(str(e), language="bash")
            st.stop()
        except Exception as e:
            st.error(f"Audio extraction failed: {e}")
            st.stop()

    # 2) Transcribe
    with st.spinner(f"Transcribing with faster-whisper ({model_size}, {compute_type})…"):
        try:
            model = load_whisper_model(model_size=model_size, compute_type=compute_type)
            transcript_text, duration = transcribe_wav(model, wav_path, language=(lang_hint or None))
        except Exception as e:
            st.error(f"Transcription failed: {e}")
            st.stop()

    if not transcript_text:
        st.warning("Transcription produced empty text.")
        st.stop()

    st.success(f"Transcribed ~{int(duration)} seconds of audio.")
    with st.expander("Preview transcript", expanded=False):
        st.text_area("Transcript", value=transcript_text, height=240)

    st.download_button(
        "Download transcript (.txt)",
        data=transcript_text.encode("utf-8"),
        file_name="transcript.txt",
        mime="text/plain"
    )

    # 3) Summarize
    client = ModelClient(cfg)
    st.subheader("Summary")

    summary_text = ""  # ensure defined for both branches

    if client.stream:
        ph = st.empty()
        chunks = []
        st.info("Generating summary (streaming)…")
        try:
            async def run_stream():
                async for piece in client.summarize_streaming(transcript_text):
                    chunks.append(piece)
                    ph.markdown("".join(chunks))
            asyncio.run(run_stream())
            summary_text = "".join(chunks)
        except Exception as e:
            st.error(f"Streaming summary failed: {e}")
            st.stop()
    else:
        with st.spinner("Generating summary…"):
            try:
                summary_text = client.summarize_blocking(transcript_text)
                st.markdown(summary_text or "_(empty)_")
            except Exception as e:
                st.error(f"Summary failed: {e}")
                st.stop()

    if summary_text:
        st.download_button(
            "Download summary (.md)",
            data=summary_text.encode("utf-8"),
            file_name="summary.md",
            mime="text/markdown"
        )

st.caption(
    "Requires: `ffmpeg`, `faster-whisper`, `httpx`, `PyYAML`, `streamlit`.\n"
    "You can also set a specific path in config.yaml with `ffmpeg_path: \"C:\\\\ProgramData\\\\chocolatey\\\\bin\\\\ffmpeg.exe\"`.\n"
    "Run: `streamlit run src\\video.py`"
)
