# Filtered populations and owned demand

The optional `repository-filtered/v1` foreground service profile composes the
existing ReplayStore, writer lease, observer, local ingress and joined drain.
It is a distinct, explicitly provisioned `repository_cache_version:3` layout.
It does not migrate or reinterpret retained generation-one/two state.

The expression API is
[`agent-pull-requests.filters/v1`](../../../libs/pr-filters/README.md).
Named definitions are inert. An authenticated dynamic caller or a configured
startup sponsor creates demand through the same subscription owner. Personal
definitions and rosters belong in adopter configuration, not this source tree.

## Selection and evidence

Use the common six families: repository, from users/groups, assigned
users/groups, creation/activity age, native tags and draft/publication state.
The selected executing native driver's `pr-watch population --contract --json`
declaration decides which predicates it can support. Registration rejects
unsupported required semantics and repository selections outside the configured
target. Expressions narrow authority; they cannot choose an executable,
credential command, endpoint, account or another configured repository.
The driver's required `acquisition` declaration uses
`agent-pull-requests.filter-acquisition/v1`: canonical user-ID syntax, native
query selectors and a finite query-cover limit. The shared pure planner checks
both native execution and the proposed aggregate live demand before subscription
or renewal commits. An older descriptor without this declaration is unsupported;
upgrade the native provider before selecting the filtered service profile.

The initial native population driver is Azure DevOps. Its user IDs are native
canonical lowercase IdentityRef UUIDs; assignment means reviewer users, not
work-item ownership. Groups and activity age are explicitly unsupported in this
driver. GitHub/Gitea population acquisition is also explicitly unsupported;
their existing unfiltered repository profiles remain available unchanged.
Do not substitute email suffixes, a local team name, review actionability,
self-review exclusions, a recency window or draft exclusions.

The observer acquires one bounded union per repository, not one reader per
subscription. It rechecks active interest before native work and publication.
One subscription's expiry/cancellation does not remove another's interest.
An in-flight read retains native cleanup custody but cannot publish for withdrawn
or replaced acquisition generations. Finite native input files remain private
until actual reader/cleanup settlement; unconfirmed cleanup retains the file
and raises rather than silently discarding that evidence.
Demand must still match before starting new native work. Once started, a shared
read may finish for any remaining captured registration generation rather than
discarding another owner's result when one participant withdraws.

Each consumer has independent membership and entry/change/departure/coverage
events and its own last-projected fact revisions, including when a new subscriber
joins an acquisition already in flight. Unknown facts preserve previously accepted membership and report partial
coverage. Partial/unavailable discovery never proves absence. Clock-only age
changes are evaluated at a captured cycle instant. Departure is selection loss,
not proof that a PR closed or merged. Population events do not confer permission
to review, vote, merge or emit a task.

Current population facts are the seven normalized filter fields; this profile
does not claim complete PR diff/source, reviewer-policy or posted-review
acceptance. The existing per-PR semantic snapshot/review consumers retain their
separate contracts.

## Explicit foreground configuration

The standalone host supports this profile on Linux using Unix sockets and
`SO_PEERCRED`. Native Windows ingress is not added. The version-two
`repository-local/v1` profile and all legacy defaults are unchanged.

A version-three `agent-pull-requests.service` configuration selects:

- `profile: repository-filtered/v1`, a fresh private `root`, `namespace`, and
  the existing finite interval/operation/drain budgets.
- `repository_cache.protocol: agent-pull-requests.filtered-population/v1`.
- The existing trusted `socket`, `peers`, `repositories` and `bindings`
  fields. Each repository carries its full namespace/provider/endpoint/account/
  repo identity, selected absolute native prefix, absolute source configuration,
  and explicit credential-command argv. Transport-derived peer identity maps to
  the configured consumer; request bodies cannot claim an owner.
- `repository_cache.definitions`, an inline inert
  `{"protocol":"agent-pull-requests.filters/v1","sets":{...}}` document.
- `repository_cache.sponsors`, either `[]` or bounded records with
  `principal`, `target`, `request_id`, `definition`, and `ttl` (1-86400 seconds).
  Every sponsor must resolve to an existing admitted binding and definition.

For example, one definition can select exact author users without selecting
draft state or a recency window:

```json
{
  "protocol": "agent-pull-requests.filters/v1",
  "sets": {
    "authors": {
      "protocol": "agent-pull-requests.filters/v1",
      "where": {
        "from": {
          "users": ["11111111-1111-1111-1111-111111111111"]
        }
      }
    }
  }
}
```

Parsing and provisioning do not start provider or credential reads. Foreground
startup qualifies the executing native population descriptor before local
admission. Merely defining the example above creates no acquisition demand.
Run the existing standalone `provision --config` and `serve --config` commands
only against the separately selected fresh profile/root.

## Supported client and lifetime

The standalone CLI's explicit `population-*` commands are `list`, `capabilities`,
`subscribe`, `query`, `inspect`, `renew`, `events`, `acknowledge`, and `cancel`.
They use `/v1/populations`, not the legacy `/v1/repositories` routes.
Every command takes `--socket` and `--namespace`; target-specific operations
also require `--target`.

Subscribe takes an original `--request-id`, finite `--ttl`, and exactly one of
`--definition` or absolute bounded UTF-8 `--filter-file`. The receipt contains
the resolved immutable expression/revision and registration ID. Retrying the
same original request returns its original baseline and delivery position.
Changing inline intent or TTL under that ID conflicts. Editing/removing a named
definition never silently changes an existing registration; use an explicitly
new request to replace intent.

Query takes `--registration-id` and optional `--after`/`--limit` to page that
registration's current membership without triggering provider IO. Inspect
returns its retained intent and lifetime. Inactive registrations cannot query
newer facts acquired by another consumer. Events retains independent admitted
delivery; ACK takes the delivered `--event-id`. An expired/cancelled consumer
can reconcile already delivered events, but cannot obtain later undelivered
events. Backpressure retains unsettled delivery rather than evicting it.
Original settled request receipts remain at least one day beyond expiry.
Inactive registrations release current-membership cache pins independently of
their immutable baseline and unsettled delivered event payloads. Reactivating
expired demand starts membership reconciliation under its new generation.

Renew takes `--registration-id --expires-at <absolute-epoch-second>`. It advances
the finite expiry monotonically, at most one day into the future. Repeating an
absolute horizon never extends it again or resets baseline/ACK. An authorized
fresh horizon can reactivate expired interest under a new acquisition generation;
cancelled demand cannot be revived. Startup sponsors use these same operations,
renewing their configured demand during ordinary observation cycles and after
restart. Cancellation remains authoritative even for a configured sponsor.
Removing a sponsor stops renewal; its remaining finite demand expires normally.

The new client first checks the running host's protocol/storage capabilities.
An old owner is explicitly unsupported; there is no broader sweep, process wait,
ambient credential or legacy-route fallback. Old clients cannot read the new
layout. Rollback requires a compatible reader with its retained root or a
separately fresh old-profile root; it never deletes or downgrades newer state.
