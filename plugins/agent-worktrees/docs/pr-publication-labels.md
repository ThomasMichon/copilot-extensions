# Publication-origin labels

Publication-origin metadata is opt-in and separate from create-only `pr.labels`,
source-attribution markers, review requirements and merge consent. Configuration
alone never labels ordinary runtime activity.

```yaml
pr:
  publication_origin:
    id: control-plane
    labels: [source:control-plane]
```

Select it explicitly for a managed publication:

```text
agent-worktrees create-pr --publication-origin control-plane
agent-worktrees push-changes --publication-origin control-plane
```

A push can attach origin to an existing tracked PR that was created elsewhere.
Attachment occurs only after the push succeeds. New publication records retain
the scope after a successful push even when provider opening needs a retry.
Later managed pushes reuse the frozen scope without requiring the flag again.
Changing an existing scope to a different policy is refused; it is not silently
reclassified by later configuration.

An originating caller need not configure every target repository. Supply a
per-command policy instead (repeat `--publication-label` for multiple labels):

```text
agent-worktrees create-pr --repo example/project --from-branch contributor:topic --publication-origin control-plane --publication-label source:control-plane
agent-worktrees push-changes --publication-origin control-plane --publication-label source:control-plane
```

Only the selected origin policy travels to foreign creation; target repository
resolution still owns the provider, endpoint, credentials and base. The label
target is the PR's base repository, not a fork containing its head branch.
An explicit label requires an explicit origin. There are no environment markers,
global source defaults or automatic related-repository policy grafts.

## Metadata-only attachment and repair

```text
agent-worktrees pr-publication-labels --publication-origin control-plane --json
agent-worktrees pr-publication-labels --repo example/project --number 42 --publication-origin control-plane --publication-label source:control-plane --json
```

The first form uses the tracked PR and freezes an explicitly selected policy, or
repairs its already-frozen policy without a flag. The second explicitly selects
a registered target and existing provider PR; it does not create a local PR
association or authorize source publication. Supply the origin again when
repairing an explicitly selected foreign PR.

Metadata repair also accepts a tracked closed or merged PR, provided its recorded
authority has not changed. Reconciliation resolves the PR's owning repository
binding and checks its provider, endpoint, repository and number against the
recorded PR URL before resolving credentials or attaching origin. Missing or
mismatched identity returns a repairable metadata error; repair the recorded
identity or owning binding rather than inheriting the caller's endpoint.
The owning binding also determines reserved consent/hold labels.
These metadata-only operations never reopen a PR or authorize another push.

Reconciliation reads existing labels, adds only missing labels and verifies the
result. It never replaces/removes labels or reapplies create-only `pr.labels`.
Configured consent and hold labels cannot be selected as publication labels.
GitHub and Azure DevOps support this optional provider capability; other
providers report an unsupported metadata result without repeating publication.
The acting account needs label write permission. Provider label-creation
semantics apply: GitHub's additive issue-label endpoint can create a missing
label definition while attaching it. No separate definition-provisioning or
permission-management request is made.

Create results and successful `push-changes --json` results include
`publication_labels` when reconciliation ran. `ok: false` retains the PR
repository/number, literal error, `publication_retained: true` and
`repair: pr-publication-labels`. The source publication remains successful;
callers must inspect this separate metadata outcome and repair it without
another push or duplicate PR.

## Worker policy transport

`pr-watch checks <repo> export-config` exports configured origin policy as
`pr.publication_origin`, including when no publication checks are configured.
It also accepts `--publication-origin` and repeated `--publication-label` to
transport an explicitly selected caller policy to that target. The output is
still an inert read/check policy, not credentials, a publication grant or a
global origin activation. A worker selects the origin at its actual
`create-pr`/`push-changes` boundary, using its own authorized repository binding.
Older runtimes reject the new flags; inspect the selected executable's help
before using this optional interface rather than silently dropping metadata.

Legacy PR records remain valid and unscoped. Older tools continue their existing
publication workflow without the new options, but may discard the optional
origin and label-result fields when saving a record written by a newer tool.
Use compatible writer versions when relying on persisted origin scope; this
mixed-version limitation does not remove labels already attached at the provider.
