# Section 7.21: evaluator agreement and calibration intelligence

## Purpose

Two independent score sheets can disagree even when each is structurally valid. This section adds a descriptive report over persisted, matched score sheets so curators and adjudicators can see where evaluators differ before resolving material disagreements.

## Comparison boundary

A report is scoped to one exact immutable artifact, fingerprint, item, task type, and language. It requires at least two distinct evaluator identities and identical criterion IDs and weights. Score sheets containing a declared critical failure are not included in agreement comparison. Only persisted score records with provider provenance and human-classified evaluator principals are accepted by the registry report.

The report exposes:
- number of evaluators and evaluator pairs;
- exact agreement rate per criterion;
- within-one-point agreement rate per criterion;
- mean absolute score difference per criterion;
- a criterion-weighted mean absolute score difference.

Pairwise comparisons are descriptive. With more than two evaluators, all unique evaluator pairs are counted. Rates and differences describe only the score sheets included in that report.

## Blind-review protection

The application facade exposes peer-score comparison only to active curators and adjudicators, not reviewers. Reviewers therefore cannot use this report to copy another evaluator's score during an independent scoring round. Curators and adjudicators should preserve the original score sheets and use the issue-level adjudication ledger to document any resolution.

## Interpretation limits

These outputs are not Krippendorff's alpha, Cohen's kappa, an intraclass correlation coefficient, or any other validated inter-rater reliability statistic. They do not establish evaluator competence, score validity, editorial truth, or population-level reliability. They are not suitable for comparing different items, tasks, languages, artifact versions, or scoring rubrics.

A future reliability study requires a prespecified study design, enough independently sampled items and evaluators, an appropriate statistic for the scale and missing-data pattern, confidence intervals, and independent methodological review. Until then, report descriptive disagreement only. Synthetic test fixtures verify software behavior and do not count as human calibration data.
