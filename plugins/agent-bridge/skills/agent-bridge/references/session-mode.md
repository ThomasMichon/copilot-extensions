# Persistent new-session mode configuration

This offline helper is the single preference owner; adopters must not parse YAML
themselves. Explicit task mode stays caller-owned and highest priority. No config
operation switches existing sessions or authorizes service rollout.

## Selected-runtime compatibility

The additive contract is `agent-bridge.session-mode/v1`. Its supported generation
is 1; stdout JSON has additive fields, and consumers require `mode` plus `source`
while ignoring unknown result fields. Absent policy defaults to ACP. No prior
command is removed or changed, and there is no deprecation window to retire.

Before requiring this capability, invoke the catalog command's
`config session-mode --help` against the runtime it actually selects. That
runtime's parser advertises the contract identity; a newer skill payload is not
proof that an older selected runtime supports it. An unknown subcommand (exit 2)
or absent/unsupported contract means unsupported capability, not missing policy.
Report the named missing capability and the need for an authorized update;
never guess a mode, silently fall back, update/restart automatically, or block
previously supported send/resume operations on existing sessions. After an
authorized update, re-probe the actually selected runtime before using v1.
This capability check does not contact a daemon.

## Read and set

Global and venue-scoped machine-local settings (the flag explicitly selects the
active service config, independent of the current checkout):

```bash
<agent-bridge catalog argv[0]> config session-mode --machine-local --set acp --json
<agent-bridge catalog argv[0]> config session-mode --machine-local --venue codespace --set cli --json
<agent-bridge catalog argv[0]> config session-mode --venue codespace --json
<agent-bridge catalog argv[0]> config session-mode --machine-local --venue codespace --unset --json
```

For versioned operator policy, resolve that repository's authorized writable
worktree through its own owner first; never guess or edit its anchor. Substitute
that absolute worktree root below, review the diff, and follow its publication
flow. Ordinary reads may use its canonical read-only checkout.

```bash
<agent-bridge catalog argv[0]> config session-mode --operator-repo "<absolute-config-worktree-root>" --set cli --json
<agent-bridge catalog argv[0]> config session-mode --operator-repo "<absolute-config-worktree-root>" --venue ssh --set acp --json
<agent-bridge catalog argv[0]> config session-mode --operator-repo "<absolute-config-worktree-root>" --repo example --venue codespace --set cli --json
<agent-bridge catalog argv[0]> config session-mode --operator-repo "<absolute-config-worktree-root>" --repo example --venue codespace --json
```

The setter changes exactly the requested scope, preserves unrelated YAML values
and other scopes (including aliased YAML), serializes setters with a per-config
kernel lock, atomically replaces the selected file and verifies readback. Lock
contention beyond five seconds fails visibly. Existing permission bits are
preserved; newly created files use restrictive permissions.
Native service save/adopt/remove and eager schema migration share that lock across their configuration
transaction. A stale native save retains a newer mode preference when it did
not edit that policy, and refuses conflicting simultaneous mode edits.
Serialized YAML uses ASCII Unicode escapes, preserving non-ASCII values even
when a legacy Windows reader uses its platform-default text encoding.
Its `written` result identifies the exact modified file/scope; top-level `mode`
and `source` report the subsequent effective resolution, which can differ when a
more specific scope or marketplace overlay wins. `--unset` removes that exact
scope, exposing the next applicable preference. A write requires exactly one
explicit destination flag; a read never infers a writable operator destination.

## Schema and authority

```yaml
session_modes:
  default: acp
  venues:
    codespace: cli
  repos:
    example:
      default: acp
      venues:
        codespace: cli
```

Owner precedence is operator repository > current checkout > active service
configuration > adopter `--default` (ACP when omitted). Within each owner:
repo+venue > repo default > venue > global. In particular an operator global
preference outranks a checkout's venue preference and `--default`.

Repository names are exact and case-sensitive, with an optional `owner/name`
form; empty/whitespace/path-like selectors are rejected. Only `acp`/`cli` modes
and the four documented lowercase venues are accepted. Unknown schema fields,
malformed policy, unreadable present files and invalid owner configuration fail
visibly even if a higher-priority preference exists.

Repository reads use the existing canonical/legacy/marketplace owner. A setter
edits the existing canonical base, otherwise the existing legacy base, otherwise
creates the canonical base. It never silently migrates legacy files or modifies
marketplace overlays. The helper does not load a service or require provisioning.
