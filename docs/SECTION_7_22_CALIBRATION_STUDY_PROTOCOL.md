# Section 7.22: benchmark calibration study protocol

## Purpose
Section 7.21 reports descriptive disagreement for matched persisted scores. It does not estimate reliability. This section adds a prespecified protocol contract so a future study can be designed and reviewed before outcome data are examined.

## Three isolated item partitions
Every item ID must belong to exactly one set:
- **Calibration:** reviewer instruction, anchor discussion, and practice. These items and answer keys must not appear in blind evaluation.
- **Blind evaluation:** independently scored without access to peer scores, adjudication outcomes, or calibration answer keys during the blind round.
- **Adjudication:** reserved for resolving disagreements under the existing adjudication process. It is excluded from the blind evaluation sample.

The validator rejects cross-partition item reuse, duplicate blind assignment rows, unassigned blind items, and assignments outside declared benchmark language/task strata.

## Sampling and reviewer preparation
The protocol records the sampling frame, selection method, inclusion and exclusion criteria, and rationale for the intended sample. It deliberately does not prescribe a universal minimum sample size. Adequacy depends on the estimand, scale, number and structure of raters/items, expected prevalence or score distribution, desired precision, and planned statistic.

Each planned reviewer must be declared human, have completed documented training, and have declared language and task qualifications matching every assigned blind item. These fields are protocol declarations, not independent proof of identity, competence, or training. Actual reviewer identity and assignment authorization remain subject to the trusted identity and assignment controls in earlier sections.

## Analysis plan
Before data collection, the study must name the measurement scale, proposed statistic, rationale, unit of analysis, confidence-interval approach, missing-data handling, independence assumptions, and an independent methodological reviewer.

The statistic must fit the design. Nominal labels, ordinal rubric ratings, and approximately interval scores do not automatically justify the same coefficient. The plan must account for whether raters and items are fixed or sampled, whether every rater scores every item, clustering, score distribution, and missing or incomplete ratings. Depending on the design, a defensible analysis may require a suitable chance-corrected agreement coefficient, an ordinal or nominal form of Krippendorff's alpha, or an intraclass correlation model. The protocol must justify its choice rather than select a familiar statistic by habit.

Report uncertainty intervals and their assumptions. Explain missingness, exclusions, and sensitivity analyses. A point estimate without design context and uncertainty is insufficient evidence of reliability. An independent methods reviewer should approve the analysis plan before the blind round.

## Immutable identity and interpretation boundary
The validator returns a canonical protocol fingerprint over the full submitted protocol. A changed design must be treated as a new protocol version and fingerprint; it must not be silently substituted after data collection begins. This validator does not itself persist protocol versions or enforce a production database freeze. A deployment must store approved protocol versions in an append-only, access-controlled study registry before operational use.

The result is always protocol_only and production_ready=false. It does not claim that sampling occurred, training was completed, scores were collected, sample size is adequate, or reliability has been established. No benchmark is promoted to production by the existence of a protocol or a future study report. Production acceptance still requires the operational security gates in Section 7.18, genuine independent human evidence, source permissions, and a separate explicit approval process.

Synthetic fixtures test validation behavior only. They are not human calibration data.
