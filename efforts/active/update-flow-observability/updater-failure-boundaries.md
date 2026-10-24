# Bounded updater output and failure settlement

Parent: [Update Flow Observability](README.md).
Coordination: #7743; #6812, #8370 and held #6859 / #7833 retain their other scopes.

## Request and ownership

The accepted bounded ownership choice was:

> Take over updater output and settlement fixes; leave broker scope held

`rusty-kiln` owns this source-only slice. No host update, replacement launch,
queue mutation, breadcrumb edit, lifetime waiver or shared-broker takeover is
authorized by it. The existing source plan and implementation for persistent
installer-owned logging, discovery and retention stay with #8370.

## Established failure

The captured-parent evidence on #7743 identifies two remaining Windows hops:
Manager's scoped engine passthrough and the installer's retained `--settle-slot`
child select `CREATE_NO_WINDOW` without explicit output streams. An empty
parent log therefore does not establish an empty child diagnostic.

The same evidence identifies a distinct settlement gap. Ordinary native cutover
writes `started` before attempting spawn. A canonical, cleanup-confirmed spawn
failure can leave no candidate handle, while current owner recovery requires
one. The installer retains both generations indefinitely rather than resolving
that positively confirmed no-launch case. This is not evidence that every
missing PID is safe, nor that the underlying broker lifetime failure is fixed.

## Plan

The following implementation choices are _(agent-recommended)_:

- Preserve explicit stdout and stderr only at the affected native noninteractive
  boundaries. Do not change ordinary interactive command passthrough or protocol
  framing, add capture pipes with unbounded descendant EOF waits, or replace
  the independent durable-logging work.
- Carry the canonical spawn failure's positive cleanup evidence to the native
  publication owner. Validate the exact owner/attempt and original route,
  recheck predecessor native birth and serving state under native authority,
  and record a terminal failure only if those observations still agree.
  A missing handle/PID or an arbitrary exception is never sufficient proof.
- Keep uncertain launch/publication custody and existing retained generations.
  Preserve the original exception even if recovery is unavailable, and leave
  selector/manifest restoration with the existing installer owner.
- Make the smallest directly dependent documentation and changefile updates.
  This closes existing installer failure-accounting intent without another
  updater, lifetime broker, receipt cache or publication authority.

## Validation plan

- Prove both stdout and stderr through the actual scoped engine call and actual
  retained-child launch, including nonzero exit, repeated output and a no-window
  captured parent. Ordinary interactive passthrough must remain unchanged.
- Exercise real owned temporary breadcrumb/routing files and the native owner
  lock for confirmed no-launch recovery. Refuse changed owners/attempts/routes,
  predecessor birth or health, unconfirmed cleanup, corrupt evidence and any
  candidate handle. Preserve the primary exception through successful rollback
  and recovery refusal.
- Cover normal success, failed child retirement, interrupted publication and
  uncertain publication without weakening existing tests or deadlines.
- Run the focused contained suites, lint and install-contract checks. Stage new
  fixture/test files before runner snapshots. Use the available disposable
  native Windows and POSIX lanes, explicitly recording any unavailable lane;
  isolated source proof is not a production update.
- Obtain required source review/CI and normal containing release. Installed
  normal-frontdoor acceptance remains separately authorized and must not claim
  that this repair fixes the held shared-broker lifetime failure.

## Journal

- Recorded the explicit bounded ownership transfer and public #7743 claim.
  Source investigation confirms both missing-stream sites and the current
  handle-dependent recovery loop. No implementation or installed update has
  been performed by this slice.
