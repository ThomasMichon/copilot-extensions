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

Inventory worktrees with the native, project-scoped readers:

```text
<worktrees-argv> --project <project> fleet --json
<worktrees-argv> --project <project> list --json --passive
<worktrees-argv> repos list --strict --json
```

`fleet` reads one project on every Copilot-enabled, SSH-ready machine in one
bounded call: a single `list --json --passive` per target, through each host's
owner command rather than a project binstub. A target that is unreachable,
times out, lacks the command or returns unusable output becomes a row with
`reachable: false` and its `error`; record it as a coverage gap. `fleet`
covers **only the selected project**, so one call is incomplete for all-agent
discovery: active work also lives in other registered projects.

`--passive` makes one bounded attempt to read the host's status-monitor and
never starts, recovers, waits for or wakes it, nor renews its scan demand, so
repeated sweeps cannot trigger scans or hold off its idle retirement. When the
monitor is not answering, has not published the project, or predates the
demand-free read, the host returns its saved roster with
`availability.status == "advisory"`; that row's `availability` and
`projection.observed_at` mark it as saved and possibly stale. A host with no
saved roster, or an older runtime that rejects `--passive` as an unrecognized
argument, returns an error that is a coverage gap. Do not fall back to a plain
or `--cache-only` `list --json`: either may start a stopped status-monitor, wait
for a fresh publication and renew its scan demand. A saved or
unlabeled roster never proves a delegate's caller gone or terminal: `fleet`
reports a caller missing from it as `unknown` and keeps the delegate
non-finalizable (`host-roster-advisory`).

For fleet-wide coverage, enumerate projects on each target host, never only on
the observer. Run that host's `repos list --strict --json` and require
`inventory_authority == "complete"`; an error, a nonzero exit, or a reply
without that field (as from an older runtime that ignores or rejects
`--strict`) makes the host's project set unknown, a host-level coverage gap.
Keep entries whose class is `worktree` or `knowledge`. Read each one, either
with `fleet` once per project (it takes its machine list from the selected
project's configuration on the observer, so it suits projects the observer
also registers) or on that host with
`agent-worktrees --project <name> list --json --passive`. Give each project
read its own caller-chosen deadline inside an outer deadline for the sweep; do
not assume a universal value or raise it automatically. Append each project's
rows or its error as it completes, for example one JSONL line per project, so
a later timeout never discards earlier results. Projects not read by the outer
deadline are unobserved, not empty or offline.

Live work is rows whose derived `state == "active"`. Do not use lifecycle
`status == "active"`, a nonempty `live_session_ids` or `--glance` as liveness:
they include idle rows and dead or retained sessions. Keep each row's
`live_rest`, `fact_provenance` and `observed_at`, and each read's
`availability` and `projection.observed_at`, so busy, live-idle,
awaiting-operator and saved work stay distinct. Count a paired sibling (shared
`pair_id`) or a session that appears in several rows once. Record an identity
conflict, such as two rows naming one native session or a worktree's last
session differing from its live-session registration, as a finding; it never
selects an alternate steering recipient.

Keep coverage honest. Report unreachable hosts, saved, stale or partial
rosters, projects that are unregistered, unadopted or unavailable on a host,
and non-worktree sessions such as Bridge or provider sessions as unknown or out
of scope, never as zero active work. Count an empty list only for a current
(not advisory) read of a project that the host's complete registry confirms.

Do not sweep with `list-sessions`: it scans every session's history and can
take over a minute on a host with a large history. Use it, and the lineage
readers, only for an identified worktree or session:

```text
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

## Remote reads

For exceptional inspection of one explicitly selected session's instruction
listing/guidance or a requested maintenance notice, use the canonical
[bounded selected-session reader](selected-session-reader.md), not handwritten
shell collectors. Its host-local deadline remains effective after client loss;
it starts no child readers. Never add guidance-body inspection to routine cycles.

For cross-machine reads, first resolve the configured machine alias and shell
through the topology reader. For a re-resolved roster, read the manifest and
the SSH capability's read-only reachability check fresh each cycle; do not
reuse an earlier cycle's readiness. Prefer `fleet` and the per-host
`list --json --passive` read to ad hoc remote scripts. A remote host has no
session command catalog: invoke its installed owner command `agent-worktrees`
with an explicit `--project`, not a project binstub, which a host need not
have.

If the topology reader loses its state-root binding, read the bound topology
manifest by its explicit path rather than guessing machines, and record that
fallback in the cycle log. A previously observed host that becomes unreachable,
for example on an SSH key-exchange or banner failure, keeps its last observed
owners as unobserved coverage gaps, not stopped or complete. Read it again next
cycle; do not retry it in a loop or repair it.

When an ad hoc remote read is still necessary, keep it bounded and headless:

- Run `ssh -o BatchMode=yes -o ConnectTimeout=<seconds>` with stdin closed, so
  it never prompts or reads the caller's input.
- Set `AGENT_WORKTREES_NO_SELFPROVISION=1` in the remote command's environment.
- Bound the command on the remote side using its owner's finite reader and
  whole-child-tree cleanup when it starts children. A local timeout ends
  only the client; some SSH servers keep the remote process running after the
  client disconnects.
- Print the reader's own JSON. Avoid deep object serialization on the remote
  host, which can be slow or unbounded.

Never install a tunnel, connect a stopped venue, write a remote collector, or
send a new agent prompt to ask for its status. Remote stdout may be captured in
the audit owner's private scratch.

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
service before querying it. When the client offers a documented no-ensure
switch, set it for every read, including remote ones. For `agent-bridge` it is
`AGENT_BRIDGE_NO_ENSURE=1` in the process environment: a command that would
otherwise ensure the service then only probes for a running one, so a down
service fails the read instead of being started. A reader run without the
switch is not read-only. If no no-wake path is available, use already
available source snapshots or report that reader unavailable.

A live-session registry can be per host: one host's roster may omit sessions
on another host or in a guest environment on the same machine. Read each host
in the roster rather than inferring fleet coverage from one registry. A
session's turn state can read running while it waits on a parked question;
read its pending-input or attention field too (see
[parked questions](pending-input.md)).

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
