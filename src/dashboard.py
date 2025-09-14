# src/dashboard.py
from pathlib import Path
from typing import List, Dict

import streamlit as st

# Optional: for auto-refresh every 10s
try:
    from streamlit_autorefresh import st_autorefresh  # pip install streamlit-autorefresh
except Exception:
    st_autorefresh = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECORDINGS = PROJECT_ROOT / "recordings"
TRANSCRIPTS = PROJECT_ROOT / "transcripts"
SUMMARIES = PROJECT_ROOT / "summaries"

st.set_page_config(page_title="📼 Recordings Monitor", page_icon="📼", layout="wide")
st.title("📼 Recordings → 🎧 Transcripts → 📝 Summaries")

# Auto-refresh every 10 seconds
colA, colB = st.columns([1, 3])
with colA:
    if st_autorefresh:
        st.caption("🔄 Auto-refresh: every 10s")
        st_autorefresh(interval=10_000, key="refresh-every-10s")
    else:
        st.warning("Auto-refresh helper not installed. Run: `pip install streamlit-autorefresh`")

with colB:
    st.caption(f"📁 Recordings: `{RECORDINGS}`  •  🗒️ Transcripts: `{TRANSCRIPTS}`  •  📝 Summaries: `{SUMMARIES}`")

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")


def list_by_suffix(folder: Path, suffixes: List[str]) -> List[Path]:
    files: List[Path] = []
    if folder.exists():
        for sfx in suffixes:
            files.extend(sorted(folder.glob(f"*{sfx}")))
    return files


def build_index() -> List[Dict]:
    recs = [p for p in RECORDINGS.glob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    txs  = list_by_suffix(TRANSCRIPTS, [".transcript.txt"])
    sums = list_by_suffix(SUMMARIES, [".summary.md"])

    tx_map = {p.stem.replace(".transcript", ""): p for p in txs}
    sm_map = {p.stem.replace(".summary", ""): p for p in sums}

    rows = []
    for v in sorted(recs):
        base = v.stem
        rows.append({
            "video": v,
            "transcript": tx_map.get(base),
            "summary": sm_map.get(base),
        })
    return rows


rows = build_index()

if not rows:
    st.info("Drop files into the `recordings/` folder. The monitor will pick them up.")
else:
    for r in rows:
        with st.container(border=True):
            st.markdown(f"**🎬 {r['video'].name}**")
            vstat = r["video"].stat() if r["video"].exists() else None
            if vstat:
                st.caption(f"Path: `{r['video']}`  •  Size: {vstat.st_size/1_000_000:.2f} MB")

            c1, c2, c3 = st.columns([1.2, 1, 1])
            with c1:
                if r["transcript"] and r["transcript"].exists():
                    st.success("Transcript ready")
                    with open(r["transcript"], "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                    st.expander("Preview transcript", expanded=False).text_area(
                        "Transcript", value=txt[:50_000], height=200
                    )
                    st.download_button(
                        "Download transcript (.txt)",
                        data=txt.encode("utf-8"),
                        file_name=r["transcript"].name,
                        mime="text/plain",
                    )
                else:
                    st.warning("Waiting for transcript…")

            with c2:
                if r["summary"] and r["summary"].exists():
                    st.success("Summary ready")
                    with open(r["summary"], "r", encoding="utf-8", errors="ignore") as f:
                        md = f.read()
                    st.expander("Preview summary", expanded=True).markdown(md)
                    st.download_button(
                        "Download summary (.md)",
                        data=md.encode("utf-8"),
                        file_name=r["summary"].name,
                        mime="text/markdown",
                    )
                else:
                    st.warning("Waiting for summary…")

            with c3:
                meta = (TRANSCRIPTS / f"{r['video'].stem}.meta.json")
                if meta.exists():
                    st.caption("Metadata:")
                    st.code(meta.read_text(encoding="utf-8")[:1500], language="json")
                else:
                    st.caption("No metadata yet.")
