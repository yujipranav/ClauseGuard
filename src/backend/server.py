from flask import Flask, jsonify

app = Flask(__name__)

@app.get("/recap")
def recap():
    # Dummy response – later replace with Whisper/summary
    return jsonify({
        "recap": "Decisions: Move milestone to Oct 5. Owner: Pranav. Next: share draft by Tue."
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)

