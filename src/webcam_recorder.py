# src/webcam_recorder.py
# Screen recorder with background eye-detection:
# - Look AWAY (>1.5s) → start screen recording
# - Look BACK → stop recording
# Webcam runs silently (no popup window)

import argparse
import cv2
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

def ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def which_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if p:
        return p
    if os.name == "nt":
        for c in [
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        ]:
            if os.path.isfile(c):
                return c
    else:
        for c in [
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/usr/bin/ffmpeg",
        ]:
            if os.path.isfile(c):
                return c
    return ""

def build_cmd(ffmpeg, fps, out_path, crf, preset):
    if sys.platform.startswith("win"):
        screen_input = ["-f", "gdigrab", "-framerate", str(fps), "-i", "desktop"]
    elif sys.platform == "darwin":
        screen_input = ["-f", "avfoundation", "-framerate", str(fps), "-i", "1:none"]
    else:
        display = os.environ.get("DISPLAY", ":0.0")
        screen_input = ["-f", "x11grab", "-framerate", str(fps), "-i", display]

    return [
        ffmpeg, "-y",
        *screen_input,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]

def stop_recording(proc):
    if not proc:
        return
    try:
        proc.stdin.write(b"q")
        proc.stdin.flush()
        proc.wait(timeout=5)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()

def monitor_and_record(ffmpeg, fps, out_dir, crf, preset, eye_cascade):
    cap = cv2.VideoCapture(0)  # webcam for eye detection
    if not cap.isOpened():
        raise RuntimeError("Could not open webcam")

    recording = False
    proc = None
    last_seen = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        eyes = eye_cascade.detectMultiScale(gray, 1.3, 5)
        now = time.time()

        if len(eyes) > 0:
            last_seen = now
            if recording:
                print("[rec] Eyes detected → stopping recording")
                stop_recording(proc)
                recording = False
                proc = None
        else:
            if not recording and now - last_seen > 1.5:
                outfile = out_dir / f"recording_{ts()}.mp4"
                cmd = build_cmd(ffmpeg, fps, outfile, crf, preset)
                print(f"[rec] No eyes → starting screen recording → {outfile}")
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                recording = True

        # exit safely with Ctrl+C
        if cv2.waitKey(1) & 0xFF == 27:  # ESC key (hidden, but works)
            break

    cap.release()
    if recording and proc:
        print("[rec] Final stop…")
        stop_recording(proc)

def main():
    ap = argparse.ArgumentParser(description="Screen recorder controlled by eye detection (no popup)")
    ap.add_argument("--out-dir", type=Path, default=Path("recordings"))
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--preset", type=str, default="veryfast")
    args = ap.parse_args()

    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        print("[rec] ERROR: ffmpeg not found on PATH.", file=sys.stderr)
        return 1

    out_dir = args.out_dir.resolve()
    ensure_dir(out_dir)

    eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")

    print("[rec] Eye detection active (no popup).")
    print("  - Look AWAY (>1.5s) → start screen recording")
    print("  - Look BACK → stop recording")
    print("  - Stop the script with Ctrl+C")

    monitor_and_record(ffmpeg, args.fps, out_dir, args.crf, args.preset, eye_cascade)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
