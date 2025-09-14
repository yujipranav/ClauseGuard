from flask import Flask, jsonify, request
import time, collections

app = Flask(__name__)

# Rolling transcript pieces from the last ~5 minutes (60 x 5s chunks)
ring = collections.deque(maxlen=60)

@app.get("/health")
def health():
    return jsonify(ok=True)

@app.post("/asr")
def asr():
    """
    Receives ~5s audio chunks (webm/opus) from the continuous recorder.
    For now we stub transcript text and append to the rolling window.
    """
    f = request.files.get("audio")
    if not f:
        return jsonify(ok=False, error="no file"), 400

    # Optional: inspect bytes for debugging
    data = f.read() or b""
    print(f"[asr] chunk {len(data)/1024:.1f} KB, content-type={f.mimetype}")

    # TODO: decode + ASR here, append real text
    piece = "Spoke about timeline and owners."
    ring.append((time.time(), piece))

    return jsonify(ok=True, text=piece)

@app.post("/segment")
def segment():
    """
    Receives a single merged 'missed segment' (webm/opus) when the user returns.
    Return a short summary of just that segment.
    """
    f = request.files.get("audio")
    if not f:
        return jsonify(ok=False, error="no file"), 400

    data = f.read() or b""
    print(f"[segment] received {len(data)/1024:.1f} KB, content-type={f.mimetype}")

    # TODO: decode -> ASR -> summarize this segment only
    summary = (
        "• Timeline moved to Oct 5\n"
        "• Owners assigned for blockers\n"
        "• Action: share draft by Tue"
    )
    return jsonify(ok=True, summary=summary)

@app.get("/recap")
def recap():
    """
    Returns a quick recap (bullets) from the last 5 minutes of rolling chunks.
    """
    cutoff = time.time() - 5 * 60
    recent = [t for ts, t in ring if ts >= cutoff]
    fallback = "Rollout timeline agreed, owners assigned, demo prep in progress."
    text = " ".join(recent) if recent else fallback

    # TODO: summarize 'text' with your on-device summarizer
    bullets = [
        "Timeline agreed for next sprint.",
        "Owners assigned to integration blockers.",
        "Demo checklist defined with due dates."
    ]
    return jsonify(ok=True, recap="\n".join(bullets))

if __name__ == "__main__":
    # Debug server; fine for local hackathon demo
    app.run(host="127.0.0.1", port=5000)



