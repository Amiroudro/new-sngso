# Section 7: editorial regression benchmarks

## Purpose

The benchmark evaluator compares a learning object with a small, explicit golden fixture. The first synthetic Bangla and English cases exercise:

- omission of a material date or attributed qualification;
- changes to expected sentence text and its claim links;
- loss of explicit attribution;
- introduction of a listed unsupported assertion; and
- language-variant mismatch.

The evaluator checks exact expected text after Unicode NFC and whitespace normalisation, ordered sentence segments, segment roles, attribution, and claim IDs. It returns named pass/fail checks and does not calculate a score.

## Limits

These first cases are synthetic regression fixtures. They are not human-reviewed language benchmarks, do not establish factual truth, and cannot judge general semantic equivalence, natural Bangla, translation quality, tone, completeness beyond the encoded expectations, or whether an omitted fact is material. The prohibited-phrase check is a narrow fixture guard, not a fact-checker.

A future benchmark set must be reviewed by qualified Bangla and English editors against approved source material. Review identity, review date, source rights, and adjudication notes must be recorded before those cases are labelled human-approved. Do not use these synthetic cases to claim production language quality.

## Use

The evaluate_editorial_benchmark helper is a deterministic, read-only comparison. It is suitable for regression tests and offline evaluation. It does not access the registry, alter audit history, generate content, translate, or approve a learning object.

The registry learning_object_quality_report remains a separate workflow/provenance gate. A passing benchmark fixture does not replace claim evidence, editorial review, or glossary approval.
