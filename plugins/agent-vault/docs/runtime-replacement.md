# Vault runtime replacement

Parent: [Architecture](architecture.md). User-facing setup and commands:
[agent-vault](../README.md).

## Incoming-first serving

`service_generation.py` owns endpoint and work custody;
`service_handoff.py` owns private transfer; `cutover.py` owns the auth-state
contract and expiration rules. `service_predecessor.py` applies the existing
shared native process/custody primitives to one displaced central Vault owner.

The Windows updater constructs and selects the incoming runtime, then invokes
its selected-generation ensure operation. It does not stop the predecessor or
depend on its IPC contract to start the new process. The scheduled launcher
continues resolving the current slot; `IgnoreNew` task behavior cannot substitute
for ensuring the selected generation independently.

Explicit Windows stop uses the installed runtime's canonical read-only
resolver, independently of the installer payload's prospective version.
Refreshing a payload alone does not make its uninstalled slot a shutdown
prerequisite. Automatic replacement still does not call predecessor stop.

Each running build holds its own generation lease. This makes repeated starts
idempotent without mistaking another build's healthy ping for currentness.
Replacement compares immutable build identities, not numerical version order.
The installer's separate explicit-downgrade policy is unchanged.

CLI service launches, the Windows task launcher and the existing POSIX service
unit keep enabled Python bytecode in the owning home's `cache/python`, outside
immutable slots. Late private-handoff imports follow that same policy; Python
bytecode is separate from credential caches and does not change auth expiry.

The newcomer binds a fresh endpoint beside a live predecessor and atomically
publishes it through the existing native-transport rendezvous. TCP can use an
ephemeral port while the predecessor owns the legacy port. After acquiring work
custody, the new daemon restores available legacy aliases without deleting a
foreign listener. If work custody transfers before the old process exits, it
retries those aliases once the predecessor's exit is observed.

One shared OS lease, keyed by the durable Vault home, excludes concurrent
credential work across generations. A published endpoint can answer health
requests while waiting for that lease, but reports `accepting_work=false`.
Credential requests receive a retriable transition refusal; CLI and installer
readiness do not mistake endpoint reachability for credential readiness.

Native generation probes send `{"action":"ping","health_only":true}` under the
existing generation protocol. This optional hint returns only generation,
admission and private-handoff metadata, without resolving default vault
configuration, checking backend state or expiring passwords. The existing
250 ms probe deadline is unchanged. Earlier peers ignore the hint and return
their ordinary full ping; new readers accept that response while retaining
PID, generation and admission checks. Ordinary public ping and strict
installer configuration/backend readiness keep their existing behavior.

Prior generation observers send ordinary pings without the hint or a vault
context. Those pings use the resident's default database selected at service
initialization, not another configuration-owner lookup. Local public clients
already send their freshly resolved database selection, so their full ping
continues reporting the caller's current context. A contextless full ping
reports the resident default until restart. This read-only status snapshot
does not select a database for credential operations: contextless credential
requests still resolve configuration freshly, and ordinary ping retains
password-expiration checks.

The old build observes a different, live, selected build and closes its work
admission. It settles already-admitted native requests, optionally transfers
compatible auth state, and releases the credential-work lease before transport
cleanup. A native-authenticated release receipt allows the newcomer to take
that shared lease without waiting for final process exit. No late credential
request can reactivate the donor. Its cleanup preserves the successor's
endpoint and PID publication. Confirmed retirement commits forward even if the
successor subsequently disappears; it does not reactivate a displaced writer.
A candidate failure before publication leaves the incumbent's route intact.

The selected incoming service's existing lifecycle tick grants the captured
predecessor thirty seconds from discovery publication for cooperative work,
handoff and exit. A failed earlier newcomer can publish discovery while an older
generation still holds the credential-work lease. Capture that distinct lock
holder too: the diagnostic PID selects a candidate, not retirement authority.
At most these two current resources are considered, never historical processes.
The writer candidate must still hold the contended lease at mutation time.
After grace, each captured target gets one bounded cold-retirement attempt,
never a descendant kill. There is no new watchdog,
recurring reaper or per-request registry scan. The publication lease serializes
the attempt with endpoint replacement; current selection and discovery are
rechecked before native mutation.

Retirement independently validates the admitted instance's central-service
role, installation slot/home, runtime directory identity, unprotected resident
status, OS principal and native birth. It uses the retained admission's registry,
not a launch-only environment variable. Test owners can replace only their own
test run. Shared `PinnedProcess` termination confirms original-process exit;
identity-only termination is not sufficient owner authority. Reused PIDs,
changed records, reselected successors, clients and unrelated installations
cannot become targets.

The native pin pauses all parent threads before census and owner revalidation.
Any live credential or unknown child refuses parent retirement and resumes it:
parent exit alone would release the writer while its backend still mutates.
The Windows native system console-host image is the only non-worker exemption,
verified by file identity against `GetSystemDirectoryW`, never a process name.
This census uses the declared `psutil` dependency only on the finite cold path.
No child is terminated. A stuck backend can therefore retain write exclusion;
it is not silently treated as a completed cold replacement. Admitted backend
work may finish normally and transfer custody after a refused cold attempt.

Health's additive `predecessor_retirement` and
`writer_predecessor_retirement` report `grace`, `exited`,
`cold-retired` or a specific `refused-*` outcome independently of
`accepting_work`. A healthy incoming service is not rolled back for refused
cleanup. Without a valid release receipt, credential admission still needs
original-predecessor exit and acquisition of the shared writer lease.
Unavailable owner/exit proof can therefore leave credential readiness unresolved;
neither code publication nor a requested termination is reported as full
replacement success. Interrupted requests have unknown outcomes, not permission
for blind mutation replay.

## Versioned, private auth handoff

The negotiated contract is `agent-vault.auth-state`, currently version `1`.
Health advertises supported versions and, during replacement, a bounded
one-use receiver. The donor transfers state only when the versions overlap.

The version-1 private frame accepts an optional boolean
`credential_writer_released`. Only literal `true` from the exact authenticated
donor establishes settled writer custody; missing or malformed values do not.
The sender includes it only after closing admission, settling credential work
and releasing its lease. Prior readers tolerate and ignore the additive field;
prior senders omit it. Both mixed directions retain auth transfer and the
original process-exit gate. No CLI syntax or auth-payload version is retired.

After confirmed predecessor exit, the newcomer can reclaim the captured legacy
Unix socket only when its owned socket type and device/inode still match and
connection is refused or the pathname is absent. Permission denial and unknown
probe failures are not stale-listener proof. Live listeners, replacement paths and unrelated files
remain untouched. PID reuse is positive exit of the original incarnation, not
permission to signal the reused PID.

The payload contains unlocked KeePass master passwords, their original set
times, and absolute expiration deadlines. It does not copy credential-value
caches or overwrite the newcomer's daemon policy. Expired, malformed or
unsupported state is not adopted. Repeated handoff or a longer incoming TTL
cannot extend the original deadline; a new explicit unlock establishes a new
password lifetime.

Transfer uses the shared `zdd` private native channels: owner-only Windows
named pipes or private Linux Unix sockets, with native PID, birth-identity and
principal verification. The ordinary Vault pipe and loopback TCP endpoint are
not secret handoff channels. No handoff payload enters a file, log, command-line
argument or environment variable.

The donor freezes credential admission and settles outstanding work before
capturing state. Receiver and sender use bounded, cancellation-owned native
operations. A refusal, expiry, incompatible contract or unavailable private
channel does not prevent the incoming process from starting or the old build
from retiring. Subsequent credential access uses its normal unlock providers,
opt-in encrypted cache or an explicit unlock if no unexpired state transferred.

## Qualification and remaining platform work

The focused packet is `python tools/run-plugin-tests.py agent-vault --test-file
tests/test_service_generation.py --test-file tests/test_service_predecessor.py
--allow-explicit-tiers`. Isolated child-process tests cover private transfer,
both mixed receipt directions, no-overlap retirement, held connections during
grace, hung credential work and transport cleanup, original native-process exit,
writer exclusion, retained durable bytes, successor-safe cleanup and legacy TCP
reconnection. Three generations exercise a discovered passive predecessor
ahead of the actual older writer. A real subprocess behind the production
KeePassXC edit constructor proves refusal while backend mutation remains live,
followed by cooperative settlement. Unit controls separately cover
owner/selection/birth refusal, changed lease holders, unconfirmed retirement,
PID reuse, literal receipt admission, stale/live/replaced Unix aliases and
expiration preservation.
These are not installed whole-suite adoption evidence.

Native pinned cold retirement is implemented for Windows and Linux. macOS
retains cooperative self-exit but refuses this unavailable native cold-retirement
primitive; private auth transfer is also unavailable there. Windows source
controls are executed locally; Linux and macOS qualification are separate.

The POSIX/systemd installer still has its legacy restart and unversioned
handoff-carrier path. Its migration and native service-manager custody remain
open; those older tests do not qualify incoming-first updates or this private
handoff contract. Neither this source implementation nor a merged change
authorizes a running host's rollout.
