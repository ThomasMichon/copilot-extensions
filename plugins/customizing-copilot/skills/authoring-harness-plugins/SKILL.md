---
name: authoring-harness-plugins
description: >
  Ship an operator-harness plugin for a repo -- the harness-<repo> standard. A
  payload-only plugin, authored by a repo's owner, that provides the skills to
  work ON that repo (contribute + diagnose), portable to any control repo that
  enables it. Use when a repo should ship its own operator skills instead of
  every consumer hand-writing a per-repo narrative.
  Trigger phrases include:
  - 'harness plugin'
  - 'harness-<repo>'
  - 'author a harness plugin'
  - 'ship operator skills for a repo'
  - 'make a harness plugin'
  - 'operator harness plugin'
  - 'portable repo skills'
---

# Authoring Harness Plugins

The **`harness-<repo>`** standard: a repo ships its own *operator harness* — a
payload-only Copilot CLI plugin that teaches an agent how to work **on** that
repo. Instead of every downstream control repo hand-writing a per-repo narrative,
the repo owner authors the operator skills **once**, versions them with the repo,
and any consumer adopts them with one `enabledPlugins` line.

The reference implementation is **`harness-copilot-extensions`** in the
copilot-extensions repo — read it as the template.

## When to author one

Ship a harness plugin when a repo is **operated on by agents from other repos**
and the "how to work on it" knowledge is worth centralizing — a shared library,
a service, a plugin suite, a tool others deploy or debug. If only one control
repo ever touches it, a plain related-narrative is enough; the plugin earns its
keep when the knowledge is reused or safety-critical.

## Harness plugin vs related-narrative

| | Harness plugin (`harness-<repo>`) | Related narrative |
|---|---|---|
| Authored by | the repo **owner**, once | each **consumer**, per control repo |
| Ships from | the target repo (marketplace) | the consumer's control repo |
| POV | neutral, portable | that consumer's POV |
| Versioning | tracks the repo it describes | tracks the consumer repo |

They **compose**: a consumer enables the harness plugin for the authoritative
operator skills and keeps a thin narrative (or trigger-redirect skill) only for
consumer-specific facts (which machines deploy it, local policy, adoption
status). Substance in the plugin; keep the narrative thin.

## The standard

1. **Name it `harness-<repo>`** — e.g. `harness-copilot-extensions`,
   `harness-my-service`. The prefix makes the intent and target obvious in a
   `plugins/` listing and an `enabledPlugins` map.
2. **Payload-only.** No runtime, no venv — it ships skills. Enabling it is the
   whole install. (See `installing-plugins` for the payload-vs-runtime model.)
3. **POV-neutral.** Write for *any* adopter, not one control repo. Don't bake in
   a specific machine, operator, or facility. Resolve the checkout path at
   runtime; never hardcode it.
4. **Point at the repo's own authoritative docs.** The skills are the operator's
   map; the repo's `CONTRIBUTING.md` / `AGENTS.md` / architecture docs remain the
   versioned source of truth. Reference them rather than duplicating detail that
   drifts.

## Structure

```
plugins/harness-<repo>/
  plugin.json                                  # payload-only manifest
  README.md                                    # overview + the harness-<repo> standard note
  skills/
    contributing-to-<repo>/SKILL.md            # how to change + land work in the repo
    diagnosing-<repo>/SKILL.md                 # symptom -> cause -> action for its deployed artifacts
```

`plugin.json` mirrors any payload-only plugin — `name`, `description`,
`version`, and `"skills": "skills/"` (skills auto-discover from the folder). See
`authoring-skills` for `SKILL.md` frontmatter and the folder convention.

### What the two skills should contain

- **`contributing-to-<repo>`** — repo layout; the contribution flow (branch/PR
  or worktree, per the repo's policy); the **gotchas that silently swallow work**
  (for a marketplace repo, the mandatory version bump); test/lint/contract gates;
  deploy-after-merge; and the "edit the source, never the deployed copy" rule.
- **`diagnosing-<repo>`** — where the deployed artifacts live; a
  **symptom → cause → action** table for the common failures; the key diagnostic
  commands; and any reset/baseline escape hatch. Lead with
  **diagnose-before-remediate** discipline.

Two focused skills beat one sprawling skill: contributors and diagnosers arrive
with different triggers.

## Wire it into the marketplace

Register the new plugin like any other: add a `plugins[]` entry in the repo's
marketplace catalog and bump the catalog version (a new plugin is a catalog
change). Follow the repo's own release rules — the harness plugin is versioned by
the same pipeline as everything else it ships.

## How consumers adopt it

In the consumer's `.github/copilot/settings.json` (see `installing-plugins`):

```json
{
  "extraKnownMarketplaces": {
    "<marketplace>": { "source": { "source": "github", "repo": "<owner>/<repo>" } }
  },
  "enabledPlugins": { "harness-<repo>@<marketplace>": true }
}
```

Then any local `repo-<repo>` redirect skill and related-narrative can slim to a
pointer at the plugin — the operator substance now lives with the repo it
describes.
