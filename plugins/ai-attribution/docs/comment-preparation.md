# Compact comment preparation

Back to [ai-attribution](../README.md) and
[signature profiles](signature-profiles.md). The reviewed
[comment-identity contract](../../../efforts/active/durable-pr-watch-delegation/comment-identity.md)
owns the wider publisher/correlation work. This optional pure helper composes
an explicitly supplied draft; it does not allocate identities, authorize,
persist, post or synchronize anything.

## Selected inputs and output

Run the helper from the actual selected payload through an existing Python:

```text
python <ai-attribution-payload>/scripts/comment_preparation.py --repo <target-root>
```

It reads one bounded JSON request on stdin and returns JSON on stdout:

```json
{
  "draft": "Selected reply.",
  "context": {
    "domain": "forge",
    "visibility": "public",
    "activity": "reply",
    "required": true,
    "human_reviewed": false
  },
  "source": {
    "protocol": "agent-worktrees.comment-origin/v1",
    "privacy": "public",
    "origin": {"codename": "example-label"},
    "effects": "none"
  },
  "comment_id": "11111111-1111-4111-8111-111111111111",
  "write_id": "22222222-2222-4222-8222-222222222222"
}
```

The CLI loads the existing effective operator/repository signature profile.
The library `prepare` accepts that same validated `Profile` explicitly. Neither
path creates another audience-policy engine. The actual publisher supplies
policy facts, exact-draft human-review evidence and destination classification;
untrusted remote request labels cannot supply them.

The source object comes from the existing
[Worktrees comment-origin producer](../../agent-worktrees/skills/worktree/references/pr-attribution.md#compact-comment-origin-producer).
Only that exact protocol/shape is supported. Its assigned codename is the public
worktree stamp; raw worktree data is accepted only for an `internal` context
and an internal-qualified source projection. A private/external repository is
not automatically internal. Anonymous source is unavailable by default; an
adopter explicitly permitting it may set `allow_anonymous: true`, retaining
`privacy: anonymous` and no invented source. This never waives AI disclosure.

The caller supplies canonical version-4 GUIDs deliberately allocated for public
comment correlation, not private session/task/attempt IDs. The logical comment
and intentional-write GUIDs must differ. The helper never mints another ID
because a call was retried. Hardware/time-derived version-1 UUIDs are not
accepted as opaque public correlation identifiers.

Successful output is `agent-forge-comment.preparation/v1` with `identity`,
`body`, `body_id` (SHA-256 of exact UTF-8 bytes) and `effects: none`. The identity
contains the supplied GUIDs and validated source snapshot. It appends exactly
one owned hidden line after the configured visible signature:

```html
<!-- agent-comment:v1 c=11111111-1111-4111-8111-111111111111 w=22222222-2222-4222-8222-222222222222 cn=example-label -->
```

Required signature text is kept off opening/closing root fence lines by adding
paragraph separation when needed; already separated boundaries remain unchanged.
The same separation preserves a leading explicit blockquote, including a quoted
metadata example, across initial preparation and retries.
Backtick and tilde blocks survive repeated preparation, including a configured
footer without its own leading newline. Overlapping header/footer boundaries
are ambiguous and refuse before decoration, including owned old variants;
current signatures take precedence over variants at their own boundary.

An allowed internal source additionally carries `wt=<worktree-stamp>`.
This is not `agent-worktrees:source`: it cannot refresh PR ownership.
No root/session/head/encrypted-record history or decorative banner is added.
An empty body, including a signature plus metadata alone, refuses rather than
preparing a filler comment.

## Retry and edit boundaries

Supply the previous complete preparation as optional `previous` when
reconciling or editing an already prepared write. Its exact body/hash,
identities and canonical outer marker must agree. With that retained record,
a retry retaining the same write GUID must produce identical prepared content;
changed profile/source/body refuses instead of silently redecorating a pending
write. Without a retained record the pure helper cannot discover prior use of
a GUID; the actual intent owner must enforce that durable binding.

An intentional edit retains the comment GUID and supplies a fresh write GUID.
Only the exact current/previous owned trailing marker can be replaced; an
unowned, foreign, malformed or duplicate outer marker refuses. Indented, inline
or multiline forms of the owned HTML namespace outside protected literal
examples are noncanonical and refuse, not untracked text beside a new tag.
Closed fenced, explicit blockquoted and same-line inline-code examples remain
authored text. HTML-comment and inline-code boundaries are processed in source
order: a backtick inside an HTML comment cannot open an inline code span.
A known private worktree stamp from the previous preparation
cannot be copied into a public edit, even as an example; the caller must
redact that draft before approval rather than the helper silently deleting it.
Repeated preparation with the same
profile/source/IDs is idempotent and does not append another signature or tag.
Do not pass the resulting tagged body through another generic signature
decorator afterward.

The helper is deliberately conservative, not a complete provider Markdown
renderer. Unterminated root fences, unbalanced/nested literal HTML comments
and ambiguous outer boundaries refuse. Backtick fence info strings containing
backticks also refuse; tilde fence info strings retain their literal backticks.
Final native rendering/body preservation
still needs the publisher/provider's own qualification.

The final body has a 64 KiB maximum, optionally lowered with integer
`max_body_bytes`; signatures and metadata count in UTF-8 bytes. The hidden line
has a 512-byte limit. CLI requests, including a previous preparation, have a
192 KiB input limit. Errors exit 2 with value-free
`comment_preparation_unavailable`; unknown or abbreviated flags refuse.
No fallback to a PR-source marker or unsupported peer is performed.

## Ownership and remaining adoption

Prepare before final preview/approval and immutable intent admission. Retain
the exact prepared bytes, public GUIDs and original source snapshot through
the existing intent/receipt owner; do not make a parallel body archive.
A lost response, restart or ownership handoff cannot become a new write.
The optional previous-record check is a local consistency check, not a durable
uniqueness registry or proof that a native comment exists.

The actual publisher still binds source/destination policy, target/account,
native comment/actor/version, mutation permission and original operation.
A matching tag or body hash cannot authorize, confirm a write, merge native
objects, resend an unknown effect or advance handled/echo progress.

No predecessor lookup or conditional-signature suppression is implemented
here. Visible attribution remains exactly as concise as the existing profile
and disclosure policy permit. Actual issue/PR/reply/inline/review publishers,
GUID allocation/persistence, observation correlation and installed/native
provider adoption remain separate work; a source helper is not all-comment
coverage or live deployment.
