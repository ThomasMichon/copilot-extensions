# Persistent new-session mode configuration

This offline helper is the single preference owner; adopters must not parse YAML
themselves. Explicit task mode stays caller-owned and highest priority. No config
operation switches existing sessions or authorizes service rollout.

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
and other scopes, atomically replaces the selected file and verifies readback.
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
