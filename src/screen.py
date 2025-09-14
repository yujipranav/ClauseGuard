import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Optional, Tuple

import cv2

# Optional: mediapipe for face landmarks; fallback is OpenCV face presence
try:
    import mediapipe as mp  # type: ignore
    _HAVE_MP = True
except Exception:
    _HAVE_MP = False


# -------------------- Config / FFmpeg Resolution --------------------

def load_cfg(path: str = "config.yaml") -> dict:
    try:
        import yaml
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}

def resolve_ffmpeg(config: dict) -> str:
    # 1) config override
    p = (config or {}).get("ffmpeg_path")
    if p and os.path.isfile(p):
        return p
    # 2) env vars
    for env_var in ("IMAGEIO_FFMPEG_EXE", "FFMPEG_BIN"):
        p = os.environ.get(env_var)
        if p and os.path.isfile(p):
            return p
    # 3) PATH
    p = shutil.which("ffmpeg")
    if p:
        return p
    # 4) common locations
    if os.name == "nt":
        candidates = [
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        ]
    else:
        candidates = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""


# -------------------- Device Helpers (Windows) --------------------

def list_dshow_devices(ffmpeg_exe: str) -> Tuple[list, list]:
    if os.name != "nt":
        return [], []
    cmd = [ffmpeg_exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stderr
    video_devices = re.findall(r'\[dshow .*?\]\s+"([^"]+)"\s*\(video\)', out)
    audio_devices = re.findall(r'\[dshow .*?\]\s+"([^"]+)"\s*\(audio\)', out)
    return video_devices, audio_devices

def pick_audio_device(ffmpeg_exe: str) -> Tuple[str, str]:
    if os.name != "nt":
        return "", ""
    _, adevs = list_dshow_devices(ffmpeg_exe)
    preferred = ["virtual-audio-capturer", "Stereo Mix", "What U Hear", "Loopback", "Speakers (Loopback)"]
    chosen = None
    for name in adevs:
        if any(p.lower() in name.lower() for p in preferred):
            chosen = name
            break
    if chosen:
        return f'-f dshow -i audio="{chosen}"', "dshow"
    # WASAPI default (may still work)
    return "-f wasapi -i default", "wasapi"


# -------------------- Face Presence Detection --------------------

def face_in_frame(frame, face_mesh) -> bool:
    """
    True if a face is detected in the webcam frame.
    Mediapipe: presence of landmarks.
    OpenCV fallback: Haar cascade frontal face.
    """
    if _HAVE_MP and face_mesh is not None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = face_mesh.process(rgb)
        return bool(res.multi_face_landmarks)
    else:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = face_cascade.detectMultiScale(gray, 1.2, 5, minSize=(80, 80))
        return len(faces) > 0


# -------------------- FFmpeg Recording (with auto-fallback) --------------------

def _build_ffmpeg_cmd(
    ffmpeg_exe: str,
    out_path: str,
    fps: int,
    crf: int,
    preset: str,
    region: Optional[str],
    audio_input: str,
    video_mode: str,
):
    # Video capture
    if video_mode == "ddagrab":
        vcap = ["-f", "ddagrab", "-i", "desktop"]
        # NOTE: ddagrab region would require -offset_x/-offset_y/-video_size on newer builds.
    else:
        # gdigrab
        if region:
            x, y, w, h = region.split(",")
            vcap = ["-f", "gdigrab", "-offset_x", x, "-offset_y", y, "-video_size", f"{w}x{h}", "-i", "desktop"]
        else:
            vcap = ["-f", "gdigrab", "-i", "desktop"]

    audio_args = audio_input.split() if audio_input else []

    cmd = [
        ffmpeg_exe, "-y",
        *vcap,
        *audio_args,
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-movflags", "+faststart",
    ]
    if audio_args:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [out_path]
    return cmd


def start_ffmpeg_record(
    ffmpeg_exe: str,
    out_path: str,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    region: Optional[str] = None,
    audio_input: str = "",
    video_mode: str = "gdigrab",
    debug_log: Optional[str] = None,
) -> subprocess.Popen:
    """
    Try multiple start strategies automatically:
      1) requested backend + audio
      2) requested backend (video-only)
      3) switched backend + audio
      4) switched backend (video-only)
    Returns a live Popen or raises RuntimeError.
    """
    if os.name != "nt":
        raise RuntimeError("This example targets Windows. Adjust flags for macOS/Linux if needed.")

    attempts = []
    # requested backend first
    attempts.append((video_mode, True))
    attempts.append((video_mode, False))
    # then the other backend
    other = "ddagrab" if video_mode == "gdigrab" else "gdigrab"
    attempts.append((other, True))
    attempts.append((other, False))

    last_err = None
    for idx, (backend, with_audio) in enumerate(attempts, start=1):
        log_path = None
        if debug_log:
            base, _ = os.path.splitext(debug_log)
            log_path = f"{base}.try{idx}_{backend}_{'audio' if with_audio else 'noaudio'}.log"

        cmd = _build_ffmpeg_cmd(
            ffmpeg_exe=ffmpeg_exe,
            out_path=out_path,
            fps=fps,
            crf=crf,
            preset=preset,
            region=region,
            audio_input=(audio_input if with_audio else ""),
            video_mode=backend,
        )

        stderr_target = open(log_path, "w", encoding="utf-8", buffering=1) if log_path else subprocess.DEVNULL
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_target)
        # give ffmpeg a moment to error out if it will
        time.sleep(0.8)
        if proc.poll() is None:
            print(f"[OK] ffmpeg started via {backend} ({'with audio' if with_audio else 'video-only'})")
            if log_path:
                print(f"[debug] ffmpeg log: {log_path}")
            return proc
        else:
            if log_path:
                print(f"[fail] Attempt {idx} ({backend}, {'audio' if with_audio else 'noaudio'}) — see {log_path}")
            last_err = RuntimeError(f"ffmpeg exited immediately on attempt {idx} ({backend}, {'audio' if with_audio else 'noaudio'})")

    raise last_err or RuntimeError("ffmpeg failed to start in all attempts.")


def stop_ffmpeg_record(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc and proc.poll() is None:
        try:
            if proc.stdin:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            t0 = time.time()
            while proc.poll() is None and (time.time() - t0) < timeout:
                time.sleep(0.05)
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()


# -------------------- Main --------------------

def main():
    parser = argparse.ArgumentParser(
        description="Auto screen+audio recorder: START when face is OUT, STOP when face returns (IN)."
    )
    parser.add_argument("--cam", type=int, default=0, help="Webcam index")
    parser.add_argument("--min-away-ms", type=int, default=800, help="Min continuous face-ABSENT time to START (ms)")
    parser.add_argument("--return-ms", type=int, default=1000, help="Min continuous face-PRESENT time to STOP (ms)")
    parser.add_argument("--fps", type=int, default=30, help="Recording FPS")
    parser.add_argument("--crf", type=int, default=23, help="x264 CRF (lower = higher quality)")
    parser.add_argument("--preset", type=str, default="veryfast", help="x264 preset")
    parser.add_argument("--region", type=str, default="", help="Optional region 'x,y,w,h' (for gdigrab)")
    parser.add_argument("--video-mode", type=str, default="gdigrab", choices=["gdigrab", "ddagrab"], help="Windows capture backend")
    parser.add_argument("--out-dir", type=str, default=".", help="Directory to save recordings")
    parser.add_argument("--no-audio", action="store_true", help="Disable system audio capture")
    parser.add_argument("--debug", action="store_true", help="Write ffmpeg stderr to per-attempt .log files")
    args = parser.parse_args()

    cfg = load_cfg()
    ffmpeg_exe = resolve_ffmpeg(cfg)
    if not ffmpeg_exe:
        print("ERROR: ffmpeg not found. Set ffmpeg_path in config.yaml or ensure it's on PATH.")
        sys.exit(1)
    print(f"[INFO] Using ffmpeg: {ffmpeg_exe}")

    # Audio device selection (can disable)
    audio_flag = ""
    if not args.no_audio:
        audio_flag, audio_backend = pick_audio_device(ffmpeg_exe)
        if audio_flag:
            print(f"[INFO] Using system audio via {audio_backend}")
        else:
            print("[WARN] No system audio device detected; proceeding with video-only.")

    # Face detector
    if _HAVE_MP:
        mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.5, min_tracking_confidence=0.5
        )
        face_mesh = mp_face_mesh
        print("[INFO] MediaPipe face mesh enabled.")
    else:
        face_mesh = None
        print("[INFO] MediaPipe not installed; using OpenCV face presence.")

    # Webcam
    cap = cv2.VideoCapture(args.cam, cv2.CAP_DSHOW if os.name == "nt" else 0)
    if not cap.isOpened():
        print(f"ERROR: cannot open webcam index {args.cam}")
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(args.out_dir, f"recording_{ts}.mp4")
    log_path = os.path.splitext(out_path)[0] + ".log" if args.debug else None

    print("🧿 Recorder will START when your face is OUT of frame.")
    print("🙂 Recorder will STOP when your face RETURNS (IN frame).")
    print("⌨️  Hotkeys: S=start, E=end/save, Q=quit")

    away_start = None
    back_start = None
    recording = False
    ff_proc: Optional[subprocess.Popen] = None

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("ERROR: webcam frame grab failed.")
                break

            in_frame = face_in_frame(frame, face_mesh)
            now = time.time()

            # Hotkeys
            key = cv2.waitKey(1) & 0xFF
            if key in (ord('s'), ord('S')):
                # Force-start regardless of face state
                in_frame = False
                away_start = now - (args.min_away_ms / 1000.0)
            if key in (ord('e'), ord('E')):
                if recording:
                    print("🛑 Manual stop. Finalizing recording…")
                    stop_ffmpeg_record(ff_proc)
                    recording = False
                    print(f"Saved: {out_path}")
                    break
            if key in (ord('q'), ord('Q')):
                if recording:
                    print("🛑 Quit. Finalizing recording…")
                    stop_ffmpeg_record(ff_proc)
                    print(f"Saved: {out_path}")
                break

            # State machine: START when face OUT for min-away-ms; STOP when face IN for return-ms
            if not in_frame:
                back_start = None
                if not recording:
                    if away_start is None:
                        away_start = now
                    elif (now - away_start) * 1000 >= args.min_away_ms:
                        try:
                            ff_proc = start_ffmpeg_record(
                                ffmpeg_exe=ffmpeg_exe,
                                out_path=out_path,
                                fps=args.fps,
                                crf=args.crf,
                                preset=args.preset,
                                region=args.region if args.region else None,
                                audio_input=audio_flag,
                                video_mode=args.video_mode,
                                debug_log=log_path,
                            )
                            recording = True
                            print(f"🎬 Recording started -> {out_path}")
                        except Exception as e:
                            print(f"ERROR: failed to start ffmpeg: {e}")
                            break
            else:
                away_start = None
                if recording:
                    if back_start is None:
                        back_start = now
                    elif (now - back_start) * 1000 >= args.return_ms:
                        print("🛑 Face returned (IN). Stopping recording…")
                        stop_ffmpeg_record(ff_proc)
                        recording = False
                        print(f"Saved: {out_path}")
                        break

            # Overlay UI
            vis = frame.copy()
            status = "REC" if recording else "IDLE"
            color = (0, 0, 255) if recording else (128, 128, 128)
            cv2.putText(vis, f"Face: {'IN' if in_frame else 'OUT'}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                        (0, 255, 0) if in_frame else (0, 0, 255), 2)
            cv2.rectangle(vis, (10, 50), (90, 90), color, -1)
            cv2.putText(vis, status, (15, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.putText(vis, "S=start  E=end  Q=quit", (10, vis.shape[0]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
            cv2.imshow("Face Presence Recorder", vis)

    finally:
        cap.release()
        cv2.destroyAllWindows()
        if recording and ff_proc:
            stop_ffmpeg_record(ff_proc)
            print(f"Saved (on cleanup): {out_path}")

    print("Done.")
    if args.debug and log_path and os.path.exists(log_path):
        print(f"[debug] ffmpeg logs saved alongside video (per-attempt).")
    print("Load the saved MP4 into video.py to transcribe & summarize.")


if __name__ == "__main__":
    main()
