# Provenance Guard

A backend service that classifies submitted text as likely AI-generated, likely
human-written, or uncertain, scores its own confidence, surfaces a plain-language
transparency label, and lets creators appeal a classification. Full design
rationale lives in [`planning.md`](planning.md).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your GROQ_API_KEY into .env
python app.py           # runs on http://localhost:5050
```

## Architecture

```
POST /submit {text, creator_id}
        │
        ▼
  Signal 1: llm_signal()        Signal 2: stylometric_signal()
  (Groq llama-3.3-70b)          (pure Python: sentence-length
  → ai_probability 0-1           variance, type-token ratio,
                                  informal-punctuation density)
        │                              │
        └──────────► combine() ◄───────┘
                 combined = 0.6*llm + 0.4*stylo
                        │
                        ▼
                   classify()
        → attribution (likely_ai / uncertain / likely_human)
        → confidence = |combined - 0.5| * 2
                        │
                        ▼
                   label_for()
        → transparency label text
                        │
                        ▼
   storage: content row + audit_log row (event_type="submission")
                        │
                        ▼
        JSON response {content_id, attribution, confidence, label, signals}


POST /appeal {content_id, creator_reasoning}
        │
        ▼
  storage.file_appeal(): content.status -> "under_review"
        + audit_log row (event_type="appeal", appeal_reasoning, status)
        │
        ▼
        JSON response {content_id, status: "under_review"}
```

`GET /log` returns both submission and appeal audit-log entries, so a reviewer
sees the original classification and any later appeal side by side.

## Detection Signals

**Signal 1 — LLM-based semantic classification (Groq `llama-3.3-70b-versatile`).**
The model is prompted to read the passage holistically and return an
`ai_probability` between 0 and 1. It captures semantic/stylistic coherence —
generic phrasing, hedged balanced arguments, lack of specific personal detail —
the things that read as "AI voice" independent of surface statistics.
**What it misses:** it has no structural/statistical grounding, can be fooled by
short excerpts with too little signal, and is itself a model judgment that can
be miscalibrated or inconsistent run-to-run.

**Signal 2 — Stylometric heuristics (pure Python, no external libraries).**
Three normalized sub-metrics averaged into one score: (1) sentence-length
variance/"burstiness" — human writing mixes short and long sentences, AI writing
tends more uniform; (2) type-token ratio (vocabulary diversity); (3)
informal/expressive punctuation density (`!`, `...`, `??`) — humans use these
far more than AI output. It captures **surface statistical regularity**, with
zero understanding of meaning.
**What it misses:** it cannot tell content is generic or nonsensical, only that
it is structurally regular — so naturally uniform, correctly-punctuated **formal
human writing (academic, legal, technical prose)** scores "AI-like" on this
signal alone even when a human wrote every word. This is a documented, expected
blind spot (see Known Limitations).

These two signals are genuinely independent — one reasons over meaning, the
other over surface statistics — which is why the combination is more
informative than either alone (both are visible individually in every
`/submit` response's `signals` and `signal_details` fields).

## Confidence Scoring & Uncertainty

```
combined_score = 0.6 * llm_score + 0.4 * stylometric_score      (0 = human-like, 1 = AI-like)
confidence     = abs(combined_score - 0.5) * 2                  (0 = total toss-up, 1 = maximal certainty)
```

The LLM signal is weighted higher because it reasons over meaning and is the
stronger standalone predictor; the stylometric signal acts as a structural
check that pulls the score back when the LLM is overconfident on ambiguous text.

**Thresholds** (asymmetric on purpose — see rationale below):

| `combined_score` | `attribution` |
|---|---|
| `>= 0.70` | `likely_ai` |
| `0.35 – 0.70` (exclusive) | `uncertain` |
| `<= 0.35` | `likely_human` |

The band required to call something confidently **AI** (top 30% of the scale)
is narrower than the band required to call something confidently **human**
(bottom 35%). On a creative-writing platform, telling a real person their work
was flagged as AI-generated is more damaging to trust than under-flagging —
so the system is deliberately harder to convince toward `likely_ai`.

### How this was validated

Tested against the 4-input set from the assignment (clearly AI, clearly human,
formal-human borderline, lightly-edited-AI borderline), plus a longer, more
formulaic AI paragraph. Two examples with clearly different confidence, taken
directly from the audit log below:

- **High confidence:** *"In todays rapidly evolving digital landscape, it is
  important to note that businesses must adapt..."* → `llm_score=0.8`,
  `stylometric_score=0.648`, `combined=0.739` → **`likely_ai`, confidence 0.479
  (48%)**. Both signals agreed: the LLM flagged generic corporate phrasing, and
  the stylometric signal independently found very low sentence-length variance
  (`3.84`) — a formulaic, uniform paragraph by both measures.
- **Low confidence:** *"The relationship between monetary policy and asset
  price inflation has been extensively studied..."* → `llm_score=0.7`,
  `stylometric_score=0.415`, `combined=0.586` → **`uncertain`, confidence 0.172
  (17%)**. This is exactly the "formal human writing" blind spot the signals
  were expected to hit: the LLM leaned AI on tone, stylometrics were mixed
  (moderate sentence-length variance), and the system correctly landed on
  "can't tell" instead of confidently guessing.

The casual ramen-review text scored `combined=0.253` → `likely_human`,
confidence 0.493 (49%), and the lightly-edited-AI-about-remote-work text scored
`combined=0.304` → `likely_human`, confidence 0.391 — a genuine miss (the LLM
was itself fooled by the informal tone), which is documented under Known
Limitations rather than hidden.

## Transparency Label

Exact label text returned by the API (`{pct}` = confidence × 100, rounded):

| Variant | Text |
|---|---|
| High-confidence AI | `"Likely AI-generated: Our analysis strongly suggests this text was written by an AI, not a person. Confidence: {pct}%."` |
| High-confidence human | `"Likely human-written: Our analysis strongly suggests this text was written by a person, not an AI. Confidence: {pct}%."` |
| Uncertain | `"Uncertain origin: We can't confidently tell whether this was written by a human or an AI. Treat this result as inconclusive. Confidence: {pct}%."` |

No "classifier output," "logit," or raw-score jargon — a non-technical reader
gets a plain sentence and a percentage. The wording visibly changes between
categories (not just the number): "Likely AI-generated" vs. "Likely
human-written" vs. "Uncertain origin... treat this result as inconclusive."

## Example: `POST /submit` → structured JSON response

```bash
curl -s -X POST http://localhost:5050/submit -H "Content-Type: application/json" -d '{
  "text": "In todays rapidly evolving digital landscape, it is important to note that businesses must adapt to remain competitive. Furthermore, organizations should prioritize innovation in order to meet the needs of their customers. Additionally, it is essential to consider the long-term implications of any strategic decision. Moreover, stakeholders across the organization must collaborate effectively to ensure sustainable growth. In conclusion, businesses that embrace change will be better positioned to succeed in the future.",
  "creator_id": "creator-alice"
}'
```

```json
{
  "content_id": "825a255c-555d-4fa3-81f2-eeff849c4951",
  "creator_id": "creator-alice",
  "timestamp": "2026-07-04T22:45:18.948018+00:00",
  "attribution": "likely_ai",
  "confidence": 0.479,
  "label": "Likely AI-generated: Our analysis strongly suggests this text was written by an AI, not a person. Confidence: 48%.",
  "signals": {
    "llm_score": 0.8,
    "stylometric_score": 0.648,
    "combined_score": 0.739
  },
  "signal_details": {
    "llm": {
      "source": "groq_llama-3.3-70b-versatile",
      "reasoning": "The passage's overly formal tone, repetitive use of transitional phrases, and lack of personal touch or nuance suggest a high likelihood of AI generation."
    },
    "stylometric": {
      "sentence_count": 5,
      "sentence_length_variance": 3.84,
      "uniformity_score": 0.904,
      "type_token_ratio": 0.767,
      "ttr_ai_score": 0.041,
      "informal_punct_density": 0.0,
      "informal_ai_score": 1.0
    }
  },
  "status": "classified"
}
```

## Appeals Workflow

`POST /appeal` with `{content_id, creator_reasoning}`:

```bash
curl -s -X POST http://localhost:5050/appeal -H "Content-Type: application/json" -d '{
  "content_id": "825a255c-555d-4fa3-81f2-eeff849c4951",
  "creator_reasoning": "I wrote this myself for a business communications class that required formal tone and transition words. Im a non-native English speaker and was taught to write this way academically -- this is genuinely my own writing style, not AI output."
}'
```

```json
{
  "content_id": "825a255c-555d-4fa3-81f2-eeff849c4951",
  "status": "under_review",
  "appeal_logged": true,
  "timestamp": "2026-07-04T22:45:50.151707+00:00"
}
```

The content's status is updated to `"under_review"` and the appeal — with the
creator's reasoning — is written to the audit log alongside the original
classification (see log entries `id: 1` and `id: 4` below, same `content_id`).
Automated re-classification is intentionally not implemented — a human reviewer
is expected to read entries with `status: "under_review"` in the log.

## Rate Limiting

`POST /submit` is limited to **5 requests per minute and 50 per day**, keyed by
IP address (`flask_limiter.util.get_remote_address` — there is no auth layer in
this project, so IP is the available key).

**Reasoning:** a real writer submitting their own work does so in bursts of a
few pieces at a time (a poem, a chapter, maybe a couple of revisions) — not
dozens of requests a minute. 5/min comfortably covers that while making a
scripted flood (someone hammering the endpoint to probe the classifier or run
up Groq API costs) hit a wall almost immediately. 50/day is generous enough for
a prolific creator or a platform doing a small backlog batch-check, while still
bounding the worst-case cost/load a single IP can generate in a day.

**Evidence** — 12 rapid requests against a 5/min limit (3 of the 5 were already
consumed earlier in the same minute by the demo submissions above, so only 2
more succeed before the 429s start):

```
request 1 -> 200
request 2 -> 200
request 3 -> 429
request 4 -> 429
request 5 -> 429
request 6 -> 429
request 7 -> 429
request 8 -> 429
request 9 -> 429
request 10 -> 429
request 11 -> 429
request 12 -> 429
```

## Audit Log

`GET /log` returns structured JSON entries (SQLite-backed, `storage.py`). Every
entry carries `timestamp`, `content_id`, `attribution`, `confidence`, both
individual signal scores, the label, and status; appeal entries additionally
carry `appeal_reasoning`. Sample — the first three submissions above plus the
appeal on entry `id: 1`, in order:

```json
{
  "entries": [
    {
      "id": 1,
      "event_type": "submission",
      "content_id": "825a255c-555d-4fa3-81f2-eeff849c4951",
      "creator_id": "creator-alice",
      "timestamp": "2026-07-04T22:45:18.948018+00:00",
      "attribution": "likely_ai",
      "confidence": 0.479,
      "llm_score": 0.8,
      "stylometric_score": 0.648365296803653,
      "label": "Likely AI-generated: Our analysis strongly suggests this text was written by an AI, not a person. Confidence: 48%.",
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 2,
      "event_type": "submission",
      "content_id": "d65b4b2f-af09-4ece-979e-43909ead1b28",
      "creator_id": "creator-bob",
      "timestamp": "2026-07-04T22:45:35.826193+00:00",
      "attribution": "likely_human",
      "confidence": 0.493,
      "llm_score": 0.2,
      "stylometric_score": 0.3333333333333333,
      "label": "Likely human-written: Our analysis strongly suggests this text was written by a person, not an AI. Confidence: 49%.",
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 3,
      "event_type": "submission",
      "content_id": "1ed87af5-f758-4773-b527-b4cb84a38a8a",
      "creator_id": "creator-carla",
      "timestamp": "2026-07-04T22:45:36.420058+00:00",
      "attribution": "uncertain",
      "confidence": 0.172,
      "llm_score": 0.7,
      "stylometric_score": 0.4145833333333333,
      "label": "Uncertain origin: We can't confidently tell whether this was written by a human or an AI. Treat this result as inconclusive. Confidence: 17%.",
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 4,
      "event_type": "appeal",
      "content_id": "825a255c-555d-4fa3-81f2-eeff849c4951",
      "creator_id": "creator-alice",
      "timestamp": "2026-07-04T22:45:50.151707+00:00",
      "attribution": "likely_ai",
      "confidence": 0.479,
      "llm_score": 0.8,
      "stylometric_score": 0.648365296803653,
      "label": "Likely AI-generated: Our analysis strongly suggests this text was written by an AI, not a person. Confidence: 48%.",
      "status": "under_review",
      "appeal_reasoning": "I wrote this myself for a business communications class that required formal tone and transition words. I'm a non-native English speaker and was taught to write this way academically -- this is genuinely my own writing style, not AI output."
    }
  ]
}
```

Entry `id: 4` is the appeal on the exact same `content_id` as entry `id: 1` —
the original classification and the appeal are both visible, side by side.

## Known Limitations

**Very short submissions (a sentence or two) are unreliable for the
stylometric signal specifically.** Sentence-length variance needs multiple
sentences to mean anything, and type-token ratio is trivially high for any
short passage regardless of who wrote it (nearly every unique word is used
once). In our own rate-limit test text — a single 12-word sentence — the
stylometric score pinned at its uniform-text extreme (`uniformity_score: 1.0,
type_token_ratio: 1.0`) not because it looked like AI writing but because
there wasn't enough text for the metric to discriminate at all. The system
still produced a reasonable `uncertain` result there because the LLM signal
carried the weight, but a stylometrics-only system would be actively misled
by short text. **Formal/academic human writing** is the second, documented
blind spot — see the "monetary policy" example above, which is exactly why it
lands as `uncertain` rather than a confident false positive.

## Spec Reflection

The planning.md thresholds (0.70 / 0.35) and signal weights (0.6 LLM / 0.4
stylometric) were followed exactly as written — deciding the label wording and
threshold boundaries *before* writing `scoring.py` made that module close to a
direct transcription of the spec, which is the value of writing the plan first.

Where implementation diverged: the plan didn't anticipate that type-token ratio
would be almost useless as a discriminator for the ~40-60 word passages used in
testing (TTR sits around 0.77-0.9 for nearly all short texts, AI or human alike,
so it contributes little beyond noise at this length). Rather than rewrite the
formula, this was left in place and documented honestly as a limitation — real
detection systems have to work with short excerpts, so pretending the metric is
more powerful than it is would be worse than exposing the gap. The plan also
initially treated the stylometric score as a strong standalone signal; in
practice the LLM signal ended up doing most of the discriminating work, which
is reflected in the 0.6/0.4 weighting rather than an even split.

## AI Usage

1. **Directed Claude to generate the Flask app skeleton and `llm_signal()`
   function** from the "Detection Signals → Signal 1" and "Architecture"
   sections of `planning.md`. The first draft returned `ai_probability` as a
   plain float parsed with `json.loads()` on the raw model output; I overrode
   this to add a regex extraction step (`re.search(r"\{.*\}", raw, re.DOTALL)`)
   after testing showed Groq sometimes prefixes its JSON with a sentence of
   preamble text despite the prompt instructing "respond with ONLY a JSON
   object" — the naive `json.loads()` call threw on that output.
2. **Directed Claude to generate `stylometric_signal()`** from the signal
   description in planning.md (sentence-length variance, TTR, punctuation
   density → single score). The first draft normalized type-token ratio by
   dividing by a cap of `0.8` in a way that saturated to the same extreme value
   for every test passage under ~100 words (all of them naturally have
   TTR > 0.8). I decided to keep the capped formula rather than "fix" it with a
   more forgiving cap, and instead documented the real limitation in the README
   — smoothing it over would have hidden a genuine blind spot rather than
   solved it.

## Testing Notes

Ran the app on `python 3.13`, `flask>=3.0`, `flask-limiter>=3.5`, `groq>=0.15`,
with a real `GROQ_API_KEY` in `.env` (git-ignored, never committed). All curl
transcripts in this README were captured directly from a running local server
on port 5050 in a single session (`provenance.db` is git-ignored and rebuilt
fresh — delete it and restart `python app.py` to reproduce this exact sequence).
