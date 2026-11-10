# Standing charters

A standing charter is a durable task for a lane of continuing responsibility,
such as one declared dispatcher seat. It is not a finite occurrence: finite
completion, submission, abandonment and ordinary claims never conclude or
staff it. Its identity, revisioned history and retirement tombstone survive
restart, owner loss and re-enrollment.

Standing work is source capability only. Reconciling, reserving or accepting a
charter never launches, appoints, materializes or stops an agent; those remain
separately authorized operations owned by their existing tools.

## Identity and revisions

A declaration names seven exact strings: `namespace`, `charter_key`,
`lane_key`, `effort_ref`, `materialization_ref`, `source_revision` and
`expected_materialization_revision`. The qualified key within one repository
lane selects exactly one task. Repeating an unchanged declaration returns the
same task without a new event; a changed declaration needs the current
`standing_revision` as `expected_revision`. Values must be 1-1024 characters,
without surrounding whitespace or control characters.

Every mutation names the exact revision it observed. A stale revision,
generation, native head or conclusion revision is refused with no effect; it
is never retried against a newer state on the caller's behalf.

| Operation | Effect |
|-----------|--------|
| `reconcile` | Upsert one declared lane; never retires, accepts or launches |
| `reserve` | Retain one exact workspace assignment through the relationship owner (not acceptance) |
| `accept` | Accept one already-existing native head; `--expected-owner-session-id` names a predecessor for succession. The same head may re-accept after a declaration change to cover the new revision without a new owner |
| `cancel` | Cancel an unaccepted reservation (including one retired before acceptance) and settle its original relationship; never inspects, accepts or launches a head |
| `release` | Detach an owner with exact native conclusion evidence |
| `retire` | Close admission; retain any occupant, hold and unresolved obligation |
| `settle-retirement` | Record retirement once no occupant, hold or obligation remains |
| `reenroll` | Explicitly reopen the same retired charter in a new declaration generation |

Lost replies replay the original operation identity rather than allocating a
second owner. Unknown custody keeps the original reservation; a successor must
accept the effective charter rather than inherit coverage implicitly.

## CLI

All `standing` commands require the coordinator's control credential
(`AGENT_DISPATCH_CONTROL_TOKEN`). They never auto-start a coordinator or fail
over through SSH; option abbreviations are refused.

```console
agent-dispatch standing reconcile --title "Dispatcher" --repo example/harness \
  --namespace owner --charter-key dispatcher --lane-key primary \
  --effort-ref efforts/dispatcher --materialization-ref roles/dispatcher \
  --source-revision <source> --expected-materialization-revision <effective>
agent-dispatch standing reserve <task-id> --expected-revision <n> \
  --machine <host> --project <project> --worktree <worktree> --path <path> \
  --creation-nonce <nonce>
agent-dispatch standing accept <task-id> --expected-revision <n> \
  --expected-generation <g> --owner-session-id <session> \
  --expected-head-revision <h> <workspace flags>
agent-dispatch standing cancel <task-id> --expected-revision <n> \
  --expected-generation <g> <workspace flags>
agent-dispatch standing release <task-id> --expected-revision <n> \
  --expected-generation <g> --owner-session-id <session> \
  --expected-conclusion-revision <c> <workspace flags>
agent-dispatch standing retire <task-id> --expected-revision <n>
agent-dispatch standing settle-retirement <task-id> --expected-revision <n>
agent-dispatch standing reenroll <task-id> --expected-revision <n>
```

## Registrar discovery

A trusted registrar pointer can supply standing declarations alongside process
declarations. In a pointed repository, put them in
`.copilot-extensions/agent-dispatch/registrar/` using the normal native-first
configuration resolver (the legacy fallback is `.agent-dispatch/registrar/`).
Use the existing [registrar pointer flow](spawn-supervisor.md), not a new
registration service. Adding a pointer to a live supervisor is an enrollment
operation and requires its own authorization.

```yaml
name: dispatcher-primary
kind: standing-charter
repos: example/harness
title: Dispatcher
spec:
  charter_key: dispatcher
  lane_key: primary
  effort_ref: efforts/dispatcher
  materialization_ref: roles/dispatcher
  source_revision: charter-v1
  expected_materialization_revision: materialization-v1
  enabled: true
```

`name` accepts letters, digits, `-` and `_`; `repos` must name one explicit
repository, not `all`. `title` and `description` are optional; the title
defaults to `name`. All six string fields in `spec` are required and follow
the identity bounds above. `enabled` is an optional boolean, defaulting to
true. The discovery owner supplies `namespace`; a document cannot override
it. Unknown fields, process settings, filters and worker requirements are
rejected. Standing declarations never become daemon registrations, enter pool
placement or acquire a process lease.

The supervisor reconciles a **complete set per repository and discovery
namespace**, including repositories retained by the coordinator even when
their last declaration is removed. The source observation is separate from
the declaration's opaque `source_revision`: a Git-backed observation requires
an unchanged HEAD and declaration contents matching that committed tree,
without untracked declaration files. Before updating a retained observation,
the supervisor proves that the new Git revision equals or descends from it.
Dirty, unreadable, incomplete, missing or rewritten sources do not authorize a
change or removal. Deleting the pointer or making its directory disappear is
not a retirement request. Plugin candidate withdrawal from `registrar.d`
likewise does not supply a complete trusted standing source set.

A complete non-Git source is unordered: it may enroll a previously unknown
lane or repeat an unchanged declaration, but cannot change or retire an
existing lane. An ordered source can update declarations, retire an explicitly
disabled lane (`spec.enabled: false`), or retire lanes omitted from a later
complete set. A first observation cannot infer removal without a retained
ordering fence. To remove the last declaration, preserve an observable
registrar directory and commit the removal; do not remove the whole source.
Unchanged reconciliation is a no-op, and stale observation compare-and-swap
fails without retrying against newer state.

Retirement closes admission, not custody: an occupant, hold or unresolved
obligation remains attached until its normal release/settlement path completes.
A reservation that was never accepted settles through `cancel`, after which
retirement can settle. Re-enabling a retiring or retired declaration does not resurrect it; explicit
re-enrollment is required. Source-set results report `enrolled`, `changed`,
`retired`, `unchanged`, `deferred` and the retained `observation`; `retired`
means retirement was requested, not settled.

## Finite children

An ordinary finite task may name an active standing parent in the same
repository lane. The link is immutable, never carries the parent's exclusive
staffing key, and a dedup key cannot be reused under a different parent.
Children keep their own claim,
verification and completion paths and may finish after the parent retires;
child completion never concludes the parent. A retiring parent admits no new
children.

```console
agent-dispatch create "Triage queue" --repo example/harness --standing-parent <task-id>
agent-dispatch list --repo example/harness --standing-parent <task-id>
```

Remote `--target-machine` create and peer `list` forward `--standing-parent`;
an older peer CLI refuses the unknown flag. The HTTP filter is
`GET /tasks?standing_parent_id=<id>`; it cannot be combined with `q` or `sweep`.

## HTTP and capability selection

| Route | Body |
|-------|------|
| `POST /standing-charters` | `title`, `repo`, `declaration`, optional `expected_revision` |
| `GET /standing-charters/source-repositories?namespace=<namespace>` | No body; returns retained repositories for that namespace |
| `GET /standing-charters/source-sets?repo=<repo>&namespace=<namespace>` | No body; returns the retained source observation |
| `POST /standing-charters/source-sets` | `repo`, `namespace`, `entries` (`title`, `declaration`, optional boolean `enabled`), required nullable `revision` and `expected_observation` |
| `POST /tasks/{id}/standing/reserve` | `expected_revision`, `workspace` |
| `POST /tasks/{id}/standing/accept` | reserve fields plus `expected_generation`, `owner_session_id`, `expected_head_revision`, optional `expected_owner_session_id` |
| `POST /tasks/{id}/standing/cancel` | reserve fields plus `expected_generation` |
| `POST /tasks/{id}/standing/release` | reserve fields plus `expected_generation`, `owner_session_id`, `expected_conclusion_revision` |
| `POST /tasks/{id}/standing/{retire,settle-retirement,reenroll}` | `expected_revision` |

Bodies reject unknown fields, booleans as revisions and revisions below 1.
A missing control credential configuration returns 503
`standing_charter_control_unavailable`; a wrong bearer returns 403
`standing_charter_control_forbidden`. Unknown tasks return 404 and fenced
conflicts 409. Successful mutations emit `task.standing_*` events.

All source-set routes also require the control credential. Their revisions are
opaque strings or null, not the integer task revisions above. The coordinator
enforces atomic source-set reconciliation and observation compare-and-swap;
the trusted source caller owns completeness and Git ancestry proof because the
coordinator cannot inspect its checkout. A partial read must never be posted
as an empty set.

`/health` advertises `standing_charters: {"version": 1, "finite_child_link": 1,
"source_set": 1, "attachment": <bool>}`. `attachment` is true only when the
selected relationship owner has configured local standing policy and native
head/conclusion inspection, so it can later accept and release what it
reserves. Clients re-check this
capability before each standing operation, standing-parent create and
standing-parent read, and refuse as upgrade-required before sending any
mutation when it is absent or malformed. Reserve, accept, cancel and release also
refuse with 409 when `attachment` is not true. Source-set operations additionally
require `source_set: 1`; they do not fall back to per-lane writes that could
lose complete-set or retirement semantics. Ordinary finite requests omit
`standing_parent_id` and keep their previous wire shape.

## Mixed versions and rollback

Store triggers prevent older writers from completing, submitting, abandoning,
reclaiming, rewriting, deleting or spawning a standing task, or from erasing
its history. Current finite routes (`/claim`, `complete`, `abandon`) refuse a
standing task with 409, and untargeted claims never select one. Older
coordinators that ignore unknown query parameters are rejected by the client
capability check rather than returning an unfiltered view.
