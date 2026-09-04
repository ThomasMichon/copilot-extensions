# copilot-extensions-harness

A **payload-only** Copilot CLI plugin that ships the **operator harness** for the
copilot-extensions repo — the skills to work *on* the plugin suite. Enable it in
any control repo and your agent knows how to **contribute** changes to the
plugins, **diagnose** the deployed runtimes, and **validate** them on a fresh box,
without you hand-writing a per-repo guide or installing a runtime.

At session start, the plugin injects one concise pointer to its contribution
boundary. The full guide remains versioned at
[`references/contribution-ground-rules.md`](references/contribution-ground-rules.md):
generic, organization-neutral capabilities are welcome; personal or
organization-specific needs are routed elsewhere.

| Skill | Covers |
|-------|--------|
| [contributing-to-copilot-extensions](skills/contributing-to-copilot-extensions/SKILL.md) | Repo layout, the PR-required worktree flow (`create` → `create-pr`/`push-changes` → `pr-merge --now` → `finalize`), the submitter's hard **merged + finalized** completion gate, the **mandatory version bump**, test + install-contract gates, deploy-after-merge, and source-of-truth rules |
| [diagnosing-copilot-extensions](skills/diagnosing-copilot-extensions/SKILL.md) | Symptom → cause → action for deployed plugins, key paths, diagnostic commands, and the baseline-reset escape hatch |
| [validating-in-clean-room](skills/validating-in-clean-room/SKILL.md) | **Run · evaluate · author** clean-room validation (`tools/clean-room/`): fresh-box scenarios, `cr-report.json` + `cr-logs/`, jam taxonomy, Tier-E literal-mode judging, and the scenario contract |

The plugin also ships `.agent-worktrees/related.yaml`, so an active
`copilot-extensions-harness` contributes portable, lowest-precedence provenance
for the `copilot-extensions` repository. Machine-local registration still owns
the checkout path and repository class, and derives the operator-relative
ownership posture (`owned` for the maintainer, `external` for other users).
Repository operations require a public GitHub account; enterprise-managed
GitHub accounts are appropriate for internal organizations, while Entra
credentials apply to Azure DevOps rather than this GitHub repository.

| Sub-agent | Covers |
|-----------|--------|
| [clean-room-judge](agents/clean-room-judge.agent.md) | Read-only Tier-E evaluator: scores a clean-room eval run against a scenario's stated outcome under **literal-mode** rules (credits only the literal task; a self-heal "pass" is a false pass), emitting PASS/FAIL + classified jams |

## Enable

No runtime, binstub, service, or setup script is involved. Enabling the plugin is
the whole install; restart the session so the skills, agent, and contribution
boundary hook are scanned.

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
