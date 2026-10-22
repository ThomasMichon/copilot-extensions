# Opt-in ordinary-launch mock ACP witness

This below-altitude integration fixture exercises existing launch contracts;
it does not add a runtime backend, agent type or active configuration.

Run through the normal bounded runner:

```powershell
$env:AGENT_WORKTREES_CREATION_NATIVE = "1"
python tools\run-plugin-tests.py agent-bridge --test-file tests/test_mock_acp_ordinary.py
```

The tests use the existing native-owner item's 120-second watchdog and the
existing protocol/cleanup bounds. No runner deadline override is needed.
`MOCK_ACP_EVIDENCE_DIR`, when explicitly set to an owned artifact directory,
retains CLI/HTTP receipts, peer wire records, reservation snapshots, task events,
allocation counts and cleanup observations. Artifacts are not source files.

## Real and substituted boundaries

- The configured `copilot_path` launches an actual stdio child through the
  unmodified `LocalSpawner` and SessionHost. The peer accepts the ordinary
  `--acp --stdio --no-auto-update` prefix and records trailing arguments without
  interpreting them as tool commands.
- The peer reuses the existing native activation test's JSON-RPC loop and the
  idle-continuation test's thought/message shapes. It performs initialize/new,
  thought/text updates and a prompt-ID-correlated `end_turn`, with no model.
- An owned ASGI carrier replaces only HTTP transport. Production Bridge CLI
  composition, typed routes, SDK, SessionHost and owner CLI handlers remain real.
  Registry/caller discovery is fixture metadata, not shared machine discovery.
- The existing synthetic owner fixture supplies isolated configuration and
  suppresses its resident monitor. Actual Git allocation, registration and
  native-source checks remain in place.
- Registration is compatibility-only: the test peer's ACP identity is explicitly
  **native-source UNQUALIFIED**. The tests do not create CAR session history,
  native hold files or source attestations. An indexed owner ledger does not
  imply CAR-native conversation data.

## Preamble control

The control reserves directly and executes ordinary create/store/send. It checks
one allocation and nonce, one host/peer/conversation/turn, thought and text
reduction, and the actual correlated terminal RPC response. The task deliberately
remains QUEUED: output text is not task completion or verification.

## Settled-response-loss counterexample

Both attempts use the actual ordinary headless spawn factory and
`Supervisor.poll_once`, with concurrency one, maximum failed attempts one and no
reactive loop. The first invocation loses its response only after actual HTTP
admission and turn settlement. The supervisor itself defers that reservation.
The test checks canonical deferral and positive retry eligibility before one
further finite poll.

The final assertion still requires exactly one turn. The initial local
counterexample reached that assertion with one retained allocation, peer and
conversation but two turns; that fail-first evidence remains retained. The
local Phase 7 source repair makes the original assertion pass, together
with an actual stdio inbound `session/prompt` count of one and equality to the
fully rendered frozen prompt. Model/task completion is not inferred from a
single database row. The test waits for ordinary prompt-task settlement and a
finite quiet observation without widening its existing deadline.

The CLI carrier parses all arguments through the production parser and forwards
keyed invocation JSON and capability flags through production builders. A
separate real-peer case covers UTF-8, quotes and CRLF/newline preservation.
Normal owned teardown checks the actual peer boot PID as well as host and
wrapper/redirector PIDs; private receipts retain all three custody observations.

The native Windows acceptance run passes the two original cases without skips;
the separate Unicode case also passes. The retained pre-repair counterexample
still records two actual inbound prompts. These are separate historical and
candidate observations, not a rewritten baseline or a released-runtime claim.

These tests do not qualify a network daemon, real Copilot boot/authentication,
model/tool behavior, qualified native lifecycle, task claim/result verification,
crash recovery or fleet activation.
