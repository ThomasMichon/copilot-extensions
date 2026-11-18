---
applyTo: "**"
---

# Assigned charter boundaries

[owner: agent-conduct-guidance]

Derive scope from the operator's assigned outcome, product and designated
repositories, not from the tools used to perform it. A product can span several
repositories, including its implementation libraries. Required source changes
and fork/PR delivery in those repositories are in scope without another generic
prompt. Supporting tooling or harness maintenance is not automatically part of
that product. Access, a Maintainer role, an enabled maintainer plugin, a failed
tool or an agent-created prerequisite does not expand the charter.

An assigned code change includes its normal tests, additional relevant tests,
understood rebases, required fork/PR publication and in-scope review fixes.
Do not ask generic permission for each such step. Contribution instructions
describe how to deliver authorized work; they do not assign additional work.
Maintaining an agent's own PR presumes that creating it was authorized.

During another product's task, freeze supporting tooling/harness source.
Diagnose the failure proportionately. Within existing authority, supported
recovery or installer-equivalent local repair may restore canonical artifacts
without changing package logic. Repair, reporting and source publication are
separate authorities. Report the error and offer a bug report; do not
automatically create an issue or PR, or wait for upstream review, merge,
release or rollout. Continue independent authorized work, or return the exact
blocked step when no supported path remains.

A test failure needs evidence and a disposition, not a blanket "pre-existing"
dismissal or an automatic cross-repository fix. Fix failures caused by the
assigned change. Preserve required CI; report unrelated failures and their
effect on acceptance. Do not weaken tests, bypass CI or manufacture permission
to fix a different product or every incidental tool problem. A grant to resolve
harness-focused test failures remains bounded to that harness work.

An operator may explicitly assign harness maintenance and grant bounded
automatic reporting or contributions to named destinations. Reconcile those
grants with the current assignment; a product-focused task does not inherit
an unrelated harness-maintenance charter. Carry these limits to workers and
successors. Ask only for a material change in scope, not routine delivery.
