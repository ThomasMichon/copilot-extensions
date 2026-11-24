# Optional GitHub snapshot batches

The selected native runtime may declare `snapshot_batch` in the existing
`pr-watch inventory --contract --json` descriptor. Its protocol is
`agent-worktrees.pr-snapshot-batch/v1`, independent of the package version.
This is a provider-first extension: no existing observer or scalar caller
selects it automatically.

```text
pr-watch snapshot-batch owner/repository 7 8 --config <absolute-file> --credential-command <JSON-argv> --max-credential-requests 12 --seconds 60 --json
```

The selected file must contain one matching `repos.<name>.pr` binding with
`provider: github`, `repository: owner/repository` and the explicit
`api_base: https://api.github.com`. No local checkout, ambient account, project,
alternate endpoint, token option, raw query, fragment or mutation is accepted.
The [renewable credential command](pr-observation.md#renewable-observation-credentials)
uses its existing typed envelope and caller-owned cleanup. It must supply
GitHub bearer material for that repository, API and `observe` permission.

Select one to ten distinct PR numbers in `1..2147483647`. The whole operation
has a `1..60` second deadline and a `1..64` acquisition/query-attempt budget;
defaults are 60 and 64. Pagination never restarts either budget. A fresh
credential is validated before every send, and the HTTP deadline is additionally
bounded by its expiry. No retry, redirect or scalar fallback occurs.

## Query and evidence

Only a source-owned query template and typed repository, PR and cursor
variables reach `https://api.github.com/graphql` through POST. Labels, reviews
and check contexts use pages of at most 100; the ten-member template stays
within 8,192 returned nodes and predicts primary cost one. It requests
candidate metadata and bounded captured check diagnostics, not PR/review bodies,
threads, diffs, source or log artifacts.

Transport retains the direct profile's `curl -q`, HTTP/1.1, HTTPS-only,
no-proxy, no-globbing, no-retry and no-redirect settings. Authorization and JSON
travel through stdin, not argv or token environment overrides. The total
response capture, including headers, is at most 1 MiB. Oversize or malformed
responses are unavailable, not truncated complete evidence. The capability's
`requires_binary_qualification: true` remains an adopter obligation: argument
construction tests do not attest a particular curl/TLS installation.

Every requested PR has one `results` entry with `pr`, `outcome`, `reason` and
`snapshot`. Complete entries reuse the version-1 scalar snapshot serializer,
add labels and omitted-failure count, and report complete review/check
collections. Reviews retain numeric 64-bit database IDs, timestamps, commit
provenance and dismissed/pending states. Check normalization shares the REST
owner's aggregation and bounded diagnostics. Native application identity and
start times select the latest run for each application/check name; a rerun with
missing or tied ordering evidence remains unavailable rather than selecting a
stale failure or inventing a current pass. Observed green checks are not a
required-policy verdict or permission to merge.

Missing/foreign members, redacted data, duplicate IDs/cursors, changing
candidate identity or collection totals, unknown checks and incomplete pages
produce per-member unavailability, never an empty successful population.
Scoped GraphQL errors invalidate that member; unscoped errors invalidate the
response. Head/base and PR mutation evidence must remain equal across pages,
but this is not an atomic multi-page provider snapshot.

The envelope identifies `schema`, version, provider, API, GraphQL endpoint and
repository. It includes the last validated `quota` (`cost`, `remaining`,
`reset_at`), optional `backoff` (`resource`, Unix-seconds `not_before`,
`reason`), `stop_reason`, credential acquisitions, query attempts and the sum
of validated primary points. Attempt counts include failures before a send;
they are not measured physical HTTP sends or provider successes.

## Backoff, version skew and rollout

Missing/malformed quota, unexpected cost, exhausted primary quota, provider
throttling or the original deadline stops further reads. Completed independent
members may remain usable after a later sibling fails. A valid final response
that exhausts primary quota can complete members but cannot start another page.
`graphql-primary` holds are distinct from `shared-secondary` holds; the latter
include `Retry-After` or a conservative minimum 60-second throttle delay.

This stateless native provider does not persist holds. Before another query,
an adopting observer/accounting owner must persist and enforce the returned
next-eligible time across restart, another PR and update. Independently
available REST may continue through a GraphQL-primary hold, never bypass a
shared-secondary hold. Consumer integration and that persistence are separate
required work, not provided by invoking this command.

Old callers retain their scalar syntax, defaults, responses and provider
support. A new caller must select this capability from the actual executing
runtime before emitting the new command. Missing/incompatible declarations
make the optional batch requirement unavailable, including after rollback:
do not probe the command and replay scalar requests. Ordinary scalar operation
does not require batch support. No old command is deprecated by this expansion.
