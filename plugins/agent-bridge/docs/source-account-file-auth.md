# Source-account file renewal

The internal `source_lease_http` and `copilot_file_auth` adapters provide finite,
explicit credential operations for an embedding that already owns approval and
config-writer custody. They do not activate at service startup, add configuration
keys or CLI commands, start a renewal daemon, enroll an account, or control a
Copilot process. Existing Bridge sessions and credential paths are unchanged.

## Fixed HTTP acquisition

`HttpSourceLeaseClient` receives an explicitly approved HTTPS endpoint, the
existing `HttpAuthInjector` and exact `HttpAuthContext`, a separately approved
discovery endpoint/injector/context, an expected installation ID, a source/account/
recipient/host binding, and an absolute caller capability-file reference.
The native provider uses `GET /health` and `POST /source-credentials`.
Discovery requires the matching `installationId` and nested
`source_credentials_contract` declaration for
`credential-relay.account-source-lease/v1`, `http/v1` before reading the
capability. Both requests carry `X-Agent-Index-Installation-Id`; the response
must carry the same installation ID. Endpoint-specific auth scopes are never
inferred from a shared origin. The POST uses only that capability and the fixed
`get-github-token` operation. It never borrows a service's stored capability,
falls back to token-only acquisition, follows a redirect, uses environment
proxies, or retries a possibly accepted POST. Every HTTP request obtains fresh
headers through the approved injector. Custom CA contexts must retain certificate
and hostname verification; omitting one uses ordinary system trust. The caller's
whole-acquisition deadline governs both requests; no shorter implicit HTTPX read
timeout cuts off an otherwise valid source acquisition.
The response must be identity-encoded JSON within the fixed byte limit;
unexpected media/encoding and non-finite JSON are refused.

Response account, host, token shape, exact lease fields, numeric bounds and
generation are checked before returning an in-memory lease. The conservative
budget subtracts the larger wall/monotonic duration of the **whole** exchange,
including discovery and auth acquisition, from the serving peer's remaining
budget. Each retained observation likewise consumes the larger elapsed duration,
including suspension that advances wall time alone. Wall and monotonic
regressions permanently expire that observation. Provider bodies and exception
details are never diagnostic output; token-bearing lease representations omit
the token. A timeout does not establish that the source did not issue a lease.

The provider expands first: an old or rolled-back endpoint without declared
lease semantics refuses this new operation before capability disclosure.
Existing token-only clients remain unchanged. No token-only fallback is
equivalent to source authorization, and no legacy path is retired here.

## Native account-file scope

`CopilotAccountFile` is explicitly **POSIX-only**. Other platforms refuse before
credential acquisition. Its caller selects the absolute native `config.json`,
the existing GitHub account and an already approved plaintext-storage slot.
Initial login, plaintext consent, directory creation, account enrollment and
keychain migration remain with their native owners. This adapter does not infer
those approvals from an endpoint or a source label.

The parent directory must be canonical, owner-only and owned by the current
UID. Config and lock files must be private, current-UID, single-link regular
files; symlinks, malformed/duplicate-key JSON, absent account slots and unsafe
permissions refuse without rewriting config. Leading native `//` comment headers,
blank lines and an optional UTF-8 BOM are preserved; the JSON body retains strict
duplicate-key rejection. Native `authTokens` uses the
`https://github.com:<account>` key. Renewal changes only that slot's token and
preserves other account and configuration values. The client account and host
must match the selected native account and `github.com`.

The adapter serializes its own writers with an OS lock and uses an exclusive
private staging file plus atomic replacement. It rechecks config identity and
the live source lease immediately before replacement. The serialized stage must
also fit the same read limit, so escaping or token growth cannot prevent later
renewal/withdrawal. Cleanup owns a stage only after exclusive creation succeeds;
a collision never deletes another writer's file. The embedding must
separately retain **exclusive native config-writer custody**: native writers
do not promise to cooperate with this adapter's lock, so the stat check is not
a universal compare-and-swap against arbitrary concurrent writers. Do not start
a renewal loop without that custody. Directory-fsync failure after replacement
is reported as unknown durability, not success or a reason to replay.

`withdraw()` removes only the exact token last installed by the same in-memory
owner. Changed token custody refuses; there is no account-wide logout, token
revocation, deletion of another account, or automatic withdrawal/replay.
The same retained owner can renew its withdrawn slot after fresh acquisition.
No token or serialized lease metadata is installed in process environment.

## Execution remains separately owned

File persistence makes the native account credential available to a later
ordinary CLI launch. It **does not prove** a running CLI reloads that file,
that a cached token expired, or that execution paused. Source authorization
is not token expiry, and persisted metadata cannot recreate live authority.
The caller must retain the lease and arrange renewal/expiry through its existing
credential and native execution owners.

The retained Host owns auth-wait admission, in-flight work, native input,
durable conversation identity and unresolved sends. Source renewal is not
permission to replay or retroactively settle a send. Same-session recovery
requires actual native capability proof. Semantic prompt admission alone does
not enforce native UI input or in-flight work. Auth-wait must remain separate
from irreversible user-input/unknown-send control holds; renewal never resets
those holds. These adapters do not close/reconnect
a headed backend, replace a process, seize a pane, or advertise an auth-wait RPC.
Until that independent owner supports and enforces the required lifecycle on
the file adapter's supported platform, this is source preparation,
**not an active lease-enforced deployment**.

The focused `tests/test_source_file_auth.py` packet covers real scoped header
construction, source/capability rotation, unsupported/redirect/malformed peers,
conservative clocks/suspension, no POST replay, actual verified TLS, FIFO refusal
before a blocking read, and private native
file changes. POSIX effects and unsupported-platform refusal have separate
platform controls. All credentials, accounts and files in it are synthetic.
The Index HTTP fixture additionally runs this client against its actual native
health/credential routes and real account-relay provider, including installation
identity, renewal and wrong-capability refusal.

## Disabled integration prestage

The default-off Driver provider delivered in #11781 exposes
`copilot-driver.headed-auth-wait/v1` through the explicit Python `run_headed`
configuration and its running private declaration. This is separate from the
earlier `source-auth-state/v1` observation proposal, which is not an implemented
RPC. Its qualified Windows path does not make this POSIX-only file adapter
interoperable: the receiver still refuses POSIX before endpoint/native creation
until independent owner-death custody is implemented and qualified.

An ordinary Driver `initialize` response, `nativeSend` support,
`backend_admitted`, a merged controller or an inbox receipt cannot substitute
for it. Missing runtime support is **unsupported**; unavailable or ambiguous
receiving identity, state or admission is **unknown**. Neither permits automatic
acquisition, file renewal, process action, fallback or replay. No enabled
configuration, renewal loop or positive integration adapter is introduced here.

The source-loss controls in `tests/test_source_file_auth.py` prestage the
credential side: an unavailable source does not extend an existing lease or
rewrite the file, and fresh acquisition through the same file owner does not
revive expired authority. Their private synthetic history fixture proves only
that these file operations leave unrelated bytes intact. It is not a native
conversation, process-preservation or recovery test.

The positive integration remains blocked on an applicable POSIX native execution
owner and actual platform-compatible composition. Its revision-bound acceptance
record must cover the actual receiving incarnation, native UI and in-flight
work, not only semantic prompt admission or a Windows receiver pass.
The [Driver core](../../../libs/copilot-driver-core/README.md) remains the owner;
`requireControl`, `prompt` and `nativeSend` are not the source-auth-wait contract,
and raw Host write observation is not an enforcement seam. Renewal must never
clear irreversible control loss, pending/sending state or unknown admission.

Prepare the following proof against that owner's implemented contract, without
inventing a new RPC or treating synthetic controls as a native pass:

| Consumer checkpoint | Required observation |
|---|---|
| Disabled, unsupported or unknown receiving owner | No source acquisition, file mutation, process action or seed submission |
| Approved source and native file | Ordinary SSH followed by plain `copilot` uses the selected account without manual login or token environment |
| One approved signed-in source remains | Fresh bounded authorization sustains renewal without changing account, file-writer or execution custody |
| All approved sources unavailable | Renewal refuses, retained source authority expires conservatively, and the original native owner stops auth-dependent UI/in-flight work while preserving history and uncertain operations |
| Fresh source after auth wait | The owner restores the same durable conversation/history, possibly with a new PID/incarnation, without replaying the seed or uncertain sends |
| Obsolete lease, source loss or changed owner | Stale authority cannot resume execution or reset irreversible control holds |
| End of the proof | Exact owned-child cleanup is confirmed; unrelated sessions and services remain untouched |

Use the existing owned native qualification lanes and coordinate any actual
core/Host/backend/launcher changes through their source owners.
Do not revoke real credentials, sign users out, alter a global clock, restart
shared services or delete history to manufacture expiry. Acceptance of that
native contract is necessary for integration readiness, not permission to
activate an installation. A source-only or draft prestage stays disabled until
the operator separately authorizes enablement and the normal runtime owner
completes its current source, caller, host and custody checks.
