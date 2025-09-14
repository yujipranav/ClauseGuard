import subprocess, sys, os, pathlib

base = pathlib.Path(__file__).parent.resolve()

os.makedirs(base / "recordings", exist_ok=True)
os.makedirs(base / "transcripts", exist_ok=True)
os.makedirs(base / "summaries", exist_ok=True)

# use the *current python executable* (the one running the EXE)
python_exec = sys.executable

# launch recorder
subprocess.Popen([python_exec, "src/webcam_recorder.py", "--out-dir", "recordings"])
# launch monitor
subprocess.Popen([python_exec, "src/recordings_monitor.py"])
# launch streamlit (blocking)
subprocess.call(["streamlit", "run", "src/dashboard.py"])
