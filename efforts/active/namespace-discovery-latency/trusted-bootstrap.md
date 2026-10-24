# Independent Windows evaluator bootstrap

This is a design sub-plan of [Namespace Discovery Latency](README.md) and
[Native startup headroom](startup-headroom.md), not another effort or an
implemented installation contract. The parent objective and completion gates
remain unchanged. The mechanism below is **agent-recommended**, following the
operator's explicit `design-reviewed-selector` choice.

## Problem and reconciliation

The canonical Python SDK can perform the same installation-context evaluation
in a fresh no-site process without PowerShell's native-helper startup cost.
It cannot choose an unadmitted target runtime to establish that runtime's own
authority. A working executable, ambient PATH match or successful `--version`
probe is not independent bootstrap admission.

The reached Containers and Codespaces gates currently evaluate through native
PowerShell before resolving runtime Python. The installer's candidate probes
execute candidates before evaluator trust is established. The existing
`Get-SignedBasePython` also executes `py` candidates before checking their
signatures. Those helpers remain valid for their existing installer contracts;
they are not reused as proof of this different pre-execution boundary.

This closes the existing Bridge vision's bounded, honest discovery delta.
It follows the installer vision's independent-plugin requirement and
`docs/patterns/README.md` principles 1, 3, 4, 5 and 9: no Manager prerequisite,
new service, checkout dependency, authority cache or redundant attestation
chain. Source/cell/governance validation stays in the existing canonical SDK.
The new selector answers only which independent executable may run that SDK.

## Proposed selection contract

### Read candidates without executing them

- Discover installed CPython registrations through Windows' existing
  [PEP 514 registry schema](https://peps.python.org/pep-0514/), limited initially
  to `PythonCore` in the current-user and machine registry views.
  Do not create a suite-owned interpreter registry or require registration of
  another plugin or Manager.
- Read registry values as data. Honor per-user shadowing of matching
  company/tag identities before selection. Tags are opaque; use declared
  version/architecture only as hints until file validation establishes
  compatibility. Apply a deterministic supported-version/architecture order
  and stable tie-breaking.
- Use the explicit executable path or PEP 514's documented `PythonCore`
  default beneath `InstallPath`. Reject relative/remote paths, arguments,
  launcher aliases, linked/redirection paths and malformed or ambiguous
  records. Never invoke `py`, `python`, a shell association, an app-execution
  alias or a candidate process to discover its executable.
- Require an independent base installation, not a virtual environment or a
  plugin's runtime. Reject a candidate with a venv marker, redirected prefix,
  embedded path override or a location inside the addressed runtime/cell.
  Do not use a target receipt's unvalidated interpreter field as authority.

### Admit the executable before its first execution

- Validate an ordinary local console CPython executable, supported Python 3
  version and compatible PE architecture through file data, not a child probe.
  The minimum must satisfy the canonical SDK's documented language floor.
- Require Windows to validate the executable's Authenticode signature and
  the expected CPython publisher identity. Neither registry labels nor a
  generic valid signature from a different publisher grants admission.
  Do not pin one expiring publisher certificate or trust an unsigned copied
  runtime because its filename is `python.exe`.
- Keep verification noninteractive and local-only. The Windows
  [WinVerifyTrust contract](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/ns-wintrust-wintrust_data)
  explicitly requires `WTD_CACHE_ONLY_URL_RETRIEVAL` to prevent network
  retrieval. Missing offline trust evidence does not authorize network
  acquisition, disabling an OS trust requirement or accepting the executable.
  The implementation must preserve the applicable Windows trust policy rather
  than turn an offline miss into a positive trust result.
- Keep the verified executable stable through launch using the established
  native file/ownership primitives where applicable. Replacement, changed
  identity or unavailable verification must not execute unchecked bytes.
  Bound this to actual stale/replaced-file risks; do not invent a second
  source/cell receipt protocol or a whole-machine attestation service.
- The trust boundary is the existing OS/user-managed base installation and
  verified executable, not a claim to defend against an equally privileged
  operator replacing that installation's entire standard library.
  Isolated/no-site startup must still prevent ambient Python variables,
  checkout modules, user site and startup customization from supplying SDK code.

### Return a finite, nonpersistent decision

The selector returns one of three typed outcomes, without writing state:

| Outcome | Consumer action |
|---------|-----------------|
| An independently admitted absolute executable | Start one fresh canonical SDK process with that executable. |
| No suitable independent bootstrap | Select the existing native PowerShell evaluator before SDK evaluation; retain a bounded diagnostic reason without polluting JSON stdout. |
| Selector execution, I/O or protocol error | Surface the actual bounded error; do not manufacture an unavailable/success result or silently try an unverified path. |

Candidate rejection is not an installation-context decision. No rejected
candidate is executed. Known absent/incompatible candidates may leave the
optional bootstrap unavailable; unexpected selector failures stay errors.
After a candidate is selected, interpreter launch failure, malformed SDK
output, installation refusal or stale/foreign ownership is terminal for that
invocation. Never fall through to another Python or PowerShell evaluator to
obtain success after evaluation has begun.

Re-read selection inputs and validate the executable on each invocation.
Do not persist a chosen path, cached signature/admission result or trusted
timestamp. Existing OS trust caches are not a new suite authority cache.

## Consumer integration and ownership

- The existing `rattling-circuit` discovery owner owns this design and the
  eventual bounded Containers/Codespaces consumer PR, not another contributor's
  installer or SDK algorithms.
- #6812 retains native startup/no-site and shared installer ownership.
  Coordination comments `6380217728` and `6380366583` ask for an existing
  selector or a concrete overlap. A missing response is not an assignment.
  Before any shared helper implementation, agree its canonical owner and
  bounded file scope with the existing owner; record that assignment.
- Prefer the existing installer-engine ownership for a standalone native
  selector. Its implementation must be payload-local and independently
  vendored for each adopter, with the ordinary synchronization, parity and
  changefile rules. Do not make runtime dispatch load an entire installer or
  require the installer application.
- Do not alter `Get-SignedBasePython`'s installer behavior incidentally.
  Reuse a qualified pure primitive if it actually meets this contract;
  otherwise add the narrowly assigned selector without a broad installer
  rewrite or changes to SDK receipt/policy/ownership algorithms.
- Consumer evaluation uses the owning payload's canonical SDK with the same
  explicit source, plugin, legacy/cell and expected-generation inputs.
  Use fresh `-I -S` startup only at the stdlib/payload SDK boundary.
  The selected plugin runtime still initializes its own package site normally.
- Preserve the native evaluator for machines with no suitable registered
  signed base Python, including genuinely Python-free bootstrap.
  POSIX dispatch is unchanged. Preserve both streams, exact arguments,
  pre/post-provision revalidation, process isolation and caller-owned cleanup.
- Plan review, bounded implementation assignment, implementation review,
  native qualification, containing release and authorized rollout are
  separate gates. This plan changes no installed state or live control pin.

## Plan and validation

- [ ] Review and merge this contract before implementation.
- [ ] Confirm the canonical selector owner and bounded assignment; refresh
  open PR files and preserve existing overlapping ownership.
- [ ] Prove registry discovery invokes no candidate, launcher, package manager,
  network or runtime-selection/provisioning path.
- [ ] Cover user/machine shadowing, registry views, opaque tags, missing optional
  metadata, deterministic order and supported native architecture selection.
- [ ] Cover relative/remote/linked paths, venvs and addressed runtimes, embedded
  overrides, missing/replaced files, unsigned/wrong-publisher executables,
  offline trust misses and explicit selector errors. Refused candidates must
  remain unexecuted.
- [ ] Implement the assigned payload-local selector with no state writes or
  persistent authority cache; enforce byte-identical adopter delivery.
- [ ] Qualify actual signed CPython and native trust verification on supported
  Windows PowerShell 5.1 and pwsh processes, including disconnected controls,
  non-ASCII/spaced paths, both streams and no visible windows/focus changes.
  Mocked signature success is not this proof.
- [ ] Integrate only the assigned consumers. Prove the fresh canonical SDK's
  complete typed output and refusal parity, absent-bootstrap native fallback,
  terminal post-selection failures and unchanged ordinary package-site loading.
- [ ] Keep effect-free units separately admitted with full source hashes,
  contextual coverage, effect observation and the existing 300-second budget.
  Native/registry/signature/process cases remain outside required pure units.
- [ ] Measure selection and evaluator together, including native helper
  compilation. Reject the optimization if its real cold cost removes the
  expected benefit; an isolated Python status microbenchmark is not acceptance.
- [ ] Rebuild final-source fixtures and repeat the parent's complete cold,
  warm, first-touch, failure and actual Bridge native-reader qualification at
  the unchanged eight-second bound. Retain every failure without averaging.
- [ ] Reconcile containing release/control adoption and authorized installed
  consumption with the original owners. Complete the parent only when its
  remaining plan/validation obligations are resolved or explicitly transferred.

This contract is not complete at plan merge or selector implementation.
The parent [startup-headroom matrix](startup-headroom.md#validation-plan)
still owns whole-consumer acceptance and deployment evidence.
