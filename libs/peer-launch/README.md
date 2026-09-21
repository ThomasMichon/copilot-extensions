# Same-cell peer launcher

`peer_launch.py` is the canonical, dependency-free native process boundary for
Agent Bridge, Agent Dispatch, Agent CodeSpaces, Agent Containers, Agent
Logger, Agent Index, and Agent Machines. `tools/sync-peer-launch.py` packages
byte-identical copies alongside each consumer's `_installation_context.py`;
`tools/sync-installation-context.py` owns those validator bytes. Neither
bootstrap imports a validator through an unvalidated payload pointer.
Supported peers are `agent-worktrees`, `agent-bridge`, and `agent-ssh` --
each a canonical `libs/installation-context` adopter in its own right, so
adding a peer is just a `PEERS` mapping entry with no boundary changes.

`launch_prefix(owner, own_root, raw_context, peer)` returns a composable native
Python argv prefix. At execution the boundary validates active owner, namespace,
and same-cell peer receipts, checks owner activation and maintenance using the
packaged primitive, delegates peer governance and interpreter resolution to
the validated peer's shipped implementation, rechecks both governance states, and launches
isolated Python with exact argv and inherited stdio. Only constant resolver
commands cross POSIX shell or Windows PowerShell 5.1. Normal POSIX venv
interpreter symlinks remain supported. This boundary does not provision,
activate, or impose exemplar-only snapshot/completion receipt contracts.

Routing roots and caller credentials are removed before rebinding the peer's
installation receipt, payload, runtime root, and plugin-specific routing.
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
