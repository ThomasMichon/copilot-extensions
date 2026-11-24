# Proposed validation and delivery

Back to the [effort](README.md) and [contract](contract-v1.md).

**Prospective tests, not passing runtime evidence.** This documentation-only
proposal exercises no resource owner, launcher, reservation or live host.

## Caller and review matrix

| Existing owner | Proposed integration / required review |
|---|---|
| Dispatch finite execution | Guard actual spawn/start and retained reactivation using the original pool/task/reservation fences, without equating assignment with a running lane or taking over #9709/#13204. |
| Dispatch standing relationship | Keep declaration/reserve/accept semantics; require the execution budget at actual launch/resume. Existing in-place adoption needs actual native execution reconciliation, not a second unit or fake launch. |
| Worktrees direct launch/resume/recovery | Join the real original launch/workspace/creation/current-head result to Dispatch's resource boundary. Per-worktree exclusion remains independent. No required wrapper flag or unmanaged fallback. |
| Bridge embodiment/providers | Preserve actual native effect and original result custody through local/remote startup, resume, transport loss and provider rollback. No task/receipt-as-admission shortcut. |
| Host/venue policy owner | Supply attributable total/protected-class policy, required participating adapters, complete relevant cohort and explicit unsupported manual/external paths. No inferred slots from RAM/process/lane counts. |
| Topology consumer | Read exact preview/support and remain held on unknown policy/cohort or any independent gate; do not write capacity truth into the saved topology. |

Each code owner reviews its actual callable and effect seam before that caller
is declared covered. Review of this plan does not appoint an implementation
worker, transfer another issue's source custody or grant deployment.

## Focused acceptance cases

| Case | Required real observable result |
|---|---|
| Missing/null total, stale/contradictory policy, missing reserve field | Explicit held/unsupported result; no default-unlimited or implicit zero, no reservation/startup. |
| Exactly protected interactive plus review headroom remains | General/trial admission refuses; the same free unit cannot satisfy both reserves. |
| Concurrent candidates share one unprotected unit | At most one atomic admission; no duplicate external startup. |
| An existing native execution is outside the configured finite pool cohort | It is accounted for through its real owner or coverage is held, not treated as absent capacity. |
| Manual/external launcher has no enforcing participant boundary | Host-wide guarantee refuses even if a one-time process census is empty. |
| Existing conversation is adopted in place | Its native identity/units are reconciled once; no relaunch or duplicate budget entry. |
| Retained cold execution resumes | Original custody remains; fresh execution admission accounts pending/unknown resume effects and does not spend the same headroom twice. |
| Policy/cohort changes after preview | Actual mutation rereads/rechecks policy/cohort; the preview cannot start anything. |
| Admission response lost or receiver changes | Exact original observation resolves the result; no new key/host or unchecked fallback. |
| Same request repeats after admission | Return the original result marked replayed; no second external dispatch or new decision revision. |
| Held decision is legitimately reevaluated | CAS the original held decision to the next revision, retaining history; unresolved original effects prevent advancement. |
| Process launch succeeds but result publication is lost | Original launching obligation remains accountable until native owner reconciliation; no automatic expiry/release. |
| Retirement is requested, outcome uncertain | Units remain held; unrelated claims and workspace custody are untouched. |
| Resource success but charter/access/monitor/lifecycle gate fails | No execution effect; no reinterpretation of resource admission as authority. |
| Preview has no configured service/control setting | Read-only unsupported result; no ensure, credential ceremony, projection write or repair. |
| Old caller/new provider, new caller/old provider, rollback | Equivalent enforced baseline or explicit pre-effect refusal; no ignored mandatory semantic. |
| Frozen old writer can otherwise bypass the required host policy | Required-policy activation refuses until actual storage/provider fencing is proved. |

## Implementation sequence after reviewed proposal

1. **Provider contract/state:** a separately accepted source owner implements
   the proposed queue semantics, read-only view, existing control-transport
   boundary and canonical migration/old-writer fence. Add focused real SQLite
   concurrency, result-recovery and policy tests first; advertise no capability
   before the implementation satisfies it.
2. **Participating caller composition:** coordinate each actual Dispatch,
   Worktrees and Bridge effect boundary. Use real parser/transport/native fixture
   evidence and retain an unguarded-entrant negative control. Do not advertise
   host-wide readiness from provider-only tests.
3. **Mixed-version/source review:** current/prior callers, provider rollback,
   strict request parsing, no-effect unsupported paths and selected-generation
   discovery. Normal touched-file lint/type/install-contract/changefile/CI and
   independent source review apply to actual code changes.
4. **Release and separately authorized adoption:** pin actual released
   contracts and installed execution owners, then qualify a disposable
   participating cohort and original-outcome recovery through real native
   startup. State unavailable clean-room/native/external lanes honestly.
   Normal adopter policy, protected consumers and effect authority still gate
   every real installation/admission.

No phase silently follows from this proposal's merge. #14006 tracks remaining
implementation/qualification; no contributor is assigned by this document.
Numeric host policy and whether an adopter can establish complete participant
coverage remain explicit adoption inputs, not implementation defaults.

## Evidence for this documentation PR

- Check local Markdown links and referenced existing source symbols.
- Check whitespace and the exact changed file set.
- Obtain normal PR review and required CI, recording a real merge.
- Runtime units, clean-room, native concurrency and live external tests are not
  run for prose-only proposed interfaces. The cases above remain obligations
  of future code/caller delivery, not claims about today's runtime.
