# src/recordings_monitor.py
import time
from pathlib import Path
import subprocess
import sys

WATCH_DIR = Path("recordings").resolve()
OUT_DIR = Path("transcripts").resolve()
SUMMARY_DIR = Path("summaries").resolve()
WORKER = Path("src/transcribe_summary.py").resolve()

POLL_INTERVAL = 5  # seconds
SEEN = set()

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def file_is_stable(p: Path, wait: float = 1.0) -> bool:
    """Heuristic: ensure file size isn't still growing (avoids processing while copying)."""
    try:
        s1 = p.stat().st_size
        time.sleep(wait)
        s2 = p.stat().st_size
        return s1 == s2 and s1 > 0
    except FileNotFoundError:
        return False


def run_worker(video_path: Path):
    """Call the transcription+summary worker on one file."""
    print(f"[monitor] New file: {video_path.name}")
    cmd = [
        sys.executable, str(WORKER),
        "--config", "config.yaml",
        "--input", str(video_path),
        "--out-dir", str(OUT_DIR),
        "--summary-dir", str(SUMMARY_DIR),
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"[monitor] ✅ Done: {video_path.name}")
    except subprocess.CalledProcessError as e:
        print(f"[monitor] ❌ Worker failed: {e}")


def main():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[monitor] Watching: {WATCH_DIR}")
    print(f"[monitor] Output to (transcripts): {OUT_DIR}")
    print(f"[monitor] Output to (summaries):  {SUMMARY_DIR}")
    print("[monitor] Press Ctrl+C to stop.")

    while True:
        for f in WATCH_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS and f not in SEEN:
                if file_is_stable(f):
                    SEEN.add(f)
                    run_worker(f)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
