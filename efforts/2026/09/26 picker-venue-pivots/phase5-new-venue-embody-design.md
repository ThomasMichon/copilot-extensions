# Phase 5 design note — New-venue → embody handoff

Sibling design note to `README.md`. This closes Phase 5 of
`picker-venue-pivots` as **design only**.

## Scope and ownership

This note records the picker-side UX and orchestration contract for
**New codespace** / **New container**:

1. prompt for the target information needed to provision a venue,
2. call the provider's own provisioning verb, and
3. hand the operator into a fresh Copilot CLI session in that venue.

It is **not** an implementation obligation of `picker-venue-pivots`.
Implementation belongs to the parallel
[`agent-bridge-cli-mode-sessions`](../agent-bridge-cli-mode-sessions/README.md)
effort, which already owns the reusable remote CLI-mode session-launch
machinery and the provider `copilot` verbs this flow should land on.

`picker-venue-pivots` owns only the venue-picker UX contract and the
assertion that a "New venue" entry point should converge on the **same**
destination as Phase 3's **Open** action, not a second embodiment path.

## Grounded constraints

- The pivot-registry contract still has **no pivot-level action slot**:
  every `PivotAction` is row-scoped. That gap was already recorded in
  `README.md`'s "Design note: New-venue entry point (Phase 1 closure,
  design-only)" section and remains real. A visible "New codespace" /
  "New container" affordance therefore needs a schema addition (or another
  picker-owned control surface), not just a manifest snippet.
- Phase 3 already proved the correct *existing-row* destination:
  `open-venue` delegates to the provider's own `copilot <name>` verb, and
  those verbs already converge on the remote CLI-mode path owned by
  `agent-bridge-cli-mode-sessions`.
- The shared provider core is already explicit in
  `plugins/*/libs/venue-copilot/README.md`: reserve CLI-mode slot on the
  host, build the remote `agent-worktrees copilot ...` command, connect, and
  release. New-venue should reuse that exact lane after provisioning.

## Finalized flow

### 1) Target-info prompt

The picker's eventual "New ..." affordance gathers only the minimum stable
inputs that actually determine venue identity:

- **New codespace**
  - target repo (normally prefilled from the current pivot / related-repo
    context),
  - optional branch,
  - any provider-required sizing/location override only when the default is
    intentionally being changed.
- **New container**
  - target fleet / devcontainer spec,
  - optional count/recreate semantics only when the provider requires them
    to materialize a new member.

This prompt is for **venue selection/provisioning**, not task chartering. A
task seed can still be carried into the embodiment handoff, but the prompt
should not attempt to merge venue-targeting questions with the Copilot
session's first-turn work prompt.

### 2) Provision with the provider's existing verb

Once the target info is collected, the picker delegates provisioning to the
provider that already owns venue lifecycle:

- **Codespaces:** `agent-codespaces create <repo> [--branch ...]`
- **Containers:** `agent-containers up <fleet> [...]`

The picker should not inline provider lifecycle logic. It invokes a small,
provider-owned command, waits for the resulting venue identifier, and treats
readiness/provisioning failures as provider-surfaced errors.

### 3) Hand off into the existing venue `copilot` path

After the venue exists (or a fresh member has been selected), the flow should
hand off exactly as though the operator had chosen **Open** on that row:

- **Codespaces:** `agent-codespaces copilot <name>`
- **Containers:** `agent-containers copilot <name>`

That handoff is the important architectural constraint. "New venue" must not
grow a second embodiment pipeline. It reuses the same provider `copilot`
verbs, which already flow through the remote CLI-mode machinery owned by
`agent-bridge-cli-mode-sessions` and ultimately into
`agent-worktrees copilot` / `agent-worktrees embody`.

## UX contract

The operator experience should read as one continuous action:

`New venue` → provide venue target info → provider provisions venue →
operator lands in a fresh Copilot session in that venue

This is intentionally the same conceptual flow as:

`Open existing venue row` → provider connects/reattaches → operator lands in
that venue's Copilot session

The difference is only whether the venue already exists.

## Non-goals for this effort

- No picker implementation of the pivot-level action slot.
- No provider-lifecycle code changes here.
- No new session-host protocol here.
- No attempt to own remote worktree/workspace creation beyond whatever the
  provider or `agent-bridge-cli-mode-sessions` already standardizes.

Those belong to the implementation effort that carries the actual
drive-CLI-agents-over-SSH / remote CLI-mode venue launch work.

## Cross-link

- Source effort: [`picker-venue-pivots`](README.md)
- Implementation counterpart:
  [`agent-bridge-cli-mode-sessions`](../agent-bridge-cli-mode-sessions/README.md)
