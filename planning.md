# Planning: Provenance Guard

## 1. Detection Signals

Two distinct, independent signals feed the pipeline:

### Signal 1 — LLM-based semantic classification (Groq, `llama-3.3-70b-versatile`)
- **What it measures:** Holistic semantic and stylistic coherence — does the passage
  read the way a large language model tends to write (hedged claims, balanced
  "on one hand / on the other" structure, generic transitions, low specificity) or
  the way a person writes (concrete idiosyncratic detail, tangents, opinionated
  asides, irregular structure)?
- **Output format:** A JSON object `{"ai_probability": float 0-1, "reasoning": str}`
  parsed from the model's response. `ai_probability` is signal 1's score.
- **What it misses:** It has no access to statistical/structural regularities — a
  model can be fooled by short text with too little signal, by human text that
  happens to be very polished/formal (e.g. academic writing), or by AI text that
  has been manually edited to add idiosyncratic detail. It is also itself a
  model-based judgment, so it can be miscalibrated or inconsistent between
  near-identical inputs.

### Signal 2 — Stylometric heuristics (pure Python, no external libraries)
- **What it measures:** Surface statistical regularity in the text:
  1. **Sentence-length variance ("burstiness")** — human writing mixes short and
     long sentences; AI writing tends toward more uniform sentence length.
  2. **Type-token ratio (vocabulary diversity)** — ratio of unique words to total
     words; low diversity relative to text length suggests repetitive, uniform
     word choice.
  3. **Informal/expressive punctuation density** — rate of `!`, `...`, repeated
     `?`, etc. Human casual writing uses these far more than AI output, which
     tends toward "correct," even punctuation.
  Each metric is normalized to 0-1 and averaged into a single stylometric score
  (0 = reads human, 1 = reads AI).
- **Output format:** `float 0-1` plus a `details` dict with the three raw metrics.
- **What it misses:** It has no notion of meaning or content — it cannot tell that
  a sentence is *nonsensical* or *generic*, only that it is *structurally regular*.
  This means **formal human writing (academic prose, technical reports, legal
  writing)** is a known false-positive risk: it is naturally low-variance,
  measured, and punctuation-light, so it can score "AI-like" on this signal even
  when a human wrote every word. This is exactly why it is never used alone —
  Signal 1's semantic read is expected to pull the combined score back down for
  genuinely human formal writing.

### Combining signals into one confidence score
```
combined_score = 0.6 * llm_score + 0.4 * stylometric_score
```
The LLM signal is weighted higher (0.6) because it reasons over meaning and is
generally the stronger standalone predictor; the stylometric signal (0.4) acts as
a structural check that doesn't depend on network access or model mood, and pulls
the score down when the LLM is overconfident on ambiguous text.

`combined_score` is a single number in `[0, 1]` where 0 = confidently human-style
and 1 = confidently AI-style.

## 2. Uncertainty Representation

We do not report `combined_score` directly as "confidence" — instead we compute
**distance from the undecided midpoint (0.5)**, scaled to `[0, 1]`:

```
confidence = abs(combined_score - 0.5) * 2
```

- `combined_score = 0.50` → `confidence = 0.00` (total toss-up)
- `combined_score = 0.75` or `0.25` → `confidence = 0.50`
- `combined_score = 1.00` or `0.00` → `confidence = 1.00` (maximally confident)

### Thresholds (asymmetric on purpose)

A false positive — telling a real human writer their work is "AI-generated" — is
more damaging to trust on a creative platform than a false negative. So the zone
required to call something **AI** is deliberately narrower/stricter than the zone
required to call something **human**:

| `combined_score` range | `attribution`   | Notes |
|---|---|---|
| `>= 0.70`               | `likely_ai`      | Only the top 30% of the scale counts as confidently AI |
| `<= 0.35`               | `likely_human`   | Bottom 35% counts as confidently human — a wider, more forgiving band |
| `0.35 < score < 0.70`   | `uncertain`      | The widest band — deliberately easy to land in |

`confidence` (the 0-1 number from the formula above) is reported regardless of
category, so a user always sees *how sure* the system is, not just *which side*
it landed on.

## 3. Transparency Label Variants (exact text)

- **High-confidence AI** (`likely_ai`):
  `"Likely AI-generated: Our analysis strongly suggests this text was written by an AI, not a person. Confidence: {pct}%."`
- **High-confidence human** (`likely_human`):
  `"Likely human-written: Our analysis strongly suggests this text was written by a person, not an AI. Confidence: {pct}%."`
- **Uncertain** (`uncertain`):
  `"Uncertain origin: We can't confidently tell whether this was written by a human or an AI. Treat this result as inconclusive. Confidence: {pct}%."`

`{pct}` = `confidence * 100`, rounded to the nearest whole number.

## 4. Appeals Workflow

- **Who:** the original creator (identified by `creator_id`), via `POST /appeal`
  with `{"content_id": ..., "creator_reasoning": "..."}`.
- **What they provide:** free-text reasoning explaining why they believe the
  classification is wrong (e.g. "I'm a non-native English speaker, my formal
  style is genuinely mine").
- **What the system does:**
  1. Looks up the content by `content_id`; 404s if not found.
  2. Sets that content's `status` to `"under_review"`.
  3. Writes a new `audit_log` entry with `event_type: "appeal"`, carrying the
     *original* attribution/confidence/signal scores for context plus the new
     `appeal_reasoning` field and `status: "under_review"`.
  4. Returns a confirmation JSON payload (content_id, new status, timestamp).
- **No automated re-classification** — a human reviewer would look at content
  with `status: "under_review"` in the log/queue and make the call manually.
  What a reviewer would see when opening the queue: every audit-log entry with
  `status: "under_review"`, showing the original signals/label side by side with
  the creator's reasoning.

## 5. Anticipated Edge Cases

1. **Formal, low-variance human writing (academic, legal, technical prose).**
   Naturally uniform sentence length, correct/sparse punctuation, and measured
   vocabulary — the stylometric signal alone will often push this toward "AI-like."
   Mitigation: the LLM signal is weighted higher and reasons over content/meaning,
   and the `likely_ai` threshold is deliberately strict (`>= 0.70`), so this case
   should usually land as `uncertain` rather than a confident false positive — and
   the creator retains the appeal path either way.
2. **Very short submissions (a few sentences or less).**
   Stylometric metrics (sentence-length variance, TTR) are statistically
   meaningless with only 1-2 sentences — a single long or short sentence
   dominates the "variance" calculation. Mitigation: acknowledged as a limitation;
   short text should be expected to land in `uncertain` more often, and the
   combined score leans more on the LLM signal in practice for short inputs.
3. **Lightly-edited AI output (AI draft + human touch-ups).**
   This is a genuine middle case that *should* land as `uncertain` — a system
   that confidently calls this "human" or "AI" is overclaiming. This is a target
   validation case (see Milestone 4 test set), not just a failure mode to avoid.

## Architecture

### Submission flow
```
Creator
  │  POST /submit { text, creator_id }
  ▼
Flask app (app.py)
  │
  ├──► Signal 1: llm_signal(text)          [Groq API call]
  │        → llm_score (0-1), reasoning
  │
  ├──► Signal 2: stylometric_signal(text)  [pure Python]
  │        → stylometric_score (0-1), {variance, ttr, punct}
  │
  ▼
scoring.combine(llm_score, stylometric_score)
  → combined_score
  ▼
scoring.classify(combined_score)
  → attribution ("likely_ai"|"likely_human"|"uncertain"), confidence (0-1)
  ▼
scoring.label_for(attribution, confidence)
  → label text (one of the 3 variants above)
  ▼
storage: create content row (status="classified") + audit_log row
  ▼
JSON response ← { content_id, attribution, confidence, label, signals: {...} }
  ▼
Creator sees the label
```

### Appeal flow
```
Creator
  │  POST /appeal { content_id, creator_reasoning }
  ▼
Flask app (app.py)
  │
  ├──► storage.get_content(content_id)   [404 if missing]
  ▼
storage.set_status(content_id, "under_review")
  ▼
storage.log_event(event_type="appeal", ..., appeal_reasoning, status="under_review")
  ▼
JSON response ← { content_id, status: "under_review", appeal logged: true }
```
Both flows converge on the same `audit_log` table (`GET /log` surfaces both event
types), so a reviewer can see a submission and its later appeal side by side.

## AI Tool Plan

- **M3 (submission endpoint + first signal):** Provide the AI tool the
  "Detection Signals → Signal 1" section above plus the Architecture submission
  diagram. Ask it to generate the Flask app skeleton with a stubbed `POST /submit`
  route, and the `llm_signal(text)` function calling Groq's chat completions API
  with a prompt that returns `{"ai_probability": float, "reasoning": str}`.
  Verify by calling `llm_signal()` directly on 2-3 test strings and checking the
  score direction makes sense before wiring it into the route.
- **M4 (second signal + confidence scoring):** Provide "Detection Signals →
  Signal 2," the "Uncertainty Representation" section (with the exact threshold
  table), and the diagram. Ask for `stylometric_signal(text)` (sentence-length
  variance, TTR, punctuation density → single 0-1 score) and `scoring.combine()`
  / `scoring.classify()`. Verify by checking the generated thresholds against the
  table above line-by-line (AI tools sometimes "round" 0.70/0.35 to 0.7/0.3 or
  invert a comparison) and running the 4-input test set from Milestone 4 to
  confirm scores move in the expected direction.
- **M5 (production layer):** Provide "Transparency Label Variants," "Appeals
  Workflow," and the appeal-flow diagram. Ask for `scoring.label_for()` and the
  `POST /appeal` route plus Flask-Limiter config. Verify by generating all three
  labels directly from `label_for()` at score boundaries (0.34/0.35/0.36,
  0.69/0.70/0.71) and confirming an appeal actually flips `status` to
  `"under_review"` and appears in `GET /log`.
