# Optional fabric readers

Back to [Monitor agent fleet](../SKILL.md).

This integration uses existing `copilot-extensions` capabilities, not new
commands. Acquire their current guidance and exact session command catalogs.
Invoke catalog argv exactly; do not guess a global executable or deployed path.
Use only installed, non-mutating readers. Missing service/runtime readiness is
a coverage gap, not permission to provision it.

## Workspace and session identity

Set `AGENT_WORKTREES_NO_SELFPROVISION=1` in the process environment for every
Worktrees invocation, including on the remote host. Catalog shims do not
automatically suppress provisioning for every diagnostic command. Treat a
refusal or missing runtime as unavailable evidence, not permission to install.

The supplied Worktrees command supports these diagnostic compositions:

```text
<worktrees-argv> list-sessions --all-projects --json
<worktrees-argv> list-sessions --all-projects --worktree <id> --json
<worktrees-argv> worktree-lineage --worktree <id> --json
<worktrees-argv> session-lineage --session-id <id> --json
```

Keep `--all-projects` when narrowing the roster to a worktree, or explicitly
select its owning project with `-p <project>`. Without either, `list-sessions`
searches only the current project and can miss a valid worktree from the roster.

Use the owning runtime's documented meanings for live/head/activation fields.
Retained active records and old metadata timestamps alone do not prove a
process is currently working. Resolve canonical efforts and their declared
agenda locators without parsing private owner databases.

For cross-machine reads, first resolve the configured machine alias and shell
through the topology reader. Use the SSH capability's bounded, headless
read-only execution procedure. Never install a tunnel, connect a stopped venue,
write a remote collector, or send a new agent prompt to ask for its status.
Remote stdout may be captured in the audit owner's private scratch.

## Existing Bridge observation

When the selected Bridge client/service has a documented no-wake read path:

```text
<bridge-argv> --json live-sessions list
<bridge-argv> --json live-sessions resolve --handle <worktree-or-session>
<bridge-argv> session-usage <session>
<bridge-argv> read <session> --range <first>:<last>
<bridge-argv> read <session> --tail <count>
```

Do not assume a status-like command is passive: some clients ensure or repair a
service before querying it. If no no-wake path is available, use already
available source snapshots or report that reader unavailable.

ACP event ranges are cursor-neutral. A represented interactive CLI's bounded
result snapshot is not a complete conversation; obtain its native transcript
through the available history reader. Do not use delivery/acknowledgement reads
that advance another consumer's cursor.

Use durable Copilot session identity for persisted references; Bridge's
temporary spawn handle is not a durable history key. Record independent errors
and observation times instead of implying atomic status/history/usage.

## History and optional messaging

The existing session-history/logger reader owns transcript parsing and archive
resolution. Select a bounded session/range; read configured synchronized sources
without running sync, compact, archive or repair operations. Keep stale/deferred
capture metadata attached to historical findings.

For explicitly authorized nudges, follow the Bridge and peer-engagement
capabilities after re-resolving the current recipient and holds. Use the
existing send operation with a prompt file or supported stdin, preserving
multiline text. Do not create a replacement session or send to a machine
default when the intended worktree's current owner cannot be resolved.

The fleet monitor's record retains pending/unknown delivery; the messaging owner
decides how to reconcile it. A successful transport response is neither worker
acceptance nor proof that the next action improved progress.
