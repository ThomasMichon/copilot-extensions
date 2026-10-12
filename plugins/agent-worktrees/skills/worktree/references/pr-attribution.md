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

### One owner key through the vault cache

Set `AGENT_WORKTREES_IDENTITY_VAULT_ARGV` to a JSON argv array naming the
absolute payload-local agent-vault command from its session catalog.
`AGENT_WORKTREES_IDENTITY_VAULT_ENTRY` selects the common entry (default:
`Identity/PR-attribution`). Store one base64-encoded 32-byte owner key in that
entry in the synced vault database; all machines use the same entry and key.
Do not create an identity key independently on each machine.

Enable the vault's protected local cache and explicitly unlock/populate it
once on each machine with the vault's `cache-populate` flow. Publication and
decode use `get <entry> password --cache-only`: no daemon contact, automatic
unlock, prompt or key creation. A cache miss or invalid key emits a sanitized
warning and never switches silently to another key source. The existing
`AGENT_WORKTREES_IDENTITY_KEY` file override takes precedence for compatibility;
it must contain the same owner key. Without either explicit source, legacy
OneDrive file resolution is unchanged.

The vault database is the common portable authority; local cache wrapping is
machine-bound and must not itself be synced. Rotating the vault key requires
explicit cache refresh on every machine; keep the previous key securely for
older PR tokens, which cannot be decrypted with a replacement key.

Windows encryption uses native CNG AES-256-GCM, including on ARM64, with the
same portable token format and shared key as the cryptography backend used on
other platforms. No machine-derived key is used for PR tokens. Machine-bound
DPAPI protection applies only to local vault cache data. On other platforms,
a missing encryption package produces a sanitized warning and omits `enc=`;
decoding reports an explicit error.

Bounded vault reads require a non-reaping POSIX exit observer so cleanup cannot
signal a reused process-group identity. Older macOS Python builds without
`os.waitid`/`WNOWAIT` omit the optional vault-backed encrypted layer with a
sanitized warning before launching an adapter; use a runtime exposing those
primitives for vault-backed reads. Existing file-key decoding remains available.

If the marker carries an `enc=<token>` field, it is a self-contained
AES-256-GCM-encrypted blob of the FULL raw identity (worktree id, machine,
session, head SHA, project, timestamp). Worktree, machine, session and head
correspond to the raw `true` marker; project and UTC timestamp are additional
encrypted metadata, absent from the raw marker. The matching key also unlocks
the optional nested `origin` identity: the originating
root/control worktree's machine, project and worktree ID. The producer fields
remain separate. Origin capture follows validated local ownership links and
honors the origin repository's attribution opt-out. Missing, remote, cyclic,
unsafe or changing chains omit `origin`, never guess it. Each new pushed marker
captures the current chain; earlier encrypted markers remain historical
snapshots after a handoff. Existing tokens without `origin` remain readable.

Only the holder of the
matching symmetric identity key can decrypt it; no SSH scan, no tracking
store, and no network access needed:

```bash
python -m agent_worktrees.identity_marker decode "<token>"
```

### Existing file-key compatibility

With no configured vault argv, the key resolves from
`AGENT_WORKTREES_IDENTITY_KEY` or the legacy OneDrive location
(`<OneDrive root>/Apps/agent-worktrees/identity.key`). Existing file-key adopters
may provision once with `python -m agent_worktrees.identity_marker generate`;
that command refuses generation when a vault source is configured. Vault
users provision the common entry and populate the cache as described above.
Generation publishes a complete key file without replacing an existing key,
including when another process creates it concurrently. Keep the old key
before using `generate --force`: replacing it prevents old markers from
decrypting. Local atomic creation cannot serialize two machines before
their sync client reconciles; provision once and wait for synchronization
before generating markers on another machine. A malformed or unreadable
key omits encryption with a sanitized warning, rather than regenerating it.
The legacy file-key alternative is a lighter-weight, lower-assurance mechanism than a real secrets
vault (a plain file, not machine-bound, not MFA-gated) -- a convenience for
low-stakes reverse lookup, not a security boundary. Without a configured
key, the `enc=` field is simply absent from every marker; nothing else
about the `codename=`/`root=` fields changes.

## When there's no marker at all

### PRs opened by another tool, including Azure DevOps

From the managed source worktree, emit a marker or prepare an existing UTF-8
body before handing it to your existing PR-opening tool:

```text
<agent-worktrees catalog argv[0]> attribution-prepare
<agent-worktrees catalog argv[0]> attribution-prepare --body-file description.md --json
<agent-worktrees catalog argv[0]> attribution-prepare --body-file description.md --tag control-harness
```

This provider-neutral command does not open or modify a remote PR, resolve
provider credentials, or change the input file. Its JSON result contains
`version`, `marker`, `body` and `attributed`. It uses the source repository's
attribution mode, codename provenance, validated root-chain and configured
shared identity key; anonymous opt-out or `--omit-source` removes stale source
markers and emits no adopter tag. Unmanaged sources and unsafe codenames fail
explicitly rather than inventing a source or falling back to raw identifiers.

`--tag` is an optional visible public label supplied by the adopter's own
invocation/configuration, not an upstream repository or organization default.
Labels containing the immediate machine/worktree identifier as a
case-insensitive substring, and malformed labels, are refused. Body composition
uses the existing source-marker helper's trailing-whitespace normalization;
the input file itself is unchanged. Copy
the prepared body into the existing GitHub or Azure DevOps publication flow;
those tools retain ownership of provider-specific rendering and posting.
An encrypted marker is optional when the common key is unavailable, following
the same sanitized warning contract as managed publication.

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
