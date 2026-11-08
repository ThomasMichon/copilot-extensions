# Same-cell peer launcher

`peer_launch.py` is the canonical, dependency-free native process boundary for
Agent Bridge, Agent Dispatch, Agent CodeSpaces, Agent Containers, Agent
Logger, Agent Index, Agent Machines, Agent Worktrees, and Agent Pull Requests.
`tools/sync-peer-launch.py` packages
byte-identical copies alongside each consumer's `_installation_context.py`;
`tools/sync-installation-context.py` owns those validator bytes. Neither
bootstrap imports a validator through an unvalidated payload pointer.
Repeated in-process owner observations retain the packaged validator by its
canonical source path, with serialized first load and a fork-reset load lock.
Only code is retained: every observation still revalidates current installation,
namespace and governance receipts. Peer validators selected from independently
updated payloads are loaded fresh.
Supported peers are `agent-worktrees`, `agent-bridge`, `agent-codespaces`,
`agent-containers`, `agent-dispatch`, `agent-ssh`, `agent-machines`, and
`agent-pull-requests`, plus `agent-vault` -- each a canonical
`libs/installation-context` adopter in its own right. Adding a peer requires
three coordinated steps, not just a `PEERS` mapping entry: (1) the mapping
entry itself; (2) target-specific environment rebinding in
`peer_environment()` (its own runtime/state/credential variables --
`AGENT_CODESPACES_HOME`, `AGENT_CONTAINERS_HOME`/`AGENT_CONTAINERS_STATE_DIR`,
and the `AGENT_DISPATCH_*` install/routing/run-dir and credential vars are
worked examples), since the generic prefix-based scrub in `peer_environment()`
strips a new peer's variables just like any other unless re-added explicitly;
and (3) registering the new peer as a `tools/sync-installation-context.py`
destination (and, if it owns a caller-side `_peer_launch.py` copy of its own,
a `tools/sync-peer-launch.py` destination too) so its packaged validator
bytes and boundary module stay in sync with the canonical source.

PR-owner calls load their bootstrap from the completed runtime's validated
immutable snapshot, not a replaceable management payload. In-process loading
does not emit bytecode, and its standalone prefix adds Python's `-B` so validator
imports cannot mutate the snapshot either. The PR owner is both a caller and a
peer; its inherited override namespace is scrubbed, then
`AGENT_PULL_REQUESTS_HOME` is rebound to that peer receipt's exact `stateRoot`.

`launch_prefix(owner, own_root, raw_context, peer)` returns a composable native
Python argv prefix. At execution the boundary validates active owner, namespace,
and same-cell peer receipts, checks owner activation and maintenance using the
packaged primitive, delegates peer governance and interpreter resolution to
the validated peer's shipped implementation, rechecks both governance states, and launches
isolated Python with exact argv and inherited stdio. Only constant resolver
commands cross POSIX shell or Windows PowerShell 5.1 for older peers without
a callable shipped `versioned_runtime.resolve_python`. Supported peers now
use that peer-owned Python resolver for both execution and child-free
`observe_peer_python`. A resolver failure refuses; it does not substitute
the native resolver's different selection. Native-only peers retain execution
but return unknown from observation.

`pinned_launch_prefix(owner, own_root, raw_context, peer)` is an opt-in
compatibility path returning `(prefix, selected_python)`. Keep that same prefix
for capability qualification and subsequent calls; creating a new prefix for
each call would select again. It requires a child-free peer-owned Python
resolver and the selected immutable slot's existing `.runtime-bootstrap`
launcher with build-identity support. Selection captures the existing
`runtime-generation` UUID and a digest of the exact completion record in the
retained prefix; subsequent calls never recapture either. At execution the boundary still checks current owner/peer receipts
and governance and rebinds the environment, but it does not consult runtime
selectors. The slot-local launcher acquires its existing retention lease before
site imports, verifies both original identity fields under that lease, and
refuses incomplete or unattributable completion. Missing,
retired, redirected or same-path recreated slots fail without selecting another runtime or replaying
the operation. Dynamic `launch_prefix` and native-only legacy execution remain
unchanged; older bootstraps and missing or malformed generation evidence refuse
only this optional route. No ordinary caller opts into pinning automatically.

Bridge's `_agent_worktrees_pinned_selection()` retains the prefix, installation
root and interpreter for an explicit caller. It also supports its existing
legacy owner boundary, retaining tombstone checks without a PATH fallback.
`worktree_command` and the lineage helpers accept the retained tuple through
`owner_prefix`; omission preserves their existing selection behavior. Pinning
identifies an executing build, not its capabilities or a resident daemon:
callers still need independent writer/workspace admission and running-generation
qualification before enabling an allocation path.

This intentionally adopts Python's canonical selector semantics for same-cell
launch: legacy `current` link recovery, parsed completion JSON, and Python's
interpreter subpath order apply. It is not an assertion that the prior shell
and PowerShell selectors were interchangeable. The selected path still passes
the existing physical slot/environment constraint. Code is retained under a
serialized first load with a fork-reset lock; every observation recomputes
selection and rechecks owner/peer receipts and governance. Replaced resolver
source refuses cached observation until a fresh caller loads it, rather than
using stale code or accumulating new fork callbacks. Imports do not write
bytecode into the peer payload. Observation additionally rechecks selection
and does not launch a child, provision or activate anything.
Failed first loads retain a refusal for that source signature, so partial
initialization cannot repeat fork callbacks on every health poll. Repairing
the source still requires a fresh caller. Ordinary load/selection exceptions
are normalized to context refusal; interruption exceptions still propagate.
The same refusal normalization covers installation-context primitive and
fragment initialization, before owner or peer validation can consume that code.

Normal POSIX venv
interpreter symlinks remain supported. This boundary does not provision,
activate, or impose exemplar-only snapshot/completion receipt contracts.
The selected interpreter may live directly in a version slot or in its
physical `.venv` application environment. Redirected slot/environment
directories and answers outside that peer's versions root are refused;
the interpreter leaf itself may remain a normal POSIX venv symlink.

Routing roots and caller credentials are removed before rebinding the peer's
installation receipt, payload, runtime root, and plugin-specific routing.
Dispatch peer clients retain the caller's named task authority and separate
local/shared credentials. Filtering must not turn an explicit authority into
legacy discovery or silently query another writer for an old task reference.
Failures return exit 126 with a diagnostic. Consumers must not interpret that
refusal as optional-peer absence. CodeSpaces allows absence only after validating
its owner and finding no same-cell worktrees installation directory; an existing
but malformed/incomplete/inactive peer is a refusal.

CodeSpaces keeps each legacy call path unchanged without explicit context.
Namespaced account lookups bypass legacy caches so switching cells or changing
receipts cannot reuse another cell's results. Its coordination preflight
distinguishes context refusal from an absent peer or a positively identified
older peer lacking the optional readiness command.
Claim, release-claim, and SSH CLI admission preserve context refusal as exit 78,
including early setup and claim-disabled paths. Lifecycle best-effort catches
must not turn context refusal into an ambient provider operation. Source hooks
can derive their owner root from the validated explicit receipt without relying
on a runtime-gate environment variable.
Best-effort obligation journaling and disposition mirroring are not admission:
they log a refused bookkeeping update and return false so cleanup can still
disconnect an existing transport. Authorization/preflight calls retain refusal.
Top-level CLI project and external-tool preflights run after context admission;
read-only status, doctor, version, and readiness diagnostics remain exempt.
Source hooks propagate explicit refusals as diagnostic failures. A rejected map
probe stops the session-guidance writer before other producers or any file
mutation; existing guidance is preserved rather than replaced with a success-shaped
empty result.
Both platform hook wrappers preserve the writer's explicit refusal status.

Containers uses this boundary for the optional knowledge-repository config
lookup. It validates its owner before selecting any config override, asks only
the same-cell worktrees installation for `state-root`, and refuses failed,
malformed, or unbound required-state responses instead of falling back to fleet
defaults. A missing peer remains optional after owner admission. Legacy config
precedence and best-effort lookup are unchanged without explicit context.
Returned knowledge roots must be existing directories. Relay profile generation
and in-process relay registration preserve context refusal before publishing
an allowlist or touching the token store.
The scrubber removes the Containers environment namespace as well as the other
callers' credentials and routing overrides.
Containers also uses this boundary for its provider-exec SSH profile
publisher (`emit_ssh_profile`), the first consumer of the `agent-ssh` peer:
unlike the optional knowledge-repo lookup above, agent-ssh is *required*
here, so a validated owner with no same-cell agent-ssh installation is a
refusal rather than a silent absence.

Logger uses this boundary for its cold-session-compaction tracked-worktree
check. Genuine absence -- a valid owner with no same-cell worktrees peer
installed -- returns `None` from `tracked_worktree_paths()`; legacy lookup is
unchanged without explicit context. Every other failure (owner-validation
error, blocked governance, a probe error, or a malformed peer response) raises
`ContextRefused` instead. Callers choose their own safe response to a `None`
or a refusal: `select_compactable` already has a per-session on-disk existence
fallback that errs toward keeping (not archiving) a session, so it folds
either into that same fallback. The hub-compaction callers have no such
fallback for a foreign machine's session, so "no peer in this cell" is no more
informative than a failure there -- both are treated as unresolved, and the
whole compaction pass fails closed rather than proceeding as if nothing
needed protecting.

Machines uses this boundary for `self_update.py`'s dtssh-mesh reachability
refresh -- the second `agent-ssh` consumer. Unlike Containers' required use,
this call is optional (not every machine runs a dtssh mesh): a validated
owner with no same-cell agent-ssh installation returns `None` (reported
`skipped`, matching legacy behavior); only a malformed/refused context
raises `ContextRefused`.

Worktrees consumes Machines' declared Git-policy preview/apply/rollback CLI
through this boundary. Machines needs only the canonical receipt, payload and
runtime-root rebinding; foreign `AGENT_MACHINES_*` overrides are scrubbed.
Legacy peer consumers can use `legacy_peer_environment()` for the same
caller-routing scrub without manufacturing a namespaced receipt.

Vault is also an opt-in same-cell peer target. Its environment uses the native
Vault owner's installation identity, home/run/cache/endpoint geometry and
service suffix, not the caller's database, cached context or endpoint overrides.
Windows service identity is normalized using the owning launcher's invariant
casing rules, including Unicode paths, rather than preserving original casing.
Linux C/UTF-8 qualification retains the owning `tr` pipeline's byte-oriented
normalization, not Python's Unicode `.lower()`. These are native-platform
identities, not a promise of identical suffixes across operating systems.
This opt-in Vault target supports native Windows and Linux; other native
platforms refuse before owner execution rather than assuming equivalent
suffix or service semantics. Ordinary peer targets and Vault entrypoints keep
their existing platform behavior.
Linux peer invocation requires `sha256sum` or `shasum`, matching the native
owner's SHA-256 route; it refuses before execution rather than inventing the
legacy checksum fallback's service identity. Ordinary Vault launchers retain
their existing fallback unchanged.
No provisioning is permitted by that invocation. Explicit configuration selection
still travels through Vault's native `--configuration-root`; peer selection
does not choose a database, unlock it, install a key or start a service.
The existing dynamic and retained-prefix APIs keep receipt/governance checks
and immutable build pinning. An older boundary refuses this target without a
legacy/PATH fallback; no ordinary caller selects it automatically.
The disposable native process control qualifies selection, exact argv and
environment rebinding using a synthetic peer module, not credential or setup
readiness. Actual Vault/SSH, logon and enrollment qualification remain separate.

Bridge uses this boundary for `handoff-check`'s `agent-worktrees
handoffs-check` call. Because `--execute` mutates predecessor state, a
validated owner with no same-cell worktrees peer is genuine absence
(returns `None`, reported as "not available in this cell"); any other
owner/receipt/governance failure raises `ContextRefused` rather than
degrading to ambient `PATH` -- a silent fallback could otherwise retire a
foreign cell's predecessor session. The legacy ambient lookup is used only
when no explicit context is present at all. The owner root passed to
`validate_owner()` is `install_dir()`'s `AGENT_BRIDGE_INSTALL_DIR` --
`runtime-gate.sh`/`.ps1` set that variable to the *validated* `runtimeRoot`
before dispatching into this process, which equals the plugin root exactly
when namespaced/active. `AGENT_BRIDGE_PAYLOAD_ROOT` is a different,
replaceable path (the marketplace source payload `runtime-gate` resolves
*from*, not the cell-namespaced plugin root) and must not be used here.

## Validation

Run `python tools/sync-peer-launch.py --check` and
`python tools/sync-installation-context.py --check` after source changes.
The dispatch `-k procutil` lane owns the shared real-subprocess proof for both
owners: disposable venvs, canonical validators and shipped resolvers, two cells,
exact argv/stdio, environment rebinding, receipt refusal, source-only hooks, and
Windows windowlessness. CodeSpaces `-k worktrees_peer` covers every converted
adapter without duplicating the process matrix. Both run through
[`tools/run-plugin-tests.py`](../../tools/run-plugin-tests.py); see
[`TESTING.md`](../../TESTING.md).
