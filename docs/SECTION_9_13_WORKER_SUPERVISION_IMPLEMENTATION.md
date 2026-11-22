# Section 9.13 implementation note: bounded POSIX worker subprocesses

Status: **first local process-supervision primitive implemented and full-suite CI verified; integration and production acceptance remain open.**

## Implemented API

`sngso_engine.worker_supervisor` provides:

- `WorkerEntrypoint`: a statically configured name, immutable argv tuple, explicit environment mapping, and JSON Schemas for request and result objects.
- `WorkerSupervisor`: a POSIX-only process-group supervisor with a bounded wall-clock deadline, graceful SIGTERM, bounded grace period, and SIGKILL escalation.
- `WorkerProcessResult`: bounded outcome metadata. It does not expose captured stderr or raw diagnostic output.

Requests are validated against the registered entrypoint's JSON Schema and byte limit, then delivered on stdin. Request data is never interpolated into argv. The process is launched with `shell=False`, `close_fds=True`, a new POSIX session/process group, and only the explicitly configured environment. No ambient environment is inherited.

stdout and stderr are drained concurrently so a full pipe cannot deadlock the child. The supervisor retains at most the configured byte ceilings, marks truncation, and rejects truncated stdout as a valid result. A child result must be a versioned JSON envelope and its result object must pass the entrypoint's result schema. Diagnostic bytes are not returned or persisted by this library.

The process group is also terminated when the main child exits but a descendant keeps inherited pipes open. Cleanup waits are bounded; if cleanup cannot be confirmed, the outcome is failed rather than reported as successful.

## Deliberate limitations

- POSIX only. Construction fails closed on Windows until equivalent process-group semantics are implemented and tested there.
- The API launches only statically registered argv vectors. It does not import arbitrary Python callables or accept shell commands.
- This is a local process primitive, not a daemon, distributed supervisor, service manager, or worker pool.
- It is not yet integrated with `WorkerLeaseSession` or `execute_checkpointed_pipeline`. The caller must not treat child exit or timeout as proof that a provider operation did not happen.
- Provider fencing, idempotency, and reconciliation remain mandatory before replaying an interrupted side effect.
- This first cut does not implement CPU/memory/file-descriptor resource limits, external signal orchestration, supervisor restart recovery, or a privileged secret/token handoff. Do not pass lease bearer tokens or secrets in argv, JSON request payloads, environment values, or output.
- The supervisor does not trigger collection, scheduling, publication, release, or deployment.

## Test coverage added

Tests cover successful stdin/stdout protocol, static entrypoint allowlisting, request size and schema rejection, result schema rejection, nonzero exits, graceful-stop/kill escalation, bounded stdout/stderr draining, explicit environment isolation, request separation from argv, and descendants retaining inherited pipes.

## Acceptance still required

Before production use: integrate lease identity and fencing through a protected IPC channel; make the worker validate current lease before each side effect; persist unresolved checkpoint state and require provider reconciliation after timeout/exit; add platform-tested CPU/memory/FD limits; add supervisor signal/shutdown orchestration and restart recovery; test cancellation, worker identity, stale fencing at provider boundaries, and crash windows; provide external monitoring, shared durable lease storage, backup/restore, and an explicit operator release decision.

No live worker is enabled by this module. No scheduler activation, collection, publication, release, merge, or deployment is authorized.


## Verification

Full Python 3.12 regression suite: **948 passed in 17.28 seconds** in GitHub Actions [run #45020303163](https://github.com/Amiroudro/new-sngso/actions/runs/45020303163), on code commit `aad427d7e6da9365a1642b4cb612e60b083d746f`. This verifies the local POSIX subprocess primitive and regression suite only; it does not verify lease integration, provider fencing, production process management, or non-POSIX behavior.
