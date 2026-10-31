# Fixed ARC Helm convergence owner

`fleet_manager.arc_owner.ArcHelmOwner` is an explicit internal owner adapter,
not a new CLI command, resident controller or installer.
It runs a real fixed Helm 3 scale-set convergence operation. Official ARC
continues to own its listener, ephemeral runner sets and pods; Komodo must not
supervise those pods as competing container resources.

This is partial implementation under #7192/#6030/#6236. It does **not** complete
the [runner-loadout plan](../../efforts/active/linux-role-fleet/runner-loadouts.md).
Drain, receipt-owned removal, authenticated readiness, automatic recovery,
production controller binding and an authorized Actions job remain open.
The normal preparation SDK still reports execution admission unimplemented:
it does not manufacture a deployment policy or invoke this adapter.

## In-process Gateway binding

The adopted SDK embedding can register
`owner.gateway_binding(exact_slot, role=..., installation=...)` through the
existing service Gateway's explicit `local_handlers` input. This exposes only
`POST <configured-prefix>/converge`, without queries, using canonical
`runner.converge` intents and the existing references-only operation shape.
It adds no standalone controller, issuer, module discovery, raw Helm/manifest
endpoint or default CLI behavior.

The Gateway verifies the original caller over TLS and supplies its retained
identity directly to this owner's separate current operation policy. The
selected role/installation are trusted registration inputs, not request fields.
The expected operation authority comes from the owner's journal, not the
Gateway's independent routing name.
Default `jwt-pop-v1` and explicitly selected `enrolled-pop-v1` callers remain
distinct admission profiles. Missing/revoked/different account, fleet, target,
generation or full deployment digest refuses before native work. Review
confirmation alone never creates an operation grant.

The handler joins the existing finite, headless native owner off the event
loop. Gateway and request withdrawal remain checked at every operation-permit
checkpoint. A lost HTTP response cannot replay Helm: original-operation
recovery consults the same durable journal and preserves UNKNOWN. The accepted
effect's native settlement remains its owner's responsibility.

This is an executable SDK binding, **not live adoption**. The fixed profile
still refuses unowned incumbent releases; do not upgrade or retag one merely
to acquire a marker. Published/selected artifacts, current support/credential
owners, durable provider protection, an incumbent-preserving adoption transition
and actual deployed controller reconciliation remain required.

## Existing-owner inputs

The existing adopted owner constructs the adapter with its `PreparationStore`,
canonical `NodeJournal`, `AdmissionAuthority`, current selected deployment
callback, integer clock and `ArcTooling`. No input is discovered from a control
request, ambient active kube context, marketplace hook or user machine.

`ArcTooling` supplies absolute Helm executable, existing kubeconfig and private
working-directory paths, an explicit context, and the existing
`agent_procutil.owned_process.run_caller_owned` process adapter. That facility
owns headless process-tree execution, bounded streams, finite lifetime and
settlement notification. This API installs no tool, namespace, chart dependency,
CRD, controller, credential, relay, node or competing process supervisor.
Missing prerequisites refuse.

The trusted `current()` callback supplies an immutable `ArcDeployment` on every
selection/revalidation. Its producer must independently authenticate the current
selected release, official `gha-runner-scale-set` chart bytes, configuration and
credential references, support readiness receipts, canonical cluster identity
and exclusive physical writer custody. It must also enforce current trust,
egress and toolchain policy. Record construction and a matching SHA-256 are
not proofs of any of these things. There is no permissive default callback,
credential resolver, trust issuer or artifact download.

The deployment record binds:

- Fleet, installation, named target/allocation, preparation generation/digest,
  exact expanded allocation digest, selected release digest, policy revision,
  validity and a finite 1-1800-second outfitting duration.
- Every runner configuration/artifact/credential/forge/group/trust/toolchain/
  cluster reference, the selected chart SHA-256 and immutable bytes.
- The exact transitive support-allocation closure with named targets and
  existing-owner receipt digests. Unknown or missing support is not readiness.
- Existing separate runner/controller namespaces, release, controller and
  restricted runner service accounts, named node, non-secret HTTPS forge scope/group, existing
  same-namespace credential-secret name and digest-pinned runner image.

No generic Compose, manifest, values, environment, mount, plugin, shell,
command, post-renderer or inline secret is accepted. Selected values are a
narrow fixed profile: named-node/architecture placement, ephemeral capacity,
per-runner CPU/memory/ephemeral-storage requests and limits, selected controller
and restricted runner service accounts, no pod token automount or privilege escalation, and dropped
Linux capabilities. This profile does not provide privileged Docker-in-Docker,
Kubernetes job-container mode, custom mounts or extra containers. A workload
needing those features requires its own reviewed supported profile, not raw
overrides or weaker isolation.

## Exact operation and effect boundary

The caller submits a canonical `runner.converge` intent containing only
`preparation`, `allocation` and `deployment` references, with its existing actor,
target and generation scope. `deployment` is the complete selected deployment
record's digest, including actual resolved settings, artifacts, support receipts
and duration. The real `AdmissionAuthority` must have issued a permit for this
exact intent. Runner `OperationGrant` requires that full request digest:
action/target/generation-only authority is deliberately insufficient.

The adapter checks the current reviewed proposal, freshness, exact built-in
`actions.arc` declaration, selected references and transitive support targets.
It revalidates current selection and runtime policy again at the effect boundary.
The SDK review remains necessary but insufficient: it neither supplies nor
widens that deployment grant. The trusted current-policy adapter, not this
library, decides whether the explicit full deployment was approved.

The existing journal atomically reserves a canonical cluster/namespace/release
resource. Another accepted or UNKNOWN operation cannot take its custody,
including after restart or under a newer generation. A terminal predecessor
permits only a strictly newer generation. This is local cooperative exclusion,
not a distributed backend fence. The adoption owner must independently ensure
that another installation/manager cannot write the same physical Helm release;
using another database or another alias is not safe takeover.

Discovery uses exact `helm list`, `status --show-resources` and `get values` queries. A release
without this installation/allocation's ownership marker refuses before a write.
Convergence invokes only `helm upgrade --install` on retained chart bytes,
with `--reset-values`, `--no-hooks`, `--skip-crds`, `--atomic`, `--wait`,
an explicit namespace/context/kubeconfig and finite timeout. Values travel on
stdin and contain only a secret **name**, never its content. No takeover,
namespace creation, remote chart reference, dependency update or force flag
exists. Plugin/ambient Helm and Kubernetes environment selectors are removed.

Native Helm JSON has its own bounded reader: 1 MiB per response, 64 nesting
levels, 4096 entries per collection and 65536 total value nodes, with duplicate
keys and nonfinite numbers refused. Kubernetes `managedFields` metadata can be
deeper than a planner document; it is not silently discarded or constrained by
the planner's unrelated depth limit. These transport bounds do not relax the
exact owned ARC spec comparison. The selected forge URL is normalized by removing
trailing slashes before generating values, matching the official chart's scope
representation.

The fixed profile also refuses numeric-leading or YAML-reserved chart names
(including boolean/null spellings) for namespaces, releases, service accounts,
credential-secret names and runner groups. The official chart emits those
fields as plain YAML scalars; accepting an ambiguous string could change its
type only after durable effect custody had begun. This is a supported-profile
restriction, not a change to Kubernetes or GitHub's general naming rules.

The ownership marker binds the selected chart and generated values, rather than
the incidental proposal generation. A repeated original operation returns its
retained receipt without any native call. A newly admitted generation always
reapplies through Helm, even if stored release values match: Helm history is
not live-resource evidence. Helm 3 uses a two-way merge for unstructured custom
resources, so reapplication alone may leave manually patched fields unchanged.
Post-operation `status --show-resources` retrieves live objects from the cluster.
Success additionally requires exactly one matching ARC scale set, its physical
UID/resource version, admitted forge/group/secret/capacity fields and exact
fixed-profile runner pod spec. Missing, extra, changed or unavailable owned-spec
evidence, or a terminating resource, leaves UNKNOWN even when Helm reports deployed.
The adapter does not
force-replace resources or silently take broader repair permission.
This API does not yet provide a live-spec-proven
no-write optimization. Admission also checks the permit's fleet against the
selected fleet before journal custody or native work.

UNKNOWN is persisted before the external attempt and the permit is consumed at
dispatch. Timeout, cancellation, lost reply, native refusal or absent matching
terminal evidence leaves responsibility uncertain; no automatic replay or
assumption of rollback follows. Confirmed process-tree cleanup does not prove
a Kubernetes request was undone. An unsettled native cleanup exception is
retained as `native_failure`; further native operations refuse until its existing
owner settles it. The parent outfitting budget includes inspection and Helm's
own possible rollback; an outer timeout remains UNKNOWN, not failed recovery.
Every exit discards any remaining local permit without refunding proof evidence,
so a pre-effect source/native refusal cannot occupy an authorization slot until
its timer expires. This cleanup does not cancel an accepted journal responsibility
or turn UNKNOWN into permission to replay.

Success requires actual post-operation Helm status, values and live owned ARC
spec matching the selected desired release. Its canonical `TargetReceipt` means observed Helm
convergence, **not** listener health, forge authentication, actual capacity,
support readiness, job use or whole-loadout completion. Historical successful
receipts are not a current health probe.

## Qualification

`test_arc_owner.py` uses the real identity verifier/admission authority,
preparation store and node journal with one synthetic Helm protocol. Pure input,
scope and drift matrices invoke that protocol in-process. Selected convergence,
exact replay and lost-reply controls run it through actual contained native
processes; contention retains two separate installed journal processes.
The native controls explicitly assert that their real-process mode is selected.
This avoids multiplying incidental interpreter startup for pure field matrices
without pooling or removing the process-lifetime and concurrent-writer proofs.
It covers exact grant binding,
revocation, fleet isolation, no takeover, exact repeat, stored/live-resource drift, lost replies,
restart and UNKNOWN custody. This is process-boundary/component evidence,
not a real Helm deployment or live-cluster proof. Its separate live-resource
fixture models Helm's unchanged custom-resource merge: unchanged stored values
do not automatically overwrite live field drift; that drift must refuse
successful reconciliation.

The opt-in `test_arc_offline_helm.py` invokes real Helm against an explicitly
selected, SHA-256-checked official chart, using generated typed owner values:

```powershell
$env:FLEET_ARC_TEST_HELM = '<absolute Helm 3 executable>'
$env:FLEET_ARC_TEST_CHART = '<selected official gha-runner-scale-set archive>'
$env:FLEET_ARC_TEST_CHART_SHA256 = '<expected archive SHA-256>'
```

Run that file through the repository's existing contained test runner/environment,
alongside `test_arc_owner.py`. Rendering uses a fixed Kubernetes version and
explicit controller service-account selection; it needs no kubeconfig, cluster,
credential or network. It checks actual ARC resources, capacity, placement,
resource limits, token/isolation settings and absence of Secret/namespace/CRD
creation. Without the explicit variables it skips, rather than downloads tools
or claims the real adapter passed. Native Windows/Linux, installed-distribution
and disposable real-cluster lifecycle lanes remain separately attributable;
offline rendering does not satisfy those deployment gates.
