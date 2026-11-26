# Section 9.13: External Worker Supervision and Bounded Stage Execution

Status: **POSIX subprocess supervision, protected lease-control IPC, lease-session integration, bounded shutdown, and read-only checkpoint recovery inventory implemented locally; production supervision remains incomplete.**

## Purpose

Section 9.12 establishes durable worker ownership and cooperative cancellation. It does not stop a process that is paused, wedged, or executing a blocking handler. Section 9.13 defines the process boundary required for bounded execution without pretending that Python can safely terminate arbitrary threads.

The companion implementation note records the exact local code boundary. This document remains the normative design contract; the initial code is not an operating-system service, production supervisor, or provider adapter.

## Non-goals and hard boundaries

- No arbitrary in-process callable may be advertised as forcibly cancellable.
- No thread timeout may be treated as termination. A timed-out thread can continue mutating state.
- Lease expiry is not proof of process death.
- Killing a child process is not proof that an external request was not accepted.
- The supervisor must not collect publisher content, activate schedules, publish editions, release artifacts, or deploy.
- No raw article text, request/response body, bearer token, secret, or exception message may enter supervisor logs or durable process metadata.

## Required topology

A long-running supervisor owns the operating-system child process. The child executes one explicitly registered worker entrypoint. The supervisor, not the child, owns the wall-clock deadline and process lifecycle.

The eventual integration must use an allow-listed worker entrypoint registry. A request identifies a registered entrypoint and a bounded, schema-validated metadata payload; it must not accept an arbitrary import path, shell command, or user-supplied Python expression.

The process boundary must be real: a separate OS process with an independently enforceable termination operation. A thread, task, greenlet, or in-process callback is not an acceptable substitute.

## Launch contract

1. Validate the entrypoint identifier against a static allowlist.
2. Validate the request against the entrypoint's versioned schema and strict byte/field limits.
3. Construct an argument vector and invoke without a shell. Never concatenate user-controlled values into a command string.
4. Use a minimal environment allowlist. Do not inherit ambient credentials or dump environment values into logs.
5. Pass worker identity, pipeline run ID, lease token, and fencing generation only through a protected process handoff. The local `worker_control` primitive now writes a bounded versioned envelope to an inherited private pipe; descriptor numbers may be supplied through the child's explicit environment, but bearer tokens must never appear in argv, environment values, stdin, logs, checkpoint metadata, or audit payloads.
6. Apply OS-level resource limits where supported: wall-clock deadline, memory, CPU, file descriptors, and output byte ceilings. Document platform-specific gaps rather than silently skipping limits.
7. Start the child in a process group/session that permits bounded termination escalation without signaling the supervisor itself.

## Execution lifecycle

- The supervisor starts the child and records only bounded operational metadata: worker ID, run ID, entrypoint ID/version, fencing generation, start time, deadline, and process outcome.
- The child must acquire or validate the current lease before work and revalidate ownership at each side-effect boundary.
- Heartbeat renewal and cancellation observation remain explicit lease responsibilities. A child must stop cooperatively when cancellation is observed.
- At deadline or supervisor shutdown, request graceful stop first and wait only for a configured grace interval.

- The optional WorkerSignalShutdown adapter installs SIGTERM/SIGINT handlers on the main thread. Handlers only set an event; a normal control thread invokes bounded supervisor shutdown. Previous handlers are restored when the adapter closes. The hosting service remains responsible for admission draining and service-manager integration.
- If the child remains alive, terminate the child process group, wait for a bounded interval, then use a hard kill as the final process-boundary action.
- A process exit code is operational evidence only. It is not a provider outcome and cannot prove whether an external side effect happened.
- On timeout, signal, abrupt exit, lost lease, or ambiguous IPC, leave any in-flight checkpoint unresolved/executing or mark it indeterminate using the checkpoint API's established rules. Require provider reconciliation before replay unless the stage is explicitly replay-safe and the provider proves it did not start.
- The supervisor must not automatically reacquire a lease and replay a stage merely because the previous process was killed.

## Fencing and side-effect safety

A child can be paused after its last lease check and resume after another worker takes over. Therefore the lease generation must accompany every side-effecting provider operation, and the provider adapter must enforce fencing atomically with the operation or use a provider-supported idempotency key plus authoritative reconciliation.

If the provider cannot enforce fencing or idempotency, the operation is not safe for automatic execution under lease takeover. Local SQLite fencing metadata alone is insufficient.

## IPC and output handling

The protected channel is implemented in `sngso_engine.worker_control` and integrated into `WorkerSupervisor.run` when a started `WorkerLeaseSession` is supplied. The parent verifies ownership before launch, sends the bounded lease envelope through an inherited private pipe, and observes the heartbeat/cancellation state while the child runs. The separate cancellation pipe carries a cooperative byte signal. The child must explicitly return cancellation_acknowledged=true in its validated result to be classified as cooperatively cancelled. The supervisor does not acknowledge cancellation in the registry or mutate checkpoint state; the caller owns the session lifecycle and must reconcile checkpoints after uncertain outcomes. `supervised_checkpoint_handler` adapts successful validated child output into the checkpoint API; non-success outcomes raise and leave non-replay-safe stages indeterminate. The checkpoint runner rechecks lease ownership before recording a handler failure, so a stale worker cannot mutate the checkpoint after lease loss.

- Use a bounded, versioned message envelope with explicit message types.
- Reject oversized, malformed, duplicate-terminal, or out-of-order messages.
- Do not use pickle or another general object deserializer across the process boundary.
- Bound stdout and stderr independently; truncate with an explicit marker and never treat captured output as trusted structured results.
- Redact known credential patterns before diagnostic persistence. Prefer not to capture child output at all unless an entrypoint has a documented diagnostic need.
- Structured result messages must contain only schema-approved metadata and fingerprints, not raw stage output.
- If IPC is interrupted after a provider call but before durable checkpoint commit, classify the outcome as indeterminate and reconcile.

## Shutdown and signals

The local `WorkerSupervisor.shutdown(timeout_seconds=...)` now provides an idempotent shutdown entry point: it atomically stops accepting new children, snapshots active process groups, sends SIGTERM, and escalates remaining children to SIGKILL within one shared bounded time budget. The active run loop also observes the shutdown event and returns a non-success `shutdown` outcome. Regression tests cover active-child termination, post-shutdown launch rejection, repeated shutdown calls, and bounded completion.

This API is callable by an external signal adapter, but it does not install process-global signal handlers. Signal handlers must not perform database transactions or complex cleanup directly; an external service entrypoint should set a shutdown event or invoke this method from its normal control thread. Shutdown never acknowledges a durable lease cancellation, marks a checkpoint successful, or proves that a provider operation did not happen.

For process restart, `StageCheckpointStore.list_unresolved()` now returns a read-only, ordered inventory of `executing` and `indeterminate` checkpoints, including their stable idempotency keys and any persisted provider operation references. It does not acquire leases, call providers, mutate state, or authorize replay. A restarted service must first authenticate, acquire a valid lease, and reconcile each candidate with its provider; unknown outcomes remain blocked from replay.

## Observability

Emit structured, bounded events for child start, heartbeat/lease loss, cancellation requested/acknowledged, timeout, graceful-stop request, forced termination, exit status, and unresolved checkpoint handoff. Correlate by run ID, stage ordinal, worker ID, fencing generation, and checkpoint ID. Never include raw content, credentials, request bodies, or exception text.

Metrics and alerts require a separate operational integration and are not supplied by this contract.

## Required implementation and acceptance tests

Before Section 9.13 can be marked implemented:

- A child process that exits normally returns a validated result and releases resources.
- A child that exceeds its deadline receives graceful stop, then bounded terminate/kill escalation.
- A child that ignores graceful stop cannot keep the supervisor waiting beyond the configured bound.
- A child process failure after a simulated provider side effect leaves the checkpoint unresolved and requires reconciliation; no automatic replay occurs.
- A stale fencing generation is rejected by a provider test double at the side-effect boundary.
- Cancellation and supervisor shutdown are idempotent and bounded.
- Malformed, oversized, duplicated, and out-of-order IPC messages fail closed.
- stdout/stderr caps are enforced; secrets and raw content are absent from persisted diagnostics.
- Launch uses structured argv, no shell, static entrypoint allowlisting, and an environment allowlist.
- Worker token material is absent from argv, logs, audit records, and checkpoint metadata.
- Tests prove that the supervisor cannot trigger collection, scheduling, publication, release, or deployment.
- Platform-specific process-group and resource-limit behavior is tested on every supported OS/CI runner.

## Rollout gates

Implementation should proceed in layers: (1) pure launch/IPC contracts and unit tests; (2) a subprocess harness with fake workers only; (3) lease/checkpoint integration; (4) provider fencing/idempotency adapters; (5) platform-specific operational hardening. No production worker should be enabled until every layer is independently reviewed.

External acceptance still requires authenticated worker identity, a shared durable lease database with HA, provider-enforced fencing/idempotency, process supervision deployed under an independently controlled service manager, resource limits, signal/shutdown drills, monitoring/alerting, backup/restore, and an explicit operator release decision.

## Current decision

Section 9.13 is **partially implemented locally**: POSIX subprocess supervision, protected lease handoff, heartbeat-loss detection, and cooperative cancellation propagation exist. Provider-enforced fencing/idempotency, OS resource quotas, service-manager supervision, signal adapter wiring, automatic service restart policy, and production operations remain unimplemented. The local shutdown API and read-only unresolved-checkpoint recovery inventory are implemented but do not themselves perform recovery or replay. Existing Section 9.11 and 9.12 local behavior remains unchanged. Draft PR only; no merge, deployment, scheduler activation, live collection, or publication.
