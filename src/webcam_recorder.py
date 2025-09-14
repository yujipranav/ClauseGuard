# src/webcam_recorder.py
# Screen + mic recorder with eye detection toggle (Windows DirectShow)
import argparse
import cv2
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# --- Helpers ---
def ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def which_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in [
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    ]:
        if os.path.isfile(c):
            return c
    return ""

def build_cmd(ffmpeg, fps, audio_device, out_path, crf, preset):
    return [
        ffmpeg, "-y",
        "-f", "gdigrab", "-framerate", str(fps), "-i", "desktop",    # Screen
        "-f", "dshow", "-i", f"audio={audio_device}",                # Mic audio
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]

# --- Eye detection loop ---
def monitor_and_record(ffmpeg, fps, audio_device, out_dir, crf, preset, eye_cascade):
    cap = cv2.VideoCapture(0)  # use webcam for eye detection
    if not cap.isOpened():
        raise RuntimeError("Could not open webcam for eye detection")

    recording = False
    proc = None
    last_eye_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        eyes = eye_cascade.detectMultiScale(gray, 1.3, 5)

        now = time.time()
        if len(eyes) > 0:
            last_eye_time = now
            if not recording:
                # Start recording
                outfile = out_dir / f"recording_{ts()}.mp4"
                cmd = build_cmd(ffmpeg, fps, audio_device, outfile, crf, preset)
                print(f"[rec] Starting recording → {outfile}")
                proc = subprocess.Popen(cmd)
                recording = True
        else:
            if recording and now - last_eye_time > 1.5:  # no eyes for >1.5s
                # Stop recording
                print("[rec] Stopping recording (eyes lost)")
                if os.name == "nt":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                recording = False
                proc = None

        # Show preview with eye boxes
        for (x, y, w, h) in eyes:
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)
        cv2.imshow("Eye Monitor (press q to quit)", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    # Cleanup
    cap.release()
    cv2.destroyAllWindows()
    if recording and proc:
        print("[rec] Stopping final recording…")
        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()

# --- Main ---
def main():
    ap = argparse.ArgumentParser(description="Record SCREEN + MIC with eye-detection auto toggle")
    ap.add_argument("--out-dir", type=Path, default=Path("recordings"))
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--preset", type=str, default="veryfast")
    ap.add_argument("--audio-device", type=str, required=True,
                    help="DirectShow audio device name (e.g. 'Microphone Array (Qualcomm...)')")
    args = ap.parse_args()

    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        print("[rec] ERROR: ffmpeg not found on PATH.", file=sys.stderr)
        return 1

    out_dir = args.out_dir.resolve()
    ensure_dir(out_dir)

    # Load Haar cascade for eye detection
    eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")

    print("[rec] Eye-detection active. Look at the camera to start, look away >1.5s to stop.")
    print("[rec] Press 'q' in the preview window to quit.")
    monitor_and_record(ffmpeg, args.fps, args.audio_device, out_dir, args.crf, args.preset, eye_cascade)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
