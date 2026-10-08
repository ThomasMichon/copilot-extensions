# Identifying a PR's Source Worktree (PR attribution & codenames)

Use the exact `argv[0]` from the agent-worktrees session command catalog for
every direct runtime operation below. Replace
`<agent-worktrees catalog argv[0]>` with that path and never search `PATH`.
Substitute the raw path and quote it at each shell call site.

This doc is written for the reader who is **not** the PR's author -- a
maintainer or reviewer who has found a PR that looks stalled, unfamiliar, or
abandoned and needs to trace it back to the worktree/machine that opened it.
If you're the author who already knows this worktree opened the PR and just
wants to resume it, see [pr-workflow.md](pr-workflow.md)'s `create-pr`
section instead -- this doc covers the same mechanism from the other side.

## What the marker looks like

`create-pr` can embed a hidden HTML-comment marker in the PR body (and
refresh it as a managed comment on every re-push), controlled by the
repo's `pr.source_attribution` config key. Its shape depends on the mode:

- **`"codename"` (default, codename-attribution-by-default).**
  ```html
  <!-- agent-worktrees:source codename=<name> -->
  ```
  A public-safe marker carrying the worktree's assigned codename -- a
  random label with no decodable meaning on its own. This is the marker
  you'll actually encounter on almost every repo, since it's the default.
  When the opening worktree is itself claimed as an outbound resource by
  ANOTHER worktree (e.g. a child worktree spun up on behalf of a
  different, calling repo's session), the marker may also carry an
  optional `root=<name>` field: the ROOT ancestor's own codename,
  resolved by walking that ownership chain. Just as public-safe as the
  main codename -- never a raw identifier -- and present only when the
  whole chain resolves safely (see `docs/architecture.md`'s *PR
  Attribution & Codenames* section for the full detail: same-machine-only
  resolution, each root's own provenance gate, and the `false` anonymous
  opt-out always withholding a root's codename too). A third, independent
  `enc=<token>` field may also be present: an AES-256-GCM-encrypted blob
  decryptable only by the holder of a shared symmetric identity key (see
  *Decrypting the `enc=` field* below) -- opaque ciphertext to anyone else,
  so it is emitted automatically whenever that key is configured. Scoped
  to this `"codename"` mode specifically -- it never appears under `true`
  (already plaintext) or `false` (the anonymous opt-out).
- **`true` (raw marker, closed-circuit repos only).** Embeds the full raw
  worktree id, machine name, session id, and head SHA directly. Never used
  on a public repo.
- **`false` (anonymous opt-out).** No marker at all -- if you don't find one,
  either the repo opted out, or the PR predates attribution being enabled.
  There's no way to trace it after the fact; ask the author directly.

If the PR has been re-pushed since it opened, prefer the **newest** marker --
check managed PR comments as well as the initial body; a later push's
comment supersedes the original body's marker.

## Resolving a codename back to its worktree

```bash
<agent-worktrees catalog argv[0]> resolve --codename <name> --dry-run
```

`--dry-run` reports what would be resolved without launching anything --
use it first when you're just investigating, not resuming. Drop it (or use
`embody --codename <name>` for a detached agent session) once you're ready
to actually open the worktree.

Resolution order:

1. **Local first.** The codename is looked up in this machine's own
   tracking store. If it's a match, you're done -- the command resolves (or
   launches) directly.
2. **Cross-machine SSH scan (Phase 3), automatic.** If no local match,
   `resolve`/`embody` automatically asks every other known, SSH-reachable
   machine whether its own tracking store recognizes that codename.

**What "fails closed" means in practice:** a match found on a *different*
machine is never auto-launched remotely. Instead the command reports which
machine and worktree id own that codename, so you know where to look --
resuming from there means SSHing to that machine directly (a future
inter-agent dispatch mechanism may automate this further). This is
deliberate: a cross-machine resolve should never silently open a live
session on a machine you didn't expect.

## Decrypting the `enc=` field

If the marker carries an `enc=<token>` field, it is a self-contained
AES-256-GCM-encrypted blob of the FULL raw identity (worktree id, machine,
session, head SHA, project, timestamp) -- the same information the raw
`true` marker carries in plaintext, just encrypted. Only the holder of the
matching symmetric identity key can decrypt it; no SSH scan, no tracking
store, and no network access needed:

```bash
python -m agent_worktrees.identity_marker decode "<token>"
```

The key resolves from `AGENT_WORKTREES_IDENTITY_KEY` (an explicit path) or,
by default, a OneDrive-rooted location (`<OneDrive root>/Apps/agent-worktrees/identity.key`)
so the SAME key file works from any of the key holder's machines --
provision one with `python -m agent_worktrees.identity_marker generate`.
This is a lighter-weight, lower-assurance mechanism than a real secrets
vault (a plain file, not machine-bound, not MFA-gated) -- a convenience for
low-stakes reverse lookup, not a security boundary. Without a configured
key, the `enc=` field is simply absent from every marker; nothing else
about the `codename=`/`root=` fields changes.

## When there's no marker at all

- Confirm the repo actually has `pr.source_attribution` configured and not
  set to `false` -- run `<agent-worktrees catalog argv[0]> attribution-audit`
  against the repo to check its configuration and branch-name-leak
  exposure.
- A PR opened before attribution was enabled on this repo, or opened through
  a manual (non-`create-pr`) push, will have no marker -- there's nothing to
  resolve mechanically; ask the PR's author, or check the branch name for
  hints (though a public-safe `head_pattern` should not leak identifiers
  either).
- A marker that fails to resolve anywhere (no local match, no cross-machine
  match) usually means the source worktree/machine was since cleaned up,
  decommissioned, or is currently unreachable over SSH -- not that the
  marker itself is malformed.

## Accepted tradeoff -- read this before treating a codename as secret

A codename is **informationless-by-decoding, not unlinkable**. The marker
itself carries no facility topology, but the *same* codename recurring
across multiple PRs from the same worktree is still a correlatable signal to
an outside observer watching that repo's PR history. This is an accepted,
deliberate tradeoff (see `docs/architecture.md`'s *PR Attribution &
Codenames* section) -- it buys stalled-PR traceability without publishing
raw identifiers, but it is not an anonymity guarantee.

## See also

- [pr-workflow.md](pr-workflow.md) -- the author-facing `create-pr` section
  this doc is the reviewer/maintainer-facing counterpart to; also covers the
  branch-name leak class and `attribution-audit`.
- `docs/architecture.md`'s *PR Attribution & Codenames* section -- the
  conceptual/invariant-level explanation of the mechanism and its
  threat-model tradeoff.
- `docs/config-reference.md` (`pr.source_attribution`, `head_pattern`) and
  `docs/cli-reference.md` (`resolve`, `embody`, `create`, `list`) -- full
  field- and flag-level detail.
