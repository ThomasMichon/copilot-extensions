# Configured execution identity

`agent-worktrees get execution-identity` is a read-only, versioned configuration
contract for optional consumers that must distinguish execution authority from
ordinary machine naming. CWD/`--project` targeting and an explicit `--session-id`
follow the existing `get` targeting rules.

```json
{
  "schema": "agent-worktrees.execution-identity",
  "version": 1,
  "status": "ready",
  "configured": true,
  "source": "project-machine-config",
  "project": "sample",
  "machine": "sample-native",
  "platform": "windows"
}
```

The existing configuration loader supplies provenance from its actual precedence:
the machine-local project configuration (including its drop-ins), then global
machine configuration, then detection. Provenance is captured in memory during
that load, not read from another YAML field or persisted as a second setting.
A detected hostname never supplies this contract's authority, even when its text
matches a canonical registry key.

`ready` requires explicit configured-machine provenance and the actual platform.
When the source registry is scoped, it shares the existing registered-current-
space validation used by ownership mutations. An unscoped or absent source
registry can still report the explicit configured identity; consumers must
qualify that key against their own execution-space topology and actual platform.
This read does not change mutation policy or migrate any ownership record.
Repository allocation and record mutation still require their own effect-side
checks; a positive identity response is not blanket mutation authorization.

`unavailable` is successful capability discovery without a configured binding:
the project is unregistered or no machine is configured.
It carries a reason and no machine key. `refused` is a compatible rejection of an
invalid configured binding, including ambiguous keys or platform mismatch, and
exits nonzero.
An explicitly present empty, null, nonstring or whitespace-only machine value
is invalid, not an absent binding. This stricter readonly certification does
not change the ordinary configuration loader's existing fallback behavior.

Consumers must recognize the schema/version and positive configured provenance.
An unknown command/schema/version is not authority. The older `get machine`
scalar remains unchanged and is not a substitute for this stronger contract.
No sibling is required to install or operate Worktrees.

## Local project registration and installation

`register` and `install` validate local naming from the actual OS hostname and
execution environment before writing project or runtime state. A missing,
empty or unrelated machine map does not require enrollment. A matching
customization supplies its own instruction metadata; a native/guest sibling's
metadata does not become the current execution entry.

The existing `--machine` option remains an explicit installation binding, with
its original spelling retained in newly scaffolded global configuration.
Detected naming is not scaffolded as an explicit global binding, so later
loading retains its observation provenance, including customized physical names
ending in platform-like text. Admission validates the binding that will actually
survive project/global/drop-in precedence and the project-only `--force` scope.
Neither path changes existing user-owned global configuration. In a scoped
installation, retained project configuration is read only through the existing
repository identity and receipt, including a different label for the same remote;
fresh adoption needs no premature registry or receipt publication.
Retained machine-local maps use that same proven prospective root, even before
the new project label is registered. Checkout-local maps keep their precedence.
Machine-map fallback uses the prospective target configuration and its retained
knowledge binding, not the ambient project's map. Force drops only settings
that the project scaffold will actually replace; retained global and drop-in
knowledge bindings still participate. The selected source uses the ordinary
canonical-plus-legacy map merge, including for a knowledge checkout.
Admission checks both explicit-project loading and ordinary loading when a
retained `repo_name` selects a different repository or drop-in scope.
Configured platform precedence selects repository paths; actual observed
execution still supplies local naming admission.
Absent prospective maps do not fall back to an older remote's registered scope.
Conflicting platform/profile bindings,
ambiguous associations, execution-name collisions and invalid declarations
produce a nonzero diagnostic before state preparation or launcher publication.
This naming admission does not replace repository hosting policy, installation
ownership, receiving-project or actual-record mutation checks.

## Versioned tracking identity

Tracking readers also recognize an explicitly versioned physical/environment
tuple. This is record metadata, not the configured-authority response above:

```yaml
machine_identity_version: 1
machine: sample-host
environment: wsl
execution_machine: custom-guest-space
platform: wsl
```

`machine` names the physical host. `execution_machine` supplies the one-term
owner in qualified worktree, session, pair, controller and resource references.
The same fields are retained in exact-record, list, lineage and resident-row
JSON. `platform` must agree with `environment`; missing version metadata keeps
the original legacy machine/platform interpretation. Unsupported, partial or
conflicting explicit tuples are errors, never fallback execution authority.

The typed tracking accessor preserves the exact historical machine label for
legacy records. Ordinary saves do not migrate their format or alter a versioned
identity. Concurrent changes to environment, execution owner or identity version
invalidate a loaded writer even when its physical host is unchanged. Mutation
still requires the receiving project's current execution context and actual
platform; sharing a physical host does not make native and guest owners equal.
The optional receiving registry customizes that context rather than enrolling
the local machine. The complete physical/environment/execution tuple must match
the validated context, and explicit registered execution keys retain their
canonical-key and platform checks. Project and actual-ledger snapshot checks
remain independent of naming; a detected name is not a receiving-record grant.
Cleanup, including forced removal, applies that tuple check before any missing,
empty or unscoped registry fallback retained for historical records. Resolved
controller navigation and strict resource-owner scans use the execution owner;
malformed explicit tuples make the scan incomplete rather than borrowing the
physical host or disappearing behind an owner filter.
Publication privacy guards check both physical and execution names. Terminal
pair receipts preserve the exact legacy identity shape; versioned receipts
retain the full tuple while reciprocal references use the execution owner.
The `run` JSON recognizer also uses that owner; invalid explicit tuple output
is reported and leaves the pending resource claim unsettled.

### Ordinary tuple producers and legacy-default source APIs

Ordinary Create (session and system), JSON New (session and bridge), interactive
New, embody New and structured request admission explicitly select the internal
`identity_mode="tuple-v1"` mode. Interactive prospective/preflight and dry-run
names use the same physical/environment derivation as actual New. Existing
lookup, Resume and completed receipt replay retain their original records.

`_create_worktree_core` still defaults to `legacy` when the internal mode is
omitted. Tuple mode derives the physical/environment/owner tuple
from existing receiving configuration, the OS hostname and actual platform,
with optional registry customization, then revalidates it before allocation.
Absent, empty or unrelated-only maps do not prevent local tuple creation.
Conflicting local execution evidence still refuses.
Callers cannot supply arbitrary tuple metadata through this mode.
The JSON resolve New/short-ID lookup/Resume path likewise accepts the actual
observed hostname and derived local execution ID without map enrollment.
Literal hostname suffixes are observations, not requested environments.
Registered keys, transport aliases and profiles retain target precedence,
including foreign-platform conflicts; an explicit different environment still
requires its own transport. Local selection does not bypass record checks:
managed tuple records must also match the receiving project's absolute worktree
namespace before lookup or resume can prepare a launch. Adopted external Git
worktrees retain their independent registration and existing checkout paths;
they are not forced into the managed allocation directory.
Derived names retain their observation provenance: a physical customization
ending in `-win` or `-wsl` is not reparsed as an explicit environment choice.
Parent-obligation settlement uses the same validated execution owner, including
when the map customizes only the native or guest sibling.
The reciprocal creation claim uses the admitted tuple owner as well; native
configuration may retain its raw hostname without changing the child's qualified
reference. Legacy creation and settlement preserve their original owner labels.
Implicit CWD ownership and the owner reference exported into launch environments
use the retained record's execution owner, not the detected machine scalar.
An absent ledger uses validated local execution context; an existing legacy
ledger is never relabeled to admit a tuple child. Explicit/env foreign owners
retain precedence and their normal receiving-record fences.
After locality is established, coordination readiness is loaded from the
qualified owner's project, even when its tuple execution owner differs from
the caller's detected scalar. A ready caller project cannot substitute for an
owner requiring an unbound knowledge repository.
Creation also checks the child's execution owner against the independently
loaded parent project's context under the parent record lock. A prospective
claim that the receiving project cannot later settle refuses before writing.
Session-registration authority preserves explicit machine bindings from project,
global and drop-in configuration, so an explicit key can disambiguate entries
sharing the observed hostname; observation alone still cannot select among them.
The authority-only registration loader uses the same naming adapter as ordinary
configuration. Legacy native records retaining the literal observed hostname
remain usable when only the sibling environment is customized. This is a
record-scoped compatibility proof: actual platform, receiving project,
checkout namespace and the existing ledger still have to agree. It does not
make a foreign execution key or hostname-only target globally local.
The record-proven legacy-parent shortcut applies only to legacy children.
Tuple children must satisfy their lifecycle's execution-owner fence before
journaling or carving; an unsupported mixed parent spelling leaves no new
obligation. Historical legacy platform/path overrides retain their existing
actual-record compatibility. Tuple admission and explicit non-legacy execution
keys still require the configured platform to match actual execution.
A legacy fallback also compares the record's platform to actual execution;
matching a configured path override alone cannot admit a foreign-platform
record. Only the earlier actual-record compatibility proof preserves a valid
legacy override.

New session and bridge names use the selected physical machine and environment;
a physical name ending in a platform-like suffix is not truncated. Names
that cannot form a portable path and Git-reference component refuse before
allocation reservation or carving, including path separators, drive qualifiers,
traversal and invalid characters. The tuple codec also rejects NUL/control-line
metadata. Identity metadata is not permission to create a nested ID. System IDs
keep their existing `sys-...` form. Tracking, JSON and paired worktree records
share the typed tuple; qualified companion references use its execution owner,
including anchor companions. Completed creation-key replay retains its stored
representation even if the caller selects the other supported mode. The
authoritative creation path validates the retained record against the receiving
execution space before returning it; a sibling environment cannot reuse the key
to obtain an allocation owned by the other environment.

`launch_request.execute` exposes the same internal legacy-default opt-in.
Its existing locked admission writer derives and retains a complete allocation:
the exact ID, branch, checkout, project namespace and original owner
representation. The core consumes that value rather than reformatting a
timestamp/suffix using current defaults, and revalidates it under the allocation
lock. New launch receipts also retain the record's creation nonce and original
owner/pair evidence. Retry and status refuse a replaced, retired, missing or
conflicting allocation; an already-started creation never automatically carves
again. Writer protocol version 6 prevents older handlers from consuming this
new evidence. Receipt owner metadata is either fully versioned or wholly
legacy: partial tuple fields refuse rather than invoking a legacy fallback.
Old receipt formats remain readable and are not backfilled.

New creation-key receipts retain the same original owner/pair evidence alongside
their existing nonce. Completed replay preserves the original result and refuses
changed relationships; legacy receipts retain their prior nonce and identity
checks without being rewritten. Explicit creation-tail repair uses the retained
tuple and exact pair stub, not a fresh allocation. A proven compatible legacy
registry alias keeps its original record and reciprocal-reference spelling.
Replacement of the retained nonce or owner during repair refuses.
Completed creation-key replay has a separate read-only compatibility check.
New keyed receipts retain the originating OS hostname, actual environment and
configured binding alongside the original record evidence. Later optional
customization can change prospective naming without changing that completed
allocation's returned row, record bytes, nonce, relationships or journal.
The completed journal must still prove the original intent, checkout and exact
record; the receiving project/root, actual environment and original configured
binding must agree. A foreign host, sibling environment, changed binding,
redirected ledger or changed owner/pair evidence refuses without carving.
Supported legacy platform/path overrides remain compatible with replay of the
proven actual-platform record. Tuple records and explicit non-legacy execution
keys still require the configured platform to agree with actual execution.
Older receipts are not backfilled: a literal original OS-derived identity can
be reconstructed, but a former customized identity with no retained observation
must still satisfy ordinary ownership checks or refuse. Refusal never permits
a replacement allocation. Ordinary record mutation, arbitrary allocation
objects, launch admissions and interrupted creation-tail repair do not gain
authority from this read-only completed-key path; each uses its own
independently validated operation boundary.

Explicit `doctor --repair-creation` validates the actual registered tracking
record, original allocation and nonce, Git repository/branch, stopped session
and unclaimed retained New intent before applying naming compatibility.
An original literal OS-derived legacy owner or tuple remains repairable after
optional naming changes without relabeling it. A former custom identity that
cannot be reconstructed must still pass ordinary ownership checks.
The receiving project, actual environment and any qualified parent retain
independent record/namespace authority. A knowledge companion additionally
requires its own current receiving-project anchor, worktree root, platform and
execution binding; the parent alone cannot authorize it. Equivalent resolved
checkout/parent paths retain their stored spelling; relative paths, symlinks
and escapes from the receiving root do not qualify. Missing companions
use the original pair allocation, with authority rechecked inside its allocation
lock. Existing companions keep their original identity and owner/pair references.
Every repeated repair check binds the complete retained seed, including text and
revision, not just its ID. Conflict refuses without replacing the allocation or
consuming/restoring a prompt. This bounded repair does not change generic
record mutation, fresh tuple admission or ordinary producer defaults.

`load_or_create_anchor_record` and `_adopt_linked_worktree` expose the same
internal opt-in for genuinely new records. The existing if-absent record lock
selects the winner before deriving a tuple. Derivation checks the receiving
project, tracking namespace and execution configuration; anchor admission also
checks the configured anchor, and external adoption keeps Git's actual anchor,
linked-checkout path, ID and branch as authority. It does not rename an externally
created checkout to match prospective naming. Opt-in derivation errors propagate
instead of falling back to a legacy write. Existing records, including a
competing creation winner, return with their original identity and bytes and
without requesting a new tuple from the registry. Newly created records retain
their supplied tracking destination for subsequent path-implicit saves,
including legacy anchor callers using a nondefault tracking directory.
Ordinary CWD inference and session registration select tuple mode only for
previously untracked linked Git worktrees. Ordinary anchor admission loads the
actual receiving project's configuration and checks its registered anchor and
qualified execution owner. It passes that configuration into the existing
if-absent factory, rather than reloading ambient configuration. Receiving
conflicts remain explicit errors, not a successful claim with no ledger.

The timestamp/suffix-only `allocation` input still cannot admit a new tuple;
the complete admitted value is required. These source APIs are not CLI flags,
settings or rollout switches. All four low-level APIs retain their omitted-mode
`legacy` defaults; the ordinary callers select tuple mode explicitly. Source
activation does not authorize an installed rollout. No historical ID, path,
stored reference or session is rewritten.

### Source addressing equivalence

The JSON `resolve` entry point uses the same declared selection for `--new`,
read-only `--worktree-id ... --dry-run` lookup and ordinary resume. A declared
`<machine>-wsl` shorthand, physical machine plus `--environment wsl`, exact
custom execution key and configured alias select the same local guest where
the registry permits them. The selector does not change `Config.machine`:
tuple creation still derives from the registered current execution owner,
and resume independently checks the retained record's authority.

Exact registered keys retain precedence over an inferred legacy spelling,
including when the key names a foreign target that must produce a remote plan.
Ambiguous declared aliases, invalid topology collisions and conflicting
environments refuse before local allocation or resume writes. A physical-host
match never makes a sibling environment's retained record mutable.
Linux has no inferred legacy `-linux` address: such a spelling must itself be
declared as a key or compatibility alias. A generated worktree ID ending in
`-linux-...` is not execution authority.

`test_tuple_resolve_equivalence.py` drives ordinary selection and retains
explicit legacy/tuple controls for historical records. It checks
tracking YAML, JSON, short-ID lookup, read-only preview, resume mutation,
retained nonce/identity and remote plans across Windows/WSL/Linux metadata
fixtures. The `test_tuple_ordinary_*` packets additionally drive ordinary
creation/admission, receiving-anchor configuration and linked Git adoption
without test-only mode injection, with retained-key/receipt and legacy-default
controls. These contained source tests do not claim installed or native-platform
acceptance.
