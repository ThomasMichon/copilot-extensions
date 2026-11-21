# Exact-session budget cue

The output-free `sessionStart` writer reads the same inert configuration and
pure posture used by `budget-guidance status`. It never grants an external
reader, calls a provider, opens a new SDK client, fetches credentials, or
provisions a runtime to compute the cue.

The default configuration remains `$BUDGET_GUIDANCE_CONFIG` or
`~/.budget-guidance/config.json`. A relative environment-selected path is
relative to the hook payload's session `cwd`, not the plugin's process directory.
Configuration must be a regular file and is limited to 65536 bytes. Missing,
invalid, unreadable, or oversized configuration produces explicit unavailable
evidence, not numeric defaults. External references remain unauthorized.
No repository-owned hook configuration can grant executable authority.

## Delivery and attribution

The owning payload's stdlib-only Python writer imports its own pure posture
source. It does not install an interpreter or import an installed application's
posture implementation. Bash and PowerShell wrappers resolve
the plugin root through the runtime's documented root variables and emit exactly
`{}`. The PowerShell wrapper suppresses console allocation at process creation,
drains both output streams concurrently, and uses the shared native bootstrap
selector to inspect the payload's declared uv-managed Python. PATH `python`,
`python3` and `py`, registry discovery, and foreign virtual environments are not
fallbacks. Explicit setup prepares missing uv/interpreter prerequisites; this
hook cannot download or provision them. Windows setup records its selected
bootstrap interpreter in the existing deployment manifest. The cue uses that
prepared stdlib execution base (or the existing explicit
`COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON` handoff) before asking uv to select the
writer. An older installation without that selection needs explicit setup;
there is no cold PATH-Python fallback. A custom installation can supply
`-InstallRoot` to the PowerShell entry.
Selection, asynchronous stdin delivery, stream capture and the writer's exit
share one five-second work deadline plus a one-second cleanup reserve. The
original PowerShell caller uses the prepared shared process-helper capsule to
contain uv and its cold-cache interpreter query in a strict Job. Query
completion, timeout and stream settlement precede return from that owner.
The selected writer remains a direct, child-free process of the original
PowerShell caller; no `uv run` or venv redirector is inserted before it.
The POSIX wrapper still requires an available Python 3.10+ command; its managed
selection migration remains separate from the Windows entry.
The POSIX wrapper uses the suite's canonical EXIT trap to emit one empty JSON
envelope on both success and failure, making its output-free behavior
recognizable by the existing session-context contract scanner.
The isolated POSIX writer arms its own five-second real-time process alarm
before resolving or importing payload source;
it does not depend solely on the host's outer hook timeout or a platform-specific
`timeout` executable. It starts no child processes of its own.
Missing writer/interpreter or write failures log a fixed, redacted diagnostic
without blocking startup.

The writer validates the runtime-provided `sessionId` and writes only:

```text
<session folder>/instructions/budget-guidance/session-guidance.instructions.md
```

Each successful fresh or resumed invocation atomically replaces that file.
Unknown source values are allowed; there is no invented source allowlist.
Directory symlinks and Windows reparse points are refused. A failed atomic
replacement leaves the previous launch snapshot intact rather than partially
publishing new data; its evaluation timestamp remains authoritative.

The file starts with `[owner: budget-guidance@<version>]` and contains one
versioned JSON data cue: evaluation time, availability/error, selected field
values and source/capture/freshness attribution, adapter states, contradiction
counts, and the shared calculated posture. Detailed contradiction values,
trailing-window arrays, raw paths, and opaque adapter error text are omitted.
Configuration labels remain data, not instructions.

The complete UTF-8 file is limited to **4096 bytes**. ASCII-escaped JSON also
keeps it within the suite's **1024-token heuristic** (`ceil(characters / 4)`);
this is not a tokenizer guarantee. An oversized cue is replaced by a small
explicit-unavailable cue with all budget values withheld, never truncated
numbers or silently dropped attribution.

The plugin's reviewed instruction projections provide the minimal file pointer
and a separate static advisory boundary. The computed file contains facts only,
not regenerated reference prose or policy. The pattern needs no cross-plugin
aggregator and does not claim native `additionalContext` composition.

## Snapshot limits

A cue is a **launch-time snapshot**, not continuously refreshed budget evidence.
Retain its evaluation instant and the selected fields' capture/freshness
metadata; an optional `budget-guidance status --json` lookup can clarify
expired or uncertain evidence.
Hookless or missing-interpreter launches have no new cue; an older retained
file is not proof of current state.

Budget remains informational: usage/quota and optional monthly economy advice
do not select models or delegation strategies, clear holds, bypass review/safety
gates, or block requested work. No upfront budget check is required. The cue grants no worker-control,
sleep, suspension, or inferred-concurrency authority. Provider refresh,
selected-account proof and rollout are separate gates.

The opt-in Windows desktop witness includes the session-cue wrapper alongside
the external-reader path. Its two successful cue cycles use an identity-recording
trampoline that delegates via `runpy` to the unchanged owning writer source;
the trampoline is the console process's literal entry point. The third cycle
substitutes a sleeping workload to exercise the production wrapper's unchanged
five-second timeout and owned cleanup. It observes console visibility, foreground
and Default Terminal acquisitions, pins process creation identities, and requires
the writer's actual parent to be the observed PowerShell root, with complete
cleanup. Native acceptance of this new mode is deferred to
[#6644](https://github.com/ThomasMichon/copilot-extensions/issues/6644), which also
tracks an unresolved reader-mode deadline failure. The failed native run is not
a desktop pass; source-only or Linux runs are not desktop proof. The follow-up
retains the original limits and requires literal timing and cleanup evidence.

See [the plugin overview](../README.md), [Configuration](configuration.md),
[Architecture](architecture.md), and the suite's
[session-scoped delivery pattern](../../../docs/patterns/session-scoped-dynamic-guidance.md).
