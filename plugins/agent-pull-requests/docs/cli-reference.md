# agent-pull-requests CLI reference

`agent-pull-requests` is a new, separate plugin for **cross-repo** PR
operations addressed directly by `--repo owner/repo`, even when the target repo
has no local worktree checkout.

## Status of this slice

This is the runtime-backed extraction from `agent-worktrees`' existing PR
verbs into a superset plugin:

- `agent-worktrees` is unchanged in this slice.
- Consumer/doc migration and deprecation are explicitly later steps.
- All four verbs (`status`, `create`, `merge`, `wait`) are implemented.
- Marketplace install deploys a real `~/.local/bin/agent-pull-requests`
  binstub backed by the plugin's own versioned runtime.

## Verb surface

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
agent-pull-requests merge  --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
agent-pull-requests wait   --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

## `status`

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
```

Reads one GitHub pull request and reports:

- PR state
- mergeable state
- review decision
- draft bit
- title and URL

## `create`

```text
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
```

Opens a pull request via `gh pr create --repo <owner/repo> --head <branch> ...`.
`--head` must already be pushed to the target repo (or a fork per `gh`'s
`<user>:<branch>` syntax); no local checkout is created or required. `--base`
defaults to the repo's default branch when omitted. Returns the created PR's
repo/number/url (parsed from `gh`'s printed PR URL).

**Requires a claimant worktree.** Run this from the agent-worktrees worktree
responsible for the work (the owning project is always the CWD, same
contract `create-pr`/`pr-watch`/`pr-merge` enforce) -- refuses with an
actionable message rather than silently opening an unowned PR when CWD
doesn't trace to a tracked worktree. On success, auto-journals a `pr`-kind
claim onto that worktree (`agent-worktrees claims add pr`) so `finalize`
knows the cross-repo work is still outstanding; the result carries
`claimed_by` (the worktree id) on success, or `claim_warning` when the
journal step itself failed (the PR was still created -- that failure is
never fatal to the create).

## `merge`

```text
agent-pull-requests merge --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
```

Merges an existing pull request via `gh pr merge`. Defaults to `--squash`;
`--auto` enables auto-merge instead of merging immediately; `--delete-branch`
removes the head branch after a successful merge.

## `wait`

```text
agent-pull-requests wait --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

Polls `status` (default 15s interval, 600s timeout) until the pull request
reaches a terminal state (`MERGED` or `CLOSED`). Exit codes: `0` merged,
`1` closed without merging (or a status error), `3` timed out while still
open.

## `watch subscribe`, `watch health`, `watch status`, `watch unsubscribe`

```text
agent-pull-requests watch subscribe --repo <owner/repo> --number <n> --subscriber-id <id> [--until <transition>] [--timeout <seconds>] [--acknowledged-notifications] [--notify-timeout <seconds>] [--json] --notify-argv <argv...>
agent-pull-requests watch health [--json]
agent-pull-requests watch status [--json]
agent-pull-requests watch unsubscribe --repo <owner/repo> --number <n> --subscriber-id <id> [--registration-id <generation>] [--json]
```

`watch subscribe` boots the existing shared watch owner when needed and returns
immediately. Repeated `--until` selects `merged`, `closed`, `review_changed`,
`mergeable_changed`, or `checks_changed` (default: merged and closed).
An already-terminal first snapshot fires immediately. `--timeout` is optional,
finite and positive; expiration also fires during provider outages.

`--acknowledged-notifications` negotiates `acknowledged_notifications/v1` from
health before registration. A successful opted-in registration returns
`registered: true`, `notification_protocol` and `registration_id`. Missing
capability or invalid options return an error/nonzero CLI exit; no legacy
subscription is silently created in their place. Without opt-in, existing
`{"registered": true}` and best-effort notification behavior remain unchanged.
`--notify-timeout` requires opt-in and accepts a finite positive number no
greater than 30 (default: 30 seconds).

### Generic consumer wire contract

Discover the owner's attributable endpoint using its existing installation
boundary and authenticated coalescing control protocol. Send `health` first;
only a response whose `capabilities` includes `acknowledged_notifications/v1`
supports the following `register` payload:

```json
{
  "repo": "example/project",
  "number": 42,
  "subscriber_id": "consumer-1",
  "until": ["merged", "closed"],
  "timeout": 600,
  "notification_protocol": "acknowledged_notifications/v1",
  "notify": {"argv": ["consumer-callback", "--consumer-generation", "generation-1"], "timeout": 10}
}
```

Use a real attributable executable path in `argv`. The list is executed directly,
without a shell. Unknown opted-in notify options, empty/malformed argv, bool/string
numeric inputs and nonfinite timeouts are rejected. Do not persist secrets in
argv. The consumer must handle ambiguous registration failures by reconciling
its exact registration rather than arming two wait owners.

The callback reads one JSON object from stdin, with:

- `repo`, `number`, `subscriber_id`, `transitions`, `timed_out` (legacy fields).
- `pr_state`, `merged`, `review_decision`, `mergeable`, `checks_state` when a
  snapshot exists (a timeout during provider outage need not have one).
- `notification_protocol: "acknowledged_notifications/v1"`, stable `event_id`,
  and the registration's `registration_id` (opted-in only).

Persist the consumer's idempotent effect for this event/registration before
exiting **0**. Any nonzero exit, launch exception or timeout is a delivery
failure, not an acknowledgement. Stdout/stderr are discarded, not an ACK
channel. Delivery is at-least-once: replay after remote commit/local ACK loss
is expected; subprocess execution itself is not exactly-once.

The owner persists the identical payload before calling the consumer and
retains it through restart/crash. Failures retry after 1, 2, 4, 8, 16, 32, then
60 seconds indefinitely (no automatic exhaustion). Subscribers remain
independent even on the same PR. Pending callbacks recover before ordinary
polling; callback execution never holds the persistence lock.

`watch status` includes `pending_deliveries`, with identity, failed `attempts`,
`next_attempt` (Unix seconds), and sanitized `last_error` codes
(`callback_nonzero`, `callback_timeout`, `callback_error`). No callback argv or
raw exception/output is exposed there.
Status/health also report `persistence_error: "state_write_failed"` during
state-write outages; successful persistence clears it. A failed state write
cannot acknowledge a remotely committed callback.

`unregister` accepts `repo`, `number`, `subscriber_id`, and optional
`registration_id`; a generation mismatch returns `unregistered: false`.
Cancellation/replacement removes pending delivery but cannot undo an in-flight
callback's side effects. Old callbacks cannot acknowledge a newer registration.
State remains in the existing `watch-subscriptions.json`; malformed opted-in
records stop startup with an explicit sanitized error and are not discarded.
Legacy persisted records still load. Do not downgrade until opted-in
registrations have acknowledged or been explicitly cancelled.

For shutdown/restart boundaries, durability, and recovery detail, see
[the owning README](../README.md#acknowledged-watch-callbacks-opt-in).

## Shared implementation details

1. GitHub-only in this slice.
2. All verbs shell out via `agent-worktrees repos gh <owner/repo> -- ...` to
   reuse the existing account/token plumbing instead of inventing a second
   auth path.
3. An explicit `owner/repo` target works **without** a local checkout, but
   the account-selection behavior still comes from `agent-worktrees`:
   - registered repos can use pinned account data;
   - unregistered repos fall back to owner-based resolution;
   - org-owned repos whose actual `gh` login differs from the owner may still
     warn and use ambient auth until this plugin grows its own lighter-weight
     resolver.

## Not implemented yet

- non-GitHub providers
- worktree-tracking integration (`set-pr`, active-PR selection, reconcile)

Those remain later parity/migration slices, kept separate so this plugin's
standalone shape stays independent of `agent-worktrees`' own worktree-bound
PR flows.
