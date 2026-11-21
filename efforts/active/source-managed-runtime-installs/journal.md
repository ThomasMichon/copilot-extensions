# Source-Managed Runtime Installs - Journal

[Back to the effort](README.md). The Plan and Validation Plan remain the completion gate.

## 2026-11-20 - Recover saved and cleared proposal drafts without the queue

The original installed reader reconstructed the proposal but lost its saved
draft before any B mutation. The opt-in `ProposalIntake.write_draft` now commits
the guarded queue result to the existing proposal records before acknowledging
it. The first such write selects a draft-aware record layout; an older reader
cannot silently accept it as a v1 history. Ordinary queue writes do not acquire
this guarantee. A failed journal commit invalidates the writer; reconstruct
from the last acknowledged records instead of mounting the partial queue.

Separate Windows installed A -> B -> A trials withheld each entire outgoing
queue before starting the next reader. Both B readers saw the exact seed
draft. One returning A saw B's exact updated draft, including its revision;
the other saw B's acknowledged clear. Task, payload and original keyed steer
receipt survived before retry, with no wake requested. These are queue-loss
rebuilds between draft-aware fixture wheels, not historical compatibility,
active task recovery, or deployment evidence.

## 2026-11-20 - Compatible Dispatch handoff retains a cleared card draft

Two installed Windows fixture wheels transferred an unowned proposal A -> B
-> A using the outgoing queue each time. B read the exact draft saved by A
and cleared it against the observed card and draft revisions. Returning A
read the same task with the draft absent. The keyed steer receipt remained
unchanged and the wake outbox stayed empty. This covers the task's draft
field, not task deletion. Both queues used format 1; the independent
proposal and steer records do not witness a draft clear after queue loss.

## 2026-11-20 - Host-index discovery without an attached session

The installed mux consumer trial merged in [#13635](https://github.com/ThomasMichon/copilot-extensions/pull/13635).
The next private fixture exercises the public Bridge target and session status
routes with an empty database. A host index record with a living but unrelated
fixture PID is listed as a candidate. A missing or unreadable-version index is
an unavailable source, not a complete empty inventory. In all three cases,
status returns `404 Session <id> not found`. No session or turn is created.

The status handler requires a managed session before calling Driver inspection.
The existing client's inspection credential is established during attachment;
the host index's owner generation identifies the **frontend**, not the host
incarnation. Neither a PID nor a saved endpoint binds a cold probe to the
original host. Do not send an attachment credential to a candidate to find
out who owns it. Original-versus-lookalike cold verification and installed
host recovery remain open until the host owner supplies a read-only route
bound independently to the original incarnation. No new store or grant.

## 2026-11-20 - Pinned mux discovery merged

[#13621](https://github.com/ThomasMichon/copilot-extensions/pull/13621)
merged to `dev` as `01bd6c08b745`. With an empty Bridge database, the
opt-in caller used an independent pin and the Driver CLI to distinguish
the original mux incarnation from a same-name replacement. Neither result
added a target or verified an agent route.

A Windows fixture then installed `8bced12e2eaa` (A) and `01bd6c08b745`
(B) non-editably into separate environments. Each Bridge and Driver import
resolved inside its selected environment. Against the same fixture-owned
loopback endpoint and pin, B observed the original incarnation in three
native reads. New Bridge with A's Driver returned `pinned_read_unsupported`
without contacting the endpoint. Returning to A restored the legacy
capability declaration; A did not have a pinned discovery caller. B later
rejected a same-name replacement in three native reads. The Bridge session
database stayed empty. This exercised the installed discovery function, not
full CLI target listing, live reattachment or acknowledged application writes.

The subsequent installed `agent-bridge targets --json` trial passed five
Windows cases against the real HTTP health and target-list handlers in the
private fixture. Ordinary inventory was unchanged on A, B and return to A.
With an independent pin, B distinguished original, lookalike, missing and
unreadable rendezvous; the mixed installation refused the older Driver
without querying the fixture mux. No route, session or turn was created.
This tests read-only discovery, not reattachment or acknowledged writes.

Offline staging of both revisions initially failed: the resolver could not
find `agent-client-protocol` in its cache. Neither failure qualified a build.
Staging the same source revisions with the configured package source succeeded;
the exercised console scripts came from those separate non-editable installs.
No offline installation, live service validation or rollout is claimed.

## 2026-11-13 - Original application integration merged

[Source and bounded native evidence](<journal/2026/11.13 canonical-application-integration.md>)
records #11121's preserved-history merge, containing-Main release and the remaining
consumer/adopter gates. The original implementation was not superseded and the
effort remains Active.

## 2026-11-11 - Ordinary suite convergence preflight

Integration lead `lopsided-anvil` retains entrypoint wiring and whole-flow
acceptance under #7028. The
[current preflight](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6807533102)
checks exact Dev `2bf1e4f0722f651d5d77b3e1bc5c5231d7a0d570`.
The source policy still defaults to marketplace; the explicit-source suite
producer returns payload and identity without an install-source wheel profile.
This is a failed static readiness gate, not a native whole-flow result.

The [producer coordination](https://github.com/ThomasMichon/copilot-extensions/pull/11121#issuecomment-6807559710)
retains #11121's canonical construction owner. The
[consumer coordination](https://github.com/ThomasMichon/copilot-extensions/pull/10570#issuecomment-6807561002)
retains #10570's independent prepared Worktrees updater and its remaining real
association, automatic selection and profile-admission gates. Neither open
contribution is superseded or qualified by this check.

The next observable outcome is one ordinary suite caller carrying the selected
Git source and complete wheel profile through the existing construction and
consumer owners. Fresh bootstrap and install-capable first-use adapters follow
that real flow rather than introducing another preparation owner. Per-slot
construction, source/feed custody, readiness, selection and component lifecycle
boundaries remain intact. Missing additional CODEOWNER approval alone does not
hold an otherwise eligible Maintainer PR; drafts, findings, required CI and
explicit holds remain independent gates. No installation or rollout ran.

On resumption, #11121 and draft #10570 retain the same published heads.
The independently claimed #12116 now owns the additive closed-wheel input to
existing native recipes, including its construction/reuse binding; convergence
work must consume that receiver rather than duplicate it. This additional
receiver does not by itself select the ordinary suite's source or produce its
complete admitted profile. The next acceptance remains the real ordinary
producer-to-consumer flow, with the three prerequisite owners kept distinct.

A focused read of #11121 at
`c3770520b3cbde2fc86e23251b393b8832b51c55` confirms that its suite caller
already replaces the legacy `prepare()` route with `prepare_applications()`.
That caller requires version-2 engine support, prepares the selected portfolio
under one Main-seeded source identity, and emits snapshot/profile records.
The unchanged legacy helper is not the proposed caller's execution path.
This credits the existing implementation without treating its unmerged source
as released or natively accepted. Default marketplace policy, final-tree
qualification and entrypoint adoption remain distinct convergence gates;
another producer implementation is not needed while this owner retains it.

- [2026-11-05 - MCP bootstrap adoption proposed](<journal/2026/11.05 mcp-bootstrap.md>)
- [2026-11-03 - Clean-break wheel migration intent reviewed](<journal/2026/11.03 clean-break wheel migration.md>)
- [2026-11-03 - Manager-first repository registration entry](<journal/2026/11.03 manager-register.md>)
- [2026-10-31 - Footprint contract evolution reviewed](<journal/2026/10.31 footprint-contract-evolution.md>)
- [2026-10-31 - Independent replacement review corrections](<journal/2026/10.31 review-corrections.md>)
- [2026-10-27 - Finite native footprints merged and macOS qualified](<journal/2026/10.27 native-runtime-removal.md>)
- [2026-10-26 - State-preserving removal foundation](<journal/2026/10.26 runtime-removal.md>)
- [2026-10-25 - Bridge fixed-recipe prerequisite](<journal/2026/10.25 bridge-first-use.md>)
- [2026-10-25 - Bridge legacy bootstrap adoption](<journal/2026/10.25 bridge-bootstrap.md>)
- [2026-10-24 - Manager maintenance integration](<journal/2026/10.24 manager-maintenance.md>)
- [2026-10-24 - Logger fixed-recipe prerequisite](<journal/2026/10.24 logger-first-use.md>)
- [2026-10-24 - CodeSpaces bootstrap adoption](<journal/2026/10.24 codespaces-bootstrap.md>)
- [2026-10-24 - CodeSpaces fixed-recipe prerequisite](<journal/2026/10.24 codespaces-first-use.md>)
- [2026-10-24 - SSH command adoption](<journal/2026/10.24 ssh-commands.md>)
- [2026-10-24 - Direct all-service backlog intake](<journal/2026/10.24 backlog-intake.md>)
- [2026-10-24 - SSH selected-snapshot recipe](<journal/2026/10.24 ssh-selected-snapshot.md>)
- [2026-10-24 - SSH fixed installation recipes](<journal/2026/10.24 ssh-first-use.md>)
- [2026-10-24 - SSH legacy bootstrap adoption](<journal/2026/10.24 ssh-bootstrap.md>)
- [2026-10-24 - Pull Requests bootstrap adoption](<journal/2026/10.24 pull-requests-bootstrap.md>)
- [2026-10-24 - Pull Requests command adoption](<journal/2026/10.24 pull-requests-commands.md>)
- [2026-10-24](<journal/2026/10.24 runtime-installs.md>)
- [2026-10-23 - Pull Requests first-use recipes](<journal/2026/10.23 pull-requests-first-use.md>)
- [2026-10-23](<journal/2026/10.23 runtime-installs.md>)
- [2026-10-23 - Vault bootstrap publication](<journal/2026/10.23 vault-bootstrap.md>)
- [2026-10-22](<journal/2026/10.22 runtime-installs.md>)
- [2026-10-21](<journal/2026/10.21 runtime-installs.md>)
- [2026-10-20](<journal/2026/10.20 runtime-installs.md>)
- [2026-10-19](<journal/2026/10.19 runtime-installs.md>)
- [2026-10-18](<journal/2026/10.18 runtime-installs.md>)
- [2026-10-17](<journal/2026/10.17 runtime-installs.md>)
- [2026-10-16](<journal/2026/10.16 runtime-installs.md>)
