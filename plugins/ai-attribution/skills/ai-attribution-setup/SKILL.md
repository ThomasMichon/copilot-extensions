---
name: ai-attribution-setup
description: >
  Adopt or repair ai-attribution policy in a repository. Idempotently reconcile
  the stable marked fallback into the repo's always-on agent instructions and
  configure host-qualified operator ownership policy. Use for first-time
  ai-attribution setup, fallback installation, ownership-account configuration,
  or validation of hook-less launch paths; not for publishing an artifact (use
  ai-attribution).
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
run plugin hooks. This skill owns the real setup mechanism: a stable,
idempotently reconciled fallback plus operator-scoped ownership policy.

## 1. Choose the always-on instruction file

Use the adopting repository's existing always-on agent instructions, normally
`AGENTS.md` or `.github/copilot-instructions.md`. Do not create a second
competing instruction file when one already exists.

## 2. Reconcile the marked fallback

Replace the one existing region between these markers, or append it once when
absent. Never duplicate the region and never alter neighboring repository-owned
instructions.

```markdown
<!-- ai-attribution:static-fallback:start -->
**Fallback policy `[owner: ai-attribution@0.1.0-dev6]`:** Before publishing,
classify the audience and repository ownership. Disclose AI assistance
prominently for another party's repository. In a verified operator-owned
repository, omit disclosure unless the operator explicitly requests it or
policy requires it; this carve-out changes disclosure only. Every public
artifact must remain persona-neutral and be scrubbed of credentials, private
identifiers, hosts, paths, accounts, record IDs, and private rationale. Use
generic placeholders, follow the target repository's conventions, and audit the
live published surface after publication.
<!-- ai-attribution:static-fallback:end -->
```

If either marker exists without its pair, stop and report the malformed managed
region rather than guessing where neighboring content begins.

## 3. Configure operator policy

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

## 4. Validate both delivery paths

1. Launch a normal session with the plugin enabled and confirm the emitted
   context starts with `[owner: ai-attribution@0.1.0-dev6]`.
2. Exercise every known hook-less launch path and confirm the marked fallback
   remains in its always-on instructions.
3. Re-run this setup and confirm it replaces the managed region in place without
   creating a duplicate.
4. Confirm operator config is outside the target repository and every ownership
   hint is host-qualified.

Do not remove or shrink a fuller pre-existing static publication policy until
this marked fallback is installed and the adopting repository's hook-less launch
paths have been validated. After both preconditions hold, remove only redundant
prose; preserve stricter repository-owned invariants.
