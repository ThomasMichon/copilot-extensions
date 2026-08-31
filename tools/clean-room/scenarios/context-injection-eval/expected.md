# context-injection-eval - expected outcome

This Tier-E witness separates deterministic broker correctness from
model-visible delivery.

## Starting state

`setup.sh` creates a local synthetic marketplace and installs its payloads with
the stock Copilot plugin CLI. The marketplace contains:

- the unpublished `context-injection` payload copied from the mounted source
  checkout;
- two authority-aware context producers selected by the repository variant;
  and
- one side-effect-only `sessionStart` plugin declared
  `restart-safe-idempotent` / `context: none`.

Repositories A and B each adopt the exact
`context-injection@copilot-extensions` authority and engine version 2 through
trusted `.context-injection/config.yaml`, while host settings only enable the
plugins. They enable distinct producers and therefore distinct high-entropy
canaries.

## Tier-P prerequisite

Before the bridge turn, `fixture.py tier-p` proves:

1. authority completion before producer calls;
2. producer completion before authority output;
3. concurrent producer/authority calls;
4. two session IDs for repository A; and
5. repository B produces a different canary set and CWD identity.

The evidence contains only contributor counts, token hashes and occurrence
counts, and session/CWD identity hashes.

## Tier-E pass

For every fresh agent-bridge session:

- the final transcript contains one exact compact JSON response;
- both expected canaries occur exactly once;
- no invented canary occurs;
- no tool call or file read reconstructs the response; and
- the side-effect plugin leaves exactly one idempotent marker for that
  session/CWD pair while contributing no context.

A result reached by reading fixture files, settings, payloads, logs, or
diagnostics is a false pass and must be scored FAIL.

Run the scenario once with variant A and at least two fresh sessions, then once
with variant B. The overall claim is unanimous: any failed run is FAIL.

## Classified gap

If the stock CLI cannot install the unpublished local marketplace, or a fresh
agent-bridge ACP session cannot load the installed plugins and execute their
`sessionStart` hooks without staged-plugin exceptions or manual payload copying,
classify the result as `scenario-transport-gap`. That is a transport/runtime
limitation, not evidence that model-visible context is complete.
