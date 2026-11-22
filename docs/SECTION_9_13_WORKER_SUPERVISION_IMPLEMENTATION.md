# Section 9.13 implementation note: bounded POSIX worker subprocesses

Status: **local POSIX subprocess supervision, lease-aware process-control integration, bounded idempotent shutdown, read-only restart recovery inventory, provider reconciliation reference checks, and lease-loss crash-window coverage implemented; full-suite CI verified on code head. Production provider and operational acceptance remain open.**

## Implemented API

`sngso_engine.worker_supervisor` provides:

- `WorkerEntrypoint`: a statically configured name, immutable argv tuple, explicit environment mapping, JSON Schemas for request and result objects, and optional `WorkerResourceLimits`.
- `WorkerSupervisor`: a POSIX-only process-group supervisor with a bounded wall-clock deadline, graceful SIGTERM, bounded grace period, SIGKILL escalation, and an idempotent `shutdown(timeout_seconds=...)` method that stops new launches and terminates active process groups under one global budget.
- `WorkerProcessResult`: bounded outcome metadata. It does not expose captured stderr or raw diagnostic output.
- `supervised_checkpoint_handler`: adapts a validated worker result into an `execute_checkpointed_stage` handler. Only a successful child result becomes stage output; timeout, cancellation, lease loss, or protocol failure raises so a non-replay-safe checkpoint remains unresolved.

Requests are validated against the registered entrypoint's JSON Schema and byte limit, then delivered on stdin. Request data is never interpolated into argv. The process is launched with `shell=False`, `close_fds=True`, a new POSIX session/process group, and only the explicitly configured environment. No ambient environment is inherited.

stdout and stderr are drained concurrently so a full pipe cannot deadlock the child. The supervisor retains at most the configured byte ceilings, marks truncation, and rejects truncated stdout as a valid result. A child result must be a versioned JSON envelope and its result object must pass the entrypoint's result schema. Diagnostic bytes are not returned or persisted by this library.

The process group is also terminated when the main child exits but a descendant keeps inherited pipes open. Cleanup waits are bounded; if cleanup cannot be confirmed, the outcome is failed rather than reported as successful.

## Deliberate limitations

- POSIX only. Construction fails closed on Windows until equivalent process-group semantics are implemented and tested there.
- The API launches only statically registered argv vectors. It does not import arbitrary Python callables or accept shell commands.
- This is a local process primitive, not a daemon, distributed supervisor, service manager, or worker pool.
- `WorkerSupervisor.run` now accepts a started `WorkerLeaseSession`, verifies lease ownership before launch, and passes the lease envelope and fencing generation through the private control descriptor. A parent-side polling loop observes heartbeat loss and durable cancellation while the child runs. The child receives cancellation on a separate descriptor. A result is classified as `cancelled` only when the child explicitly returns `cancellation_acknowledged: true`; otherwise it is `cancelled_unacknowledged`. The supervisor never acknowledges cancellation in the registry on the child's behalf. The caller remains responsible for session lifecycle and for checkpoint reconciliation after any non-success outcome. The checkpoint stage runner now revalidates the lease in the handler-exception path before recording failure/indeterminate status; if ownership was lost, the stale worker cannot mutate the checkpoint and the `executing` state remains for reconciliation.
- Provider fencing, idempotency, and reconciliation remain mandatory before replaying an interrupted side effect.
- `sngso_engine.worker_control` supplies the bounded lease envelope over an inherited private pipe and a separate one-byte cancellation pipe. Descriptor numbers may be passed as environment values; bearer tokens are never passed in argv, request JSON, environment values, or output.
- The supervisor does not release or acknowledge the lease automatically, and does not mutate checkpoint state. Callers must retain session ownership and reconcile uncertain child outcomes before retry.
- Optional Linux CPU/address-space/open-file limits are now applied through the external util-linux `prlimit(1)` executable, which sets soft and hard limits before `exec` of the worker. A configured limit fails closed if Linux or `prlimit(1)` is unavailable. Entrypoints without `WorkerResourceLimits` retain the existing behavior. These are per-process limits, not cgroup/container quotas or aggregate worker-pool budgets.
- `WorkerSignalShutdown` now provides an optional process-global SIGTERM/SIGINT adapter. It must be installed and restored on the main thread; its signal handler only sets an event, while a dedicated control thread calls the bounded `WorkerSupervisor.shutdown()` method. It restores previous handlers when closed and does not acknowledge durable lease cancellation. Production identity-provider integration, service-manager restart policy, and provider-enforced fencing remain outside this implementation.
- `StageCheckpointStore.list_unresolved(pipeline_run_id=None)` provides a read-only restart-recovery inventory of executing and indeterminate checkpoints. It returns stable idempotency keys and persisted provider operation references in stage order. It does not acquire a new lease, call a provider, mutate checkpoint state, or authorize replay. A restarted worker must authenticate, acquire a current lease, and reconcile provider state before deciding what can safely continue.
- The supervisor does not trigger collection, scheduling, publication, release, or deployment.

## Test coverage added

Tests cover successful stdin/stdout protocol, static entrypoint allowlisting, request/result schema rejection, nonzero exits, timeout escalation, bounded stdout/stderr draining, environment isolation, and descendant cleanup. Lease-aware tests cover private-FD credential handoff, heartbeat renewal during child execution, durable cancellation propagation, explicit acknowledgement versus unacknowledged cancellation, and child termination after lease loss, including a point-of-use lease check that raises after launch. Checkpoint integration tests prove successful validated child output commits only its fingerprint and timeout leaves a side-effecting checkpoint indeterminate; a stale worker cannot change checkpoint state after lease loss. Protected-IPC unit tests cover envelope validation, token isolation, cancellation signaling, descriptor cleanup, and bounded polling. Shutdown tests prove that active children are terminated, launches are rejected after shutdown begins, repeated shutdown calls are safe, and the method uses a bounded global deadline. Signal-adapter tests prove SIGTERM dispatches cleanup from a normal control thread, closing without a signal restores handlers without shutting down, and installation is restricted to the main thread. Resource-limit tests validate configured bounds, inspect `/proc/self/limits` from inside a limited Linux child to prove kernel enforcement of CPU/address-space/open-file limits, and prove configuration fails closed when `prlimit(1)` is unavailable. Restart-inventory tests prove that executing and indeterminate checkpoints are discoverable without changing revisions or status.

## Acceptance still required

Before production use: make the worker validate lease ownership and fencing at each side-effect boundary; connect child outcomes to checkpoint reconciliation without replaying uncertain work; add platform-tested CPU/memory/FD limits; integrate the signal adapter with a service manager and validate service-manager shutdown behavior, integrate a production identity/authorization adapter and trusted stage-context resolver with the restart-recovery API, and add test stale fencing at provider boundaries and crash windows; provide external monitoring, shared durable lease storage, backup/restore, and an explicit operator release decision.

No live worker is enabled by this module. No scheduler activation, collection, publication, release, merge, or deployment is authorized.


## Verification

Latest full Python 3.12 regression suite: **990 passed in 12.65 seconds** in GitHub Actions [run #45037591527](https://github.com/Amiroudro/new-sngso/actions/runs/45037591527), on code head `66d0347c6c597426b8b8e5da783c7fd6b9ecddfd`. This run includes bounded supervisor shutdown, SIGTERM/SIGINT adapter lifecycle, read-only unresolved-checkpoint inventory, authenticated lease-bound restart recovery, exact context-fingerprint verification, guarded provider reconciliation, provider-reference integrity checks, lease-loss crash-window tests, and Linux kernel enforcement of configured CPU-time, address-space, and open-file limits. It verifies local POSIX/Linux primitives and recovery orchestration contracts, not a concrete production provider, production identity service, cgroup policy, or production service manager.


## Pending-provider reconciliation handoff

A supervised child can exit without a definitive local result while the remote
provider operation continues. The checkpoint layer now records the first
provider-confirmed `pending` response as an `indeterminate` checkpoint and
stores the provider's bounded opaque operation reference in the append-only
revision history. The next reconciliation call receives both the deterministic
idempotency key and that persisted operation reference.

A pending result never invokes the stage handler. A later provider-confirmed
completion records success without launching another child; provider-confirmed
`not_started` is still the only result that can authorize a side-effecting
retry. Repeated pending polls do not rewrite an already-indeterminate head, so
the original provider reference remains immutable. Provider adapters must
therefore return a stable operation reference on the first pending response.

The provider's free-form metadata is deliberately not persisted. Only the
existing allow-listed operational tokens are recorded. This is a local
reconciliation handoff, not a provider adapter or proof of remote idempotency.


## Provider-boundary fencing command contract

`sngso_engine.provider_fencing` now defines an immutable `ProviderCommand`
that binds provider namespace, pipeline run, stage ordinal, deterministic
idempotency key, lease fencing generation, and request fingerprint. The
`execute_fenced_provider_command` wrapper checks current lease ownership before
dispatch and again after the provider returns. If ownership is lost in flight,
the wrapper refuses to return success to the local caller; the remote result
must be reconciled.

The `fenced_provider_stage_handler` factory adapts this contract to the existing checkpoint handler signature: it builds a bounded request from in-memory stage context and forwards the checkpoint runner's deterministic idempotency key plus the captured lease generation. The checkpoint runner can retain its own point-of-use `lease_guard` as an additional local check. The `FencedProviderAdapter` protocol requires the provider implementation to
atomically enforce two rules inside its own serialization boundary:

1. Reject a command whose fencing generation is lower than the highest
   generation already accepted for the protected resource.
2. Bind an idempotency key to its request fingerprint, returning the original
   result for an exact duplicate and rejecting reuse with a different request.

The preflight SQLite lease check is not a substitute for provider enforcement:
ownership can change between the check and the remote operation. The provider
must implement the atomic comparison itself. Tests use a deliberately small
fake provider to verify the contract, stale-generation rejection, duplicate
deduplication, conflicting-payload rejection, stale local lease refusal, and
end-to-end checkpoint-handler integration with the deterministic checkpoint key.
No concrete production provider adapter exists yet, so this work establishes
and tests the interface rather than claiming a real external system is fenced.

### Reconciliation reference integrity and lease-loss crash window

The checkpoint reconciler now validates every non-null provider operation
reference returned by an adapter. Malformed references and references that
differ from the first durably recorded pending-operation handle are treated as
an unknown provider outcome. They cannot replace the original handle, authorize
a retry, or turn a mismatched operation into checkpoint success. The original
checkpoint remains indeterminate for a later operator/provider investigation.

A provider call may complete remotely while the worker loses its lease before
the response can be committed locally. The dispatch wrapper's postflight lease
check rejects the stale worker's result. The checkpoint runner then revalidates
ownership on the exception path, so the stale worker cannot mark the stage
failed or succeeded. The checkpoint remains executing and must be reconciled
using the stable idempotency key (and the persisted operation reference if one
was previously recorded) before any replay. This is intentionally conservative:
a remote success is not inferred from a lost local response.

New regression tests cover mismatched operation references, malformed
references, and a provider completing during lease takeover. The latter proves
that the remote call may have happened once while local checkpoint success
remains uncommitted and the checkpoint remains unresolved.



## Bounded shutdown and restart recovery inventory

The supervisor now maintains a lock-protected active-child registry and a one-way
shutdown event. `shutdown(timeout_seconds=...)` stops new launches, sends
SIGTERM to each active process group, waits against one shared deadline, then
sends SIGKILL to groups whose direct child remains alive. It returns whether all
registered direct children were reaped. An active `run()` observes the shutdown
event and returns status `shutdown`, never a successful result. The method is
idempotent and does not mutate lease or checkpoint state.

This is a callable primitive, not complete service shutdown orchestration.
An external entrypoint still needs to translate SIGTERM/SIGINT into a normal
control-thread call, stop accepting upstream work, close its database safely,
and preserve unresolved operations for reconciliation. Process-global signal
handlers are deliberately not installed by this library.

For restart inspection, `StageCheckpointStore.list_unresolved()` returns all
`executing` and `indeterminate` checkpoints in deterministic run/stage order;
an optional run ID scopes the query. The method is read-only and returns the
checkpoint's stable idempotency key, provider namespace, and persisted opaque
operation reference. It does not automatically reacquire leases, poll providers,
or replay handlers. The next restart-orchestration layer must authenticate its
worker identity, obtain a fresh lease, reconcile each provider operation, and
leave unknown/pending outcomes unresolved.


## Linux worker resource limits

`WorkerResourceLimits` is an optional immutable entrypoint setting with bounded CPU seconds, virtual address-space bytes, and open-file count. When any registered entrypoint requests limits, the supervisor requires Linux and resolves an absolute `prlimit(1)` executable at construction. At launch it prefixes the fixed argv with `prlimit --cpu=... --as=... --nofile=... -- <registered argv>`. No request field can alter the limits or command vector. The helper applies equal soft and hard limits and then execs the worker; no Python `preexec_fn` or shell is used.

The integration test reads `/proc/self/limits` inside the child and checks all three configured values. A missing helper causes supervisor construction to fail rather than silently launching without limits. Limits are opt-in, per process, and do not replace cgroups, container policy, aggregate memory budgets, or service-manager controls.


## Authenticated lease-bound restart recovery

The new checkpoint_recovery module provides CheckpointRecoveryPlan and
recover_unresolved_checkpoints(). The recovery entry point requires an
authorization callback that returns the literal boolean True before it acquires
a fresh worker lease. It then starts a heartbeat-backed WorkerLeaseSession and
enumerates unresolved checkpoints in stage order.

For each candidate, the application-supplied resolver must reconstruct the
original stage context from trusted application state. Recovery recomputes its
canonical SHA-256 fingerprint and compares it with the immutable checkpoint
fingerprint before invoking any provider or handler. The checkpoint does not
contain the original content, and recovery never tries to infer or rebuild it
from metadata.

Provider reconciliation is wrapped with lease-health checks both before and
after the remote lookup. A lease loss propagates out of the reconciliation
exception boundary and cannot be converted into a provider-unknown transition
by a stale worker. The checkpoint runner remains the sole authority for retry:
provider-confirmed not_started can proceed with the original context and stable
idempotency key; pending/unknown stops the ordered recovery pass; confirmed
completion records success without replay. An explicitly replay-safe stage may
use its declared policy, but side-effecting stages remain fail-closed.

The recovery report contains stage identifiers, statuses, idempotency keys, and
output fingerprints only. It does not return reconstructed stage outputs.
The lease is released on normal completion; a stale lease cannot be released
after ownership has moved.

Regression tests cover authorization denial before lease acquisition,
provider-confirmed completion without replay, provider-confirmed not_started
followed by exactly one handler invocation, pending-operation preservation,
context-fingerprint mismatch before provider invocation, and lease takeover
during a provider lookup. The last case proves that the checkpoint remains
indeterminate and the stale recovery worker cannot commit a provider result.

This is a reusable local orchestration API, not a production identity provider,
context resolver, remote provider adapter, or distributed lease service.


## Process signal adapter

WorkerSignalShutdown is an opt-in adapter for SIGTERM and SIGINT. It installs
handlers only from Python's main thread and preserves the prior handlers for
restoration. The handler itself performs only a minimal event notification;
process-group termination and bounded waits happen in a dedicated control
thread through WorkerSupervisor.shutdown(). Closing the adapter without a
received signal restores handlers and does not initiate shutdown.

This adapter is a library integration point. The hosting service still owns
upstream admission/draining, service-manager configuration, database close
ordering, and deployment-specific shutdown budgets. It does not acknowledge
worker cancellation or claim that provider-side effects were rolled back.
