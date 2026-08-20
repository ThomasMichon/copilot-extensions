# Resume-spawn crash-isolation repro (#1612)

Reproduction / validation harness for the **core** half of
[dotfiles#1612](https://github.com/tmichon_microsoft/dotfiles/issues/1612): a
stopped **local `command`** session whose persisted target executable has since
**moved / been cleaned** (a venv / worktree / version-slot path), so the resume
`CreateProcess` raises `FileNotFoundError: [WinError 2]`.

## The bug this guards against

During a long multi-turn local dispatch, re-attaching to a stopped `command`
session drives a **resume**, whose `spawn` → `asyncio.create_subprocess_exec`
raised `FileNotFoundError [WinError 2]` because the persisted `session.target`
pointed at a path that no longer resolved. On the reporting build this escaped
the async `resume_session` handler and took the **whole uvicorn listener down**
(connection-refused, not a per-session 500); afterwards even a **fresh**
`start_session` 500'd — *"once the spawn machinery is poisoned, no session can be
created"* — and the daemon could not rebind.

## The invariant it pins

A resume whose target cannot be spawned must fail as a **contained, per-session
error** and leave the `SessionManager` fully able to serve everything else. On
dev297 the `resume_session` recovery ladder (`except Exception`, #1468) contains
the missing-target `FileNotFoundError`: the ladder re-rolls, then ends the
session `stopped` and re-raises a caught `OSError` — it does **not** poison the
event loop / spawn path.

## Running the harness

Hermetic Python driver (no real copilot, network, credentials, elevation, or a
live daemon — uses a fake ACP copilot child, `fake_copilot.py`), run from the
plugin's dev venv:

```powershell
.venv\Scripts\python.exe spikes\resume-spawn-crash-isolation\repro.py
```

`repro.py`:

1. Starts a real process-owned `command` session (fake ACP copilot child),
   drives it to `idle`, then **stops** it.
2. Repoints its `session.target` at a **nonexistent executable** and calls
   `resume_session`, asserting it raises a **contained** `OSError` /
   `FileNotFoundError` and the session lands terminal (`stopped`), not wedged in
   `starting`.
3. Asserts the spawn machinery is **not poisoned**: a **fresh** `start_session`
   on the same manager still reaches `idle` after the failed resume (the #1612
   "no session can be created" escalation must not reproduce).
4. Asserts resume itself is healthy: repointing the stopped session back at a
   **valid** target resumes to `idle` (the failure was target-specific).

Exit code 0 = all checks passed. Everything is cleaned up on exit.

## Note — the still-open route-mapping gap

The repro prints an informational `[NOTE]`: the raised type is an `OSError`
subclass, which the HTTP `resume_session` route (`routes/sessions.py`) does
**not** map — it catches only `KeyError` / `ValueError` / `RuntimeError`. So
containment currently relies on the `SessionManager`'s `except Exception` ladder
rather than the route. Mapping `OSError` → `500` at the route (so a missing-target
resume is a clean per-session `500` by construction, independent of the ladder)
is the remaining hardening #1612 asks for.
