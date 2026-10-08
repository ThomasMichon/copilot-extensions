# Repository issue loops

`repository-issue-loop` is the declarative composition of existing
agent-dispatch registrar, singleton-supervisor, emitter, supervised-lane,
headless ACP, exclusive-spawn, steering, and recorded-outcome primitives. It
realizes the agent-dispatch vision's recipe, scheduled-production,
pools-as-filters, headless-worker, steering, and recorded-outcome intent; it
does not add a second task or policy store.

Adopting one from scratch? Start with
[`repository-issue-loop-adoption.md`](repository-issue-loop-adoption.md) --
the declaration schema reference, the available worker identities, and a
worked example. This doc covers the recipe's internal behavior instead.

## Declaration and occurrence model

The adopter owns the complete active declaration under the canonical
`.copilot-extensions/agent-dispatch/registrar/`, with legacy
`.agent-dispatch/registrar/` fallback. The runtime reads the canonical
directory first and may merge an explicit marketplace-specific overlay from
`.copilot-extensions/agent-dispatch/marketplaces/<marketplace-id>/registrar/`.
Registrar discovery expands the effective declaration set in memory into:

1. `<name>-source`, a lease-gated periodic emitter; and
2. `<name>-workers`, a concurrency-one headless supervised lane.

`cadence_seconds` defines Unix-epoch-anchored occurrences. The emitter's
`tick_interval_seconds` may be shorter so missed work and failures become
visible promptly. The occurrence identity is stable across supervisor restart
and redeploy. A task already recorded for the current occurrence suppresses a
replay; any nonterminal task carrying the loop-wide exclusive key suppresses
later occurrences until terminal resolution. These checks use repository,
exclusive key, and occurrence identity rather than the mutable task `source`;
changing `source` preserves the active worker and occurrence history.
Task state is checked before forge discovery. An active loop task or task
already recorded for the current occurrence returns immediately without any
forge API call.

## Eligibility and reservation

Open issues become eligible only after `quiet_period_seconds` has elapsed since
their last update. All `include_labels` must be present, any `exclude_labels`
rejects the issue, the conventional `bootstrap` label is always excluded, and
existing visible reservations reject it. Remaining
issues sort by the first matching `priority_labels` rank, then `created_at`,
then issue number. `batch_size` bounds the selected set.

The forge boundary is intentionally narrow: list open issues, reserve, promote
to claimed, and release an orphan. Three adapters implement it today: `github`
(via `gh`), `azure-devops` (via the `az` CLI's `devops`/`boards`
subcommands, with an untagged work item's title, tags, and dates read through
`az boards work-item show` and its reservation markers carried as work-item
comments through the generic `az devops invoke` REST bridge), and `script`
(a declared subprocess that implements the same four operations itself --
see "The `script` provider" below). `repo` is
`owner/name` for GitHub and `organization/project` for Azure DevOps -- the
same two-segment shape either way; `script` interprets `repo` itself and
accepts any non-empty string.

GitHub discovery uses a bounded GraphQL
connection: at most ten 100-issue pages, with the latest 100 comments and
first 100 labels fetched inline per issue. This keeps call count proportional
to bounded pages rather than issue count; missing cursors, GraphQL errors, or
exceeding the 1,000-issue bound are observable failures rather than an empty
result. The declaration names the expected `forge.producer_login`; every read
or mutation verifies that the authenticated identity (`gh api user` for
GitHub, the Azure DevOps connection data's `providerDisplayName` for Azure
DevOps) matches that login and that the identity resolves the configured
repository/project. Credentials remain outside the declaration and follow the
caller's ordinary authenticated CLI boundary. Reservation markers are accepted
only from that verified login and only after strict field, issue-number,
state, occurrence, and task-id validation.

Azure DevOps work-item discovery has no natural per-repo boundary the way a
GitHub `owner/name` repository does: an Azure DevOps `organization/project`
can hold far more items than a single GitHub repo ever would, and a flat,
unscoped WIQL query can exceed the platform's 20000-item result cap on any
project with real history (confirmed live). Discovery therefore always
scopes its WIQL by `[System.TeamProject]` explicitly -- `az boards query
--project` alone does **not** scope the query to that project despite
appearances, confirmed live: an otherwise-identical query without an
explicit `TeamProject` clause runs organization-wide. A declaration may
narrow further with the optional, azure-devops-only `forge.discovery_scope`
(`work_item_types`, `area_path`, `max_age_days` -- at least one required),
rendered as additional `And`-joined WIQL clauses.

Read discovery may reuse a verified repository identity within one provider
instance. Mutations may not: each comment or label/tag mutation re-runs the
configured producer-login and repository checks immediately before invoking
`gh` or `az`, so a changed ambient credential cannot inherit an earlier
verification.

Forge mutation and coordinator task creation cannot be one transaction. The
source therefore separates visible attribution from authoritative election:

1. adds the configured reservation label and ownership marker comment;
2. atomically reserves each canonical forge/repository/issue key in the
   coordinator;
3. visibly releases any provisional marker that lost that election;
4. creates one deterministic goal-bearing task for the elected bounded set;
5. binds the coordinator reservations to that task and appends claimed markers
   with its id; and
6. on retry, promotes a reservation whose task exists or releases its own stale
   unclaimed reservation whose task does not.

The coordinator's unique resource key makes overlapping declarations safe even
when both observe the issue before either forge comment is visible: exactly one
loop wins before task creation. Each acquisition returns an opaque token;
binding and releasing require the exact key, provenance owner, and token. A
stale same-owner process therefore cannot mutate a replacement reservation
after expiry and reacquisition. An unbound election expires after
`orphan_after_seconds` for crash recovery; a task-bound election persists until
terminal reconciliation.

A task-create transport error is indeterminate rather than proof of failure.
The source re-queries by repository, loop exclusive key, occurrence origin, and
dedup identity. It binds a uniquely committed task and releases only after that
authoritative query confirms absence; an unavailable or ambiguous query leaves
the reservations intact for reconciliation.

Immediately before creation, the source renews every issue reservation using
its exact acquisition token. The task is first created as `proposed`, which is
not runnable. Only after every reservation binds successfully is it approved
into the worker queue. A post-create bind failure abandons that proposed task
with an explicit failed-reservation reason and releases only resources whose
exact tokens are still owned, so a short-TTL takeover cannot leave two runnable
tasks.

Subsequent ticks reconcile any task still in `proposed`. If every required
resource key is present under the expected occurrence owner and bound to that
task with its current token, the source retries approval. If the binding set is
missing or incomplete, it retries terminal abandonment instead. A failed or
lost abandonment response is followed by an authoritative task read;
reservations are retained while the task remains nonterminal and released by
exact token only after terminal state is confirmed.

Another loop's active marker or coordinator reservation is always a blocker.
Reconciliation never silently releases another loop's ownership. Forge labels
are tracked by exact label identity: loser cleanup retains a label only when
another active trusted reservation explicitly uses that same label, and removes
the loser's distinct label otherwise.

When a claimed task becomes terminal, its coordinator resource reservations
are released. An issue that is still open also receives a released marker and
has the claim label removed, so a later occurrence may reconsider it under the
same eligibility and retry policy. A completed task whose issue is already
closed keeps its historical forge marker but no longer holds the coordinator
resource.

## Worker charter

The generated task requires triage before implementation: duplicate,
already-done, vision/scope fit, and feasibility. Accepted issues follow the
repository's contribution flow through required checks, review, merge, and
issue closure. Unclear requests use a durable steering card. A blocked steering
task deliberately occupies the loop until explicit steer, release, or abandon.

Every turn ends terminal, with a steering card, or with a task-id waiter/resume
contract suitable for a cold headless body. Worktree-only nudges are forbidden.
Reusable headless workspaces are not deleted at completion; they must be clean
and synchronized.

The default blast-radius charter also forbids force-push, bypassing checks,
merging branches the worker did not create, selecting excluded/bootstrap
issues, and changing the active declaration. The adopter must explicitly set
`allow_self_config_changes: true` to relax only the last restriction.

## The `script` provider

`forge.provider: script` replaces the forge entirely with a declared,
repo-packaged subprocess that implements the same four backlog operations
itself (`list_open_issues`, `reserve`, `claim`, `release`) -- the
`extend-any-declaration` vision's script-path-hook model realized for this
engine (`visions/plugins/agent-dispatch/README.md`). The loop's own
scheduling/lease/quiet-period/dedup machinery is reused unchanged; the
script supplies only the domain-specific backlog source.

```yaml
forge:
  provider: script
  command: ["scripts/my-backlog-source.py"]
  cwd: "scripts"              # optional, defaults to the declaring repo root
  timeout_seconds: 30          # optional, defaults to 30; must be finite, > 0, and <= 1800
  producer_login: "my-bot"     # required (same field as github/azure-devops); forwarded unverified
  backlog: "pending-work-items" # optional; see "`repo` vs. `forge.backlog`" below
```

A relative `command[0]` (the script path) and a relative `cwd` both resolve
against the declaring repo root (the same `cwd` `repository_issue_loop`
already threads for `worker_identity`), never the daemon process's own
incidental working directory. `repo` is interpreted by the script itself and
accepts any non-empty string -- it is not required to be `owner/name`.

**`repo` vs. `forge.backlog`:** `repo` is never purely a `script`-local label
-- the loop also hands it to the task queue as the created task's routing
lane (`client.list`/`client.create`, and `project_for_task` resolving which
local project `embody` spawns into). A `repo` that is actually an arbitrary,
non-`owner/name` backlog label (e.g. `"pending-work-items"`) therefore risks
misrouting the task -- embody would try to spawn into a project named after
that label instead of the repo the declaration actually lives in. Set
`forge.backlog` (any non-empty string) when the script's own natural backlog
identifier differs from a real, embody-routable project: the script's four
operations then receive `forge.backlog` instead of `repo`, while `repo`
itself keeps routing the task normally. `forge.backlog` defaults to `repo`
when unset, so an existing declaration with no real project to route to --
and no need for one -- is unaffected.

**Known limitation with `extends:`:** that repo root is always the *leaf*
declaration's own root, never the directory of whichever hop in an
`extends:` chain actually supplied the `script` override. A declaration
that inherits a *relative* `forge.command`/`forge.cwd` is refused outright
at registration (a clear `RegistrarError`) whenever the *nearest hop that
actually defines that field* (not merely some hop elsewhere in the chain)
lives outside this repo -- rather than silently resolving it against the
wrong root. Generalizing per-hop origin tracking to cover these fields,
the way `kind: emitter`'s own `spec.cwd` already does, remains a known,
tracked gap. Use an absolute `command`/`cwd` for a cross-repo inherited
override (or declare `forge.command`/`forge.cwd` directly in the
extending file instead of inheriting them) until that gap is closed. A
field whose defining hop lives in this same repo -- even if some other,
unrelated hop further up the same chain lives elsewhere -- or a leaf file
that declares `forge.command`/`forge.cwd` itself rather than inheriting
them, is never affected by this.

The resolved `command[0]` is then prefixed with whatever interpreter/shell
its suffix requires, mirroring the plugin companion script-path resolver
(`companion.py`): `.py` is run via the current Python interpreter
(`sys.executable`), `.sh` via `bash`, and `.ps1` via `pwsh` (or, on
Windows, a fallback to the bundled Windows PowerShell). Any other suffix is
executed directly, relying on its own executable bit/shebang -- which works
on POSIX but not on Windows, so a cross-platform declaration should use one
of the three recognized suffixes.

An explicit `forge.namespace` (any non-empty string) overrides the
coordinator's auto-derived resource-key namespace outright -- set this for
a correctness-critical redundant/failover deployment (the same declaration
running on more than one host) rather than relying on the auto-derived
identity, which is a best-effort git-remote probe cached by resolved repo
root for the life of the daemon process (not re-run on every tick) and can
fall back to the machine-local checkout path if that probe ever fails. A
transient failure on that first probe is cached the same way, until the
process restarts -- so the auto-derived namespace is stable between ticks
on one running host, but can still differ across a restart, or across two
redundant hosts whose first probe happened to fail differently.

### Subprocess JSON contract

The runtime invokes `<command...> --op <name>` once per operation, writing a
single-line JSON request object to the script's stdin and expecting a single
JSON response object on stdout:

| Op | Request body | Expected response |
|----|---------------|--------------------|
| `list_open_issues` | `{"repo": ..., "producer_login": ...}` | `{"issues": [<issue>, ...]}` |
| `reserve` | `{"repo": ..., "issue": <issue>, "reservation": {...}, "producer_login": ...}` | any object (ignored) |
| `claim` | `{"repo": ..., "issue": <issue>, "reservation": {...}, "task_id": ..., "producer_login": ...}` | any object (ignored) |
| `release` | `{"repo": ..., "issue": <issue>, "reservation": {...}, "reason": ..., "producer_login": ...}` | any object (ignored) |

The request body's `"repo"` is `forge.backlog` when the declaration sets it,
otherwise the declaration's own `repo` (see "`repo` vs. `forge.backlog`"
above) -- the wire field name is unchanged either way, only the source of
its value differs.


An `<issue>` object is
`{"number": int, "title": str, "url": str, "labels": [str, ...],
"created_at": float, "updated_at": float, "reservations": [...]}` (epoch
seconds for the two timestamps). `producer_login` is always the declaration's
`forge.producer_login` -- unlike `github`/`azure-devops`, the script is
trusted to interpret or ignore it itself; nothing verifies it against the
script's own identity. Empty stdout is treated as `{}` rather than an error.

A non-zero exit is always a real error (stderr surfaced verbatim, truncated
to 400 characters); so is exceeding `timeout_seconds`, a start failure (e.g.
the command is not executable), malformed/non-JSON stdout, or a JSON value
that is not an object -- none of these are silently treated as an empty
success. `list_open_issues`'s response additionally requires an `issues`
list of well-formed issue objects; a malformed entry is also a real error.

### Reservation object schema and state transitions

An issue's `reservations` entries carry the loop's own
reserve/claim/release history -- the same state machine a `github`/
`azure-devops` adapter encodes as a marker comment (see "Eligibility and
reservation" above), just reported directly as JSON objects instead of
parsed out of comment text. Each entry is:

```json
{
  "loop": "<declaration name>",
  "occurrence": 0,
  "state": "reserved",
  "at": 0.0,
  "label": "<reservation.label>",
  "issue": 0,
  "task_id": "<task id, 'claimed' only>",
  "reason": "<release reason, 'released' only>"
}
```

`loop`/`occurrence`/`state`/`at`/`label`/`issue` are always required;
`task_id` is required (non-empty string) when `state` is `claimed`,
forbidden (must be absent/`null`) when `state` is `reserved`, and optional
when `state` is `released` (carrying forward the claim that was released,
if any); `reason` is required (non-empty string) when `state` is
`released`, and forbidden when `state` is `reserved` (unvalidated,
effectively ignored if present, for `claimed`). `issue` must equal the
containing issue's own `number` -- a mismatch (or any other schema
violation) is rejected as a malformed issue entry, the same way a
mistyped core field is.

The script, not the runtime, owns persisting these markers so a later
`list_open_issues` call reports them back:

1. On `reserve(repo, issue, reservation)`, persist `{**reservation, "issue":
   issue["number"]}` (`reservation` already carries `loop`/`occurrence`/
   `state: "reserved"`/`at`/`label`) against that issue.
2. On `claim(repo, issue, reservation, task_id)`, persist a new marker with
   `state: "claimed"` and `task_id` set (the same `loop`/`occurrence`/`at`/
   `label`/`issue` as the reservation).
3. On `release(repo, issue, reservation, reason)`, persist a new marker with
   `state: "released"` and `reason` set (carrying forward `task_id` if the
   release follows a claim).

The loop reads the full accumulated history back (never just the latest
entry) to reconcile orphaned reservations and in-flight claims, so a script
backlog must retain every prior marker for an issue, not just overwrite it.

## Operations

```bash
agent-dispatch repository-issue-loop setup <declaration>
agent-dispatch repository-issue-loop inspect <declaration>
agent-dispatch repository-issue-loop discover <declaration>
agent-dispatch repository-issue-loop status <declaration>
agent-dispatch repository-issue-loop doctor <declaration>
agent-dispatch repository-issue-loop disable <declaration> --reason <reason>
agent-dispatch repository-issue-loop enable <declaration>
```

`discover` performs forge discovery and deterministic selection without
reserving issues or creating a task. `doctor` is nonzero for an unavailable
forge, a failed or stale emitter, missing registrar pointer, unserved unit,
coordinator failure, kill switch, or blocked steering task.

## Host migration

Move producer authority deliberately; copying the declaration first can run two
eligible emitters.

1. Disable the loop on the old host and confirm no emitter command is in flight.
2. Move the adopter-owned declaration placement or its top-level
   `filters.permit.machine` authority to the new host.
3. Release the old emitter lease with
   `agent-dispatch schedule lease-release emitter:<emitter-id>
   --holder <old-holder>`. A lease TTL is observability-only: expiry never
   transfers this pin. For report-first, renewal-fenced recovery of a stale
   foreign pin, use [emitter diagnosis](emitter-recovery.md).
4. Register/discover the declaration on the new host, enable it there, and
   confirm `status` shows the new source and worker lane served.
5. Run `discover`, then inspect visible reservations and the active occurrence
   before allowing a mutating tick.

If a separate producer previously selected these issues, disable that producer
before enabling this loop. Preserve its visible reservations until the new
producer can identify their live tasks or an operator explicitly releases
them; producer authority transitions must never manufacture overlapping claims.
