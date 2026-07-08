# harness-copilot-extensions

A **payload-only** Copilot CLI plugin that ships the **operator harness** for the
copilot-extensions repo — the skills to work *on* the plugin suite. Enable it in
any control repo and your agent knows how to **contribute** changes to the
plugins and **diagnose** the deployed runtimes, without you hand-writing a
per-repo guide.

| Skill | Covers |
|-------|--------|
| [contributing-to-copilot-extensions](skills/contributing-to-copilot-extensions/SKILL.md) | Repo layout, the worktree contribution flow, the **mandatory version bump**, test + install-contract gates, deploy-after-push, and source-of-truth rules |
| [diagnosing-copilot-extensions](skills/diagnosing-copilot-extensions/SKILL.md) | Symptom → cause → action for deployed plugins, key paths, diagnostic commands, and the baseline-reset escape hatch |

## Install

No runtime — the skills load from the marketplace payload when enabled.

```bash
copilot plugin marketplace add ThomasMichon/copilot-extensions
copilot plugin install harness-copilot-extensions@copilot-extensions
```

Or enable it per-repo in that repo's `.github/copilot/settings.json`:

```json
{ "enabledPlugins": { "harness-copilot-extensions@copilot-extensions": true } }
```

## The `harness-<repo>` standard

This plugin is the reference implementation of a small, reusable pattern.

A **harness plugin** is a payload-only plugin, **named `harness-<repo>`**, shipped
**by** a repo, that provides the skills to operate *on* that repo — typically to
**contribute** to it and **diagnose** it. Because it lives in the repo it
describes, it is **versioned with that repo** and **portable**: any control repo
adopts it with one `enabledPlugins` line, whether the adopter contributes
directly or only needs to diagnose when something breaks.

### How it differs from a related-narrative

| | Harness plugin (`harness-<repo>`) | Related narrative |
|---|---|---|
| **Authored by** | the repo **owner**, once | each **consumer**, per control repo |
| **Ships from** | the target repo (marketplace) | the consumer's control repo |
| **Point of view** | neutral, portable | that consumer's POV |
| **Versioning** | tracks the repo it describes | tracks the consumer repo |
| **Adopt via** | enable `harness-<repo>@<marketplace>` | write `.agent-worktrees/related/<repo>.md` |

They compose: a consumer can **enable the harness plugin** for the authoritative
operator skills and keep a thin related-narrative (or trigger-redirect skill)
only for the *consumer-specific* bits — which machines deploy it, local policy,
adoption status. Prefer the plugin for the substance; keep the narrative thin.

### Authoring your own

To ship a `harness-<repo>` plugin for a different repo, use the
**`authoring-harness-plugins`** skill in the `customizing-copilot` plugin — it
walks the structure (this plugin as the template), the naming rule, what the
contributing/diagnosing skills should contain, and how consumers adopt it.

## License

[MIT](../../LICENSE)
