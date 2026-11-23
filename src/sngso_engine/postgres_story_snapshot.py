"""PostgreSQL story editorial snapshot builder with SQLite-compatible fingerprinting.

The builder uses the caller's transaction cursor and locks every current row
that contributes to the snapshot. Benchmark-linked stories remain fail-closed
until the PostgreSQL benchmark-review quality-report port is complete.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .canonical import sha256_fingerprint
from .registry import Registry


class PostgresStorySnapshotIncomplete(RuntimeError):
    """Raised when a story snapshot depends on a subsystem not yet ported."""


def _object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PostgresStorySnapshotIncomplete(f"{label} is corrupt JSON") from exc
    if not isinstance(value, dict):
        raise PostgresStorySnapshotIncomplete(f"{label} must be a JSON object")
    return value


def _timestamp_key(value: str) -> tuple[datetime, str]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PostgresStorySnapshotIncomplete("story relation timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PostgresStorySnapshotIncomplete("story relation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc), value


def _timeline_history(cursor: Any, story_id: str) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT id, occurred_sort_key, precision, supersedes_event_id,
                  created_at, payload_json
           FROM timeline_events
           WHERE story_id = %s
           ORDER BY (occurred_sort_key IS NULL), occurred_sort_key, created_at, id
           FOR SHARE""",
        (story_id,),
    )
    rows = cursor.fetchall()
    superseded = {row[3] for row in rows if row[3]}
    return [
        {
            "event_id": row[0],
            "precision": row[2],
            "occurred_sort_key": row[1],
            "created_at": row[4],
            "supersedes_event_id": row[3],
            "is_superseded": row[0] in superseded,
            "record": _object(row[5], "timeline event"),
        }
        for row in rows
    ]


def _perspective_history(cursor: Any, story_id: str) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT id, source_id, article_id, supersedes_perspective_id,
                  created_at, payload_json
           FROM perspectives WHERE story_id = %s
           ORDER BY created_at, id
           FOR SHARE""",
        (story_id,),
    )
    rows = cursor.fetchall()
    superseded = {row[3] for row in rows if row[3]}
    return [
        {
            "perspective_id": row[0],
            "source_id": row[1],
            "article_id": row[2],
            "created_at": row[4],
            "supersedes_perspective_id": row[3],
            "is_superseded": row[0] in superseded,
            "record": _object(row[5], "perspective"),
        }
        for row in rows
    ]


def _claim_relation_history(cursor: Any, story_id: str) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT id, claim_id, related_claim_id, relationship,
                  supersedes_relation_id, created_at, payload_json
           FROM claim_relations WHERE story_id = %s
           FOR SHARE""",
        (story_id,),
    )
    rows = cursor.fetchall()
    rows.sort(key=lambda row: (*_timestamp_key(row[5]), row[0]))
    superseded = {row[4] for row in rows if row[4]}
    return [
        {
            "relation_id": row[0],
            "claim_id": row[1],
            "related_claim_id": row[2],
            "relationship": row[3],
            "created_at": row[5],
            "supersedes_relation_id": row[4],
            "is_superseded": row[0] in superseded,
            "record": _object(row[6], "claim relation"),
        }
        for row in rows
    ]


def _story_decisions(cursor: Any, story_id: str) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT payload_json FROM story_decisions
           ORDER BY decided_at, id
           FOR SHARE"""
    )
    decisions = [_object(row[0], "story decision") for row in cursor.fetchall()]
    return [item for item in decisions if story_id in item.get("story_ids", [])]


def _reporting_independence(
    cursor: Any,
    story_id: str,
    story_payload: dict[str, Any],
    article_payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    article_ids = list(story_payload.get("article_ids", []))
    cursor.execute(
        """SELECT d.decision_id, d.article_id, d.outcome,
                  d.duplicate_of_article_id, d.decided_at
           FROM ingestion_current c
           JOIN ingestion_decisions d ON d.decision_id = c.decision_id
           ORDER BY d.decided_at, d.decision_id
           FOR SHARE OF c, d"""
    )
    latest_review: dict[str, tuple[Any, ...]] = {}
    relevant = set(article_ids)
    for row in cursor.fetchall():
        article_id, outcome = row[1], row[2]
        if article_id not in relevant or outcome not in {
            "possible_duplicate", "confirmed_duplicate", "distinct"
        }:
            continue
        previous = latest_review.get(article_id)
        if previous is None or (row[4], row[0]) > (previous[4], previous[0]):
            latest_review[article_id] = row

    statuses: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    confirmed_pairs: list[dict[str, str]] = []
    reviewed_distinct = 0
    no_candidate = 0
    external_confirmed = 0
    parent = {article_id: article_id for article_id in article_ids}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for article_id in article_ids:
        payload = article_payloads[article_id]
        decision = latest_review.get(article_id)
        if decision is None:
            status, duplicate_of = "no_candidate", None
            no_candidate += 1
        else:
            status, duplicate_of = decision[2], decision[3]
            if status == "possible_duplicate":
                unresolved.append({
                    "article_id": article_id,
                    "duplicate_of_article_id": duplicate_of or "",
                })
            elif status == "distinct":
                reviewed_distinct += 1
            elif status == "confirmed_duplicate":
                confirmed_pairs.append({
                    "article_id": article_id,
                    "duplicate_of_article_id": duplicate_of or "",
                })
                if duplicate_of in parent:
                    union(article_id, duplicate_of)
                else:
                    external_confirmed += 1
        statuses.append({
            "article_id": article_id,
            "source_id": payload["source_id"],
            "duplicate_status": status,
            "duplicate_of_article_id": duplicate_of,
        })

    grouped: dict[str, list[str]] = {}
    for article_id in article_ids:
        grouped.setdefault(find(article_id), []).append(article_id)
    confirmed_groups = [sorted(group) for group in grouped.values() if len(group) > 1]
    return {
        "story_id": story_id,
        "article_count": len(article_ids),
        "publisher_count": len({item["source_id"] for item in statuses}),
        "articles": statuses,
        "unresolved_candidates": unresolved,
        "confirmed_duplicate_pairs": confirmed_pairs,
        "confirmed_duplicate_groups": confirmed_groups,
        "unresolved_candidate_count": len(unresolved),
        "confirmed_duplicate_count": len(confirmed_pairs),
        "external_confirmed_duplicate_count": external_confirmed,
        "reviewed_distinct_count": reviewed_distinct,
        "no_candidate_count": no_candidate,
        "independence_claim": "not_established_by_publisher_count_or_no_candidate",
    }


def _source_independence_report(
    story_id: str,
    independence: dict[str, Any],
    article_payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    articles = []
    for item in independence["articles"]:
        article = article_payloads[item["article_id"]]
        duplicate_status = item["duplicate_status"]
        if duplicate_status == "confirmed_duplicate":
            duplicate_review = "confirmed_duplicate"
        elif duplicate_status == "distinct":
            duplicate_review = "reviewed_distinct_article_only"
        elif duplicate_status == "possible_duplicate":
            duplicate_review = "unresolved_duplicate_candidate"
        else:
            duplicate_review = "not_reviewed"
        articles.append({
            "article_id": item["article_id"],
            "source_id": item["source_id"],
            "title": article.get("title"),
            "canonical_uri": article.get("canonical_uri"),
            "duplicate_review_status": duplicate_review,
            "duplicate_of_article_id": item["duplicate_of_article_id"],
            "independent_reporting_status": "not_established",
            "independence_evidence": {
                "original_reporting_provenance": "not_assessed",
                "syndication_or_republication": "not_assessed",
                "shared_source_or_common_origin": "not_assessed",
                "direct_interview_or_primary_document": "not_assessed",
            },
        })
    return {
        "story_id": story_id,
        "article_count": len(articles),
        "publisher_count": independence["publisher_count"],
        "articles": articles,
        "confirmed_duplicate_groups": independence["confirmed_duplicate_groups"],
        "unresolved_duplicate_candidates": independence["unresolved_candidates"],
        "independent_reporting_count": None,
        "independence_status": "not_established",
        "interpretation": (
            "A distinct-article review addresses whether two article records are duplicates; "
            "it does not establish independent reporting. Publisher identity, article count, "
            "different wording, and absence of a duplicate candidate are insufficient evidence."
        ),
        "evidence_required_before_independence_claim": [
            "reviewed original-reporting provenance for each article",
            "syndication and republication checks",
            "shared-source and common-origin checks",
            "where relevant, direct interviews or primary-document evidence",
        ],
        "production_ready": False,
        "authorization_granted": False,
    }


def _perspective_attribution_report(
    story_id: str,
    perspectives: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    reporting: dict[str, Any],
) -> dict[str, Any]:
    current_perspectives = [item for item in perspectives if not item["is_superseded"]]
    current_relations = [item for item in relations if not item["is_superseded"]]
    by_kind: dict[str, int] = {}
    by_language: dict[str, int] = {}
    by_source: dict[str, list[str]] = {}
    entries: list[dict[str, Any]] = []
    unreviewed: list[str] = []
    unattributed: list[str] = []
    claim_coverage: set[str] = set()
    for item in current_perspectives:
        record = item["record"]
        kind, language = record["kind"], record["language"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_language[language] = by_language.get(language, 0) + 1
        source_id, article_id = item["source_id"], item["article_id"]
        if source_id:
            by_source.setdefault(source_id, []).append(item["perspective_id"])
        else:
            unattributed.append(item["perspective_id"])
        if record.get("review_status") != "approved":
            unreviewed.append(item["perspective_id"])
        claim_coverage.update(record.get("claim_ids", []))
        entries.append({
            "perspective_id": item["perspective_id"],
            "kind": kind,
            "attribution": record["attribution"],
            "source_id": source_id,
            "article_id": article_id,
            "language": language,
            "text": record["text"],
            "claim_ids": sorted(record.get("claim_ids", [])),
            "review_status": record.get("review_status", "unreviewed"),
            "reviewed_by": record.get("reviewed_by"),
            "reviewed_at": record.get("reviewed_at"),
        })
    contradictions = [
        {
            "relation_id": item["relation_id"],
            "claim_id": item["claim_id"],
            "related_claim_id": item["related_claim_id"],
            "review_status": item["record"]["review_status"],
            "reviewed_by": item["record"].get("reviewed_by"),
            "reviewed_at": item["record"].get("reviewed_at"),
            "resolution_note": item["record"].get("review_note"),
        }
        for item in current_relations if item["relationship"] == "contradicts"
    ]
    return {
        "story_id": story_id,
        "perspective_count": len(entries),
        "reviewed_count": sum(1 for item in entries if item["review_status"] == "approved"),
        "unreviewed_perspective_ids": sorted(unreviewed),
        "unattributed_perspective_ids": sorted(unattributed),
        "by_kind": dict(sorted(by_kind.items())),
        "by_language": dict(sorted(by_language.items())),
        "source_attributions": [
            {"source_id": source_id, "perspective_ids": sorted(ids)}
            for source_id, ids in sorted(by_source.items())
        ],
        "perspectives": entries,
        "claim_ids_with_perspectives": sorted(claim_coverage),
        "contradictions": contradictions,
        "reporting_independence": reporting,
        "independence_caveat": (
            "Distinct publisher IDs and distinct articles do not establish independent reporting; "
            "syndication, shared sourcing, and common-origin material require separate evidence."
        ),
        "production_ready": False,
        "authorization_granted": False,
    }


def load_postgres_story_editorial_snapshot(cursor: Any, story_id: str) -> dict[str, Any]:
    """Build the SQLite-compatible story snapshot under PostgreSQL shared locks."""
    cursor.execute(
        """SELECT revision, updated_at, payload_json FROM domain_records
           WHERE record_type = 'story' AND id = %s
           FOR SHARE""",
        (story_id,),
    )
    story_row = cursor.fetchone()
    if story_row is None:
        raise PostgresStorySnapshotIncomplete(f"unknown story: {story_id}")
    story_revision, story_updated_at, story_raw = story_row
    story_payload = _object(story_raw, "story")
    article_ids = sorted(story_payload.get("article_ids", []))

    articles: list[dict[str, Any]] = []
    article_payloads: dict[str, dict[str, Any]] = {}
    if article_ids:
        cursor.execute(
            """SELECT id, revision, updated_at, payload_json FROM domain_records
               WHERE record_type = 'article' AND id = ANY(%s)
               ORDER BY id FOR SHARE""",
            (article_ids,),
        )
        rows = cursor.fetchall()
        if len(rows) != len(article_ids):
            raise PostgresStorySnapshotIncomplete("story editorial snapshot found a missing article")
        for row in rows:
            record = _object(row[3], "article")
            article_payloads[row[0]] = record
            articles.append({
                "id": row[0], "revision": row[1],
                "updated_at": row[2], "record": record,
            })

    cursor.execute(
        """SELECT id, revision, updated_at, payload_json FROM domain_records
           WHERE record_type = 'claim' AND payload_json::jsonb ->> 'story_id' = %s
           ORDER BY id FOR SHARE""",
        (story_id,),
    )
    claim_rows = cursor.fetchall()
    claims = [
        {"id": row[0], "revision": row[1], "updated_at": row[2],
         "record": _object(row[3], "claim")}
        for row in claim_rows
    ]
    claim_ids = [row["id"] for row in claims]
    evidence: list[dict[str, Any]] = []
    if claim_ids:
        cursor.execute(
            """SELECT id, revision, updated_at, payload_json FROM domain_records
               WHERE record_type = 'evidence'
                 AND payload_json::jsonb ->> 'claim_id' = ANY(%s)
               ORDER BY id FOR SHARE""",
            (claim_ids,),
        )
        evidence = [
            {"id": row[0], "revision": row[1], "updated_at": row[2],
             "record": _object(row[3], "evidence")}
            for row in cursor.fetchall()
        ]

    timeline_history = _timeline_history(cursor, story_id)
    perspective_history = _perspective_history(cursor, story_id)
    claim_relation_history = _claim_relation_history(cursor, story_id)
    decisions = _story_decisions(cursor, story_id)

    cursor.execute(
        """SELECT id, revision, updated_at, payload_json FROM learning_objects
           WHERE story_id = %s ORDER BY kind, language, id FOR SHARE""",
        (story_id,),
    )
    learning_objects = [
        {"id": row[0], "revision": row[1], "updated_at": row[2],
         "record": _object(row[3], "learning object")}
        for row in cursor.fetchall()
    ]
    glossary_ids = sorted({
        item["record"].get("content", {}).get("glossary_entry_id")
        for item in learning_objects
        if item["record"].get("content", {}).get("glossary_entry_id")
    })
    glossary_entries: list[dict[str, Any]] = []
    if glossary_ids:
        cursor.execute(
            """SELECT id, revision, updated_at, payload_json FROM glossary_entries
               WHERE id = ANY(%s) ORDER BY id FOR SHARE""",
            (glossary_ids,),
        )
        glossary_entries = [
            {"id": row[0], "revision": row[1], "updated_at": row[2],
             "record": _object(row[3], "glossary entry")}
            for row in cursor.fetchall()
        ]

    reporting = _reporting_independence(cursor, story_id, story_payload, article_payloads)
    source_independence = _source_independence_report(story_id, reporting, article_payloads)
    perspective_report = _perspective_attribution_report(
        story_id, perspective_history, claim_relation_history, reporting
    )

    benchmark_reviews: list[dict[str, Any]] = []
    if article_ids:
        cursor.execute(
            """SELECT review_id, payload_json FROM benchmark_reviews
               ORDER BY review_id FOR SHARE"""
        )
        for review_id, raw in cursor.fetchall():
            review_payload = _object(raw, "benchmark review")
            linked_article_ids = {
                item.get("article_id")
                for item in review_payload.get("source_materials", [])
                if isinstance(item, dict)
            }
            if set(article_ids).intersection(linked_article_ids):
                raise PostgresStorySnapshotIncomplete(
                    "story snapshot includes benchmark reviews; PostgreSQL benchmark quality-report parity is not implemented"
                )

    snapshot = {
        "story_id": story_id,
        "story_revision": story_revision,
        "story_updated_at": story_updated_at,
        "story": story_payload,
        "articles": articles,
        "claims": claims,
        "evidence": evidence,
        "timeline_history": timeline_history,
        "timeline": [item for item in timeline_history if not item["is_superseded"]],
        "perspective_history": perspective_history,
        "perspectives": [item for item in perspective_history if not item["is_superseded"]],
        "perspective_attribution_report": perspective_report,
        "claim_relation_history": claim_relation_history,
        "claim_relations": [item for item in claim_relation_history if not item["is_superseded"]],
        "story_decisions": decisions,
        "learning_objects": learning_objects,
        "glossary_entries": glossary_entries,
        "benchmark_reviews": benchmark_reviews,
        "benchmark_acceptance_reviews": [],
        "reporting_independence": reporting,
        "source_independence_evidence_report": source_independence,
    }
    snapshot["narrative_assembly_report"] = Registry._story_narrative_assembly_from_snapshot(snapshot)
    snapshot["fingerprint"] = sha256_fingerprint(snapshot)
    return snapshot
