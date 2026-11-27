# Android client-edition exporter v1

**Status: implementation proposal in a feature branch.** This is a deterministic transformation library, not a production publisher, HTTP endpoint, scheduler, or storage adapter.

## What it does

`sngso_engine.client_edition.build_client_edition` converts already validated engine domain records into the Android read projection. It:

- requires an edition with status `published`;
- preserves the exact order of `edition.story_ids`;
- requires an explicit primary article selection for every story;
- requires explicit localized story titles for every edition language;
- validates Edition, Story, Article, LearningObject, and SourcePermission records against their existing v1 schemas;
- checks current `metadata` permission at an explicit, timezone-aware export timestamp;
- includes only approved brief and detailed summary learning objects for each language;
- rejects inactive/missing articles, missing source metadata, missing permissions, ambiguous or missing summaries, and unapproved language variants;
- computes the projection's own SHA-256 fingerprint using the engine's canonical JSON implementation; and
- validates the finished projection against the engine-owned client-edition v1 schema.

## Deliberate limits

The engine domain model currently has no Source schema/repository record with a canonical display name, so the exporter requires an explicit source metadata mapping (`id`, `name`). It also requires the editorial workflow to supply `primary_article_by_story` and `titles_by_story`. It never chooses the first article or invents a translation.

The current Android v1 projection exposes one primary source per story. All article IDs remain in the engine Story record; the selected primary article is a deliberate client display choice, not a destructive change to the domain model. If Android needs multiple citations per story, revise the projection to a `sources[]` collection before declaring v1 frozen.

The caller supplies `permission_evaluated_at` so permission evaluation is reproducible and auditable; an expired or revoked permission blocks a fresh export even if the edition was published earlier. The exporter does not create or approve summaries, decide source permissions, approve editions, persist artifacts, publish URLs, or replace a prior edition. Those remain responsibilities of the editorial workflow and future repository/hosting adapters.

## Run tests

```bash
python -m pip install -e ".[test]"
python -m pytest tests/contracts -q
```

The fixture is synthetic and must never be presented as live news.
