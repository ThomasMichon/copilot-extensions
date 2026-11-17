# PR filter expressions

Dependency-free shared expression owner for PR drivers and the PR service.
This library performs no IO, discovers no identities, grants no access and
starts no subscription. The [accepted contract](../../efforts/active/durable-pr-watch-delegation/filter-subscriptions.md)
retains provider, service, credential and delivery ownership.

`Expression.parse({"protocol": "agent-pull-requests.filters/v1", "where": ...})`
accepts `all`/`any` composition or a conjunction of leaf fields:

```json
{
  "repo": ["Project/repository"],
  "from": {"users": ["user-id"], "groups": ["group-id"]},
  "assigned": {"users": ["reviewer-id"]},
  "age": {"field": "created", "max_seconds": 604800},
  "tags": {"any": ["needs-review"]},
  "publication": ["published"]
}
```

Arrays are OR selections, except explicit `tags.all`. Age supports `created`
or `activity`, with inclusive `min_seconds`/`max_seconds`. Identity strings
are exact executing-driver identifiers, not portable email/group guesses.
Drivers declare `fields`, supported identity-group fields under `groups`, and
`age_fields`, alongside the protocol. `require()` enforces that declaration
and every nested repository reference against the admitted scope.

`pr_filters.acquisition.plan(expression, constraints)` shares pure native-query
admission between service and driver callers. The versioned
`agent-pull-requests.filter-acquisition/v1` declaration selects canonical
lowercase UUID user IDs, maps author/reviewer fields to native query selectors,
and bounds the aggregate query cover. Conjunction chooses one equivalent
narrowing cover; disjunction combines covers or requires a full repository
scan when a branch is unbounded. All residual user IDs are validated even when
another predicate supplies the cover. This planner performs no provider IO.

Evaluation consumes driver-normalized `repo`, `from`/`assigned` identity arrays,
`created_at`/`activity_at` epoch instants, native `tags`, and `publication`.
Missing facts remain `unknown`. A trusted driver's group evidence contains
`members` and explicit `complete`; known positive membership can match partial
evidence, but missing negative evidence stays unknown. The driver owns current
membership freshness and assignment semantics.

The immutable canonical expression has a SHA-256 content `revision`.
`Definitions.parse({"protocol": ..., "sets": {"name": <expression>}})` stores
inert names; `resolve()` returns immutable content for the subscription owner,
not a self-updating reference. Strict JSON `loads()` rejects duplicate members,
nonfinite values, invalid UTF-8, unknown syntax and bounded-complexity overflow.
