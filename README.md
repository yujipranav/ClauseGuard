# Snapdragon Hackathon — Recording & Transcription Pipeline 🎥➡️📝➡️📊

This project is a **privacy-focused meeting recorder and summarizer** built for the Snapdragon Hackathon.  
It automatically records your **screen**, transcribes audio with **Whisper**, and generates **executive summaries** using an LLM backend.

---

## 🚀 Features
- 🎬 **Screen Recorder** with **eye-tracking** toggle:
  - Look **away** → recording starts
  - Look **back** → recording stops
- 🎙️ Audio transcription with [faster-whisper](https://github.com/guillaumekln/faster-whisper)
- ✨ AI-generated summaries of transcripts
- 📂 Organized outputs:
  - `recordings/` → raw `.mp4` files
  - `transcripts/` → plain-text transcripts
  - `summaries/` → Markdown summaries
- 🔄 Streamlit dashboard for live summaries & transcript viewing

---

## 🛠️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/<your-username>/Snapdragon_Hackathon.git
cd Snapdragon_Hackathon
