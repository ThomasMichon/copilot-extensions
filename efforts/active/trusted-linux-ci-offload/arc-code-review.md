# Official ARC Code Review Capacity - Approved Slice

Parent contract: [README.md](README.md), Phase 1a. This records the approved
design and staged activation gates, not a declaration of ARC runner readiness.

The first implementation slice stages the dedicated setup on literal
`ubuntu-latest`. It must remain hosted-safe even if independently promoted
before ARC capacity is ready. The final static `copilot-extensions-review`
selector requires a separate explicit READY decision; no dynamic readiness
expression or runner-label mutation substitutes for that gate.

Hosted staging itself needs no new custom-route exception: the existing guard
already accepts literal `ubuntu-latest`. Once the same hosted definition lands
through dev, an owner-authored workflow-only main seed can publish it under
existing admission without transferring helpers or activating ARC. An
independent promotion also remains safe because dev carries the hosted file.
The four-file compatibility and preservation sequence below is still required
before the later custom-selector activation, not before this hosted-only stage.

## Intent and supported product

Prioritize GitHub's official Copilot Code Review runner integration. This moves
its Actions-backed agentic work onto explicitly isolated capacity; it does not
self-host the model service, replace the product with a CLI reviewer, or
guarantee removal of independent Copilot service throttling.

[GitHub's runner configuration reference](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-runners)
requires ARC-managed Ubuntu x64 Linux capacity. An existing persistent runner
cannot be relabeled into compliance. Use the dedicated
`.github/workflows/copilot-code-review.yml`, whose
`jobs.copilot-setup-steps.runs-on` names the ARC scale set. The proposed public
name is `copilot-extensions-review`; no different label is required by the
planned guard exception.

Keep `copilot-setup-steps.yml` absent: using the dedicated review file avoids
changing the coding agent's fallback environment. At assessment time neither
file existed in the contribution or default branch.

## Distinct admission and isolation boundary

Operator-approved direction: one ephemeral review slot in an isolated Ubuntu
x64 ARC environment, with no open LAN access and deny-by-default outbound
networking. Allow only the documented GitHub Actions hosts and the additional
review hosts `api.githubcopilot.com`, `uploads.github.com` and
`user-images.githubusercontent.com`. The capacity operator owns the complete
resource budget, controller, ephemeral worker lifecycle and network enforcement.
No host provisioning belongs in this public repository.

Broader fork/external review workloads are accepted only inside that isolated
review envelope. This is an explicit review-only change, not a claim that all
review authors satisfy the validator lane's durable-collaborator checks, and
not permission to run fork CI on persistent trusted validator capacity.
GitHub's managed firewall is not a substitute for operator-owned self-hosted
network controls.

Preserve the existing automatic review requester: it checks invited collaborator
membership and the PR author's presence in base-branch CODEOWNERS, then requests
through the licensed Maintainer identity. That code does not impose a
same-repository or event-sender-write requirement. Native/manual review requests
can bypass that automatic-requester path. The approved isolation boundary must
therefore protect the worker independently of who requested the review.
Do not expand automatic review eligibility or change licensing credentials as
part of routing.

## Minimal public source design

The following decomposition is **agent-recommended** implementation, not
permission to publish implementing code before the integration owner's gate.

| Surface | Proposed change |
|---------|-----------------|
| `.github/workflows/copilot-code-review.yml` | Official dedicated setup workflow; exact `copilot-setup-steps` job; literal `copilot-extensions-review` scale-set route; minimum documented permissions and bounded setup |
| `tools/check-trusted-ci.py` | One exact review-workflow/job/custom-label exception; retain all existing trusted validator authorization and denial of other routes |
| `tools/test_check_trusted_ci.py` | Positive official setup fixture; wrong filename, job, label, dynamic route, extra self-hosted job and secret/permission regressions |
| `.github/workflows/base-branch-reminder.yml` | Add only the dedicated review workflow to the trusted control-declaration allowlist, with corresponding admission tests |
| `.github/promotion-control.json` | Preserve the dedicated review workflow across promotion, after its exact-path admission support is active |
| `tools/test_promotion_control.py` | Prove exact-path preservation and unchanged existing release-control declarations; the generic preservation implementation needs no new path-specific capability |
| `tools/test_release_publication_boundary.py`, `tools/test_post_ci_policy.py` | Exercise the actual main-admission code, exact reviewed-source compatibility transaction, and rejection of mixed/unrelated source changes |
| `docs/pipelines.md`, `TESTING.md` | Describe official review placement, distinct trust envelope, operator prerequisites, guard coverage, activation and rollback |

Do not add ordinary PR/push triggers merely to execute the setup on every
update. Match GitHub's supported setup-workflow invocation contract. Use only
minimal setup steps; do not preload repository tests, invoke unofficial review
actions or pass repository release credentials into review code.

The guard must keep review and validator routes distinct. Neither an ARC-like
label prefix nor an arbitrary workflow input may authorize private execution.
The exact setup exception does not relax `trusted-ci.yml` or allow other
workflows to select `copilot-extensions-review`.

## Default-branch activation and narrow compatibility transaction

The baseline default-branch snapshot
`f1837c45e71ead77fe21f01227931f0d85d6786e` carries:

- A workflow-only, owner-authored main bootstrap exception.
- `check-trusted-ci.py` rejecting every custom runner route except the existing
  trusted bridge job.
- A preservation manifest naming only the base reminder, CI and promoter.
  The trusted main-admission code allows only those same three workflow paths
  in a control transaction.

Consequently, adding the new setup YAML alone would leave main's guard
incompatible, and adding it to the manifest alone would fail trusted admission.
A dev merge does not activate either repair while promotion is paused.

The operator explicitly authorized a minimum compatible control-plane bootstrap
after its generic source changes land through dev. This is not permission to
author unrelated source directly on main, promote an unqualified dev snapshot,
or resume the release pipeline. Coordinate this slice with the existing #6237
owner; #7122 owns review-capacity integration, not the release campaign.

The planned compatibility-source set is exactly:

- `tools/check-trusted-ci.py`
- `tools/test_check_trusted_ci.py`
- `tools/test_release_publication_boundary.py`
- `tools/test_post_ci_policy.py`

The last two files carry the actual admission-policy regressions. Keep the
generic `tools/promotion_control.py` implementation unchanged. Additional
preservation fixtures in `tools/test_promotion_control.py` may land through dev,
but are not automatically included in the main compatibility transfer. If local
qualification demonstrates another necessary dependency, explain that concrete
need and obtain scoped approval before expanding this exact set.

Required sequence:

1. Clear the proposal and integration-owner intent/publication gates.
2. Land and locally validate the narrow dev-owned guard/tests/docs and
   control-admission support through their authorized PR flow.
3. Pin the actually merged, locally validated dev source commit. Through an
   owner-authored workflow-only main PR, activate a narrowly reviewed admission
   recipe in `base-branch-reminder.yml` for only the four compatibility paths
   above and only their approved immutable source blobs. The existing trusted
   workflow-only exception admits this enabling policy, not helper source in
   the same PR. Tests must exercise the prepared main workflow itself.
4. In a separate main compatibility PR, transfer only those exact reviewed dev
   blobs. The already-active trusted main admission code checks the complete
   diff, exact source identity, regular-file modes and content equivalence
   without importing candidate code. A candidate cannot change its own source
   pin or admission recipe. Refuse unrelated paths, arbitrary test/helper
   imports and newer mutable dev content. Retire the bounded source-transfer
   exception after the compatible guard is installed.
5. Activate the exact review-workflow preservation allowlist, then add the
   setup workflow and canonical preservation declaration through the admitted
   main control transaction. Preserve the existing three workflow declarations.
   Reconfirm the actual main parent and compatible helper identities before
   each transaction.
6. Only after the capacity operator proves ARC Ubuntu x64 readiness, ephemeral
   lifecycle and network isolation, enable the actual route. Record the final
   default-branch setup blob SHA and actual scale-set identity.
7. Request one ordinary official review through the existing supported route;
   verify a real ARC-assigned job and resulting official review. Do not rerun an
   old Actions execution and assume it selects the new workflow definition.

The explicit source-compatibility authorization covers the narrow, dev-first
recipe above. It does not make the old workflow-only rule capable of admitting
Python source without that enabling policy transaction. Exact-head
edit/merge/restore consent separately addresses blocked PR checks. Any temporary
protection exception closes and is live-audited before further coding, waiting
or another PR; no forged check results or globally disabled ruleset is used.

## Validation and rollback obligations

- Exact guard exception and negative route/permission fixtures retain ordinary
  validator refusals and hosted/native coverage.
- Main control transactions reject mixed source changes, undeclared files,
  incompatible helpers and stale control inputs. The explicitly bounded source
  compatibility transaction admits only its pinned, dev-merged four-file set;
  wrong source, changed bytes, added imports and non-regular modes are refused.
- Preservation survives a later promotion; no unrelated main controls or
  candidate-generation behavior change.
- The operator proves denied LAN/unapproved egress, required-host connectivity,
  one-slot resource admission and ephemeral cleanup before external review work.
- A real official review canary confirms the intended ARC scale set; unsupported
  setup behavior or independent service limits remain visible failures.
- Roll back the dedicated setup to the documented hosted route through the same
  control ownership. Never alias a persistent runner to hosted or ARC labels.
  The capacity operator owns draining and infrastructure teardown.

Local Python validation remains under the bounded test supervisor, with new
tests staged before isolation snapshots. Private infrastructure evidence and
protection snapshots never become public source or required publication
artifacts.
