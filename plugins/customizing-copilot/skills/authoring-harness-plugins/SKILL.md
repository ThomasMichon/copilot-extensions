---
name: authoring-harness-plugins
description: >
  Ship an operator-harness plugin for a repo -- the <repo>-harness standard. A
  payload-only plugin, authored by a repo's owner, that provides the skills to
  work ON that repo (contribute + diagnose), portable to any control repo that
  enables it. Use when a repo should ship its own operator skills instead of
  every consumer hand-writing a per-repo narrative.
  Trigger phrases include:
  - 'harness plugin'
  - '<repo>-harness'
  - 'author a harness plugin'
  - 'ship operator skills for a repo'
  - 'make a harness plugin'
  - 'operator harness plugin'
  - 'portable repo skills'
---

# Authoring Harness Plugins

The **`<repo>-harness`** standard: a repo ships its own *operator harness* — a
payload-only Copilot CLI plugin that teaches an agent how to work **on** that
repo. Instead of every downstream control repo hand-writing a per-repo narrative,
the repo owner authors the operator skills **once**, versions them with the repo,
and any consumer adopts them with one `enabledPlugins` line.

The reference implementation is **`copilot-extensions-harness`** in the
copilot-extensions repo — read it as the template.

## When to author one

Ship a harness plugin when a repo is **operated on by agents from other repos**
and the "how to work on it" knowledge is worth centralizing — a shared library,
a service, a plugin suite, a tool others deploy or debug. If only one control
repo ever touches it, a plain related-narrative is enough; the plugin earns its
keep when the knowledge is reused or safety-critical.

## Harness plugin vs related-narrative

| | Harness plugin (`<repo>-harness`) | Related narrative |
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

1. **Name it `<repo>-harness`** — e.g. `copilot-extensions-harness`,
   `my-service-harness`. This is the repo-scoped **suffix** family; the token
   placement carries meaning, so name to the taxonomy:

   | Pattern | Means | Installs where |
   |---------|-------|----------------|
   | `agent-<thing>` | adds capability `<thing>` to the **general agent** | broadly (infra) |
   | `harness-<thing>` | adds capability `<thing>` to the **general harness** | broadly (infra) |
   | **`<repo>-harness`** | **harness-side** plugin to work ON / **control** `<repo>` from a control plane | the **control harness**, never `<repo>` itself |
   | `<repo>-agent` | **in-repo** agent capabilities for `<repo>` | `<repo>`'s own venue, never outside it |

   A harness plugin authored here is the **`<repo>-harness`** case: it targets a
   *specific repo* (not a general capability of the harness), so the repo name
   leads and `harness` is the suffix. Leading with the repo name also groups a
   repo's plugins together (`<repo>-agent`, `<repo>-harness`, …) in a `plugins/`
   listing and an `enabledPlugins` map. Two propagation rules follow from the
   family:
   - **Never install a `<repo>-harness` into `<repo>` itself** — it's control-plane
     side, for an *external* operator of the repo.
   - **Never install a `<repo>-agent` outside `<repo>` itself** — it's in-context,
     only meaningful inside that repo's venue.
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
plugins/<repo>-harness/
  plugin.json                                  # payload-only manifest
  README.md                                    # overview + the <repo>-harness standard note
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
  "enabledPlugins": { "<repo>-harness@<marketplace>": true }
}
```

Then any local `repo-<repo>` redirect skill and related-narrative can slim to a
pointer at the plugin — the operator substance now lives with the repo it
describes.

## Overlap & precedence (multiple marketplaces enabled)

Once a consumer enables several marketplaces (copilot-extensions, a team catalog,
a repo's own `/.ai`), two skills can answer the same request. **Copilot has no
precedence setting** — every enabled marketplace exposes the same root
`marketplace.json`, and enabled skills are surfaced to the model by their
**description/trigger**; the model selects by match. So you can't *declare* "my
skill wins." You engineer it, with four levers:

1. **Narrow, specific triggers beat broad ones.** Write each skill's
   `description` + trigger phrases to the *exact* task, and add explicit
   **disambiguation** — "use this for X; **not** for Y (use Z)". A precise match
   is chosen over a vague "ALWAYS invoke on all changes" skill, and the negative
   clause tells the model when to defer. Broad, imperative triggers ("ALWAYS
   invoke on ANY edit") are an anti-pattern: they shadow narrower peers — don't
   author them, and be wary of enabling plugins that do.
2. **Your `enabledPlugins` set is the real precedence knob.** The surest way a
   competing skill doesn't win is to **not enable it** — or to enable it only
   where it's wanted. Curate deliberately; don't blanket-enable a broad-trigger
   catalog globally.
3. **Scope by settings layer.** Settings compose additively across **user → repo →
   workspace** (`~/.copilot/settings.json`, a repo's `.github/copilot/settings.json`,
   a workspace file). Keep your always-on harness skills at **user** scope so
   they're present everywhere; enable a broad or repo-specific tool **narrowly**
   (repo/workspace) so it's only in play where it belongs. Narrow-trigger +
   narrow-enable *is* the precedence mechanism.
4. **Name-prefixing keeps identities unambiguous.** A plugin is addressed as
   `<plugin>@<marketplace>`, and an aggregating catalog may prefix names (e.g. a
   synced team catalog surfaces `<catalog>-<name>`). Enable the specific prefixed
   plugin you want so there's no ambiguous bare name.

**Don't encode precedence as per-skill "prefer me" notes.** A line in a skill
body that says "prefer this over the other plugin's skill for now" is a smell —
the model doesn't read one skill while choosing another, so the note never fires
at selection time. Fix overlap at the source instead: tighten your triggers
(lever 1) and curate what's enabled (levers 2–3). If two of your *own* skills
overlap, split or merge them so each has a clean, non-colliding trigger.
