"""Confidence scoring and transparency labels for Provenance Guard.

Thresholds and formulas here must match planning.md exactly -- see
"Uncertainty Representation" and "Transparency Label Variants".
"""

LLM_WEIGHT = 0.6
STYLOMETRIC_WEIGHT = 0.4

AI_THRESHOLD = 0.70
HUMAN_THRESHOLD = 0.35


def combine(llm_score, stylometric_score):
    """Weighted average of the two signal scores -> combined_score in [0,1]."""
    return LLM_WEIGHT * llm_score + STYLOMETRIC_WEIGHT * stylometric_score


def classify(combined_score):
    """Map combined_score to (attribution, confidence).

    confidence = distance from the undecided midpoint (0.5), scaled to [0,1].
    """
    confidence = abs(combined_score - 0.5) * 2
    confidence = round(min(1.0, confidence), 3)

    if combined_score >= AI_THRESHOLD:
        attribution = "likely_ai"
    elif combined_score <= HUMAN_THRESHOLD:
        attribution = "likely_human"
    else:
        attribution = "uncertain"

    return attribution, confidence


_LABEL_TEMPLATES = {
    "likely_ai": (
        "Likely AI-generated: Our analysis strongly suggests this text was "
        "written by an AI, not a person. Confidence: {pct}%."
    ),
    "likely_human": (
        "Likely human-written: Our analysis strongly suggests this text was "
        "written by a person, not an AI. Confidence: {pct}%."
    ),
    "uncertain": (
        "Uncertain origin: We can't confidently tell whether this was written "
        "by a human or an AI. Treat this result as inconclusive. Confidence: {pct}%."
    ),
}


def label_for(attribution, confidence):
    """Render the exact transparency label text for the given category."""
    pct = round(confidence * 100)
    return _LABEL_TEMPLATES[attribution].format(pct=pct)
