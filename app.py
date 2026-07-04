import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import scoring
import signals
import storage

app = Flask(__name__)
storage.init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("5 per minute;50 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")
    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    llm_score, llm_details = signals.llm_signal(text)
    stylo_score, stylo_details = signals.stylometric_signal(text)
    combined = scoring.combine(llm_score, stylo_score)
    attribution, confidence = scoring.classify(combined)
    label = scoring.label_for(attribution, confidence)

    content_id = str(uuid.uuid4())
    signal_details = {"llm": llm_details, "stylometric": stylo_details}
    timestamp = storage.create_content(
        content_id, creator_id, text, attribution, confidence, label,
        llm_score, stylo_score, signal_details,
    )

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {
            "llm_score": round(llm_score, 3),
            "stylometric_score": round(stylo_score, 3),
            "combined_score": round(combined, 3),
        },
        "signal_details": signal_details,
        "status": "classified",
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")
    if not content_id or not creator_reasoning:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    timestamp = storage.file_appeal(content_id, creator_reasoning)
    if timestamp is None:
        return jsonify({"error": f"no content found with id {content_id}"}), 404

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "appeal_logged": True,
        "timestamp": timestamp,
    })


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": storage.get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5050)
