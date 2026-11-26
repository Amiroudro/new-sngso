# Section 9.13 Observability Implementation Note

## Local worker outcome metrics

WorkerTelemetry provides a bounded in-process metrics primitive for
WorkerSupervisor. A supervisor may receive one recorder configured with the
same statically registered entrypoint names. Each completed child execution
contributes an outcome counter and a fixed-bucket duration observation. The
recorder retains only a bounded ring of recent outcome metadata.

Recorded dimensions are limited to registered entrypoint, a finite status
allow-list, and a finite termination allow-list. No request payload, run ID,
worker ID, bearer token, provider operation reference, stdout, stderr, or
exception text is recorded. Metric cardinality is bounded by the entrypoint
allow-list and finite outcome labels. The recent-outcome ring is independently
bounded and can be disabled with capacity zero.

## Integration and failure semantics

When configured, WorkerSupervisor records the final process outcome. Without
a recorder, existing behavior is unchanged. The recorder is local memory only:
process restart clears its counters, and it is not a durable audit log. A
snapshot is a detached copy suitable for a future authenticated metrics
endpoint or exporter.

This is not Prometheus/OpenTelemetry export, remote aggregation, alert
delivery, trace correlation, or production monitoring. No network exporter,
metrics endpoint, credentials, service, or alert route is configured by this
change. Deployment owners must select an authenticated backend, define
retention and access controls, and test scrape/export failure behavior before
treating metrics as operational evidence.

## Verification boundary

Tests cover fixed label validation, cardinality control, bounded event retention,
histogram boundaries, concurrent updates, and exclusion of identity/request
fields. The full repository CI is the authoritative integration check.
