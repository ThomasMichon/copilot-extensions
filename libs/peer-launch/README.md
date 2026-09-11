# Same-cell peer launcher

`peer_launch.py` is the canonical, dependency-free native process boundary for
Agent Dispatch and Agent CodeSpaces. `tools/sync-peer-launch.py` packages
byte-identical copies alongside each consumer's `_installation_context.py`;
`tools/sync-installation-context.py` owns those validator bytes. Neither
bootstrap imports a validator through an unvalidated payload pointer.

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
Source hooks propagate explicit refusals as diagnostic failures. A rejected map
probe stops the session-guidance writer before other producers or any file
mutation; existing guidance is preserved rather than replaced with a success-shaped
empty result.

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
