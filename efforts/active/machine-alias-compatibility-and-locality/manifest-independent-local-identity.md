# Manifest-Independent Local Identity - Phase 3d Amendment

Parent: [Machine Alias Compatibility and Locality](README.md).
Related contract: [physical/environment boundaries](physical-machine-environment-boundary.md).
Tracking: [#5674](https://github.com/ThomasMichon/copilot-extensions/issues/5674).

## Operator requirement

> control-plane repositories should not need to declare *any* machines in their maps in order to be able to register, load, and launch the harness on the current machine. The machine manifest is purely for enabling *additional customizations*, such as friendly-name mapping, per-machine service loadouts, ssh roles, dispatch assignments, etc.

This supersedes the gated producer's registry-required assumption and the
proposed remedy of requiring related projects to bind a control-plane map.
Ordinary local use is foundational; the optional manifest customizes it.
The standing intent is
[`local-first-machine-customization`](../../../visions/agent-fabric/README.md#local-first-machine-customization).

## Boundaries

No manifest, an empty manifest, or one containing only other machines must not
disable current-machine registration, configuration loading, local creation,
lookup, resume or Manager launch. This is not a silent legacy-format fallback:
tuple-first new records remain the selected intent.

Use the existing local execution and configured-identity authorities. An
optional matching map can supply a friendly physical name and execution key;
without such customization, local OS/substrate identity and actual environment
remain usable. Do not require copied maps, a new registry, manual enrollment of
every project, a declared SSH role or a running fleet service for local use.

Local execution authority does not extend to another requested machine, guest,
project, namespace or owner. A suffix, shared hostname, display name, stored
record or incumbent owner cannot make a foreign execution space local.
Keep explicit mapping conflicts and invalid declarations visible rather than
concealing them as absence. Preserve existing identifiers, relationship bytes,
allocation receipts and their receiving-side provenance.

## Implementation sequence

The realization below is **agent-recommended** and requires ordinary plan review
before implementation.

1. Extend the existing shared identity seam with current-machine context
   independent of optional topology. Keep physical identity, observed hostname,
   actual environment and one-term execution key distinct. Preserve literal
   physical names ending in platform-like text; do not trim them heuristically.
2. Adapt registration/configuration, new tuple derivation and receiving-side
   tuple/ownership checks to that same context. An unrelated explicit map must
   not require a declaration of the current machine. Retain genuine foreign,
   ambiguous and conflicting execution-space refusals.
3. Check Manager and actual creation/lookup/resume entry points against the same
   authority. Apply optional names, service loadouts, SSH roles and dispatch
   assignments only where declared; missing customization does not deny the
   underlying local capability. Do not take over those wider consumer campaigns.
4. Keep ordinary producer defaults legacy while this corrective source slice is
   reviewed, qualified and verified through installed opt-in boundaries.
   Review ordinary-default activation separately after those gates pass.

### Shared-context foundation

The additive `machine_transport.resolve_local_execution_context` API separates
current OS/configuration observations from target selection. Its immutable
result preserves observed hostname, physical name, actual environment,
one-term execution identity and optional customization key. Pure source cases
cover Windows, WSL and Linux with empty, unrelated and matching maps, literal
platform-like hostname suffixes, projected legacy names, explicit configured
selection, ambiguity and generated-key collisions. Target selection retains its
authority boundaries; mixed-registry legacy guest lookup and conflicting named
profiles have focused compatibility regressions.

Worktrees configuration now consumes this context through its existing local
detection adapter. Absent, empty and unrelated-only maps allow configuration
loading on Windows, WSL and Linux; matching customization, native legacy binding
strings, literal OS suffixes and explicit conflict refusals have direct loader
regressions. Update and launch consumers share that naming adapter while
retaining the independent runtime manifest policy.

This adoption does not yet change tuple producers, receiving-side writes or
Manager entry points. Registration/creation entry-point proof, the remainder of
steps 2-3 and the complete validation matrix below remain open. In particular,
observed naming facts are not receiving-project or allocation authority;
existing admission receipts and identifiers must not be regenerated from a later
optional customization.

## Validation Plan

- [ ] Cross Windows, WSL and Linux source cases with absent, empty, unrelated-only
  and matching customized manifests; ordinary local registration/load/launch
  works without adding entries.
- [ ] Drive actual new tuple creation, tracking, JSON, lookup and resume through
  the corrected boundary, retaining physical/environment/one-term agreement.
- [ ] Preserve custom names and execution keys without losing uncustomized local
  operation; retain explicit ambiguity, malformed declaration, platform,
  foreign-guest, receiving-project and namespace refusals.
- [ ] Replay admitted legacy and tuple allocations unchanged, including when
  optional customization is added later; no implicit relabel or owner transfer.
- [ ] Pass bounded decision/ownership regressions and existing guarded Source CI.
  Keep actual native, installer, Git and process evidence outside promotion.
- [ ] Qualify and release the corrective source, verify installed opt-in behavior
  on all registered native/guest projects, then review ordinary activation.
