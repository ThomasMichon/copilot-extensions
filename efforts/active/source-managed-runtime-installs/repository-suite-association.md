# Repository-suite association adapter

[Back to the effort](README.md) · Coordination: #7028

## Objective and ownership

Connect an explicitly addressed Manager configuration root to the repository
whose declaration governs that installation's desired suite. The existing
declaration reader remains the interpreter of repository data; native source
acquisition, immutable composition and application installation retain their
owners. This closes part of the existing
[repository-selected suite intent](../../../visions/installer/README.md#repository-selected-suite-source).
It does not introduce another installer, resolver, service or project.

The [accepted source split](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6890789048)
leaves this association/declaration adapter separate from ordinary update
callers and native qualification. Review the
[receiving contract](../../../docs/fresh-source-recovery.md#association-adapter-receiving-contract)
before changing shared caller interfaces. Preserve #10570, application-footprint
production, execution custody and all operational holds.

## Delivery sequence

1. Review the explicit association schema, configuration owner, replacement
   semantics and observation boundary. This document and the receiving contract
   supply that design; they do not advertise a callable adapter.
2. Implement the adapter inside the existing Manager package. Reuse its
   configuration path, shared writer lock and atomic publication. Provide
   effect-free inspection and explicit association/clear operations, with
   expected-prior-root checks. Test the actual module entry and configuration
   files, not only serialization helpers.
3. Give existing cold setup and ordinary-update owners the addressed association
   through the reviewed API. Reuse the current declaration/parser and immutable
   selection owners. Keep caller updates separate from the adapter contribution
   and reconcile concrete overlaps before changing their files.
4. Qualify actual setup, later desired-source convergence and retained identity
   through their existing native lanes. Adapter tests and configuration writes
   are not construction, activation, content delivery or installed acceptance.

## Validation plan

- [x] Review the receiving contract before shared caller changes; #12987 merged
  with exact-head approval and passing required checks.
- [x] A missing association is an explicit unassociated observation and creates
  no configuration, lock or repository.
- [x] Inspection ignores ambient cwd and repository configuration there, reads
  only the selected Manager configuration, and neither parses adopter YAML nor
  starts Git, a daemon or setup.
- [x] Invalid TOML, unknown association versions/fields and malformed roots
  refuse without becoming an absent association or overwriting data.
- [x] Apply changes only the owned association table through the existing
  configuration writer. Manager self-source, runtime-source policy and all
  unrelated values survive.
- [x] An initial binding, repeat request, explicit replacement and clear work
  through the real module boundary. A stale expected root refuses without
  changing the association.
- [x] Equivalent native path spellings do not create an accidental retarget.
  Inspection of the stored identity does not require the adopter to remain
  available; consuming its declaration retains the existing path checks.
- [x] Cold invocation with the trusted source/tool dependency closure works
  without an installed Manager, registry or running daemon.
- [ ] Old consumers do not gain association support merely because they ignore
  the new TOML table. Unsupported module/schema discovery remains explicit.
- [ ] Real installation, ordinary-update convergence and each content/runtime
  domain retain separate actual evidence before parent completion.

## Current boundary

The receiving contract is reviewed; the isolated Manager module/API and its
explicit association operations merged in #13012 as
`59077d83c93c750a8ad4af5cbb1bd83b11199ac4` after exact-head approval and
passing required CI. The native Windows packet
passes 23 controls, including actual cold-module inspect/propose/apply/replace/
clear, expected-root revalidation under the shared lock, failed publication,
input bounds and unrelated-policy retention. The two changed Python files pass
whole-file strict analysis for Linux and Windows Python 3.10 targets. The
Manager checker uses a scoped hash-locked TOML backport environment; this does
not change the whole project's pending admission.
Both mutation operations also reject abbreviated apply flags before any
configuration effect; the parent parser's strictness is set on each subparser.

Schema-2 recovery remains preview-only. No ordinary caller consumes this
association yet, and no operator installation was configured by the source
tests. There is no new bootstrap request version, installed pin, migration,
repository write or rollout. Old-caller negotiation, actual source convergence
and native installation/adoption gates remain open in the parent.

The separate read-only `repository_suite_declaration` module now connects the
addressed association to the existing declaration reader and native
source-locator validation. Its independent capability/schema prevents an
old association-only provider from advertising declaration support. Nine native
controls cover actual current/changed intent, unavailable-adopter identity,
malformed policy, secret-free rejection and a cold first-party package with
explicitly available or absent parser dependencies. Both changed Python files
pass whole-file strict analysis on Linux/Windows Python 3.10 targets.
These are source controls, not actual Python 3.10 execution or an installed
consumer. Ordinary callers and the native immutable preparation owners remain
unchanged; no source is acquired by declaration observation.
