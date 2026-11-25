# Pattern: hook architecture (native shell, published files, fire-and-forget Python)

> **Serves the vision:** `visions/plugin-services` §`hooks-and-callbacks-are-transient`,
> §`process-count-scales-with-services-not-sessions`.
> **Building on:** [cold-spawn-latency-budget](cold-spawn-latency-budget.md)
> (what each process start costs),
> [session-scoped-dynamic-guidance](session-scoped-dynamic-guidance.md)
> (output-free guidance delivery),
> [uniform-runtime-resolution](uniform-runtime-resolution.md) (stable binstubs
> and version slots), and
> [windows-background-process-launch](windows-background-process-launch.md)
> (how a detached start stays invisible).
> **Enforced by:** `tools/check-hook-architecture.py` (CI, "Hook architecture").

This is the binding constraint document for every hook that a
copilot-extensions plugin declares in `plugins/<plugin>/hooks.json`
(`sessionStart`, `preToolUse`, `postToolUse`, `sessionEnd` and any later
event). Read it before you add or change a hook.

## Rules

1. **A hook does its primary logic in its native shell.** The `bash` entry
   runs POSIX `sh`/`bash`; the `powershell` entry runs Windows PowerShell 5.1
   syntax (no PowerShell 7-only features); a `.cmd` entry runs `cmd`. A hook
   must not start Python: no `python`, `python3`, `py`, `uv run`, `uvx`,
   `hook-uv-python` or bootstrap-Python helper, no inline Python heredoc, and
   no `.py` program.
2. **The plugin hook redirects quickly.** The plugin payload hook entry is
   thin. It locates the **installed stable hook binstub** for its plugin
   (installer-owned, under the plugin's install root, for example
   `~/.agent-worktrees/bin/pre-tool-hook.ps1`), and runs it. The binstub
   redirects to the selected **version slot** through the existing installer
   machinery (`runtime_install`, the `current` marker and `last-known-good`;
   see [uniform-runtime-resolution](uniform-runtime-resolution.md)). Do not
   add a second binstub or slot mechanism. When the binstub is not installed
   yet, the payload hook degrades (rule 5); it does not run the payload copy
   of a Python program instead.
3. **The hook reads published values; it does not compute them.** Anything
   that needs Python, network, `git` history walks, JSON-heavy processing or
   more than a few file reads is computed ahead of time by the owning service
   (daemon, installer or CLI) and written to a published file. The hook reads
   that file. See [Published-file contract](#published-file-contract).
4. **Python from a hook is fire-and-forget only.** The one permitted reason
   for a hook to cause Python to run is to start or restart a stopped
   daemon. The hook starts it detached (no inherited stdout/stderr, no wait
   on its exit or output, hidden window on Windows), through the installed
   binstub, and returns at once. The hook never blocks on Python, never reads
   its output, and never retries in a loop.
5. **Missing or stale data degrades fast.** If the binstub, the published
   file or the daemon is not ready, the hook emits nothing (or `{}` where a
   JSON object is required), or the last known published value with its
   staleness stated. It does not compute a replacement, install anything or
   wait. One short stderr line may name the gap.
6. **Keep startup output and behavior stable.** A conversion to native shell
   keeps the same stdout contract, exit code and written files as the program
   it replaces. Prove it with the plugin's existing hook tests plus tests for
   the native entry on both Windows PowerShell 5.1 and `sh`.

## Time budget

`timeoutSec` in `hooks.json` (10-45 s today) is a safety cap for a stuck
host, not a budget. The design budget is:

| Hook kind | Budget for the hook's own work (after its shell started) |
|---|---|
| `preToolUse` / `postToolUse` (runs on every tool call) | 50 ms typical, 100 ms worst case |
| `sessionStart` / `sessionEnd` | 100 ms typical, 250 ms worst case |
| Fire-and-forget daemon start | the detached spawn call only (under 30 ms); never the daemon's start-up |

The shell start itself (about 210 ms for `powershell.exe` on Windows, see
[cold-spawn-latency-budget](cold-spawn-latency-budget.md)) is paid once per
declared hook entry, so merge sibling entries instead of adding new ones.
Measure changes with [performance-measurement](performance-measurement.md).

## Redirect chain

```text
plugins/<plugin>/hooks.json entry (inline bash / powershell, thin)
  -> installed stable hook binstub   (~/.<plugin>/bin/<hook>.{sh,ps1,cmd}, native shell)
      -> version-slot implementation (~/.<plugin>/versions/<v>/..., native shell)
          -> reads published files   (~/.<plugin>/published/<topic>.*)
          -> optional: detached daemon start through the installed CLI binstub
```

The installed binstub is the stable path that survives plugin updates. The
version slot holds the code of the selected release. The plugin payload copy
is used only when the plugin has no installed runtime (payload-only plugins);
then the payload script is the native implementation.

## Published-file contract

A service that publishes a value for hooks follows these rules:

- **Location.** Under the plugin's own install/state root, in a `published/`
  directory: `~/.<plugin>/published/<topic>.<ext>` (Windows:
  `%USERPROFILE%\.<plugin>\published\`). Per-session values use the session
  state folder that the hook already receives
  (`~/.copilot/session-state/<sessionId>/...`). Never write published files
  into a source checkout.
- **Shape.** Line-oriented and shell-readable without a JSON parser: either
  the final ready-to-emit text (for example a guidance Markdown body or a
  complete hook JSON response), or `key=value` lines. The first line is
  `schema=<n>`; a reader that does not know the schema degrades (rule 5).
- **Freshness.** The file's modification time is the freshness signal. Each
  topic documents its maximum age (for example 24 h for install facts,
  5 min for live status). A reader treats an older file as stale: it uses
  the value only where the topic says that a stale value is safe, and states
  that it is stale.
- **Atomic write.** Write to a temporary file in the same directory, flush,
  then rename over the target (`os.replace` in Python). Readers never see a
  partial file. A writer that fails leaves the previous file in place.
- **Ownership.** One owning service writes each topic. Hooks only read. The
  owner refreshes the file when its inputs change (install, update, config
  change) and on its own schedule, not on hook demand.

## Allowed exceptions

The validator keeps an explicit allowlist in
`tools/hook-architecture-allowlist.json`. Each entry names one hook file (or
inline `hooks.json` entry), the reason and its status:

- `pending-conversion` — an existing hook that still starts Python and is
  scheduled for conversion. The list only shrinks: the validator fails when a
  listed file no longer starts Python (remove the entry), and when a new hook
  starts Python without an entry.
- `binstub-runtime` — a hook that must reach the Python runtime because the
  work cannot be precomputed (for example a policy decision that depends on
  the exact tool input). It calls the installed CLI binstub, fails soft and
  fast when the binstub or Python is not ready, and the reason explains why a
  published file cannot serve it.

Reading an installed venv's or slot's interpreter path to check that a
runtime is provisioned (`Test-Path ...\Scripts\python.exe`) is a file check,
not a Python start, and is allowed.

## What the validator checks

`tools/check-hook-architecture.py` reads every `plugins/*/hooks.json`, each
inline `bash` and `powershell` command, and each plugin script that an entry
names directly. In non-comment lines it reports a Python start when it finds
a `python`/`python3`/`py` command lookup or call, a call through an
interpreter variable (`& $python`, `-FilePath $py`, `"$py" -I`), `uv run` or
`uvx`, a Python selector helper (`hook-uv-python`, `bootstrap-python`,
`*_python` shell functions), or an inline Python heredoc. A `.py` file name
alone (for example in a hash or freshness list) is not a Python start. It does not follow helpers that those scripts call;
reviewers check the rest of the chain against this document.

Run it locally with `tools/run-python tools/check-hook-architecture.py`.
