# copilot-extensions-harness

A **payload-only** Copilot CLI plugin that ships the **operator harness** for the
copilot-extensions repo — the skills to work *on* the plugin suite. Its
**primary purpose** is to make sure any agent using an enabling plugin can
quickly identify which local scripts/processes/paths belong to this system and
never monkey-patches a deployed copy — file a bug upstream or auto-update
instead. Its **secondary purpose**, for repos that also choose to be
contributors, is to describe the correct flow for landing a real fix. Enable it
in any control repo and your agent knows how to **diagnose** the deployed
runtimes, **contribute** changes to the plugins, and **validate** them on a
fresh box, without you hand-writing a per-repo guide or installing a runtime.

The plugin declares two checked-in static projections. Adopting
repositories synchronize them with the
`customizing-copilot:reviewing-customizations` manager; each remains available
without a competing `sessionStart` output.
- **`contribution-boundary`** — the full guide remains versioned at
  [`references/contribution-ground-rules.md`](references/contribution-ground-rules.md):
  generic, organization-neutral capabilities are welcome; personal or
  organization-specific needs are routed elsewhere.
- **`cross-repo-debug-tracking`** — before concluding an `agent-*` or
  `context-handoff` plugin's source is undocumented, or filing an upstream
  bug against one, resolve its actual checked-out location first (for example
  `<agent-worktrees catalog argv[0]> related resolve <repo>` -- the exact
  `argv[0]` from the session command catalog, never a bare PATH lookup), and
  cross-link a local symptom's tracking issue with its upstream
  `ThomasMichon/copilot-extensions` issue/PR in both directions.

| Skill | Priority | Covers |
|-------|----------|--------|
| [diagnosing-copilot-extensions](skills/diagnosing-copilot-extensions/SKILL.md) | **Primary (hard guidance)** | Identify what belongs to this system before touching it; never monkey-patch a deployed copy (file a bug upstream, sanitized, or auto-update instead); symptom → cause → action for deployed plugins; key paths, diagnostic commands, and the baseline-reset escape hatch |
| [contributing-to-copilot-extensions](skills/contributing-to-copilot-extensions/SKILL.md) | Secondary (opt-in) | Repo layout, the PR-required worktree flow (`create` → `create-pr`/`push-changes` → `pr-merge --now` → `finalize`), the submitter's hard **merged + finalized** completion gate, the **mandatory version bump**, test + install-contract gates, deploy-after-merge, and source-of-truth rules |
| [validating-in-clean-room](skills/validating-in-clean-room/SKILL.md) | **Run · evaluate · author** clean-room validation (`tools/clean-room/`): fresh-box scenarios, `cr-report.json` + `cr-logs/`, jam taxonomy, Tier-E literal-mode judging, and the scenario contract |

The plugin also ships `.agent-worktrees/related.yaml`, so an active
`copilot-extensions-harness` contributes portable, lowest-precedence provenance
for the `copilot-extensions` repository. Machine-local registration still owns
the checkout path and repository class, and derives the operator-relative
ownership posture (`owned` for the maintainer, `external` for other users).
Repository operations require a public GitHub account; enterprise-managed
GitHub accounts are appropriate for internal organizations, while Entra
credentials apply to Azure DevOps rather than this GitHub repository.

The plugin also registers one **preToolUse** guard (`hooks.json` +
`scripts/copilot-mention-guard.py`): it denies a `gh` invocation that would
publish an `@copilot` mention in a PR/issue comment or review, scoped to this
plugin's own repo (`ThomasMichon/copilot-extensions`, read live from
`plugin.json` -- never hardcoded, so a fork retargets automatically). On
GitHub, that mention doesn't nudge the review bot; it delegates to the
separate Copilot **cloud coding agent**, which starts pushing its own commits
directly to the PR branch. See `CONTRIBUTING.md`'s own "Do not comment
`@copilot review`" rule for the written policy this hook mechanically
enforces. A control repo enabling this plugin purely for its instruction
projections is never touched by the guard -- it only fires for a `gh`
invocation whose current directory resolves to this plugin's own target repo.

| Sub-agent | Covers |
|-----------|--------|
| [clean-room-judge](agents/clean-room-judge.agent.md) | Read-only Tier-E evaluator: scores a clean-room eval run against a scenario's stated outcome under **literal-mode** rules (credits only the literal task; a self-heal "pass" is a false pass), emitting PASS/FAIL + classified jams |

## Enable

No runtime, binstub, service, or setup script is involved. Enabling the plugin is
the whole install; restart the session so the skills and agent are scanned.
Synchronize the declared instruction projection in each adopting repository so
the contribution boundary is available on launch paths that load checked-in
instructions.

In a control repo, declare the marketplace (if it is not already declared) and
enable the plugin in `.github/copilot/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": {
      "source": { "source": "github", "repo": "ThomasMichon/copilot-extensions" }
    }
  },
  "enabledPlugins": {
    "copilot-extensions-harness@copilot-extensions": true
  }
}
```

Then use the skills directly by asking to contribute to copilot-extensions,
diagnose an installed plugin/runtime, or validate a plugin in the clean room.

The retained cross-platform boundary emitter is not registered as a
**sessionStart** hook. It is the policy producer seam for a future direct
plugin-owned `additionalContext` path, which may be activated only after
native host composition is proven at the supported Copilot CLI version floor
-- multiple plugins' own `additionalContext` currently has no composition
story. The `preToolUse` guard above has no such composition hazard (each
plugin's `preToolUse` hooks run independently, with nothing to compose), so
it is registered normally.

## The `<repo>-harness` standard

This plugin is the reference implementation of a small, reusable pattern.

A **harness plugin** is a payload-only plugin, **named `<repo>-harness`**, shipped
**by** a repo, that provides the skills to operate *on* that repo — typically to
**contribute** to it and **diagnose** it. Because it lives in the repo it
describes, it is **versioned with that repo** and **portable**: any control repo
adopts it with one `enabledPlugins` line, whether the adopter contributes
directly or only needs to diagnose when something breaks.

### How it differs from a related-narrative

| | Harness plugin (`<repo>-harness`) | Related narrative |
|---|---|---|
| **Authored by** | the repo **owner**, once | each **consumer**, per control repo |
| **Ships from** | the target repo (marketplace) | the consumer's control repo |
| **Point of view** | neutral, portable | that consumer's POV |
| **Versioning** | tracks the repo it describes | tracks the consumer repo |
| **Adopt via** | enable `<repo>-harness@<marketplace>` | write `.agent-worktrees/related/<repo>.md` |

They compose: a consumer can **enable the harness plugin** for the authoritative
operator skills and keep a thin related-narrative (or trigger-redirect skill)
only for the *consumer-specific* bits — which machines deploy it, local policy,
adoption status. Prefer the plugin for the substance; keep the narrative thin.

### Authoring your own

To ship a `<repo>-harness` plugin for a different repo, use the
**`customizing-copilot:authoring-harness-plugins`** skill — it
walks the structure (this plugin as the template), the naming rule, what the
contributing/diagnosing skills should contain, and how consumers adopt it.

## License

[MIT](../../LICENSE)
