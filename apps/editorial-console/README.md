# Sarangsho Studio: Editorial Console Prototype

This folder contains an isolated, dependency-free browser prototype for the
new Sarangsho Engine. It is deliberately separate from the legacy website,
Android application, and backend-only Section 9 pull request.

## Run locally

Open `index.html` in a modern browser. No build step, server, package install,
or external API is required. The prototype uses system font fallbacks and does not require external font or API requests.

## What is represented

- Editorial overview and pipeline workload
- Edition list and language/status labels
- Local-only edition composition with reorder controls and derived pre-publication blockers
- Story desk with local search, stage filtering, and a read-only story detail file
- Claim-linked evidence comparison, conflict flags, and illustrative decision history
- Human review queue for evidence, translation, rights, and attribution
- Operation-specific source permission register
- Operations and worker-supervision preview

## Prototype boundary

Every record is illustrative. No publishers are contacted, no source content is
collected, and no approvals, publication, worker operations, or database writes
are possible. Backend-dependent controls are disabled. The only export is a
small JSON file identifying the demo itself. Edition ordering is temporary browser state and is not persisted.

Do not treat the sample percentages, stories, dates, permissions, or counts as
real project state.

## Integration direction

The console should consume versioned API contracts rather than query SQLite or
PostgreSQL directly from the browser. Before connecting it:

1. Define authenticated session and role contracts.
2. Expose read-only edition, story, review, source-permission, run, checkpoint,
   worker-health, and audit projections.
3. Enforce authorization and permission checks server-side for every command.
4. Add command endpoints only after backend transactions and audit semantics
   have acceptance tests.
5. Keep publication as a separate, explicit, human-approved operation.
6. Add contract tests for API responses and UI loading, empty, stale, denied,
   partial, and failure states.

## Visual and interaction principles

- Responsive desktop-first workspace, usable on narrow screens.
- Navigation organized by editorial workflow, not database tables.
- Clear distinction between draft, review, approval, and publication.
- English/Bangla variants retain independent workflow states.
- Rights are shown per operation; unknown is not presented as allowed.
- Unresolved conflicts and missing evidence are visible blockers.
- Destructive and publication actions require confirmation, role checks,
  reason capture, and an immutable audit record.

This is a UI foundation, not a production-ready editorial system.
