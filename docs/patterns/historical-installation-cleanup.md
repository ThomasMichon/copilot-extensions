# Pattern: owner-scoped historical installation cleanup

**Serves:** the installer vision's
[on-demand historical cleanup](../../visions/installer/README.md#on-demand-historical-cleanup).
**Coordination:** [#10180](https://github.com/ThomasMichon/copilot-extensions/issues/10180).

This is the implementation contract for an explicit maintenance operation, not
a claim that all resource families already expose historical cleanup. Missing
detectors, unavailable resource owners and unsupported disposal remain visible.

## Separate installation, recognition and disposal

An ordinary update constructs the requested release independently of historical
cleanup. Compatible incumbent recognition permits incremental reuse; failure to
recognize an old footprint does not veto installing the replacement. Updating,
uninstalling a whole installation and reclaiming selected old resources are
different operations with different protected sets.

Historical recognition identifies a candidate. It does not establish that the
candidate is obsolete, disposable, or currently within the caller's authority.
Age, package version, a familiar name, path resemblance and absence from the
active/previous route pair are insufficient disposal predicates.

The maintenance front door composes resource owners. It does not introduce a
second registrar, process reaper, installation driver, periodic scan or cleanup
ledger. Existing installation/cell identity, resource serialization, operation
custody and failure recovery remain with their current owners.

## Retain finite detector knowledge

Keep the supported historical detectors independently of current incremental
installation recognizers. Removing an optimization or changing today's writer
must not silently remove the ability to identify a previously supported
generated command, registration, daemon generation or disposable runtime layout.

A detector has a stable identity and revision, a component/resource-family owner,
an explicit platform and installation scope, and finite attributable producer
evidence. Retain the canonical historical bytes or declarative shape needed to
recognize that supported producer. Tests exercise those historical fixtures
without deriving expected bytes from today's writer. Retiring a detector is an
explicit support decision, not a side effect of a package-version bump.

The archive is data or maintained owner code, not an arbitrary command registry.
Never execute a program named by an observed artifact, import code from an old
runtime merely to identify it, or run an old installer/uninstaller as a detector.
An unsupported archive revision cannot become guessed recognition. Archive
integrity and matching evidence establish consistency, not installation custody
or permission to mutate a resource.

Receipt-free historical recognition is permitted only where an existing owner
already admits a finite canonical producer form. Do not convert a self-declared
managed header or matching task name into a new adoption mechanism.

## Report first, apply only selected current findings

Inspection is read-only and explicit. It resolves the selected components and
installation identities through their existing resolution paths; it does not
scan every drive, invent default roots, enable disabled plugins or provision a
missing runtime. One unavailable family does not hide independently safe
findings from another. Incomplete enumeration is not an authoritative empty set.

Each finding names its detector/revision, component and installation scope,
exact resource, observed evidence, current disposition, preservation reason
and owning remedy. Dispositions distinguish a recognized obsolete candidate
from current, foreign, edited, protected, uncertain and unsupported resources.
The report states family coverage and completeness; zero actionable findings
cannot imply that no historical residue exists outside that coverage.

Apply receives an explicit selection from a prior report and asks each existing
resource owner to revalidate that same resource at its mutation boundary. A
saved report, candidate identifier or content digest is not a grant. Changes
to current selection, installation identity, contents, native identity or
protection invalidate the proposed action. Refuse that action and report the
changed fact rather than silently substitute a newly discovered target.

Do not implement every resource family as a generic file deletion:

| Resource | Required owner boundary |
|---|---|
| Daemon generation | Native lifecycle/cutover owner verifies present custody, accepted-work/session protection and exact process birth, then uses its pinned native termination/exit proof. Recognition cannot authorize killing an ever-served generation with unknown publication custody. |
| Scheduled task or service | Registration owner re-reads the exact action, principal, installation binding and registration contents, checks that current desired state no longer needs it, and performs its own stop/unregister/verification sequence. Name-only presence is report-only. |
| Generated command | Command owner checks physical-file safety, exact admitted bytes, required sibling set and installation/source/cell binding immediately before its existing retirement transaction. Preserve edited or foreign commands. |
| Runtime slot or other residue | Runtime owner preserves current and rollback selections, live process/lease/cutover references, retained evidence, configuration and durable data. It revalidates the finite disposable target under its existing collection lock and retention rules. |
| Drop-in entry | Its consumer-owned doctor retains the registration/adoption and exact-entry rules in [drop-in registry hygiene](drop-in-registry-hygiene.md). A pointer cleanup never recursively deletes its target. |

An owner may support detection without safe apply. Report that limitation;
never route around it using a lower-level name-based task helper, a raw PID kill,
whole-install uninstall or recursive path sweep. Conditional recovery and
unsettled operations remain holds, not cleanup opportunities.

## Preserve truthful outcomes and version skew

Use the existing owner's idempotency and recovery semantics. A requested stop,
accepted signal, returned ACK or missing discovery record is not confirmed exit
or confirmed unregistration. Return the actual confirmed, preserved, refused,
failed or unknown outcome for each selected action. Retain the original resource
and operation identity for an unsettled effect; do not replay it as fresh work.

New maintenance syntax is optional and provider-first. Declare the capability
against the exact selected executable runtime, not a newer installed payload or
package-version equality. Old providers may lack historical cleanup without
losing their ordinary update/inspection operations. Do not probe by issuing a
destructive new invocation and retry an older form after an ambiguous error.
Follow [service contract evolution](service-contract-evolution.md).

## Initial native composition

The Manager front door selects component owners rather than loading an arbitrary
historical helper. Its own command/runtime families use the running Manager's
owner code. Worktrees resolves the installed engine through the existing
marker/installation receipt resolver, asks that exact executable for the optional
capability, and sends a selected daemon-family batch over its native CLI. It
never substitutes a payload shim that could self-provision. Index requires an
explicit current cell selection validated by its native installation-context
owner before loading the selected payload's maintained registration adapter.

Index's Windows registration owner closes Scheduler admission by reversibly
disabling an admitted task before deletion, verifies unchanged configuration
(apart from that enabled setting), native security and zero outstanding
instances, then confirms native absence. A queued/running instance preserves
the registration; a failed deletion restores its prior enabled state only while
the same configuration and security remain proven. Changed ownership or an
unconfirmed restoration is reported as failure, never successful cleanup.

The Worktrees control-v1 archive uses an authenticated previous endpoint with
native birth/runtime attribution, a complete immutable slot and a positively
ready current serving route. Unreferenced or unattributable census generations
remain visible and preserved. The cutover owner retains old code before closing
admission. Busy/unknown results preserve that identity and code hold; only the
same owner after positive original-process exit releases it. Ordinary updates
never wait for this optional cleanup or an older provider's maintenance syntax.

Manager runtime collection keeps partial-removal evidence outside the selected
slot so deleting its completion/inventory files cannot erase custody. This is a
narrow runtime-owner recovery record, not an aggregate cleanup registry or a
replacement for canonical construction/retention/publication locks. Remaining
entries and parent identities must still match; a journal alone grants nothing.

## Qualification

Use focused fail-first fixtures for finite historical forms and the real owner
adapter, not only a mocked cleanup success. Cover current and foreign resources,
edited contents, missing or contradictory ownership, incomplete enumeration,
unsupported archive/owner contracts and change between inspection and apply.
Exercise the actual CLI parser and selected-runtime capability direction,
including an older provider that truthfully lacks the optional operation.

Native qualification uses disposable resources through the existing contained
test runner. Prove exact target retirement and positive native exit or native
registration absence; preserve a current owner, unrelated resources and held
work alongside it. Distinguish platform support and unexercised lanes. Never use
an installed production cleanup as a substitute for disposable qualification or
infer live rollout from a source merge.
