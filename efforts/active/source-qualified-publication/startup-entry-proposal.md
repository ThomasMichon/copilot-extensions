# Proposed bounded current-feed entry

Back to [the effort](README.md) and [existing evidence](validation.md).
**Plan amendment; implementation awaits this source review gate.** The exact
scope is operator-admitted, subject to fresh ownership checks. F1 and its
startup diagnosis remain preserved in `ee0baacc4` and `5a1dcc818`.

## Current ownership evidence

Read-only forge state checked on 2026-10-23:

| PR | Current head | Exact relevant ownership |
|---|---|---|
| #8031 | `36ccbd5325fa55783d0a65daab80262cfa73c1a7` | `__main__.py`: import hunk at 115, launch hunk at 2166, monitor sweep hunk at 3074 |
| #8045 | `f499860010ab3944ad9ef1044afd172539fbbf3c` | `__main__.py`: `_run_session_lifecycle` hunk at 3949 |
| #7788 | `f7da36b87da5850bc0b6f351d5abeb5b77a12782` | `update_stage.py`, `update_launch_cli.py`, their tests, launch-session scripts and associated documentation |
| #7449 | `01dadfc9437524aed0c256049d2c43acbb8e2192` | `manager_launch_cli.py`, `test_cli_routing.py`, `test_manager_probe_diagnostics.py` and associated documentation |
| #7833 | `97d9820907281f23dd367cfda3d8aab668d1ad47` | Shared process-lifecycle library; no library changes are admitted here |

All were open. The issue still preserves the bounded F1 reservation. The
candidate new early-entry hunk is disjoint from those `__main__.py` hunks,
**not globally unclaimed**. Recheck exact heads and
candidate-file claims at implementation admission. No existing agents were
contacted to infer permission.

## Causal target, not a new latency claim

Existing evidence shows the same case fails before the first owner request
with and without F1. The new observation-contract module measured 3.5 ms
self import; it is not a supported explanation for the multi-second delay.
Using the same prepared interpreter, 1,100-row native case and unchanged
two-second verdict, F1 reached first health at 8.640 s/done at 8.859 s;
pre-F1 source reached health at 6.110 s/done at 6.328 s. Processor samples
were 100% before/after both runs. These are uncontrolled observations, not
worst-case qualification or causal attribution of the difference to F1.

`__main__.py:51-119` eagerly imports the broad command surface before its
`main()` sees argv. Moreover, `list` is absent from `_LAZY_DISPATCH_TABLE`,
so `main():6440-6458` reaches `_ensure_cluster_loaded()` and later
`_load_full_command_surface()` before dispatching `cmd_list`. The chosen
`cmd_list` branch immediately calls `worktrees_client.emit_current` when
`--follow` is present; none of the unrelated launch/maintenance command
implementations is needed for that branch.

An early selector alone is insufficient: `worktrees_client` eagerly imports
`status_monitor_runtime`, and `status_monitor_cutover.routing_dir()` imports
that module even when its caller already supplied `runtime_home`.
`status_monitor_runtime._aw_runtime_home()` invokes `_core_helper`, whose
`_core()` imports `__main__`. That is a concrete route back into the full
dispatcher, not merely an optional import optimization.

The proposal removes these *structural* dependencies on the healthy feed
path. It does not assert their cumulative profile times are additive or
promise a two-second pass on the observed 100%-load host.

## Proposed API and exact file surface

All paths below are under `plugins/agent-worktrees/` unless stated otherwise.

| File / declaration | Proposed bounded delta |
|---|---|
| `src/agent_worktrees/__main__.py` | One new direct-`-m` entry selector immediately after the future import and before existing imports. Leave import 115, launch, sweep, lifecycle and all existing fallback dispatch code untouched. |
| New `src/agent_worktrees/current_roster_entry.py` | `select_current_feed(argv) -> Callable[[], int] \| None`, plus `console_entry()`. Syntactic selection is side-effect-free; accepted callback owns normal argument/context setup and calls existing `emit_current`. |
| `pyproject.toml:117`, `[project.scripts]` | Point `agent-worktrees` to `agent_worktrees.current_roster_entry:console_entry`. Without this declaration, optimizing only `python -m` would leave the installed console wrapper on the old import path. No installer, generated binstub, native broker or launch-session edits. |
| New `src/agent_worktrees/list_arguments.py`; `list_cli.py:63-162` | Extract the existing list parser declaration unchanged; retain `list_cli.add_parsers` as a forwarding owner. The early entry must not maintain a second flag/default/validation table. |
| New `src/agent_worktrees/invocation_project.py`; `front_door_cli.py:38-97,290-355,408-473` | Extract/reuse the exact explicit-project/CWD helpers in a lightweight module. Retain compatibility wrappers and injected override seams in `front_door_cli`; do not import its Manager-launch projection at 568 on the early path. No edits to `manager_launch_cli.py` or installer modules. |
| `worktrees_client.py` | Load monitor recovery machinery only inside the existing recovery/unavailable branches. Healthy reads use the canonical `cfg.install_dir()` root and pass it explicitly to existing endpoint/token path functions. Preserve supported monkeypatch/override access lazily where necessary. |
| `worktrees_feed_client.py:_client` | Pass that same canonical runtime root to the existing routing/token helpers. Preserve health/capability checks, recovery conditions and authentication. |
| `status_monitor_cutover.py:routing_dir` only | Move its monitor-runtime import inside the `runtime_home is None` branch. Explicit-root callers must not load owner recovery code. Default owner callers remain unchanged; no cutover/election/drain behavior changes. |
| New `tests/test_current_roster_entry.py` | Selector, parser/context parity, console/module delegation and import-boundary tests. Do not edit #7449's routing/probe test files. |
| Existing `tests/test_worktrees_feed.py` and F1 client tests | Retain the same actual CLI/1,100-row/two-second case and existing authenticated control contract. |

The extra shared files requiring admission are therefore the single early
`__main__` insertion, console-script declaration, parser/context extraction
sites, and the conditional-import-only `routing_dir` hunk. The two Worktrees
client modules already belong to F1. No shared library or server rewrite is
part of this proposal.

## Admission and command semantics

Initially select only unambiguous canonical explicit-project invocations:
`--project <name> list ... --follow` (including the existing Manager flags).
Require exact option spellings and a canonical known list shape for the
pre-selector. Duplicates, abbreviations, alternate prefix/sibling routing,
implicit-CWD discovery, help, unknown flags, missing values and every other
verb **decline** to the existing entry before side effects. Do not invent
new errors for declined inputs. The accepted callback still uses the shared
canonical parser, not values guessed by the pre-selector.

Preserve these semantics, rather than treating them as removable overhead:

- Capture invocation CWD and configure UTF-8 stdout/stderr as today.
- Consume the canonical explicit-project form, validate the project name,
  clear the existing routed-project marker, and set active project in-process.
  Never trust inherited `WORKTREE_PROJECT`/`WORKTREE_ID`.
- Preserve explicit-project “assume anchor unless already inside the project”
  CWD behavior, including vanished CWD, bare/worktree anchor resolution and
  current error/fallback behavior. Do not substitute `record_config.metadata_project`
  merely because it is cheap: that API deliberately has different semantics.
- Keep the current project/installation-cell configuration and registry-root
  readers. Extracting context helpers must not fork registry precedence or
  make an alternate config resolver. Where an existing installer module
  owns a read-only registry function, retain that call without changing the
  installer; no setup/install operation runs.
- Preserve `--follow` authority, selectors, platform/default status filtering,
  history/refresh validation, demand cache key and `note_demand`.
- Use `_shutdown_exit.run_and_exit` around the selected callable, **before**
  execution, preserving KeyboardInterrupt/SystemExit and the narrowly scoped
  CPython shutdown behavior. A declined console entry imports and delegates
  to the existing console entry exactly once.

No entrypoint extraction may force-import `__main__` merely to inspect an
optional compatibility override. Use an already-loaded module or injected
callback; preserve existing consumers' overrides rather than constructing a
stub or silently losing them.

## Configuration, authentication and publication imports retained

Keep the existing `config`/registry/installation-cell readers, output/error
helpers, `list_cache`, `zdd.routing`, original `ControlClient`, token bounds
and read-only token handling. Keep existing health capability negotiation,
endpoint crash recovery and advisory labeling. The early selector does not
prove owner authority, create credentials, start another publisher or bypass
normal endpoint admission.

Keep `request_selection`, projection/row validators and `FeedReceiver`:
generation/cursor matching, begin/done and update/commit, bounded frames,
selection/count/order/delete consistency, v1/v2 metadata admission, structured
errors, deadline/cancellation and reconnect behavior remain the existing
contract. No F2 mutation/publication receipt is added or skipped.

Initially retain `status_monitor_cutover`'s existing client dependencies,
including `agent_procutil`; do not proactively refactor its owner/server
imports or shared libraries. If the unchanged gate remains red after the
admitted early-entry/back-edge repair, report the new evidence and request a
separate narrowly justified expansion instead of silently extending this one.

## Validation before publication

1. Assert selecting/declining argv performs no project/import/process effects.
2. Compare accepted parser results, output/exit behavior and explicit-project
   CWD transitions against existing canonical helpers; test decline cases
   through the normal entry. Cover vanished CWD, mismatched configured anchor
   and runtime cell, legitimate compatibility overrides, errors, interrupts
   and cancellation. Importing `agent_worktrees.__main__` as a library
   must never execute the fast path or exit the importing process.
3. Prove both `-m` and console declarations reach the same selected callable;
   default commands retain the existing dispatcher and shutdown semantics.
4. On a healthy synthetic owner, assert no `_load_full_command_surface`,
   `_ensure_cluster_loaded`, `_run_session_lifecycle`, monitor recovery import
   or installer operation occurs. Exercise absent-owner recovery separately,
   retaining the same owner boundary and error semantics.
5. Run the existing native full-roster case with its **unchanged two-second
   bound**, then relevant client/entry selectors. Retain F1's 145 Worktrees/
   83 Manager/native socket/1,000-row evidence; repeat broader suites only if
   changed contracts require it. No deadline waiver or red-check bypass.

No implementation precedes this amendment's source review. Scope admission
does not assert that shared paths are free to edit if a newer owner head
overlaps, nor waive the unchanged native gate. A passing unit-only declaration
test is not the required actual console-entry and `-m` smoke evidence.
