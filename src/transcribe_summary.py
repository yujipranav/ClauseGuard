# src/transcribe_summary.py
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx
import yaml
from faster_whisper import WhisperModel


# ---------------- Config ----------------
def load_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if not isinstance(cfg, dict):
            raise ValueError("config.yaml is not a mapping/dict.")
        return cfg
    except FileNotFoundError:
        raise SystemExit(f"[worker] config file not found: {path}")
    except Exception as e:
        raise SystemExit(f"[worker] failed to parse config '{path}': {e}")


# ---------------- FFmpeg resolve ----------------
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

    # 4) common installs
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
            "/opt/homebrew/bin/ffmpeg",  # macOS (brew, Apple Silicon)
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    return ""


# ---------------- Model client ----------------
class ModelClient:
    def __init__(self, config: dict):
        try:
            self.api_key = config["api_key"]
            self.base_url = config["model_server_base_url"].rstrip("/")
            self.workspace_slug = config["workspace_slug"]
        except KeyError as e:
            raise SystemExit(f"[worker] missing config key: {e}")

        # CLI uses blocking JSON endpoint; read timeout can be large
        self.stream_timeout = int(config.get("stream_timeout", 600))
        self.chat_url_blocking = f"{self.base_url}/workspace/{self.workspace_slug}/chat"

        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }

        # Timeouts tuned for large payloads
        self.timeout = httpx.Timeout(
            connect=10.0,           # quick fail if unreachable
            write=180.0,            # allow time to upload long transcripts
            read=self.stream_timeout,  # main knob for slow generation
            pool=60.0
        )

        # Reuse a client connection (helps on Windows)
        self.client = httpx.Client(timeout=self.timeout)

    @staticmethod
    def build_prompt(transcript: str) -> str:
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

    def _post_with_retries(self, url: str, json_body: dict, max_retries: int = 3) -> httpx.Response:
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                return self.client.post(url, headers=self.headers, json=json_body)
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError) as e:
                last_err = e
                backoff = min(2 ** attempt, 8)  # 2s, 4s, 8s
                print(f"[worker] summarize attempt {attempt}/{max_retries} failed: {e}. Retrying in {backoff}s…")
                time.sleep(backoff)
        raise last_err or RuntimeError("Unknown HTTP failure")

    def summarize_blocking(self, transcript: str) -> str:
        data = {
            "message": self.build_prompt(transcript),
            "mode": "chat",
            "sessionId": "video-summary-cli",
            "attachments": [],
        }
        resp = self._post_with_retries(self.chat_url_blocking, data)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:800]
            raise SystemExit(f"[worker] summary HTTP {e.response.status_code}:\n{body}")
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            raise SystemExit(f"[worker] summary endpoint returned non-JSON:\n{resp.text[:800]}")
        return (payload.get("textResponse") or "").strip()

    def summarize_chunked(self, transcript: str, chunk_chars: int = 4000) -> str:
        """Chunk very long transcripts to avoid huge payloads/timeouts."""
        if len(transcript) <= chunk_chars:
            return self.summarize_blocking(transcript)

        # 1) Summarize each chunk
        chunks = []
        total = math.ceil(len(transcript) / chunk_chars)
        for i in range(0, len(transcript), chunk_chars):
            chunk = transcript[i:i + chunk_chars]
            print(f"[worker] summarizing chunk {i // chunk_chars + 1}/{total}…")
            chunks.append(self.summarize_blocking(chunk))

        # 2) Combine chunk summaries into a final executive summary
        combine_text = "\n".join(f"- {c}" for c in chunks if c.strip())
        combine_prompt = (
            "You are an expert editor. Combine the bullet summaries below into a single, crisp executive summary. "
            "Keep it under ~180 words, deduplicate points, keep concrete decisions/actions.\n\n"
            f"{combine_text}"
        )
        data = {
            "message": combine_prompt,
            "mode": "chat",
            "sessionId": "video-summary-cli-merge",
            "attachments": [],
        }
        resp = self._post_with_retries(self.chat_url_blocking, data)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:800]
            raise SystemExit(f"[worker] summary (combine) HTTP {e.response.status_code}:\n{body}")
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            raise SystemExit(f"[worker] combine endpoint returned non-JSON:\n{resp.text[:800]}")
        return (payload.get("textResponse") or "").strip()


# ---------------- Audio / ASR ----------------
def extract_audio_to_wav(ffmpeg_exe: str, src_video: Path) -> Path:
    if not ffmpeg_exe or not os.path.isfile(ffmpeg_exe):
        raise RuntimeError(
            "ffmpeg not found. Set 'ffmpeg_path' in config.yaml or ensure it's on PATH."
        )
    tmp_dir = Path(tempfile.mkdtemp(prefix="vs_"))
    out_wav = tmp_dir / "audio_16k.wav"
    cmd = [
        ffmpeg_exe, "-y",
        "-i", str(src_video),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if not out_wav.exists():
        raise RuntimeError("ffmpeg did not produce the expected WAV file.")
    return out_wav


def load_whisper_model(model_size: str = "base", compute_type: str = "int8") -> WhisperModel:
    return WhisperModel(model_size, device="cpu", compute_type=compute_type)


def transcribe_wav(model: WhisperModel, wav_path: Path, language: Optional[str] = None) -> Tuple[str, float]:
    segments, info = model.transcribe(
        str(wav_path),
        language=language,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    parts = [seg.text.strip() for seg in segments if getattr(seg, "text", None)]
    transcript = " ".join(parts).strip()
    return transcript, float(getattr(info, "duration", 0.0) or 0.0)


# ---------------- Main pipeline ----------------
def run_pipeline(
    video_path: Path,
    out_dir: Path,
    cfg_path: Path,
    model_size: str,
    compute_type: str,
    language_hint: Optional[str],
    summary_dir: Optional[Path] = None,
) -> Path:
    cfg = load_config(str(cfg_path))
    ffmpeg_exe = resolve_ffmpeg(cfg)
    if not ffmpeg_exe:
        raise SystemExit("[worker] ffmpeg not found. Provide ffmpeg_path in config.yaml or add to PATH.")

    out_dir.mkdir(parents=True, exist_ok=True)
    if summary_dir is None:
        summary_dir = out_dir
    summary_dir.mkdir(parents=True, exist_ok=True)

    base = video_path.stem
    transcript_path = out_dir / f"{base}.transcript.txt"
    summary_path    = summary_dir / f"{base}.summary.md"
    meta_path       = out_dir / f"{base}.meta.json"

    print(f"[worker] extracting audio via ffmpeg…")
    wav_path = extract_audio_to_wav(ffmpeg_exe, video_path)

    print(f"[worker] loading faster-whisper ({model_size}, {compute_type})…")
    model = load_whisper_model(model_size=model_size, compute_type=compute_type)

    print(f"[worker] transcribing…")
    transcript_text, duration_sec = transcribe_wav(model, wav_path, language=language_hint)
    if not transcript_text:
        raise SystemExit("[worker] transcription produced empty text.")

    transcript_path.write_text(transcript_text, encoding="utf-8")

    print(f"[worker] summarizing via model server…")
    client = ModelClient(cfg)
    summary_text = client.summarize_chunked(transcript_text, chunk_chars=4000)
    if not summary_text:
        summary_text = "_(empty)_"
    summary_path.write_text(summary_text, encoding="utf-8")

    meta = {
        "input_video": str(video_path),
        "duration_seconds": int(duration_sec),
        "whisper": {"model_size": model_size, "compute_type": compute_type, "language_hint": language_hint or ""},
        "ffmpeg_path": ffmpeg_exe,
        "outputs": {"transcript": str(transcript_path), "summary": str(summary_path)},
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # cleanup temp dir
    try:
        shutil.rmtree(wav_path.parent, ignore_errors=True)
    except Exception:
        pass

    print(f"[worker] ✅ wrote:")
    print(f"  - {transcript_path}")
    print(f"  - {summary_path}")
    print(f"  - {meta_path}")
    return summary_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Transcribe a video and summarize it (CLI worker).")
    p.add_argument("--input", required=True, type=Path, help="Path to input video/audio file")
    p.add_argument("--out-dir", required=True, type=Path, help="Directory to write transcript/meta outputs")
    p.add_argument("--summary-dir", type=Path, default=None, help="Directory to write summaries (defaults to out-dir)")
    p.add_argument("--config", default="config.yaml", type=Path, help="Path to config.yaml")
    p.add_argument("--model-size", default="base", choices=["tiny", "base", "small"], help="faster-whisper model size")
    p.add_argument(
        "--compute-type",
        default="int8",
        choices=["int8", "int8_float16", "float16", "float32"],
        help="faster-whisper compute type",
    )
    p.add_argument("--lang", default="", help="Optional language hint, e.g., en, es, ta")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    inp: Path = args.input.resolve()
    if not inp.exists():
        print(f"[worker] input not found: {inp}", file=sys.stderr)
        return 2
    try:
        run_pipeline(
            video_path=inp,
            out_dir=args.out_dir.resolve(),
            cfg_path=args.config.resolve(),
            model_size=args.model_size,
            compute_type=args.compute_type,
            language_hint=(args.lang or None),
            summary_dir=(args.summary_dir.resolve() if args.summary_dir else None),
        )
        return 0
    except subprocess.CalledProcessError as e:
        print(f"[worker] ffmpeg failed: {e}", file=sys.stderr)
        return 3
    except httpx.HTTPError as e:
        print(f"[worker] summary HTTP error: {e}", file=sys.stderr)
        return 4
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 5
    except Exception as e:
        print(f"[worker] unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
