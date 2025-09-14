#!/bin/bash
# run_all.sh
# Launch screen recorder, recordings monitor, and Streamlit dashboard together.

# Activate venv
source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate

# Ensure folders exist
mkdir -p recordings transcripts summaries

# Launch the screen+audio recorder (press Ctrl+C in its terminal to stop recording)
echo "[start] Screen recorder → saves MP4s into ./recordings/"
gnome-terminal -- bash -c "python src/webcam_recorder.py --out-dir recordings; exec bash" 2>/dev/null \
|| start cmd /k "python src\webcam_recorder.py --out-dir recordings"

# Launch the recordings monitor (auto-transcribe + summarize)
echo "[start] Recordings monitor → watches ./recordings/, writes transcripts+summaries/"
gnome-terminal -- bash -c "python src/recordings_monitor.py; exec bash" 2>/dev/null \
|| start cmd /k "python src\recordings_monitor.py"

# Launch Streamlit dashboard (auto-refresh)
echo "[start] Streamlit dashboard → http://localhost:8501/"
streamlit run src/dashboard.py
