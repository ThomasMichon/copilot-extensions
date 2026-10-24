# Authelia profile qualification

This opt-in experiment defaults to the existing `jwt-pop-v1` verifier.
Explicit `--profile enrolled-pop-v1` selects the separate common enrolled-key
profile; these are the only two choices. It does not change either profile,
operate an alternative token issuer, enroll a real principal, authorize a
service or install anything on a target machine.
It qualifies only the fixed confidential `client_credentials` flow; human
login and current enrollment/policy require their own evidence.

## Boundary and invocation

Use a native amd64 Linux/WSL host with an existing Docker engine, as a
non-root user. Obtain approval for the following exact local effects first:
build an SDK image, pull the pinned Authelia image, create a private internal
network and three bounded fixture containers, then stop and remove those
exact receipted containers/network. Images and owner-private result files
are retained. No host port, Docker socket, host credentials, production
configuration, privileged container or external runtime network is granted.
The build and pull need registry/package network access.

Commit all five files in this directory together with the SDK source under
test. There is no live-overlay or uncommitted-source mode. Create an empty
owner-private results parent outside every repository and session-state tree,
using the platform's configured temporary storage:

```text
python tools/clean-room/authelia-profile-qualification/run.py \
  --source-commit <full-commit> --results-root <private-results-parent>
```

To select the separate enrolled-key protocol, after committing and auditing
the exact source and receiving the same effect authorization:

```text
python tools/clean-room/authelia-profile-qualification/run.py \
  --source-commit <full-commit> --results-root <private-results-parent> \
  --profile enrolled-pop-v1
```

Omitting `--profile` remains `jwt-pop-v1`, including its token-endpoint DPoP
request, optional one-nonce challenge and original incompatibility criteria.
Profile selection is fixed before acquisition, recorded in `receipt.json`,
and passed only as the closed probe argument. There is no automatic retry
under another profile, arbitrary probe command, issuer/configuration override,
live overlay or uncommitted-source mode.

The fixed vendor input is Authelia **4.39.28**, released 2026-09-17, pinned
to the multi-platform image digest in `run.py`. The runner inspects the
actual selected amd64 image and records its immutable image ID; it does not
accept an image, URL, command, credential or mount override.

The SDK build reuses the closed Git source materialization and Gateway
Dockerfile. Both the sibling runner closure and these qualifier files must
match that same commit. Default SDK dependencies and executable behavior are
unchanged. Runtime uses the installed wheel with its existing
`service-gateway` extra, not a checkout import or editable installation.

Each container has a read-only filesystem, non-root caller UID/GID, all
capabilities dropped, no-new-privileges, 256 MiB memory/swap, one CPU, 64 PIDs
and bounded tmpfs/log storage. Preparation has no network. Only its explicit
fixture output is writable. The provider mounts only provider files; the
consumer never receives the issuer signing private keys. The consumer hosts
the verifier on its own HTTPS loopback, not a published host port.
Authelia's `server.disable_healthcheck` is explicitly enabled: Docker's
`--no-healthcheck` alone does not prevent its startup code from rewriting
the image's `/app/.healthcheck.env`, which is incompatible with a read-only
root. The harness supplies its own real TLS readiness probe. The isolated
fixture also disables the external NTP startup probe, since its private
network intentionally cannot reach an external time server. It still uses
the host's clock; TLS validity, token expiry and proof freshness checks
remain enabled and unchanged.
The SDK build defaults to 900 seconds (maximum 1800); runtime, including the
bounded image pull, has a 300-second budget. Cleanup has separate bounded
commands and is attempted on failure and termination.

## What is measured

Offline preparation generates throwaway client credentials, issuer signing
keys, a private fixture CA, separate issuer/resource TLS keys and a separate
client proof key. It writes exclusively created 0600 files below 0700
directories, without modifying the process umask. JSON configuration is valid
YAML. No secret enters an image, argument, environment variable or report.

The consumer CA-verifies the real provider's fixed discovery, token and JWKS
endpoints with redirects and ambient proxies disabled. Both profiles request
the explicit resource audience, read scope and ES256 access-token signing.
The default `jwt-pop-v1` sends a P-256 DPoP token-endpoint proof and handles at
most one explicit provider nonce challenge. The enrolled profile deliberately
omits token-endpoint DPoP and makes exactly one token request, since its proof
key is independently approved by the fixture. A failed request, including a
nonce challenge in that profile, refuses; it never falls back from an attempted
DPoP request or retries as bearer acquisition.

Authelia's required RSA key may coexist with the explicitly authorized EC
key. Only the configured EC key ID is selected from the TLS-authenticated
JWKS. No token-supplied discovery/key selection, claim rewriting, re-signing,
key-shape rewriting, ID-token substitution or bearer-only resource fallback occurs.

A separate real HTTPS resource handler invokes the existing SDK verifier,
with server-derived method/target, the actual vendor access token and fresh
client DPoP proof bound to the resource nonce and token hash. Merely passing
an HTTPS string to a verifier, acquiring a token or selecting ES256 cannot
produce a successful result.

### Fixed enrolled-key experiment

The new path uses the maintained common `EnrolledIdentityVerifier`, not an
alternative verifier or a provider callback. The original provider-signed
access token must satisfy pinned issuer, ES256, `at+jwt`, dates, audience,
exact client/subject mapping and any present `cnf`. There is no claim injection,
rewriting or re-signing. The fixed resource accepts only the configured
`AUDIENCE`: its string or a unique one-element array. Additional audiences
refuse. The resource always requires the separate DPoP authorization scheme
and independent key proof over its actual CA-verified loopback HTTPS request.
An issuer response with `token_type=Bearer` is not by itself a refusal of this
separate protocol, nor evidence of issuer-bound DPoP or permission to omit the
resource proof.

The mapping is constructed from committed constants and the separately
generated fixture proof key, **never from decoded token traits**. The exact
fixture assumptions are `client_id == CLIENT` and `sub == ENROLLED_SUBJECT`,
where both constants are `qualification-client`. That client-credentials
subject shape is an explicit experiment input, not a general provider claim
contract inferred from the successful fixed flow. Actual claims which differ
must produce a truthful refusal; no observed subject is adopted or guessed.
The canonical mapped principal is fixed `qualification-principal`, kind
`caller`, resource `AUDIENCE`, key-source label `qualification-proof`, with
the independently generated proof thumbprint. Trust and mapping expiry are
bounded to 120 seconds; original token expiry may shorten that lifetime.

After a successful protected request, the probe must also observe refusals
over that same real TLS resource for a copied original token presented with a
proof signed by an unapproved key, missing proof, and replay of the exact accepted proof.
Each check must return its expected value-free SDK category before a positive
result is emitted. Failure of a check is an experiment failure, not narrow
compatibility. Signature, method/target, token hash, nonce, freshness and replay
enforcement remain in the unchanged common engine.

Fixture approval is synthetic experiment input, not authenticated real-world
enrollment or a current resource grant. This path qualifies the fixed
token-plus-key crypto/TLS conjunction only: it does not exercise the full
service-policy publisher, current `ServiceAuthority` resource grants, durable
source custody, provider renewal/revocation or a production lifecycle.

## Evidence and non-claims

`receipt.json` records the selected profile, exact source hashes, image identities, owned resource
IDs and cleanup, plus only finite boolean/enum traits and SDK refusal codes.
Unverified token traits are labeled as such; they do not authenticate a
principal. The enrolled profile adds only boolean `client_id_exact`,
`subject_exact`, `audience_allowed` and a closed `negative_checks` record
(`wrong_key_refused`, `missing_proof_refused`, `replay_refused`). Receipt admission
checks the outcome against the originally selected profile, not a profile
announced by the probe. No raw subject, client secret, token, key or claim
values are retained in these traits/checks. Before cleanup, the runner captures each container's status, exit
code, OOM flag and redacted engine error. Docker diagnostic files retain the
provider's error message, nested error and stack, after redacting generated
fixture secrets, private keys, tokens, password hashes and authorization
values. Redaction happens before truncation and before persistence. Records
and text lengths are bounded; omitted records are counted explicitly. Other
JSON fields and unrecognized output are not copied into the evidence.
Configuration-validation errors use the pinned provider's text format before
it switches to JSON. The runner parses only structured key/value records from
that format, including quoted and escaped messages, through the same approved
field/redaction path. There is no arbitrary text-output fallback.

If fixture redaction inputs cannot be read safely, diagnostic text is
withheld and `diagnostic_capture_error` is recorded; there is no raw-output
fallback. Exact-ID cleanup still runs. The private evidence files survive
container/network teardown, so a failed provider's useful cause is not
destroyed with the container.
Receipt or diagnostic-persistence failure cannot prevent owned teardown.
Such failures remain explicit in cleanup errors; if even the final receipt
cannot be written, the command returns failure and reports
`final-receipt-write-failed` and failed persistence on stdout. Signal handlers
are restored regardless of that final write.

Exit **0** means this narrow token/proof flow was verified over actual TLS.
Exit **2** means a valid acquisition produced an incompatible wire/profile
result. Exit **1** means setup, acquisition, source, execution or cleanup
failed; it is not proof that the vendor is globally unsupported.
An incompatible result does not change or weaken the shared profile.

Every result leaves human login, enrollment, current authorization,
production deployment, provider renewal/revocation and service lifecycle
qualification open. Synthetic unit tests exercise the harness boundaries
only; they are not real-provider evidence.

### Observed profile-specific results

The isolated real-provider run against source
`00c96c4f3a7f30bb3af7f991da62bdf02e9691cd` and the pinned Authelia 4.39.28
image completed acquisition and an actual resource-verification attempt on 2026-10-22
UTC. Both provider and resource TLS were verified. The provider remained
running until deliberate exact-ID cleanup; no OOM was observed.

| Requested/observed trait | Result |
|---|---|
| ES256 access-token signature selection | Observed |
| `at+jwt` access-token type | Observed |
| Exact string audience required by `jwt-pop-v1` | Not met: audience was an array |
| `cnf.jkt` binding to the client proof key | Not present |
| DPoP token type | Not returned |
| Unchanged SDK resource verifier | Refused with `invalid-credential` |

This establishes **incompatibility of this exact client-credentials
configuration with `jwt-pop-v1`**, not that every Authelia
flow or future version is unsupported. No token was rewritten, re-signed or
accepted as an ID-token/bearer substitute. Human login remains untested.
Selecting an interoperable provider/profile remains an explicit identity-owner
decision; a green provider container does not resolve it.

The separately authorized, exact-committed-source `enrolled-pop-v1` run
against the same pinned provider **verified the narrow client-credentials
experiment**. Provider and resource TLS were CA-verified, the original ES256
`at+jwt` had an allowed array audience, and both fixed client/subject matches
held. The token still had no `cnf.jkt` and a non-DPoP token type; those facts
were not rewritten or reported as issuer-bound proof.

The resource accepted the original token only with the independently approved
key proof. Copied-token/wrong-key, missing-proof and exact-proof replay requests
all refused with their expected SDK categories. The provider was running
before deliberate cleanup, no OOM was observed, and all three exact fixture
containers and the internal network were independently confirmed absent
afterward. No host ports or Docker socket were exposed.

This result does not change the default-profile incompatibility or qualify
human access, a downstream acquisition adapter, real enrollment/current grants,
renewal/revocation, service lifecycle or production. Synthetic regressions
remain harness evidence, not substitutes for this real-provider flow.

Provider references:
[client access-token settings](https://www.authelia.com/configuration/identity-providers/openid-connect/clients/),
[provider configuration](https://www.authelia.com/configuration/identity-providers/openid-connect/provider/),
[released implementation](https://github.com/authelia/authelia/tree/v4.39.28).
The retained shared contract is
`fleet-manager/docs/runtime-authentication.md`; coordination remains under
[#5789](https://github.com/ThomasMichon/copilot-extensions/issues/5789).
See the [clean-room index](../README.md) for other isolated lanes.
