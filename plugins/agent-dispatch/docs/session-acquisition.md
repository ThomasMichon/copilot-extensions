# Local worker session acquisition

One-shot `create --spawn`, local supervisor `make_headless_spawn`/
`make_embody_spawn`, and interactive embodiment acquire and deliver through
`agent-bridge acquire --request-file -`, HTTP protocol 30. Existing
`spawn_backend=bridge` selects ACP; explicit `embody` selects CLI. No missing-
capability fallback changes mode or invokes worktree session launch directly.
Standalone task/script dispatch still works without bridge.

Allocation, reservation recording, task claim/adoption, evaluation, pending
steers and terminal cleanup keep their original owners. Dispatch conveys
current generation/reservation, recorded worktree provenance, explicit
retirement (including unknown), carried identity, charter, project, driver and
verification timeout. The `dispatch:` namespace exposes current owner facts
and the latest acquisition reservation for bridge verification.

Prompt/delivery identity is stable per reservation, not a random worker ID on
each retry. Bridge—not dispatch—selects reuse/resume/create. A task generation
increment on claim/adoption does not itself retire its transcript. A carried
handle from another task/exclusive occurrence does not prove transcript affinity.
Missing native/retirement proof refuses rather than starting blank.

CLI success returns its exact native session/worktree cleanup identities.
ACP retains `local-body:<bridge-id>` and separately returns native conversation
identity. Missing bridge/native-output proof and uncertain launch effects never
become “body absent”; ambiguous outcomes defer for reconciliation. Interactive
uncertain launch preserves its worktree claim instead of releasing it beneath
a possible running process.

Fleet/remote acquisition and legacy helper retirement are still Phase 6 work.
CLI charter/model/effort overrides are capability-refused until effective-state
verification is implemented. Existing configured defaults are not replaced
when an acquisition request has no override.

ACP observation (`wait`) is separate from acquisition. A failed/timed-out
observation reports `observation_error` and a warning while retaining successful
acquisition status, bridge/native identities, and the cleanup handle; it never
turns an existing body into a spawn failure. Protocol-29 pre-request capability
refusal is a proven unsupported capability, not an ambiguous launch.

Bridge-delivered prompts supply the verified native conversation to
`start --owner-session-id <native-id>`; the CLI forwards it into the existing
start admission field. New explicit acquisition workers use a stable
`acquired-<reservation>` identity and cannot enter STARTED without that native
binding. Claim/start clears prior activity instead of inheriting an earlier
turn's IDLE. Local supervisor activity samples carry their observation time;
samples taken before the current claim/start are ignored. An explicitly
non-resting owned RUNNING turn wins over lingering idle metadata.
CLI native-handle activity probes, as well as local ACP probes, timestamp their
snapshot before the liveness query. A delayed pre-start IDLE response cannot
restore stale task activity after start.
