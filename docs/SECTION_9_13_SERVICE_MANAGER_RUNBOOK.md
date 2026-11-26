# Section 9.13 service-manager deployment runbook (systemd reference)

Status: deployment guidance only. No service unit is installed, enabled, or
started by this repository change. The hosting team must adapt and validate
this reference on the target Linux host.

## Required worker entrypoint behavior

Before exposing readiness or accepting new work, the service entrypoint must:

1. Load its trusted identity-provider, request-context, and per-run RBAC
   integrations.
2. Open the registry from its configured durable path and validate migrations
   and integrity.
3. Construct the registered `WorkerSupervisor` and install
   `WorkerSignalShutdown` on the main thread.
4. Run restart recovery against unresolved checkpoint inventory. Recovery must
   use `OIDCWorkerRecoveryAuthorizer` with a trusted server-side
   request-context factory and deployment-owned per-run authorization policy.
5. Keep unresolved/unknown provider operations out of the normal retry queue.
6. Become ready only after recovery has reached a policy-approved safe state.

A process restart is not permission to replay work. Pending, unknown, denied,
or context-mismatched provider outcomes remain blocked for operator/provider
resolution. The worker must not advertise readiness merely because its local
process started.

## systemd reference unit

Replace the executable, account, environment file, database path, and resource
budgets for the deployment. The example assumes the application-provided
entrypoint installs `WorkerSignalShutdown` and performs the startup sequence
above. Do not install this unit unchanged.

```ini
[Unit]
Description=Sarangsho Plus supervised pipeline worker (%i)
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=sngso
Group=sngso
WorkingDirectory=/opt/sngso
EnvironmentFile=/etc/sngso/worker-%i.env
ExecStart=/opt/sngso/venv/bin/python /opt/sngso/bin/worker_service.py --worker-id %i
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=25
SendSIGKILL=yes
UMask=0077

StateDirectory=sngso
RuntimeDirectory=sngso
StateDirectoryMode=0700
RuntimeDirectoryMode=0700

NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
RestrictSUIDSGID=true
ReadWritePaths=/var/lib/sngso /run/sngso

# Set deployment-appropriate cgroup budgets after load testing.
MemoryMax=1G
CPUQuota=100%
TasksMax=64
LimitNOFILE=1024

[Install]
WantedBy=multi-user.target
```

## Shutdown budget

The application supervisor has its own bounded shutdown budget. Set that budget
below `TimeoutStopSec`, leaving time for Python cleanup and the service manager
to reap the process. With the example above, a five-second supervisor shutdown
budget leaves margin inside the 25-second systemd stop timeout. Validate the
actual budget against the maximum expected number of active child process
groups and the configured termination grace.

`KillMode=mixed` sends the initial stop signal to the main process and lets
the application supervisor terminate its child process groups. systemd remains
the final outer boundary and may kill remaining processes after
`TimeoutStopSec`. This outer kill is not a durable cancellation acknowledgement
and does not prove that an external provider did not commit a request.

## Startup, restart, and operational acceptance

Before enabling a unit:

- Run the worker under its dedicated unprivileged account.
- Verify the registry path is writable only by the service account and backed
  up independently.
- Verify OIDC issuer, audience, trusted signing-key resolver, session-revocation
  checker, and subject-to-principal mapping.
- Verify the request-context factory obtains a real authenticated session and
  cannot manufacture credentials from a worker ID.
- Verify per-run authorization checks tenant/run ownership and worker roles.
- Verify provider adapters enforce fencing and idempotency atomically at the
  remote provider boundary.
- Verify startup recovery blocks unresolved/unknown outcomes before readiness.
- Verify SIGTERM with active child processes, a provider call in flight, and a
  pending cancellation request.
- Verify systemd timeout escalation and confirm no stale process can commit a
  protected provider operation after lease takeover.
- Set `MemoryMax`, `CPUQuota`, and `TasksMax` from measured workload
  requirements. These are cgroup-level aggregate limits, unlike the
  supervisor's opt-in per-process `prlimit(1)` limits.
- Test backup restoration and audit-chain verification on a separate host.

Example operator commands (after replacing the unit name and reviewing the
unit file):

```sh
sudo systemd-analyze verify /etc/systemd/system/sngso-worker@.service
sudo systemctl daemon-reload
sudo systemctl start sngso-worker@worker-01.service
sudo systemctl status sngso-worker@worker-01.service
sudo journalctl -u sngso-worker@worker-01.service --since today
sudo systemctl stop sngso-worker@worker-01.service
```

Do not enable automatic restart until startup recovery is mandatory and
provider-boundary fencing is proven. A service manager can restart a process;
it cannot determine whether a remote side effect happened.

## Acceptance boundary

This runbook is a deployment checklist and reference configuration, not proof
of service-manager integration. Production acceptance still requires an
application-specific service entrypoint, real identity and authorization
adapters, a concrete provider adapter, measured resource budgets, systemd
integration tests on the target distribution, and operational evidence for
backup/restore, monitoring, and incident recovery.
