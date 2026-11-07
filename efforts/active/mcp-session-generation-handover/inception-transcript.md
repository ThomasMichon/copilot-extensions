# MCP Session Generation Handover - Inception

[Effort](README.md)

## 2026-11-04 - Operator request

> Okay, we need to do something about the MCP retirement issue. After an update the next round of fresh `agent-mcp` calls need to go to the new version, but held-alive servers are permitted to hold onto the outermost wrapper at the old version

## Clarification

The coordinator asked:

> Fresh launches will use the new version either way. For connections already held open, which behavior should this fix guarantee? Your earlier description called for reattachment; allowing the old shared host to drain would be a narrower first fix.

The selected option was:

> Old wrappers reattach to the new shared host without losing their MCP sessions

The response value was `reattach`. The alternative permitting existing
connections to remain on an old shared host until disconnect was not selected.

## Capture validation

The Request, Guiding Intent and Plan preserve both parts of the operator's
request: fresh calls select current code, and held outer wrappers need not be
replaced to preserve their sessions. The clarification makes reattachment an
acceptance requirement. The coordinator's proposed phases, contract analysis,
tests and legacy adoption handling are marked agent-recommended in the effort;
they are not additional operator grants.

## 2026-11-04 23:50 - Architecture reconciliation

> Right. File necessary bugs and correct existing efforts to reflect this architecture, which should already be the north-star

The agent-recommended reconciliation applies the existing authoritative-runtime-
replacement contract: independently qualify and land incoming publication first;
outgoing recovery/cleanup and full adopted-client reattachment are separate
outcomes, not incoming prerequisites. Reattachment remains required work; this
does not substitute old-host draining or close whole-effort validation.

## 2026-11-05 - Observer scope

The operator asked:

> Why do we need a quiet moment?

After the coordinator explained that the existing SDK predicate observes the
whole desktop, rather than only the fixture, the operator requested:

> Can we make the observer *not* watch the whole desktop?

### Capture validation

The request is to change observation scope, not to ask for a quiet desktop or
filter unrelated global observations into a passing consumer result. The
fixture-owned acquisition, native identity, console-host attribution and
fail-closed coverage design in the effort is agent-recommended implementation.
Keeping the existing SDK self-test profile separate is a compatibility
recommendation, not an assertion that the scoped consumer path already exists.

This direction does not replace the fresh-execution and session-reattachment
request or grant additional native effects. Any separately granted bounded
calibration approval retains its original scope and consumption state.

## 2026-11-06 - Withdraw the MCP observer prerequisite

The operator corrected the scope:

> We should disconnect anything to do with MCP from terminal host focus; not sure how that got intertwinced

### Capture validation

The coupling was agent-introduced. MCP generation selection, session preservation
and owned cleanup do not depend on observer/focus/terminal-host/calibration
qualification. Those application-specific gates are withdrawn, not validated.
Generic SDK work is independently tracked by #10925 and
[its canonical effort](../fixture-scoped-native-observation/README.md).

The earlier observer request remains historical intent for SDK scope, not a
renewed MCP prerequisite. Existing source work and journals are preserved.
No prior application-bound calibration approval transfers to the SDK; no native
effect, debugger implementation/execution or new process graph is authorized by
this correction or by the source-only decoupling slice.
