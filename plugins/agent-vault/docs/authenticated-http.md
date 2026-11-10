# URL-bound authenticated HTTP requests

[Plugin overview](../README.md) | [Configuration](configuration.md)

`agent-vault curl -- <supported curl arguments>` performs one request with the
system curl executable. Windows uses `System32\curl.exe` explicitly, not a
PowerShell alias; POSIX uses known OS/package paths instead of caller PATH
shadows. Callers supply a URL, never a vault entry or authentication
header. Prefer an existing authenticated domain CLI or MCP tool when it already
implements the operation.

## Bindings are explicit owner configuration

The default file is `http-bindings.json` beside the selected native
`config.json`. Native settings may select another file:

```json
{"http_bindings": "http-bindings.json"}
```

A native relative path resolves beside that native configuration file, not the
request's working directory. A trusted extension config source may contribute
an absolute `http_bindings` path when native settings do not specify one.
Present invalid values and extension resolution failures refuse rather than
falling through to another policy.
Repository `.agent-vault.json` cannot select HTTP bindings, and the request has
no `--entry` or `--bindings` override. These are trusted owner policy files,
not a sandbox against local code with the owner's filesystem access.

Example binding document:

```json
{
  "version": 1,
  "bindings": [
    {
      "url_prefix": "https://api.example.test/v1/",
      "entry": "API/service",
      "scheme": "bearer",
      "allow_injection": true
    }
  ]
}
```

Every record must carry a literal boolean `allow_injection`; only `true` enables
it. The entry is a full KeePass path in the configured vault, not a bare name
whose group could vary by repository. Credential reads retain Vault's selected
database and cache-first custody rules. KeePass's URL field is not used to guess
an entry.

A binding permits credential injection, not arbitrary API mutations,
credential creation/rotation or administrative actions. The caller's existing
authorization and confirmation requirements still apply.

Matching requires the same HTTP(S) scheme, exact hostname (case-insensitive),
effective port and case-sensitive boundary-aware path prefix. `/v1/` permits
`/v1` and `/v1/items`, not `/v10` or `/v1-other`. Queries do not select a
credential. Missing, disabled or overlapping active bindings fail closed:
`no binding for <host>` or `ambiguous binding for <host>`. There is no
longest-prefix identity guess.

Userinfo, fragments, trailing-dot/ambiguous hosts, dot segments, repeated
slashes and percent-encoded paths are refused. Query encoding remains allowed.
Use HTTPS for credentials crossing a network; HTTP bindings also support
explicitly selected local endpoints.

## Authentication and custody

| Binding scheme | Internally constructed header | Vault fields |
|---|---|---|
| `token` | Authorization with the token scheme | `password` |
| `bearer` | Authorization with the Bearer scheme | `password` |
| `basic` | Authorization with a UTF-8 user/password pair encoded as Basic | `username`, `password` |

Basic permits an empty username or password (but not both), supporting API-key
conventions. With an empty password, the username value is protected as the
credential.

The header is supplied to curl through a private stdin configuration. It never
appears in argv, a temporary config file, a caller environment variable or
request diagnostics. Protected credentials found in request operands are refused
before process launch. The child environment omits TLS key logging, proxies and
inherited values containing a resolved credential or supported encoding.
Curl's default config and proxies are disabled, URL
globbing is disabled, and only HTTP(S) protocols are admitted. Native TLS
verification remains enabled.

Responses are captured before output. The complete rendered output, including
the write-out suffix and its boundary with the body, is screened. Output containing the injected
credential, complete auth value, or supported JSON/URL/Base64 representation
is withheld with a secret-free error, rather than streaming an auth echo into
the caller. Curl stderr is captured, bounded and never forwarded verbatim.
Capture is limited to 8 MiB of response data and 64 KiB of diagnostics.
Credentials exceeding the 8 KiB authentication-header limit are refused.

Configuration failures are reported separately from credential-read failures.
Known safe context diagnostics identify invalid client-port selection or a
missing/mismatched qualified owner. Other exception text is withheld because
configuration and extension errors may contain private values. A configuration
failure happens before credential lookup; unlocking cannot repair it.

The permitted destination is a trusted credential consumer. This is not a
general detector for unrelated secrets returned by an API, nor a cryptographic
sandbox against an approved service deliberately transforming a credential
into an arbitrary representation. Use purpose-specific tools for operations
that return credentials.

Redirects are never followed, even to the same host. A 3xx response fails without
a second request or response output. A different target requires its own
explicitly matching binding.

## Supported request arguments

```bash
agent-vault curl -- -sS -H 'Accept: application/json' \
  -w '\n%{http_code}\n' 'https://api.example.test/v1/items?offset=2'
agent-vault curl -- -X POST -H 'Content-Type: application/json' \
  --data-raw '{"name":"example"}' 'https://api.example.test/v1/items'
```

```powershell
& '<agent-vault catalog argv[0]>' curl -- -sS `
  'https://api.example.test/v1/items?offset=2'
```

| Area | Supported form |
|---|---|
| One destination | Positional URL or `--url` |
| Method | `-X` / `--request`: GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS |
| Response | `-I` / `--head`, `-i` / `--include` |
| Non-auth headers | `-H` / `--header`, with literal header values |
| Inline body | `-d` / `--data`, `--data-raw`, `--data-binary` |
| Timing | `-m` / `--max-time`, `--connect-timeout`; positive values up to 60 seconds |
| Quiet/error flags | `-s`, `-S`, `-f`, combined `-fsS`, long equivalents |
| Status suffix | `-w` / `--write-out`: literals and `%{http_code}` only |

File/stdin request data is unsupported; `--data-raw` treats a leading `@`
literally. Response files, write-out file/diagnostic operators, config/netrc,
verbose/trace, redirect, proxy/resolve/connect-to and TLS-verification bypass
options are refused before credential lookup. Caller authentication, Cookie,
Host and common API-key headers are refused too. Unknown options and
abbreviations are not silently passed through.

HTTP errors return exit 22; `-f` suppresses their response body. A native curl
failure retains its exit code with a secret-free diagnostic. Binding, syntax,
credential, redirect and unsafe-response refusals return 2. No error authorizes
trying another entry or manually constructing an auth header.

An ordinary request does not opt into a vault ceremony. `curl --prompt -- ...`
explicitly permits the existing unlock flow; unavailable credentials otherwise
require repairing or unlocking the selected vault through its normal owner.

## Version skew and validation

The executing runtime advertises `authenticated-curl-v1` through the existing
read-only `agent-vault invocation-contract --json` capability list. The binding
document supports version 1. Older runtimes retain their existing commands but
reject `curl`; loading newer documentation does not upgrade a runtime.
Update through the owning installation flow or use an existing authenticated
domain tool, never a manual token/header fallback.

Focused tests are `tests/test_authenticated_curl.py` (T1 policy and construction)
and `tests/test_authenticated_curl_native.py` (T2 real curl and isolated loopback
HTTP, synthetic credentials only). The latter belongs to the existing ordinary
full-plugin integration selection, not required unit-only CI. It verifies actual
CLI parsing, credential-free argv/environment, all three schemes, redirect
refusal, auth-echo withholding, response limits and child cleanup. Skipped native
prerequisites are not platform qualification or deployed-system proof.
