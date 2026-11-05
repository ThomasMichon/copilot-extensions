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
