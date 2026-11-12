# Explicit mux question fallback

The [native question path](live-question-answers.md) remains preferred. An
authorized operator client may explicitly opt into a degraded local mux path
when the exact registered interactive session has no available native answer.
This does not turn a remote permission prompt into an answerable form.

## Inspect, answer, inspect the outcome

Use the payload-local command selected by the session command catalog:

```text
agent-bridge questions <exact-native-session-id> --via mux
agent-bridge answer <exact-native-session-id> --via mux --inspection-id <inspection-id> --option 2
agent-bridge answer <exact-native-session-id> --via mux --inspection-id <inspection-id> --text "A single-line answer"
agent-bridge questions <exact-native-session-id> --via mux --inspection-id <inspection-id>
```

Choose **one** answer command. Inspection returns one-based offered options,
freeform availability and a one-use `inspection_id` expiring after 60 seconds.
The answer must name that exact session and inspection. Aliases, guessed panes,
automatic transport fallback and decline/cancel/permission selectors are not
supported. A fresh native answer capability takes precedence: use its normal
request and generation instead.

The initial supported rendering is a single scalar enumerated choice field, optionally
with the native Other text box. Unknown layouts, multiple fields or cursors,
multi-select, permission panels, already edited text, control-bearing input
and missing native pending-request evidence refuse without input.
Rendered labels must match that field's declared choices; wrapped or
unrecognized option descriptions cannot become additional selectable rows.
Answers are bounded printable single lines; standalone mux command separators
are not answer payloads. Normal punctuation inside a literal answer is retained.

## Evidence and uncertainty

The existing local Worktrees owner resolves the native lock/process ancestry
and exact pane. Bridge confirms mux session/pane/process identity and native
incarnation, and recaptures the current viewport rather than stale scrollback.
Every mux target includes session, immutable window ID and pane index, resolved
only from session-scoped metadata matching the authoritative native pane ID and
PID. PSMux can reuse pane IDs across sessions; a bare ID is not globally unique.
Only TMux on POSIX and PSMux on Windows are supported. Each effect rechecks the
same native pending request, form and live binding. Selecting Other may expand
the native text box and scroll unrelated history; that is not a new request.
Screen identity excludes only the recognized native waiting clock immediately
before the form and the greeting card's two known blinking-eye frames. All
other viewport text, including the complete question, choices and cursor,
remains significant. These decorations cannot create a fresh admission.

Inspection and current pane evidence are complementary. Represented history
alone never grants answer authority. A missing or ambiguous authoritative
session binding cannot be replaced with a worktree-name-to-pane guess.

The authenticated Bridge operator domain remains the authority boundary.
`sender` is audit attribution, not an independent authorization or delegation
grant. Only explicitly authorized clients should possess the Bridge credential.

Capture and key delivery are **not atomic with human terminal input**. This
fallback cannot exclude the human between its last check and a key. It stops
on detected races, makes no atomic-human-exclusion or SDK-acceptance claim,
and does not retry uncertain input. A terminal participant changing input
context inside that final gap remains a residual risk of choosing this
degraded transport; use the native path when that exclusion is required:

| State | Meaning |
|-------|---------|
| `answered`, `accepted: true` | The form disappeared and a new matching native answer echo was observed. |
| `raced`, `accepted: false` | The request, screen or binding changed before any key was attempted. |
| `unverified`, `accepted: null` | Input may have happened but completion was not established. Inspect the original audit and terminal; do not resend. |

The CLI returns nonzero unless the outcome is `answered`. A lost HTTP response
or successful `send-keys` is not proof of completion.
Completion requires the full question and its adjacent exact field/value;
only a separate right-aligned native seconds/minutes duration is optional.
Admission is durably recorded before keys; the exact inspected screen can be
admitted only once.
An in-flight or uncertain attempt blocks another mux inspection for that native
session. A daemon restart revokes all unsubmitted inspection tokens and retains
the original audit, including unfinished admission. Use the terminal to resolve
uncertainty; no retry/reset endpoint bypasses that boundary.

Restored serving prepares fresh question owners; it does not inherit old
inspection tokens. Audit/admission storage requires schema 43, including the
complete `mux_question_answers` table and its unique screen admission.
Supported writable preparation upgrades earlier databases before adoption.
Migration-free candidate and writable-adoption paths refuse missing or malformed
storage instead of advertising an unusable answer capability.

The existing observation stream also records admission and outcome with caller,
answer and inspection. Original audit reads do not depend on continued live
registration. The implementation never calls abort, prompt/message submission,
mode, session-lifecycle, tool-permission or trusted-human SDK operations. It
does not claim that screen scraping can provide the native path's atomic
request-scoped application boundary.

## Optional contract and tests

The selected running daemon advertises `mux_question_answers: 1` independently
of package versions. New clients negotiate it before calls. An old or rolled
back provider refuses this optional path before effects; old native and owned
ACP callers retain their existing syntax and behavior.

| Operation | Authenticated route |
|-----------|---------------------|
| Inspect one current mux form | `GET /api/v1/live-sessions/{native-id}/questions/mux` |
| Submit inspected choice or text | `POST .../questions/mux` |
| Read original durable audit | `GET .../questions/mux/answers/{inspection-id}` |

The POST body requires `inspection_id`, `sender` and exactly one `option` or
`text`. Key navigation and submission have a finite effect deadline; each
captured subprocess is bounded and windowless. No recurring timer, callback
process or additional daemon is added.

Focused HTTP/orchestration and local-owner/CLI tests are
`tests/test_mux_questions.py` and `tests/test_mux_question_client.py`.
Actor-free parser/input/completion cases live in the required promotion unit
packet. The opt-in [private native fixture](live-question-answers.md#validation)
qualifies native, mux option and mux Other answers against the real built-in
form, with same-conversation continuation and no abort/replacement prompt.
On POSIX select `TMUX_TEST_BIN`; on Windows select `PSMUX_TEST_BIN`.
Fixture transport proof does not substitute for installed owner resolution.
PSMux capture must preserve interior blank columns. Version 3.4.0 includes the
upstream blank-cell capture fix; the delayed native fixture qualifies that release.
Older lossy captures can concatenate the question and its elapsed duration;
that ambiguous completion remains `unverified`, never a prefix match or retry.

See [agent-bridge](../README.md) and
[native interactive answers](live-question-answers.md).
