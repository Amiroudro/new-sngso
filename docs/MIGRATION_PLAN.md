# Knowledge Transfer and Migration Plan

## Objective

Use previous Sarangsho work as a source of requirements and lessons without inheriting its implementation. The old private repository and public editions repository remain read-only.

## Preservation rules

- Never edit, delete, rename, or overwrite files in either existing repository.
- Never merge or close their pull requests.
- Never change their schedules, secrets, Pages settings, or deployment configuration.
- Never publish new output to the existing editions repository.
- Never assume an existing publisher record grants collection or republication permission.
- Any future integration or production promotion requires separate explicit approval.

## Transfer categories

### Knowledge to preserve
Product requirements; editorial principles; exam-preparation requirements; publisher research; historical bugs and root causes; lawful edition examples as read-only fixtures; vocabulary curation and taxonomy concepts.

### Logic to evaluate, not automatically copy
Feed discovery; URL normalization; duplicate detection; story clustering; relevance scoring; fact extraction; validation; question generation; revision scheduling; Bangla rendering. Each candidate must pass design review against new contracts, rights rules, tests, maintainability, and measurable quality. Reimplement where the old design is coupled, unsafe, or insufficient.

### Data to migrate selectively
Publisher identities and domains; documented permission evidence; editorial taxonomies; vocabulary with provenance; synthetic or approved historical fixtures. Do not migrate full article bodies, images, excerpts, or protected material without documented permission for the intended use.

## Stages

### 0. Inventory
Record each candidate asset's path, purpose, licence/rights concerns, dependencies, test coverage, known defects, and disposition. Do not copy files yet.

### 1. Domain model and contracts
Define publisher, permission, observation, article, story, claim, evidence, event, learning object, edition, and run contracts. Validate sample fixtures.

### 2. New foundation
Create the Python package, configuration, migrations, CI, deterministic fixtures, and run-report format.

### 3. Permission-first connectors
Implement a small number of clearly permitted sources. Verify policy enforcement before network calls. Use synthetic fixtures for tests.

### 4. Story and evidence layer
Implement identity resolution, evidence links, contradiction handling, and append-only editorial decisions.

### 5. Editorial and learning outputs
Build summaries, timelines, perspective comparisons, bilingual workflow, and learning objects as independent modules.

### 6. Parallel evaluation
Run new and old logic against the same approved or synthetic fixtures. Compare accuracy, traceability, duplication, runtime, and editorial usefulness. Do not publish from this stage.

### 7. Compatibility exporter
After the new schema and quality gates are stable, implement a one-way exporter to the legacy app's expected format. Export only to this repository's staging artifacts.

### 8. Explicit handover
Produce migration report, rollback plan, and compatibility test results. Obtain explicit approval before connecting any production app, schedule, or public archive.

## Acceptance criteria

- No write operations have occurred in the old repositories.
- Every migrated record has provenance and a documented permission basis where applicable.
- No legacy implementation is imported without a review decision.
- New and legacy outputs can be compared reproducibly.
- Compatibility export cannot publish directly.
- Last known-good production editions remain unchanged.
