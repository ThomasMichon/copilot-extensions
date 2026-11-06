# Incoming publication and retained clients

Ordinary installation constructs a real nested runtime slot and uses the
existing MCP installed callback owner to authenticate readiness and publish the
selected generation. It then publishes command bindings and the deployment
manifest. Outgoing process census, control-home idleness, forced drain,
version retirement and slot collection are not steps in that incoming gate.
Build, source admission, authentication, permission, I/O and incoming health
failures remain failures; an unknown foreign installation is not taken over.

## Current admission

The default current handle is `$AGENT_MCP_HOME/current/serve.sock`. Both a
configured `AGENT_MCP_SERVE_SOCKET` and an explicit command `--socket` retain
their exact-handle meaning. An explicitly occupied handle still passes the
existing attribution/admission checks; it is not silently bypassed by choosing
a sibling directory. The old default namespace remains available to pre-feature
clients, not reset or removed during publication.
Installed startup receives the actual requested handle, including a command
override different from the configured/default handle; it does not start an
unrelated default resident. Installation `--root` identifies runtime custody,
not a replacement for the ordinary data-home resolver.
The generated Windows CMD launcher delegates interpreter selection to the
co-deployed canonical marker resolver in noninteractive, read-only mode.
It does not bypass completion/reparse checks with a hand-built slot path.

Fresh installed bridge, call and list clients qualify the actual immutable
launch slot, installation root, authenticated serving readiness and supported
`agent-mcp.fresh-generation` contract before application admission. Package
version equality and capability support alone do not qualify an old provider.
Selection is revalidated after publication; the server checks the expected
generation and root before opening an upstream or dispatching a request.
The installed callback remains the owner of native identity, supported slot
layout and compare-before-publication checks.
Reuse and publication also authenticate the serving process's installation
root. Equal physical version strings from separate installations do not confer
ownership of a shared handle. Missing or foreign installation identity refuses
before replacement; no foreign listener or held client is closed to obtain it.

An explicit bridge configuration need not appear in global discovery. When
serving is enabled, fresh admission can publish the selected generation on
demand. A configured catalog alone does not start an inactive resident during
installation. Prepared setup also observes a separate default legacy handle as
an intent hint, without claiming or modifying its ownership. An endpoint file
for the current exact configured handle is not separate legacy intent: a dead
sidecar alone leaves the resident inactive.
`AGENT_MCP_NO_SERVE` and `AGENT_MCP_NO_CUTOVER` inhibit installer
activation; `AGENT_MCP_NO_ENSURE_SERVE` additionally inhibits client on-demand
activation. A skipped activation is not permission to execute a stale graph:
a fresh command can use truthful current direct execution or refuse before
effects.

### Caller execution context

Generation readiness does not establish credential equivalence. An installed
caller also derives a keyed digest from its own resolved bridge configuration,
environment and working directory using the peer's private, process-local
`agent-mcp.execution-context/2` challenge. The backend independently compares
its execution context before opening an upstream or obtaining a warm entry.
Raw environments and credentials are not transferred, logged or checkpointed;
the backend environment is never rewritten from caller input.
The semantic environment separates exact owner-declared daemon/custody and
wrapper bookkeeping from caller authority. Explicit configuration references
keep those fields significant; unrelated credential keys are never filtered.
Explicit reference matching follows Windows' case-insensitive environment
lookup, while remaining case-sensitive on POSIX.
An unset or empty, unreferenced `AGENT_MCP_HOME` compares as the canonical
resolved default that daemon startup adds. Nonempty overrides and explicit
configuration references remain significant.
The private MCP control token is not inherited by an upstream unless explicitly
configured. Process-custody metadata remains available to its lifetime owners.

This conservative sharing boundary supports equivalent stdio configurations
with none/env authentication. Different contexts, dynamic acquisition, routed
targets and CLI/HTTP transports use current direct execution rather than
assuming the installer's authority is the caller's authority. Compatible
contexts may still pool; full per-session credential custody remains separate
handover work. Unmanaged development retains its existing peer contract.

Cold bridge, call, materialized-stub and catalog execution revalidate the
launched physical slot immediately before new execution, including explicit
`--no-serve` paths. Selection refusal is distinct from an unavailable optional
accelerator. Once a warm mutation may have been written, missing/corrupt replies
or non-object/invalid-shaped replies are reported as an unknown outcome and
never cold-replayed. A refusal marker contradicting success or result fields is
also an unknown outcome. Only an unsuccessful, result-free pre-effect refusal
allows direct-context fallback. Already-admitted
calls and attached sessions are not canceled by this new-work guard.

## Independent outgoing outcomes

Installation reports predecessor retirement and slot collection as deferred.
It does not invoke the version reaper, including with a force option, and does
not reset control state to make incoming publication succeed.
`AGENT_MCP_NO_VERSION_REAP` is a legacy installer opt-out, not an inhibition
of the explicit reaper helper. Explicit non-terminating inspection requires
`--check-only`.
Retained slots preserve lazy imports for clients already launched from them;
their collection needs separate qualified ownership and lifetime evidence.

Preserving an existing old client is **not** session-preserving reattachment.
Pre-feature wrappers cannot gain new protocol code retroactively. Full adopted
wrapper handover, credential isolation and independent central retirement
remain governed by the
[active handover effort](../../../efforts/active/mcp-session-generation-handover/README.md).

## Qualification

The focused `tests/test_incoming.py` packet covers capable-but-stale admission,
activation inhibition, foreign installation refusal, changed selection/endpoint,
failed candidate health and occupied legacy discovery with an empty global
catalog. These are isolated source-boundary controls, not installed acceptance.

The opt-in Windows `tests/test_incoming_installer.py` witness sets
`COPILOT_EXTENSIONS_MCP_NATIVE_INSTALL_QUALIFICATION=1` and runs through the
[canonical bounded runner](../../../TESTING.md). It performs two ordinary native
installs into explicit private roots, keeps one PATH literal bridge connected,
and proves that a fresh literal command executes different Pipeline code in the
incoming physical slot despite unchanged package versions. The old upstream PID
and single initialization survive; retirement tripwires are not called. Both
empty global discovery and installer-discovered configuration are exercised.
The discovered variant additionally constructs the same physical version in a
second installation, refuses its shared-handle publication and preserves the
first installation's route and attached upstream.
The installer/backend receives fixture credential A while fresh bridge and call
clients receive B; actual upstream tool output must report B. Old interpreters
paused across selection changes must refuse both warm/fallback and no-serve
calls, with no stale mutation recorded. This does not qualify reattachment,
POSIX installation or an installed consumer, nor does it inherit the managed
application owner's separate namespace/rollback/reuse qualification.

Prepared installed-application qualification has a separate authentic-input
prerequisite in `tests/test_installed_application_namespace.py`. Its explicit
opt-in requires `COPILOT_EXTENSIONS_MCP_APPLICATION_QUALIFICATION=1` and an
approved exact `COPILOT_EXTENSIONS_MCP_APPLICATION_SOURCE_COMMIT`. It uses the
existing Source/profile preparation owners and their governed-feed policy;
preparation alone is not application or namespace compatibility proof.
This separately owned prepared-application qualification remains open under
[#10742](https://github.com/ThomasMichon/copilot-extensions/issues/10742) and
the #7028 campaign. It does not gate the operator-approved ordinary-installer
slice, and that slice makes no claim that the prepared matrix has passed.
The test environment's missing governed inputs are not an ordinary incoming
failure or a waiver: actual incoming authentication, build, health and admission
failures still prevent ordinary installation completion.

See also: [agent-mcp](../README.md).
