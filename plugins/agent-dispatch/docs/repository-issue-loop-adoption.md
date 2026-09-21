# Adopting a repository-issue-loop: a colleague's turnkey path

This is the standalone path for a colleague unfamiliar with the
agent-dispatch runtime to stand up a `repository-issue-loop` declaration
from the schema and an existing worker identity alone -- without reading
`repository_issue_loops.py` or any other engine source. For the recipe's
internal behavior (reservation protocol, forge adapters, host migration),
see [`repository-issue-loop.md`](repository-issue-loop.md); this doc is the
adoption path, that one is the reference.

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
| `repo` | string | yes | `owner/name` (GitHub) or `organization/project` (Azure DevOps) -- same two-segment shape either way. |
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
| `provider` | string | yes | `github` or `azure-devops`. |
| `producer_login` | string | yes | The expected authenticated identity; every read/mutation is verified against it. |
| `discovery_scope` | mapping | no | **azure-devops only.** Narrows the WIQL discovery query beyond the always-applied `TeamProject` scoping. At least one of the three sub-fields is required if present: `work_item_types` (list of strings), `area_path` (string), `max_age_days` (positive number). |

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

## 4. What still requires engine source (open gap)

Everything above is resolvable from this doc, the schema table, and an
identity's own file. One gap remains: diagnosing a `doctor` failure's exact
cause today still benefits from reading
`repository_issue_loops.py`/`registrar_discovery.py` for a genuinely novel
failure mode not covered by the **Operations** section's list in
`repository-issue-loop.md`. Closing this fully (e.g. an expanded, colleague-facing
failure-mode reference) is left as a follow-up; it was not bundled with this
adoption-path pass. Once anyone identifies a `doctor` failure that couldn't
be resolved from this doc, add its resolution here rather than treating
engine source as an unavoidable last resort.
