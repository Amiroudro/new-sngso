# Section 7.19: criterion-level scoring and calibration contract

## Purpose

The benchmark content contract requires scoring criteria, but free-text criteria alone do not produce consistent or comparable evaluations. Section 7.19 adds a versioned score-sheet contract with explicit criterion weights, anchored 0–4 judgments, evaluator rationale, and evidence references.

## Scoring rules

- Every score sheet is bound to a benchmark ID, exact SHA-256 artifact fingerprint, item ID, task type, language, evaluator ID, and timezone-aware evaluation timestamp.
- Each criterion has a stable ID, positive weight, integer score from 0 to 4, a written anchor describing the selected level, rationale, and at least one evidence reference.
- Criterion IDs must be unique and weights must sum to 1 within a tolerance of 0.000001.
- Weighted score is computed with decimal arithmetic and rounded to four decimal places. The calculation is deterministic; it is not a judgment of truth.
- A declared critical failure requires a written reason and blocks comparison eligibility even when the weighted score is high. This prevents a severe factual fabrication from being hidden by strong style or fluency scores.
- Scores are comparable only when rubric version, task type, item, language, and scoring conditions match. Do not compare Bangla and English scores as if they were one scale without a separately validated calibration study.

## Calibration and interpretation

Before operational use, evaluators should independently score the same calibration items, discuss disagreements against source evidence, revise ambiguous anchors, and then repeat a blind scoring round. Retain both pre-discussion and post-discussion scores. Report criterion-level distributions and disagreement, not only a single average. A small sample or a high weighted score does not establish inter-rater reliability.

The validator checks contract shape, unique criterion IDs, weight totals, and deterministic aggregation. Section 7.20 adds durable, append-only score records. Scores are accepted only through the authenticated application facade for a recently authenticated, human-classified reviewer whose active language-qualified assignment is accepted. The registry independently verifies the exact immutable artifact fingerprint and item/task binding. Score rows retain provider identity metadata and a content fingerprint; they cannot be updated or deleted. The database does not retain raw session tokens.

The score store does not authenticate people by itself: the configured identity provider and deployment must still verify real sessions, signatures, revocation, issuer/subject mapping, and provider metadata. The registry also does not verify that evidence references prove the rationale, validate rubric quality, or establish inter-rater reliability. Synthetic fixtures only test code paths.

## Production boundary

This section is an evaluation contract, not a production approval. Score sheets must remain tied to immutable benchmark artifacts. A release must separately verify evaluator identity and qualification, source permissions, adjudication requirements, and the operational acceptance checklist in Section 7.18.
