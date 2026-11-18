# Product and Engineering Principles

## Product outcome

Sarangsho Engine should help a reader answer: What happened? What is known and uncertain? How did the story develop? How do credible sources describe it differently? Why does it matter for civic understanding and exam preparation?

This is an evidence-led learning publication system, not a headline scraper or paraphrase factory.

## Originality

This is a greenfield design. Previous Sarangsho repositories may be consulted for requirements, historical examples, editorial lessons, and migration fixtures. Their code, pipeline sequence, database schema, naming conventions, and assumptions are not the default architecture. Every component must justify its existence against the new requirements.

## Evidence before prose

- Store source observations separately from editorial claims.
- Link every material factual claim to one or more source observations.
- Preserve source URL, publisher, publication time when available, retrieval time, and content fingerprint.
- Distinguish direct evidence, attributed claims, analysis, estimates, and editorial inference.
- Preserve conflicting reports as conflicts; never silently choose or average them.
- Confidence must be explainable through evidence and review state, not a decorative score.

## Rights before collection

- A source registry entry is not permission by itself.
- Model permission separately for discovery, metadata, excerpts, full text, images, translation, storage, and republication.
- Default to deny when permission is unknown, expired, ambiguous, or withdrawn.
- robots.txt is a technical access signal, not a copyright licence.
- Collect only the minimum data permitted and necessary.
- Keep attribution and permission provenance with every stored observation.
- Ambiguity blocks the affected operation and routes it to review.

## Deterministic core

The core pipeline must run without a paid AI service. Prefer deterministic parsing, normalization, clustering, rule-based validation, and human review. Optional language models may be added behind provider-neutral interfaces with cost controls, privacy rules, provenance, and fallback behavior.

## Human accountability

Human review is required for disputed or high-impact claims, sensitive subjects, uncertain translation, rights exceptions, and final publication. Automation may recommend; it must not disguise unresolved decisions as approval.

## Bilingual integrity

English and Bangla are first-class outputs with separate quality checks and review states. A glossary substitution or machine translation is not equivalent to an approved translation.

## Reproducibility

Every run records code and configuration versions, source registry and policy versions, input identifiers and fingerprints, stage outcomes, warnings, failures, approvals, and output hashes. Deterministic stages should produce identical outputs from identical frozen inputs.

## Explicit failure

No silent fallback may create misleading content. Failed or skipped stages are visible. A partial edition cannot pass publication gates unless an explicit policy permits the omission.

## Security and privacy

Collect no personal data unrelated to editorial purpose. Secrets never belong in source control, logs, fixtures, or published artifacts.

## Free-first, not fragile-at-any-cost

Prefer free and open-source tools where they meet reliability and legal requirements. Do not rely on fragile scraping proxies, rotating identities, unapproved APIs, or unlimited GitHub Actions assumptions.

## Verifiable increments

Each milestone has acceptance criteria, tests, migration notes where relevant, and a rollback path. Code existing is not proof that a feature is complete.
