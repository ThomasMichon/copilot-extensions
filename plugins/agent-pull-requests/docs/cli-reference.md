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

The examples below describe the legacy GitHub route. For target-qualified
native author operations and their optional delivered worker `--config`, see
[native author operations](author-operations.md). Native status, wait and merge
retain the selected Worktrees peer's policy and do not accept that author-only
configuration option.

```text
agent-pull-requests status --repo <owner/repo> --number <n> [--json]
agent-pull-requests create --repo <owner/repo> --head <branch> [--base <branch>] --title <t> [--body <b>] [--draft] [--json]
agent-pull-requests merge  --repo <owner/repo> --number <n> [--squash|--merge|--rebase] [--auto] [--delete-branch] [--json]
agent-pull-requests wait   --repo <owner/repo> --number <n> [--interval <s>] [--timeout <s>] [--json]
```

## Native `diff`

```text
agent-pull-requests diff --repo <registered-target> --number <positive-number> --worktrees-command <attributable-command> [--json]
```

Reads a unified diff through the selected Worktrees `pr-watch diff` owner,
without a claimant worktree or target checkout. The named target supplies
provider, endpoint and account; managed execution retains same-cell peer
selection. This native-only command has no default forge route, credential
or endpoint override, workspace allocation, mutation or automatic retry.

The complete `agent-worktrees.pr-diff-read` version-1 report is emitted in
both modes, including valid failures. Exit 0 requires owner exit 0,
`supported: true` and no report error. Extra frontend diagnostics go to
stderr; when no valid report exists, JSON mode reports a frontend error
with the requested repo/number instead of an invented empty diff.

Completeness and candidate freshness are independent of acquisition.
The existing provider primitive yields null completeness on success,
false on error/unsupported acquisition and no base/head identity.
The frontend preserves true/false/null completeness and additive evidence
in any valid report. Empty text is not proof of no changes, and exit 0
is not a candidate-bound review artifact or write authority. Azure DevOps
explicitly reports unified diff unsupported; its generation/source workspace
contract remains separate. GitHub honors the configured endpoint, retaining
ambient/default host selection only when none is configured. No diff is
persisted in a watch cache or journal. Unsupported old/rolled-back runtimes
fail explicitly without another invocation.
Malformed configured GitHub endpoints refuse, and supplied selected tokens
override public/Enterprise aliases. Explicit capture preserves CRLF/bare CR
and rejects invalid UTF-8; tracked diff normalization remains unchanged.

## Native `threads`

```text
agent-pull-requests threads --repo <registered-target> --number <positive-number> --worktrees-command <attributable-command> [--json]
```

Reads discussion bodies on demand through the selected Worktrees
`pr-watch threads` owner. The named target's binding supplies provider,
endpoint and account; neither a claimant worktree nor a target checkout is
required. Managed execution uses the existing same-cell peer selection.
There is no default GitHub route, token/endpoint override, resolution,
reply, mutation or automatic retry.

A valid `agent-worktrees.pr-threads` version-1 report is emitted in full in
both output modes, including valid failure reports. Exit 0 requires a
successful owner invocation, `supported: true` and no report error; other
outcomes exit nonzero. Additional frontend diagnostics go to stderr.
Without a valid report, `--json` emits a frontend error with the requested
repo/number; plain mode reports the error on stderr.

Completeness and candidate freshness are independent of exit success.
Preserve `complete: false` or null and any `candidate_changed` or native
revision fields. An empty list or exit 0 does not prove complete/current
evidence or authorize a write. Provider-specific IDs, bodies and iteration
context remain unmodified; no body is added to a persistent watch cache.
An incompatible or rolled-back runtime yields an explicit error without
fallback or another invocation.

## Source report facade

Selected ADO resolution is a separate
[native author operation](author-operations.md#selected-native-thread-resolution).
Neither `threads` nor the report facade below mutates discussions.

The optional [existing-owner report API](watch-gateway.md#optional-existing-owner-report-reads)
has a source embedding facade:

```text
report capabilities
report threads --request <bounded-json-file-or-stdin-dash>
report diff --request <bounded-json-file-or-stdin-dash>
```

Invoke `agent_pull_requests.__main__.main(argv, report_host=client)` from the
actual embedding with its selected `ReportClient`. Read JSON contains only
`number` and `budget_seconds`. It discovers the contract before reading and
emits the whole validated exchange. Exit 0 requires owner exit 0, supported
acquisition and no report error. Without a host it returns unavailable; it
does not discover an ambient Gateway, executable, credential or account.
This is not an ordinary installed HTTP client migration. Existing native
`threads`/`diff`, workspace and watch syntax remain unchanged.
An explicitly injected [v2 client](report-bounded.md) can negotiate a larger
bounded response without changing request JSON, CLI flags or the default host.

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

An acknowledged registration must echo `notification_protocol` and a nonempty
`registration_id`. If an owner rolls over to a legacy build between capability
probe and registration, the CLI reports `ambiguous_registration` and fails.
The subscription may exist: reconcile it by its target and subscriber identity
before retry or process fallback. It is not reported as acknowledged success,
and the CLI does not blindly cancel a potentially newer registration.

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
For native subscriptions, a selected merged/closed result from the admission
read is persisted as a pending event before registration succeeds. Delivery
does not depend on another provider read; repeated continuous registration
preserves the existing generation and any older pending payload.

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

- other direct forge adapters beyond the existing attributed Worktrees/native
  route described in [native author operations](author-operations.md)
- worktree-tracking integration (`set-pr`, active-PR selection, reconcile)

Those remain later parity/migration slices, kept separate so this plugin's
standalone shape stays independent of `agent-worktrees`' own worktree-bound
PR flows.
# Native foreground retirement

Legacy `serve` and the reserved passive watch entry retain their original
daemon admission and singleton/writer custody until accepted handlers, polling
and delivery work actually settle. Shutdown first closes intake successfully;
a failed close is unknown even with zero counted handlers. Bounded lower-level
close APIs still raise on timeout, but the foreground owner retries settlement
and reports the first failure only after releasing settled custody. Repeated
interrupts do not abandon workers, replay requests, expire production ownership
or authorize replacement. Partial startup follows the same retirement path.
## Workspace source frontdoor

`workspace` uses the [existing-owner tool façade](workspace-tools.md). It accepts
strict bounded JSON requests; operational methods require an actual Python host
binding and never load one from flags, files or environment. Unbound discovery
is passive, and unavailable execution does not bootstrap or change backend.
Existing commands retain their original semantics.
