# Read-only source Picker review

Use this developer lane to review the **production Picker from a checkout**
against existing owners' real local data, without installing the candidate or
waiting for promotion. It is separate from `picker mock` (simulated mutations)
and `--demo` (fixture data), and does not replace installed acceptance.

## Prepare once, then iterate

Prepare the repository's normal development/test environment:

```text
python tools/run-plugin-tests.py worktree-manager --prepare-only
```

Use its platform-specific Python under `.test-venvs`. For example, in
PowerShell from the source worktree:

```powershell
$python = '.\.test-venvs\win32\worktree-manager\Scripts\python.exe'
& $python .\worktree-manager\scripts\picker-review.py my-project
& $python .\worktree-manager\scripts\picker-review.py my-project --capture .\review.svg
& $python .\worktree-manager\scripts\picker-review.py my-project --pivot Workers --capture .\workers.txt --format text
```

On POSIX use `.test-venvs/<platform>/worktree-manager/bin/python` and the same
script/arguments. Run in an interactive terminal for the first command.
The script puts **only this checkout's Manager source** on its import path.
It does not import Worktrees domain code, rewrite an installed launcher,
select a runtime, start an agent or provision an owner.
Restart the command after edits; no build/promotion or updater is involved.

The banner includes `READ-ONLY` and a digest of the candidate Manager Python
sources, including uncommitted edits. `--candidate LABEL` overrides that label.
Stderr records the executing source directory and selected owner root.
Captures use the existing production Textual compositor, not a second renderer.
They can contain private real data: keep them local, review before sharing, and
delete them when no longer needed. A capture proves rendered state, not physical
keyboard/display timing or successful actions.

The installed CLI also exposes `worktree-manager picker review PROJECT` with
the same arguments; the checkout script is the explicit candidate inner loop.

## Read and effect boundaries

The selected installed Worktrees owner must already be running and support
`worktrees-feed-v1`; its installed CLI must support `picker-bootstrap --json`.
Review admits only that exact read-only bootstrap and `picker-paths --json`, using the complete
installed runtime rather than a self-provisioning shim. Roster reads use the
existing loopback feed and validate publications with the ordinary receiver. It
never runs the ordinary `list` CLI's demand-file/recovery path. Refresh reads
another publication; it does not gather/classify, repair or reconcile.

This initial lane is **local-only**. Profiles reads the local saved column;
remote columns remain unavailable. Plugin contributions are discovered without
materializing registry pointers. The qualified Workers catalog uses the
complete installed Dispatch runtime with coordinator autostart disabled, not
its self-provisioning payload shim. Its command and state root come from the
same selection and are bound in that call's environment, never by changing
the parent process environment. The Worktrees command is retained with its
owner root for the review session. Workers uses the qualified cancellable
one-shot JSON catalog even when its ordinary manifest enables streaming.
The declaration does not admit `--stream`, `--subscribe` or arbitrary catalog
options in this mode. Other contributed reads and streaming
providers show an explicit unqualified/unavailable error rather than running
arbitrary declared commands. Missing or outdated owners remain unavailable;
the candidate does not repair them or fabricate a healthy empty result.
Admitted read subprocesses use the existing caller-owned process-group/Windows
Job boundary, so timeouts and exit also clean up their synchronous descendants.
Session teardown cancels and drains pending bootstrap/catalog reads before
removing transient state, and refuses late reads. The POSIX adapter preserves
piped owner diagnostics, including older-owner bootstrap compatibility errors.

Contributed actions, form submissions/previews, shared draft writes, streaming
actions, worktree/session launches, profile apply, updates and maintenance are
refused at execution boundaries. Forms may be browsed, but no real save or
activation is offered by this mode. Candidate form recovery and crash state use
one disposable directory; normal tracing, update-cache checks, anchor healing,
Picker root registration and housekeeping are disabled. The refusal fence
survives UI teardown so late callbacks cannot regain live-write authority.
The disposable directory is removed on normal exit. Installed selectors,
owner configuration, credentials and service ownership are not changed.

This is a cooperative review mode for trusted candidate code, **not a security
sandbox** for hostile source or arbitrary providers. Ordinary Picker and mock
behavior remain unchanged outside this opt-in, fresh process.
