# Vault runtime replacement

Parent: [Architecture](architecture.md). User-facing setup and commands:
[agent-vault](../README.md).

## Incoming-first serving

`service_generation.py` owns endpoint and work custody;
`service_handoff.py` owns private transfer; `cutover.py` owns the auth-state
contract and expiration rules. `service_predecessor.py` applies the existing
shared native process/custody primitives to one displaced central Vault owner.
`service_unix_alias.py` retains fixed-path socket custody independently of
the advertised primary endpoint.

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

Generation detection supports both direct `versions/<slot>` environments and
wheel-application `versions/<slot>/.venv` environments. Both read the same slot's
build-generation marker and obey its current-version selection; a nested
environment is not a development invocation.

The wheel-application installer calls the hidden `__source-start` receiver.
It requires the executing package inside its physical, selected, completed
application environment and a matching Vault home, then honors ordinary
`enabled: true` activation. Inactive configuration returns exit 2 without
starting a service. Active startup uses the existing selected-generation ensure
operation, never ordinary `start` or an incumbent's healthy ping. Failed
credential-work readiness or changed selection returns exit 1; success returns
0 only after rechecking the selected completed slot. It does not configure
scheduling, unlock a database, select optional dependencies or retire a peer.
Older runtimes reject this callback rather than falling back to ordinary start.

CLI service launches, the Windows task launcher and the existing POSIX service
unit keep enabled Python bytecode in the owning home's `cache/python`, outside
immutable slots. Late private-handoff imports follow that same policy; Python
bytecode is separate from credential caches and does not change auth expiry.

The newcomer binds a fresh endpoint beside a live predecessor and atomically
publishes it through the existing native-transport rendezvous. TCP can use an
ephemeral port while the predecessor owns the legacy port. After acquiring work
custody, the new daemon restores available legacy aliases without deleting a
foreign listener. If work custody transfers before the old process exits, it
retries those aliases until each configured transport is restored. An older
generation can release writer custody yet retain its transports after the
discovery predecessor exits. Completion requires actual fixed TCP and named-pipe
binds, not Unix-path readiness on Windows. A bounded native pipe-presence check
treats only not-found as absent; busy, denied and unknown outcomes preserve the
incumbent. Successfully restored transports are not rebound on later retries.

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
Writer-admission PID publication shares the same lease as cleanup and rechecks
selection/discovery. Contention leaves admission closed until a later tick
publishes successfully, preventing Windows replacement races with the donor's
PID-file reader.

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
rechecked before native mutation. Cold retirement also holds the immutable
slot's retained SDK selector lease through native termination and confirmed
exit, serializing activation and rollback with that decision. Missing, invalid
or busy retained selector custody reports `refused-selection-custody` without
terminating a process; source/development invocations retain cooperative
replacement but cannot perform that cold fallback.

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

The existing finite `RetirementCustody` worker owns each native pin outside the
production caller's cleanup lifetime. Caller death drops its private lifeline
and resumes the original paused process. The generation retains that same
custodian until its exit is confirmed, including partial startup and failed
cleanup, and reports `refused-native-resume` while cleanup remains unsettled.
Its lifecycle retries only custodian settlement, never native retirement or
an ambiguous resume. The worker holds an ambiguously closed pin until original
process exit instead of guessing whether another resume is safe. Shutdown closes
work admission and waits for settlement, not a discarded handle or false success.
Pending recovery prevents new writer acquisition or further cold
attempts, but never suppresses successor detection: an already-ready generation
still closes admission, drains admitted work and transfers its writer before
waiting for its retained pins during shutdown.

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

Each fixed Unix bind atomically records its installation, native owner birth
and socket device/inode in a private `legacy-unix-owner.json` under the run
directory. A suffixed primary does not overwrite that separate owner record.
The first bind creates an absent run directory before writing the record;
it does not depend on later discovery publication to create that directory.
It is pathname custody only, never another retirement target. Legacy fixed-primary
owners can also be captured before exit; unknown ownership leaves the alias
untouched without blocking incoming discovery.

After confirmed alias-owner exit, the newcomer can reclaim the captured legacy
Unix socket only when its owned socket type and device/inode still match and
connection is refused or the pathname is absent. Permission denial and unknown
probe failures are not stale-listener proof. Live listeners, replacement paths and unrelated files
remain untouched. PID reuse is positive exit of the original incarnation, not
permission to signal the reused PID. Alias mutation and cleanup share the
publication lease; cleanup only unlinks the socket identities bound by that
generation, preserving a later owner's path. Alias retries continue until its
actual owner exits, including when that owner predates the discovery predecessor.
Fixed-path cleanup also requires the current alias record's PID and native birth
to match this process, so inode reuse alone cannot authorize removing a later
owner's socket.

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
--test-file tests/test_service_unix_alias.py --allow-explicit-tiers`.
Isolated child-process tests cover private transfer,
both mixed receipt directions, no-overlap retirement, held connections during
grace, hung credential work and transport cleanup, original native-process exit,
writer exclusion, retained durable bytes, successor-safe cleanup and legacy TCP
reconnection. Three generations exercise a discovered passive predecessor
ahead of the actual older writer. A real subprocess behind the production
KeePassXC edit constructor proves refusal while backend mutation remains live,
followed by cooperative settlement. Unit controls separately cover
missing or invalid retained SDK custody. A real SDK rollback attempted after
the final owner check remains blocked until pinned predecessor exit is confirmed.
Syntactically valid truncated SDK helpers also refuse without taking down the
incoming service. A real paused-process control holds resume failure through
reselection and shutdown, then confirms original-process continuation without
termination once the retained pin can settle.
Other unit controls cover
owner/selection/birth refusal, changed lease holders, unconfirmed retirement,
PID reuse, literal receipt admission, stale/live/replaced Unix aliases and
expiration preservation. Endpoint-derived fixture requests use the advertised
native transport, including Unix-only discovery; explicit fixed TCP requests
remain TCP controls. Linux controls also cover successive cold replacements
and a fixed alias held by the older writer in a three-generation chain.
The Windows retained-transport control lets the discovery predecessor exit
while an older process has released writer custody but still owns its listeners.
After that older listener's controlled exit, fixed TCP and named-pipe requests
both reach the selected generation. Portable controls cover absent run-directory
creation, partial restoration and native not-found versus busy/denied results.
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
