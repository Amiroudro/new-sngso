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

The current validator checks contract shape, unique criterion IDs, weight totals, and deterministic aggregation. It does not authenticate evaluators, verify evidence references, establish rubric validity, calculate a statistically defensible reliability coefficient, or persist score sheets in the registry. Those require later integration and real independent human data. Synthetic fixtures only test code paths.

## Production boundary

This section is an evaluation contract, not a production approval. Score sheets must remain tied to immutable benchmark artifacts. A release must separately verify evaluator identity and qualification, source permissions, adjudication requirements, and the operational acceptance checklist in Section 7.18.
