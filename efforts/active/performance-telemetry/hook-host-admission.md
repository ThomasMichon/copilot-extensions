# Windows hook capability smoke: prelaunch admission result

**Result: BLOCKED BEFORE HOST LAUNCH (2026-10-23 UTC).**
This is bounded evidence for the next step in
[the reviewed host-contract plan](hook-host-contract.md), merged by #8118,
not a timing qualification, new implementation plan or production hook change.
The coordinator admitted at most one isolated capability-only host invocation,
conditional on proving custody, a supported deterministic event trigger and
the complete effective fixture plus required-policy roster before launch.
The latter two conditions were not established. **Zero experimental Copilot
sessions and zero fixture hook invocations ran.**

## Source and public-host identities

* Source revision: `9db3e9242c8ee3b8d27fc0ca6d46d348129db083` on `dev`.
  The earlier worktree was finalized; this attempt used a fresh managed workspace.
* `Get-Command copilot -CommandType Application` selected the public WinGet
  `copilot.exe` entry; invoking that exact absolute entry with `--version`
  returned **GitHub Copilot CLI 1.0.103**.
* The current session documentation helper still reported a missing
  **1.0.101** README. That session identity is not substituted for the
  separately resolved executable. No CLI private installation internals were
  inspected, and no runtime was restarted or switched.
* A captured invocation of the same absolute executable with `--help` returned
  exit **0**, **14,546 stdout bytes**, **0 stderr bytes**. It was a public
  help command, not a session launch. Machine-specific paths are retained in
  the task receipt, not committed as portable configuration.

## Custody preflight: one inert child

Selected the existing
`agent_procutil.owned_process.run_caller_owned` from this source revision,
not a hand-written spawn/kill layer. The
[owner contract](../../../libs/agent-procutil/README.md#caller-owned-native-execution-with-streaming-limits)
requires strict non-breakaway Windows Job custody, bounded concurrent stdout/
stderr draining, and zero active Job processes before successful return.

The exact logical invocation was:

```python
payload = (
    'import os,sys,json; print(json.dumps({"pid":os.getpid(),'
    '"argv":sys.argv[1:],"stdin":sys.stdin.read(),"cwd":os.getcwd()},'
    'ensure_ascii=True))'
)
argv = [resolved_python, "-I", "-c", payload, "literal space", "\u03bb"]
result = run_caller_owned(
    argv, timeout=10, max_stdout_bytes=8192, max_stderr_bytes=8192,
    input_text="h4-inert-input\n",
)
```

`resolved_python` was the already selected CPython 3.9.5 executable; cwd was
the owned source workspace. The child does not invoke a tool, network command,
hook or descendant. It only reports its own synthetic argv/stdin/cwd/PID.
The parent asserted exact argv, input and cwd equality, exit 0 and empty stderr.
This is an inert process-control witness, **not** hook input/provenance parity.

Win32 `EnumWindows`/`IsWindowVisible` and `GetForegroundWindow` were sampled
from before spawn until return, with a 10ms observer wait and a joined observer
thread. The observer counted every newly visible top-level window and every
foreground change, without reading titles or controlling other processes:

| Observation | Result |
|---|---|
| Inert child executions | 1 |
| Exit / stderr | 0 / empty |
| Exact synthetic argv, stdin and cwd | Matched |
| Window/foreground samples | 25 |
| New visible top-level windows | 0 observed |
| Foreground changes | 0 observed |
| Observer errors / unsettled observer thread | 0 / none |
| Captured-child cleanup | Successful strict-Job return; streams settled |

The exact owned PID was retained in the task receipt. No numeric-PID or
process-name termination was used. Sampling is finite evidence: it is not a
guarantee against sub-sample visibility, and it says nothing about a future
CLI hook process tree. There was no periodic workload or retry matrix.

**Retained error:** the first attempt to import the repository's existing
test observer stopped at `ModuleNotFoundError: pytest` before any child spawn.
No package was installed and no test was silently skipped. The observation
was then made with direct stdlib Win32 calls while retaining the same canonical
captured-child primitive. Only the one inert execution above occurred.

## Admission gates that did not pass

### Supported deterministic `preToolUse` trigger: not established

The official
[command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
and public `--help` do not establish a command that invokes a selected
`preToolUse` hook with exact supplied tool arguments before ordinary execution.
The [hook reference](https://docs.github.com/en/copilot/reference/hooks-reference#pretooluse--pretooluse)
defines the event and its payload; it is not an external dispatch API.

Interactive `!` executes a shell command, but the inspected documentation does
not promise that this path drives the same `preToolUse` boundary. A model prompt
requesting a particular tool is not deterministic input. `workflow run` is a
documented workflow command, not proof of a hook-test entry point; no workflow
or extension was authored or run to bypass this missing evidence.

`sessionStart` has a documented startup trigger, but substituting it would not
demonstrate the retained tool-event boundary. No claim is made that all hook
events lack deterministic triggers, or that a suitable supported interface
cannot exist elsewhere. This admission attempt did not establish one.

### Complete prelaunch effective roster: not established

The same official command reference, after **Using `copilot lsp`**, says:

> Custom agents and session-scoped hooks aren't covered by `copilot instruction`,
> `copilot lsp`, `copilot plugin`, `copilot mcp`, or `copilot skill`. All require
> a live session.

The session help advertises `/env` for loaded environment details including
hooks. That is a live-session inspection surface, not a prelaunch inventory
receipt. The hook reference lists policy, user, repository and plugin sources;
`--plugin-dir` adds a plugin, but does not attest that these other sources are
absent or identify their effective admitted composition.

Static file enumeration alone was not credited as the actual host's effective
policy/trust roster. No user settings, policy, installed plugin registrations,
trust decisions or hook-disable flags were changed to manufacture an isolated
roster. A complete roster could not be asserted before the authorized launch
boundary, so the experiment stopped there rather than launching to discover it.
This is an **unmet admission prerequisite**, not proof of a host defect.

## Native signals and cleanup

No experiment host command was selected or executed. The native file exporter
was **not enabled**; no OTLP endpoint/header, content-capture setting or collector
was touched. Consequently fixture argv/stdin/cwd/plugin-provenance delivery,
decision consumption, pre-spawn timing, cleanup events and native export
coverage are **not observed**, not missing-at-runtime or zero-cost results.
No fixture path with spaces/Unicode was presented to the host; the inert
child's literal Unicode argument is not a substitute for that pending check.

No experimental session, fixture registration, output file, background helper
or process-custody obligation remains. No shared service, installed payload,
telemetry module, production hook or root effort file changed. Required
policy/trust protections were not bypassed.

## Coordinator return

The exact missing capabilities are (1) a supported deterministic way to drive
the intended tool-event input and (2) authoritative effective-roster evidence
available before experimental host launch. Parent review must decide whether
to obtain those contracts or explicitly revise the admission sequence; this
report authorizes neither. No external issue was filed or host owner contacted.

The ten root clauses preserved by #8086/#8118 remain unchecked and owned by
the active [performance effort](README.md). Manager priority and independent
entry/process/telemetry/parser/maintenance owners remain unchanged.
Previously observed **3.312s blocked-Git / 2.789s slow-body** cases remain
unresolved. No p99, two-second pass, unavoidable floor, release, installation
or serving-runtime claim follows from this inert preflight or its publication.

Managed workspace allocation separately reported its ordinary automatic source
workspace trust/permission registration and a missing optional
`machine-governance` payload-guidance warning. Neither was used as experimental
host admission, and no payload repair was attempted. The worktree manager owns
the corresponding finalization cleanup; the experiment made no manual shared
settings or trust-relaxation changes.
