# Adopting a repository-issue-loop: a colleague's turnkey path

This is the standalone path for a colleague unfamiliar with the
agent-dispatch runtime to stand up a `repository-issue-loop` declaration
from the schema and an existing worker identity alone -- without reading
`repository_issue_loops.py` or any other engine source. For the recipe's
internal behavior (reservation protocol, forge adapters, host migration),
see [`repository-issue-loop.md`](repository-issue-loop.md); this doc is the
adoption path, that one is the reference.

## 0. Runtime prerequisites for headless embody

A `repository-issue-loop` spawns each occurrence's worker as a **headless**
Copilot process -- that spawn path has two genuine runtime dependencies
beyond `agent-dispatch` itself, neither of which this schema reference
enforces at declare time (a `doctor`/`status` check sees a healthy
*declaration*, not a broken spawn path):

1. **A real, addressable agent-bridge venue.** `--headless-agent AGENT`
   (default `task-worker`) names the venue a spawned body targets --
   [`spawn-supervisor.md`](spawn-supervisor.md) documents that this name
   must already be registered with `agent-bridge` on the host *before* any
   occurrence ticks, or the spawn silently deadlocks waiting on a venue
   that was never there.
2. **The declaring repo registered with `agent-worktrees`.** Every headless
   spawn's `create_worktree()` unconditionally shells out to
   `agent-worktrees create` -- so the repo needs an `agent-worktrees`
   project registration, not just a plain git clone, before the first
   occurrence ticks. The registered project **name** matters too:
   `embody.project_for_task()` resolves a task's `--project` by first
   reversing its `repo` field (this declaration's own bare `owner/name`
   string) through `agent-worktrees`' canonical-remote registry -- which
   always carries a host prefix (e.g. `github.com/owner/name`), so a bare
   `owner/name` never matches it directly -- then falling back to that
   `repo` string's own trailing path segment (`owner/name` -> `name`).
   Register the project under exactly that trailing segment for the
   fallback to resolve it.

Neither dependency is specific to this recipe -- any headless-embody
consumer needs both -- but a colleague following only this adoption path,
with no other agent-dispatch headless deployment to crib from, can easily
miss both and watch a declaration sit healthy-but-silent forever. Set up
both *before* `setup`, not after diagnosing a stuck occurrence.

## 1. Declaration schema reference

A declaration is one YAML (or JSON) document under
`.copilot-extensions/agent-dispatch/registrar/<name>.yaml` (legacy
`.agent-dispatch/registrar/` also resolves). Every field below is validated
at `discover`/`setup` time; an unknown key or wrong type is a clean
`RegistrarError`, never a silent no-op.

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Letters, digits, `.`, `_`, `-` only. |
| `kind` | string | yes | Must be the literal `repository-issue-loop`. |
| `repo` | string | yes | `owner/name` (GitHub, Gitea) or `organization/project` (Azure DevOps) -- same two-segment shape either way. Any non-empty string for `forge.provider: script`, but it also routes the created task (see `forge.backlog` below if the script's own backlog label is not itself a real project). |
| `source` | string | yes | Free-form label for the emitter's task `source`; safe to change later without losing history. |
| `cadence_seconds` | number ≥ 1 | yes | Occurrence period. |
| `tick_interval_seconds` | number ≥ 1 | no (default: `min(60, cadence_seconds)`) | How often the emitter checks in, independent of the occurrence period. |
| `quiet_period_seconds` | number ≥ 0 | no (default `0`) | Seconds since an issue's last update before it becomes eligible. |
| `batch_size` | integer ≥ 1 | no (default `1`) | Bounds the selected set per occurrence. |
| `include_labels` / `exclude_labels` | list of strings | no | Eligibility filters; a label cannot be in both lists. The conventional `bootstrap` label is always excluded. |
| `priority_labels` | list of strings | no | First-matching-label rank, then `created_at`, then issue number. |
| `task_label` | string | yes | The dispatch task's `task_label`. |
| `owner` | string | no | Free-form owner attribution. |
| `description` | string | no | Free-form declaration description. |
| `require_verification` | boolean | no (default `false`) | Opt new tasks into the submitted-verification gate; workers stop at `submitted`, and a matching evaluator/manual review must close the claim. Pair it with `evaluator_ref` when enabled. |
| `evaluator_ref` | string | no | Opaque selector naming the trusted evaluator registration that verifies submitted tasks created by this declaration. |
| `allow_self_config_changes` | boolean | no (default `false`) | Relaxes the default charter restriction against the loop editing its own declaration. |
| `worker_identity` | string | no | A named identity (see §2). Mutually exclusive with `worker_guidance`. |
| `worker_guidance` | string | no | Inline behavioral prose. Prefer a named identity for anything beyond a one-off. |
| `forge` | mapping | yes | See below. |
| `reservation` | mapping | yes | See below. |
| `pool` | mapping | yes | See below. |
| `filters` | mapping | no | Top-level placement; only the `machine` dimension is supported (`permit`/`reject`). |

### `forge`

| Field | Type | Required | Notes |
|---|---|---|---|
| `provider` | string | yes | `github`, `azure-devops`, `gitea`, or `script`. |
| `producer_login` | string | yes | The expected authenticated identity; every read/mutation is verified against it (`github`/`azure-devops`/`gitea`). For `script`, it is still required but only forwarded to the subprocess as `producer_login`, unverified. |
| `discovery_scope` | mapping | no | **azure-devops only.** Narrows the WIQL discovery query beyond the always-applied `TeamProject` scoping. At least one of the three sub-fields is required if present: `work_item_types` (list of strings), `area_path` (string), `max_age_days` (positive number). |
| `api_base` | string | yes for `gitea` (unsupported otherwise) | The Gitea instance's base URL (an absolute `http`/`https` URL with a real hostname, e.g. `https://gitea.example.com`; a path-hosted instance such as `https://host.example.com/gitea` is also accepted). Must not embed credentials, a query string, or a fragment. |
| `token_env` | string | no, `gitea` only (default `GITEA_TOKEN`) | The environment variable holding the API token for `producer_login`. |
| `command` | list of strings | yes for `script` (unsupported otherwise) | The subprocess argv; `--op <name>` is appended automatically. A relative first element resolves against the declaring repo root. |
| `cwd` | string | no, `script` only | Subprocess working directory; relative resolves against the declaring repo root. Defaults to the declaring repo root. |
| `timeout_seconds` | number, `0 < n <= 1800`, finite | no, `script` only (default `30`) | Per-invocation execution bound. |
| `namespace` | non-empty string | no, `script` only | Explicit coordinator resource-key namespace; overrides the auto-derived (best-effort, process-cached) identity outright. Set this for a redundant/failover deployment of the same declaration across hosts. |
| `backlog` | non-empty string | no, `script` only | Overrides the identifier the script's own four operations receive as `repo` (wire field name unchanged), decoupling it from the top-level `repo`, which keeps routing the created task regardless. Defaults to `repo`. Set this whenever the script's natural backlog label is not itself a real, embody-routable project -- see [`repository-issue-loop.md`](repository-issue-loop.md#the-script-provider)'s "`repo` vs. `forge.backlog`". |

See [`repository-issue-loop.md`](repository-issue-loop.md#the-script-provider)
for the `script` provider's full subprocess JSON request/response contract.

### `reservation`

| Field | Type | Required | Notes |
|---|---|---|---|
| `label` | string | yes | The forge label/tag applied while an issue is reserved or claimed. |
| `comment` | boolean | no (default `true`) | Must be `true` -- ownership must stay visible. |
| `orphan_after_seconds` | number ≥ 60 | no (default `max(cadence_seconds, 3600)`) | Crash-recovery TTL for an unbound reservation. |

### `pool`

A concurrency-one headless supervised lane. `max_active_processes` (or the
legacy `concurrency`) must be `1`. `body.type` must be `headless` (or
omitted, since `headless` is the default). `name`, `labels`, `repos`, `kind`,
`spec`, `owner`, `description` are derived by the recipe and must not be set
here directly.

## 2. Available worker identities

A named identity replaces inline `worker_guidance` prose with a
`<name>.identity.md` file in the same frontmatter (`name`, `description`)
plus markdown-body (behavioral rules) shape as an in-session `*.agent.md`
sub-agent. Resolution order (first hit wins), from
`agent_dispatch.worker_identities`:

1. A repo-local override:
   `<repo>/.copilot-extensions/agent-dispatch/identities/<name>.identity.md`
   (legacy `<repo>/.agent-dispatch/identities/<name>.identity.md` also
   resolves), or an explicit marketplace overlay under
   `<repo>/.copilot-extensions/agent-dispatch/marketplaces/<marketplace-id>/identities/`.
2. The packaged built-in identities shipped inside this package
   (`agent_dispatch/identities/<name>.identity.md`).

**Built-in library (tier 2) today:**

- `repository-issue-loop-default` -- a generic identity that reads the target
  repository's own contribution docs, triages each eligible issue for scope
  fit and feasibility, blocks for steering on genuine ambiguity, and never
  supersedes another contributor's open pull request. Intended as a starting
  point; most adopting repositories are expected to author their own
  repo-local identity (tier 1) once routing needs diverge from this default.
  Read the full identity at
  [`src/agent_dispatch/identities/repository-issue-loop-default.identity.md`](../src/agent_dispatch/identities/repository-issue-loop-default.identity.md).

Authoring your own repo-local identity means writing a new
`<name>.identity.md` at the tier-1 path above -- no engine change is needed,
and it takes effect the next time the declaration resolves `worker_identity`.

## 3. Worked example: a new repository from scratch

Say `octo/widgets` wants a quiet-backlog loop using the default identity,
checked every 5 minutes with an 8-hour occurrence cadence, batches of 3,
GitHub-backed:

```yaml
# octo/widgets: .copilot-extensions/agent-dispatch/registrar/backlog.yaml
name: widgets-backlog
kind: repository-issue-loop
repo: octo/widgets
source: widgets-backlog
cadence_seconds: 28800
tick_interval_seconds: 300
quiet_period_seconds: 600
exclude_labels: [wontfix, invalid, duplicate, question]
priority_labels: [bug, enhancement, documentation]
batch_size: 3
task_label: widgets-backlog-work
owner: octo/widgets maintainers
description: >-
  Every eight hours, triage and drive a bounded set of quiet widgets
  backlog issues through durable resolution with one headless worker.
worker_identity: repository-issue-loop-default
forge:
  provider: github
  producer_login: octo-bot
reservation:
  label: agent-reserved
pool:
  max_active_processes: 1
  body:
    type: headless
filters:
  permit:
    machine: [ci-runner-1]
```

Then, from the repository:

```bash
agent-dispatch repository-issue-loop discover \
  .copilot-extensions/agent-dispatch/registrar/backlog.yaml
```

`discover` is the safe dry run: it performs forge discovery and deterministic
selection without reserving anything or creating a task. Once its output
looks right, `setup` registers the declaration for real, and
`status`/`doctor` cover ongoing health. See
[`repository-issue-loop.md`](repository-issue-loop.md)'s **Operations**
section for the full lifecycle command set.

An Azure DevOps-backed declaration only changes `repo` (to
`organization/project`) and `forge.provider` (to `azure-devops`); everything
else -- reservation, pool, filters, worker identity selection -- is
identical. Add `forge.discovery_scope` only if the target project's own item
count is large enough that the always-applied `TeamProject` scoping alone
isn't narrow enough (see [`repository-issue-loop.md`](repository-issue-loop.md)).

A Gitea-backed declaration keeps `repo` as `owner/name`, sets
`forge.provider` to `gitea`, and adds the instance's `forge.api_base` (an
absolute `http`/`https` URL with a real hostname; `forge.token_env`
defaults to `GITEA_TOKEN` if omitted):

```yaml
forge:
  provider: gitea
  producer_login: octo-bot
  api_base: https://gitea.example.com
  token_env: GITEA_TOKEN
```

## 4. `doctor`/`status` failure-mode reference

`doctor`'s `diagnoses` array is a closed, code-derived set -- every entry
below is exhaustive as of this writing (`loop_commands.py`); a declaration
in good health reports `["healthy"]` and nothing else. This table is the
resolution for every diagnosis the command can actually emit, so a genuinely
novel `doctor` failure not listed here is the only case that should still
require reading engine source -- if you hit one, add it here rather than
falling back to source-reading as the default.

| Diagnosis | Meaning | Resolution |
|---|---|---|
| `healthy` | No problem found. | -- |
| `missing-pointer` | The declaration isn't registered with the registrar. | `agent-dispatch repository-issue-loop setup <declaration>` (also the action `doctor` prints). |
| `supervisor-stalled` | The daemon is alive but its current reconcile cycle has been running >180s without finishing (a genuinely wedged call, not just an old status file). | Check the daemon's log for a hung call; update via `install.ps1 update`, **never** a manual process kill. |
| `declared-but-unserved` | This declaration matches the current machine's placement filter, but the running daemon isn't actually serving it. | Confirm the daemon has picked up the current registrar state (may need a restart/reconcile) -- not self-resolving by re-running `setup`. |
| `overridden-off` | A local kill switch has disabled this declaration on this machine. | `agent-dispatch repository-issue-loop enable <declaration>`. |
| `coordinator-unavailable` | The coordinator couldn't be reached to list tasks. | Check `agent-dispatch health`; usually a wrong URL or a down daemon, not an empty queue. |
| `forge-unavailable` | Listing open issues/work items from the forge failed. | Check the `forge.producer_login`'s credentials/authentication and the adapter's own error text (surfaced in the full `doctor` JSON's `forge_error`). |
| `emitter-failure` | The emitter's own last-written health snapshot reports `ok: false`. | Read the snapshot at the health path `doctor` reports; the failure is whatever the emitter itself last recorded. |
| `emitter-health-unreadable` | The emitter's health snapshot file exists but couldn't be parsed. | Check for a corrupted/partial write at that path; a fresh emitter tick should overwrite it. |
| `emitter-never-ran` | The supervised-lane unit is served, but the emitter has no health snapshot at all yet. | Wait one `tick_interval_seconds`; if it persists, the emitter process itself likely isn't starting -- check the daemon's log. |
| `emitter-stale` | The emitter's last health update is older than `cadence_seconds + 2 * tick_interval_seconds`. | The emitter has stopped ticking; check the daemon's log for why its periodic call stopped running. |
| `blocked` | The active occurrence's task is awaiting steering. | `agent-dispatch steer` the task (per its own steering card), or `release`/`abandon` it explicitly. |
| `spawn-dead-lettered` | The active occurrence exhausted its spawn-attempt budget without ever starting. | Use the `rearm` action `doctor` prints for the dead-lettered task id -- never hand-clear the dead-letter state another way. |

`status` reports the same underlying fields without the pass/fail verdict --
useful for watching `active_occurrence`, `emitter`, and `pool` state directly
rather than only a health summary.
