---
name: ai-attribution-setup
description: >
  Adopt or repair ai-attribution policy in a repository. Synchronize the
  plugin-owned static fallback projection, configure host-qualified operator
  ownership policy, and validate hook-less launch paths. Use for first-time
  ai-attribution setup, fallback installation, ownership-account configuration,
  or hook-less validation; not for publishing an artifact (use ai-attribution).
  Trigger phrases include:
  - 'set up ai attribution'
  - 'configure ai attribution'
  - 'install the attribution fallback'
  - 'adopt ai-attribution'
  - 'configure owned accounts'
  - 'validate hook-less attribution'
---

# AI Attribution Setup

Adopt the plugin's durable safety boundary in a target repository. Enabling the
plugin supplies the richer `sessionStart` kernel, but some launch paths do not
run plugin hooks. The canonical minimal fallback lives at
`instructions/publication-safety.instructions.md` and is declared by
`instruction-projections.json`; do not duplicate its prose in setup guidance.

## 1. Synchronize the static projection

Use the projection manager shipped by
`customizing-copilot:reviewing-customizations`:

```bash
python3 <reviewing-customizations-skill-dir>/scripts/manage-instruction-projections.py \
  sync <repo-root>
```

The manager reads only explicit data declarations from enabled plugin payloads,
writes the declared `.github/instructions/ai-attribution/` destination, and
updates `.github/copilot/context-projections.json`. It refuses unmarked,
modified, differently owned, malformed, escaped, symlinked, or reparse-point
destinations and never deletes files. Review and commit the resulting diff.

If `customizing-copilot` is not installed, ai-attribution remains independently
usable through its primary hook and skills, but the hook-less projection cannot
be materialized by this reference workflow. Enable that manager or use another
implementation of the same declarative contract; do not hand-copy the template.

## 2. Configure operator policy

Reconcile operator-owned policy outside the target repository, preferring
`~/.copilot/ai-attribution.conf`. Preserve existing comments and valid settings;
add or update only the requested policy lines:

```text
disclosure=third-party
owned_account=github.com/example-owner
```

Use one `owned_account=<public-host>/<public-account>` line per forge account.
Host and account are both required; a bare account is invalid because the same
owner name can belong to different people on different forges. Use
`disclosure=always` only when the operator wants disclosure in owned
repositories too. Do not place ownership hints or private identifier lists in
target-repository config.

The target repository may optionally add only additive guide paths:

```text
contribution_guide=CONTRIBUTING.md
```

at `.github/ai-attribution.conf`.

## 3. Validate both delivery paths

1. Launch a normal session with the plugin enabled and confirm the emitted
   context starts with `[owner: ai-attribution@0.1.0-dev10]`.
2. Run the projection scan and require no blocking findings:

   ```bash
   python3 <reviewing-customizations-skill-dir>/scripts/manage-instruction-projections.py \
     scan <repo-root> --from-settings
   ```

3. Exercise every known hook-less launch path and confirm the projected
   instruction remains active.
4. Re-run sync and confirm the generated file and lock are unchanged.
5. Confirm operator config is outside the target repository and every ownership
   hint is host-qualified.

The scanner reports old `ai-attribution:static-fallback` managed regions as
legacy migration findings. Do not remove or shrink a fuller pre-existing static
publication policy until the projection is installed and the adopting
repository's hook-less launch paths have been validated. Then remove only the
legacy duplicate manually and preserve stricter repository-owned invariants.
