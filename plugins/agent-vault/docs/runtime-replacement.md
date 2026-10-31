# Vault runtime replacement

Parent: [Architecture](architecture.md). User-facing setup and commands:
[agent-vault](../README.md).

## Incoming-first serving

`service_generation.py` owns endpoint and work custody;
`service_handoff.py` owns private transfer; `cutover.py` owns the auth-state
contract and expiration rules.

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

The newcomer binds a fresh endpoint beside a live predecessor and atomically
publishes it through the existing native-transport rendezvous. TCP can use an
ephemeral port while the predecessor owns the legacy port. After acquiring work
custody, the new daemon restores available legacy aliases without deleting a
foreign listener.

One shared OS lease, keyed by the durable Vault home, excludes concurrent
credential work across generations. A published endpoint can answer health
requests while waiting for that lease, but reports `accepting_work=false`.
Credential requests receive a retriable transition refusal; CLI and installer
readiness do not mistake endpoint reachability for credential readiness.

The old build observes a different, live, selected build and closes its work
admission. It settles already-admitted native requests, optionally transfers
compatible auth state, and retires itself. Its cleanup preserves the successor's
endpoint and PID publication. Confirmed retirement commits forward even if the
successor subsequently disappears; it does not reactivate a displaced writer.
A candidate failure before publication leaves the incumbent's route intact.
An older predecessor without this supersession protocol is not automatically
terminated. Its survival can leave credential readiness unresolved after code
publication; a successful code install is not falsely reported as a healthy
replacement.

## Versioned, private auth handoff

The negotiated contract is `agent-vault.auth-state`, currently version `1`.
Health advertises supported versions and, during replacement, a bounded
one-use receiver. The donor transfers state only when the versions overlap.

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

Isolated child-process tests cover Windows private transfer, no-overlap
retirement, original native-process exit, writer exclusion, retained durable
bytes, successor-safe cleanup and legacy TCP reconnection. Unit controls cover
expiration preservation and malformed/unsupported contracts. These are not
installed whole-suite adoption evidence.

The POSIX/systemd installer still has its legacy restart and unversioned
handoff-carrier path. Its migration and native service-manager custody remain
open; those older tests do not qualify incoming-first updates or this private
handoff contract. Neither this source implementation nor a merged change
authorizes a running host's rollout.
