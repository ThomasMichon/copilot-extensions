# Maintenance-aware local resident recovery

Parent: [complete effort](README.md), Step 7.
Source obligation: [S2](sidecar-live-timeline.md#s2---worktree-and-agenda-owner-observations).

**Status:** maintenance-aware source behavior merged in #10744 and actual
private normal-ensure process qualification merged in #10758. The
[source readback](journal/2026/11.05-sidecar-resident-recovery.md) records the
qualified transaction, refusal, readiness and child-custody controls. The
additional actual private normal-ensure process packet passes its positive and
maintenance-negative controls. This finite source amendment is qualified;
installed operation and the complete parent are not accepted by this receipt.

## Operator direction

> If the local resident daemon isn't running, we should check if the maintenance-window flag is up, otherwise retry on a brief backoff and then use normal CLI commands which should wake it

Agendas and their accepted-operation history remain local SQLite data. This
direction concerns the Worktrees resident serving canonical claim, disposition
and effort-binding writes, not remote agenda storage or a new synchronization
system. It supersedes the proposed durable offline-capture investigation as the
next source approach; it does not promise recovery of never-captured old events.

## Proposed source sequence (agent-recommended)

Use the existing normal CLI's canonical write/ensure boundary, not a new
supervisor, receipt-publication endpoint, spool or retrying Sidecar reader.

- If no usable resident exists, inspect the current user's maintenance notice
  and applicable existing installation maintenance markers before recovery.
  Active maintenance prevents automatic wake/recovery. Unreadable or invalid
  maintenance evidence cannot authorize wake; report that uncertainty without
  declaring an invented maintenance owner or silently clearing a hold.
- Without a maintenance hold, wait one brief bounded backoff and reread the
  existing rendezvous. A resident that becomes ready during the delay needs
  no spawn. Recheck maintenance immediately before the existing normal ensure.
- Use the normal command's existing ensure/start mechanism and bounded startup
  wait. Do not restart, reap, install, repair routing or alter configuration
  merely because a resident is absent. Effort-binding commands should use
  the same ordinary wake policy as claim/disposition commands.
- Retain exact live-owner/version compatibility handling. A live incompatible
  owner is not an absent daemon and must not be restarted to force support.
  Existing explicitly no-wake native/receipt paths remain no-wake.
- Retry discovery, never an ambiguous mutation. Once request bytes may have
  been sent, retain the existing unknown-outcome refusal; do not resend or
  run the direct implementation. If bounded normal startup still fails, retain
  the existing truthful direct fallback and partial observation qualification.
- Keep Sidecar observation/history commands passive, selected-source fenced
  and cancellable. They do not gain permission to wake a daemon during painting,
  subscription, history paging or automatic reconnection.

Source implementation and isolated tests are authorized by the continuing
effort. This direction does not authorize an installed rollout, a real daemon
wake/restart, live Mux rebinding or any real-data effect in this source session.
Native/manual/Relay, qualified PRE and whole-parent acceptance remain separate.

## Plan and validation

- [x] Review this amendment before changing recovery behavior.
  #10741 merged as `431e2506ba50836d481b66efc81156afadec7358` after
  independent current-head approval and green CI, before implementation.
  The full parent, three original contracts and this amendment were read
  immediately after actual merge and confirmed unchanged by reconciliation.
- [x] Reuse the maintenance signals and normal ensure boundary, with one brief
  pre-start retry and a maintenance recheck at wake.
- [x] Align effort-binding's normal CLI wake policy without changing passive
  readers or deliberately no-wake native paths.
- [x] Test absent/active/invalid/unreadable maintenance, a resident appearing
  during backoff, maintenance appearing during backoff, successful bounded
  normal startup, persistent absence, live incompatible owners and no-wake
  callers. Prove a post-send failure never replays canonical work.
  #10744 qualifies the isolated discovery/readiness/refusal state machine and
  canonical intake. The additional private process packet passes actual normal
  ensure/spawn, PID/birth/health, native mutation history and warm reuse, plus
  maintenance/no-spawn and exact leased cleanup controls. #10758 merged as
  `9eedc950ead88d1d80e2ffdada7dc80a7f6d3982` after current-head independent
  approval and all required CI, followed immediately by complete parent,
  original-contract and recovery-amendment readback.
- [x] Exercise the actual canonical transaction and resident observation intake
  through isolated owned fixtures. A mocked getter or a wake return value alone
  does not establish a captured mutation.
- [x] Obtain independent current-head review, required green CI and actual
  merge; immediately reread the full parent and original contracts, journal
  only earned source progress and continue the complete parent gate.
  #10744 merged as `a3184d0d46a77ddcd03f2979064aab8d35b711d3`.
  The complete parent, all three original contracts and this amendment were
  read immediately afterward, before bookkeeping or further source work.
