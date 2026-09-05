# efforts

The **efforts planning system** for GitHub Copilot CLI.

An **effort** is a comprehensive planning folder that represents a *stretch of
work* — its initial premise, its evolving plan, a validation plan, the running
journal of what actually happened, and the coordination surface for the
participants (machines, CodeSpaces, containers, branches) doing the work. One
effort, one folder, one README that everyone — human and agent — reads and
writes.

The name is deliberately **not** `feature`, `bug`, or `task`: those nouns are
owned by issue trackers (GitHub, Gitea, Azure DevOps). An effort is the
planning workspace *around* tracked work — it spawns and references issues, and
outlives any single one.

## What this plugin ships

This is a **payload-only plugin** — no runtime, virtualenv, service, installer,
or binstub. Enabling the plugin is the whole install. It delivers two skills, an
explicit repository-adoption contract, a minimal static completion-gate
fallback, and cross-platform policy producers:

| Skill | Role |
|-------|------|
| **planning-efforts** | The workflow: start, plan, resume, and archive efforts. Governs the canonical effort pattern (folder layout, README schema, lifecycle, journal, the participants seam) and optionally binds a managed worktree to its declared active effort slice. Ships the reference guide and the effort README template as skill assets. |
| **efforts-setup** | Adoption: how a repo takes on the efforts system — create the `efforts/` tree and write a short repo **addendum** that specializes the bindings. |

An adopting repository commits
`.copilot-extensions/efforts/config.json` with the exact version 1 policy:

```json
{
  "version": 1,
  "enforcement": "required"
}
```

That file, not directory presence or a repository name, declares support and
required use. The canonical hook-less completion fallback is
`instructions/completion-gate.instructions.md`, declared by
`instruction-projections.json`; `efforts-setup` routes adopters through the
projection manager rather than duplicating that policy in repository prose.

For cross-repository placement, the same strict validator exposes a read-only
target capability probe. Resolve an authoritative local checkout or worktree
through the repository owner, then invoke the producer from this plugin's
payload:

```bash
bash <efforts-plugin-root>/scripts/emit-policy.sh --check-adoption <absolute-target-path>
```

```powershell
& <efforts-plugin-root>\scripts\emit-policy.ps1 -CheckAdoption <absolute-target-path>
```

Only the exact response
`{"version":1,"capability":"efforts","adopted":true}` authorizes a target-owned
effort. `{}` means the target is absent, malformed, unavailable, or otherwise
not proven compatible. The probe resolves the target's Git root and reads only
the bounded adoption JSON; it does not execute target code, infer capability
from a repository name, or inspect a remote-only target piecemeal.

`scripts/emit-policy.sh` and `scripts/emit-policy.ps1` implement the richer,
cwd/config-gated policy kernel and are covered by live parity tests. They are
not yet registered in `plugin.json`: Copilot CLI issue
[#1234](https://github.com/ThomasMichon/copilot-extensions/issues/1234) currently
discards all but one enabled plugin's valid `sessionStart` `additionalContext`
result. Registering another producer before deterministic aggregation is fixed
could displace a sibling's command catalog. The static fallback is therefore the
primary ambient path until #1234 closes; the producer is staged for immediate
registration afterward.

The POSIX wrapper uses a system `python3` (falling back to `python`) for strict
JSON and path handling; the plugin does not provision or own a Python runtime.
If no usable interpreter is available, the wrapper emits one diagnostic and
fails open to `{}`. The PowerShell producer is native and has no Python
dependency.

## The skill governs the pattern; each repo adds an addendum

The `planning-efforts` skill is the **single source of truth** for the effort
pattern. An adopting repo does not redefine it — it writes a short **addendum**
that specializes only the local bindings:

| Binding | What the addendum sets | Examples |
|---------|------------------------|----------|
| **Grouping** | flat vs. by-repo folder layout | `efforts/active/<slug>/` (flat) · `efforts/active/<repo>/<slug>/` (by-repo) |
| **Participants seam** | what executes the work, and how it's reached | machines (SSH/agent-bridge) · CodeSpaces · containers · branches |
| **Sections** | any additions/renames to the README schema | rename `Participants` → `Machines`, add repo-specific sections |

The addendum lives in the adopting repo (its `efforts/README.md` or a binding
doc such as `docs/efforts.md`), keeping repo- and environment-specific details out of the
portable core.

## How executor plugins build on efforts

The README's **participants seam** is where executor plugins can plug in. The
efforts plugin is standalone: a repo can use the README contract with no
agent-worktrees registration and no executor plugin at all. When executor
plugins are present, the adopting repo's addendum binds the seam to a provider:

- [`agent-codespaces`](../agent-codespaces) → GitHub **CodeSpaces**
- [`agent-containers`](../agent-containers) → local **containers**
- SSH/[`agent-bridge`](../agent-bridge) → **machines** in a fleet

The efforts plugin owns the planning document and lifecycle; executor plugins
own only their provider-specific borrow/dispatch/claim mechanics. Keep the
schema and lifecycle executor-neutral — anything provider-specific belongs in
the participants binding, not the core.

When agent-worktrees is present, `planning-efforts` uses its `effort-focus`
command as optional enrichment. The worktree record owns one
repository-relative pointer plus declared participant/slice identity; an open
binding derives the existing follow-up cleanup gate and contributes a bounded
pointer through agent-worktrees' existing session-conduct hook. The efforts
plugin still owns no worktree state file and registers no session-start hook;
its Phase 1 policy producers remain staged and unregistered.

## Enable

There is no plugin-local setup command. Enable `efforts@copilot-extensions` in
the normal Copilot CLI plugin configuration/marketplace flow for your harness;
because this is payload-only, that makes the skills and policy assets available.

Then run the **efforts-setup** skill in a repo to create the exact adoption
config, effort tree/addendum, and static completion-gate projection.

## See also

- `skills/planning-efforts/references/efforts.md` — the full reference guide
- `skills/planning-efforts/assets/TEMPLATE.md` — the effort README template
- [docs/install-contract.md](../../docs/install-contract.md) — plugin/runtime
  contract (efforts has no runtime; payload-only)
