# Model-free skill-selection SDK spike

This is an **opt-in transport witness**, not a selector, registered extension,
model-quality evaluation or production routing profile. It uses public SDK
APIs in a separate, caller-owned runtime and synthetic inputs only. Importing
`scripts/skill-selection-spike.mjs` does not connect or infer.

Run the sibling `scripts/run-skill-selection-spike.mjs` with an absolute native
SDK directory (containing `index.js`) and a **fresh, nonexistent fixture root**:

```text
node run-skill-selection-spike.mjs <absolute-sdk-directory> <fresh-fixture-root> [absolute-runtime-executable]
```

Keep the process headless and caller-contained. The root holds only synthetic
workspace/session state; existing roots are refused. The caller owns retention
and cleanup. By default the selected SDK supplies its owned stdio runtime.
An explicitly selected absolute runtime executable uses the SDK's public
`RuntimeConnection.forStdio({ path })` option; a bundled-package failure never
selects a substitute automatically. This still spawns a separate owned runtime,
not an external connection. Private parent fields, provider endpoint guessing,
global settings, shared services and foreground extension reload are forbidden.

The lifecycle census classifies the SDK `sessions.fork()` site as
caller-contained. It copies session data in the owned runtime, not an OS
process fork; the native runtime remains under the caller's cleanup boundary.

The process-global `CopilotRequestHandler` intercepts every model request.
Chat Completions and Responses HTTP replies/usage are synthetic, including
their distinct SSE formats; WebSocket is refused, never forwarded.
Do not install this interceptor in a shared or production runtime. Native
model enumeration may use the vendor's existing login; the script neither
exports nor prints credentials. Enumeration errors are unavailable, not zero
capacity, and there is no live inference fallback.

The child environment strips the SDK's GitHub token aliases, including
enterprise aliases, case-insensitively; authentication stays in the vendor
store. Model preflight retains capability summaries for every returned model,
not a selected family only. Missing SDK exports and constructor failures produce
sanitized startup reports; no client means cleanup is not applicable.

For a **separate read-only model preflight**, append `--list-models`. This mode
starts an owned client, calls the public `client.listModels()` retrieval API,
then stops it without creating sessions or sending prompts. Model enumeration
may contact the vendor through its existing login. No synthetic interceptor is
registered in this mode: that interceptor also refuses model-list requests.
The report distinguishes vendor enumeration from zero-forwarding synthetic
transport and cannot qualify a model's selection quality.

To witness an explicitly bound candidate configuration, supply `--model <id>`,
`--reasoning-effort <level>` and `--context-tier <tier>`. The owned sessions
restrict their allowed models to that ID, and native task dispatch passes it
explicitly. Effort/tier options require a model; unknown or duplicate options
are rejected. Record observed session model snapshots and child configuration/
usage independently of these requested values. Missing child tier evidence is
still unknown, never inferred from a token limit or pricing tier.

The witness freezes native skill descriptors (including actual identity,
disabled state, aliases, complete description and pathless metadata), then
compares a full persisted fork, exclusive event-boundary fork, automatically
bounded primary user/assistant history, and an explicit brief. The catalog
is identical across arms. Bounded history is tail-first, durable and page/byte
limited; it excludes reasoning, tool output and child payloads and refuses
expired or missing boundaries. It retains newest whole messages in order.

JSON output identifies the request format and reports supplied input separately from **assembled request**
bytes, message/tool overhead, synthetic sentinel inclusion/order, inherited
event IDs, effective model/configuration and owned-task lifecycle observations.
For Responses, message counts/bytes describe serialized input items, not an
assumed Chat Completions message representation.
Native task events are subscribed before dispatch, scoped to owned IDs;
cancellation acknowledgement is not settlement. No session-wide drain is used.
All sessions are aborted and the owned client is stopped through public SDK
lifecycle methods, including after errors. Cleanup errors remain visible.
Ownership-acquiring calls (startup, create, fork, resume and dispatch) are
awaited to completion rather than raced against an application deadline.
Run this probe under the caller's finite supervisor: a hung acquisition cannot
advance to another arm or claim cleanup while a late resource is untracked.
Read-only operations still have local deadlines. Capture slots are reserved
before asynchronous body reads; instructions and input both participate in
Responses sentinel scans. First dispatch and completion models are separate.

Limitations: forks require the owned server client and do not establish access
from an ordinary joined extension. Native task input has no isolation promise.
Synthetic completion/usage does not prove model quality, fallback quality,
context capacity, compaction behavior, pricing or savings. Unknown native
capabilities, model limits, effort/tier support and accounting remain unknown.
The eventual selector, candidate/evaluator binding, live comparison and main
preference remain separate gates in the canonical effort.

Focused helper regression packet (through the caller's bounded supervisor):

```text
python tools/run-plugin-tests.py customizing-copilot --test-file tests/native/test_skill_selection_spike.py --allow-explicit-tiers
```

Stage new source/tests before invoking the runner. This native Node packet is
opt-in; it adds no inference or exhaustive subprocess matrix to required CI.
