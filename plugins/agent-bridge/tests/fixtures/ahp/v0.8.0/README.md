# Agent Host Protocol 0.8.0 contract fixture

This directory freezes the released Agent Host Protocol (AHP) 0.8.0 inputs
that the first `agent-bridge` implementation targets. It is an offline
compatibility fixture, not a runtime implementation.

## Pinned source

- Repository: <https://github.com/microsoft/agent-host-protocol>
- Tag: `v0.8.0`
- Commit: `7153143f1c6993fa886d7d59870811cdad479d83`
- Upstream license: MIT; the original `LICENSE` is retained under `upstream/`.

The pinned corpus contains:

- the five generated JSON schemas;
- all reducer and round-trip JSON fixtures;
- the tagged specification documents;
- the exported command and notification maps;
- the protocol version registry and compile-time method-map checks; and
- release metadata for the Go, Kotlin, Rust, Swift, and TypeScript clients.

`source-manifest.json` records the byte size and SHA-256 digest of every
upstream file. `compatibility-policy.json` records the selected version,
source-authority rules, complete method classification, named capability and
optional-state gates, extension policy, conformance probe plan, and explicit
compatibility exceptions.

The local `.gitattributes` disables text normalization below `upstream/` so a
fresh checkout retains the exact tagged bytes verified by the manifest.

## Update rule

Do not edit files under `upstream/`. Updating the contract requires a reviewed
version decision: copy the artifacts from one exact upstream tag, regenerate
all manifest entries, update the compatibility policy for that version, and
run `tests/test_ahp_contract.py`. Different AHP versions require isolated
codecs; artifacts or semantics from another tag must not be mixed into this
fixture.

The 0.8.0 sources disagree about `disposeChat`: exported types include the
method while the tagged chat-channel specification says it is not exposed.
The policy therefore recognizes the wire name but keeps the handler
unimplemented, requires JSON-RPC `MethodNotFound`, and forbids advertising
`AgentCapabilities.multipleChats` until the lifecycle semantics are reviewed
again.
