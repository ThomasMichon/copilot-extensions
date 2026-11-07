# Live interactive question answers

An authenticated operator or explicitly delegated trusted host can answer an
agent's pending `ask_user` in a registered interactive CLI session without
aborting its turn or taking over its process. This is separate from owned ACP
answers and Dispatch task-card steering.

## Inspect and answer

Use the payload-local command from the session catalog:

```text
agent-bridge questions <exact-native-session-id>
agent-bridge answer <exact-native-session-id> --request-id <native-request-id> --generation <extension-generation> --field color=blue
```

`questions` returns JSON with `availability`, `generation`, and `requests`.
Each request names its native `request_id`, `family`, `tool_call_id`, message,
form schema or choices, and `answerable`/`reason`. Select the exact request and
generation you inspected; aliases and worktree handles are not answer selectors.
Consecutive identical forms still have different native request IDs.

For typed form values use the existing answer `--json` object option.
`--decline` and `--cancel` resolve a structured elicitation without content.
A native `user_input` request accepts `--field answer=<text>`; it has no
decline/cancel response. `--option-id` cannot be combined with `--generation`.
The existing owned ACP `answer` behavior is unchanged when generation is absent.

The JSON answer result includes `answer_id`, `state`, and `accepted`.
Only `state: accepted` / `accepted: true` means the actual native RPC accepted
the answer. The CLI exits nonzero for rejection or uncertainty. `in_flight` or
`unknown` is not permission to resend: inspect the original outcome or terminal.
An already-admitted response returns HTTP 409 with its original `answer_id`,
including when the original reply was lost.

## Authentication and scope

The existing authenticated Bridge operator domain is the authority boundary.
Only give its credential to the operator or trusted hosts acting within explicit
delegation. There is no new per-agent ACL or independent identity provider here.
`sender` is required audit attribution, not proof of delegation; calling oneself
`operator` does not authenticate a caller. Do not expose this endpoint or its
Bridge credential directly to untrusted agents or web origins.

The extension admits only observed root-agent `ask_user` requests with native
tool/request identity. Tool permissions, plan/auto-mode/policy decisions, URL
requests, sourced MCP elicitations and sub-agent requests remain outside this
answer path. Represented permissions still omit answer correlation IDs.
Question answers use only `handlePendingElicitation` or
`handlePendingUserInput`, never permission RPCs or the SDK's `handleHuman*`
trusted-human operations. A delegated answer therefore does not manufacture
human authorization evidence. The observer contributes no permission handler.

## Wire contract and recovery

Support is the running daemon's `/health.capabilities.live_question_answers: 1`,
independent of package versions and optional within the existing HTTP contract.
The client freshly negotiates that capability against the selected endpoint
before its operation and does not retry an ambiguous answer write. The
extension caches provider discovery for at most 30 seconds per endpoint and
publishes current question snapshots on its existing two-second timer.

| Operation | Authenticated route |
|-----------|---------------------|
| Current native snapshot | `GET /api/v1/live-sessions/{native-id}/questions` |
| Submit exact answer | `POST .../questions/answers` |
| Original durable outcome | `GET .../questions/answers/{answer-id}` |
| Extension snapshot and question-only claim | `POST .../questions/poll` |
| Extension's native RPC outcome | `POST .../questions/answers/{answer-id}/ack` |

Submission requires `generation`, `request_id`, `family`, `sender`, `action`
and `content`; `wait_timeout` defaults to 30 seconds (0 returns after admission).
The unique native-session/generation/family/request key admits only one answer.
Retries and conflicting answers are refused, not replayed. A timed-out unclaimed
control is withdrawn; a claimed control with no outcome remains `in_flight`.
Each control also carries a finite application deadline. Terminal-first
completion, supersession, expired or changed native identity, and unsupported
SDK operations refuse before an answer can apply or return native rejection.

The same durable control store records sender, exact original session,
generation/request binding, response, claim time and outcome. Generic message,
mode and readiness consumers cannot claim or acknowledge question controls.
Question polling is independent of prompt-delivery custody, including a retained
inbox; it cannot deliver prompts, switch mode or approve permissions. Audit
reads and delayed acknowledgments stay pinned to the original native ID even
after rollover or registration loss.

Current snapshots expire after 15 seconds and bind the registered extension
process incarnation. A Bridge restart starts with no answer authority; the
still-running extension may republish its actual pending in-memory request.
An extension restart gets a new generation and does not reconstruct pending
questions from transcript text. Unless a native request is observed again,
use the original terminal. Forwarded requested/completed events improve
representation, but passive history remains read-only and cannot mint answers.

Older daemons, extensions or SDKs retain manual terminal answering. An
unsupported or stale observation is unavailable, not an empty healthy snapshot.
There is no interrupting-message fallback, automatic takeover, rollout or
restart. Old callers and owned ACP routes retain their existing behavior;
rollback to a provider without this capability refuses the optional operation.

## Validation

`tests/questions.test.mjs` exercises the ordinary RPC selection, stale identity,
terminal/remote races, duplicate controls, unsupported SDKs and lost-ACK recovery.
`tests/test_live_questions.py` covers authenticated HTTP admission and durable
outcomes; representation tests cover native lifecycle correlation separately.
Two actor-free validator controls are admitted in the required promotion unit
packet; native and process-bearing integration remain opt-in.

The opt-in Windows native test uses an existing CLI and PSMux executable,
private CLI/Bridge homes and mux namespace, and an offline synthetic model:

```powershell
$env:COPILOT_NATIVE_TEST_EXECUTABLE = "<absolute-existing-copilot.exe>"
$env:PSMUX_TEST_BIN = "<absolute-existing-psmux.exe>"
python tools\run-plugin-tests.py agent-bridge --test-file tests\native\test_live_question_roundtrip.py --allow-explicit-tiers
```

It invokes the real built-in structured `ask_user`, observes the parked native
form, answers through HTTP and the bundled extension, and requires native tool
completion, same-conversation continuation, one user prompt, no abort, cleared
represented state and terminal completion. It does not answer existing sessions,
grant tool permissions, use credentials or contact a model provider outside its
own loopback fixture. The current native fixture qualifies structured elicitation;
`user_input` compatibility has focused protocol/RPC tests, not a separate native
legacy-tool qualification claim.

See [agent-bridge](../README.md) and the
[delegation contract](delegation-contract.md).
