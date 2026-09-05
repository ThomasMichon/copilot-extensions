# Configuration

The hook is safe without configuration. It reads the `sessionStart` JSON payload
from stdin and treats its `cwd` as the authoritative target. In every git
repository selected by that payload it emits the generic third-party disclosure
and public-sanitization policy; outside a git repository it emits `{}`.
Missing or malformed payloads are diagnosed and emit `{}` rather than falling
back to the hook process's working directory.

Configuration is inert data. The Bash and PowerShell hooks parse it directly
without sourcing, dot-sourcing, `eval`, command expansion, Python, jq, or YAML
modules.

## Grammar

- UTF-8 text, one setting per line.
- A setting is `key=value`; the first `=` separates key from value.
- Leading and trailing whitespace around the line, key, and value is ignored.
- Blank lines and lines whose first non-whitespace character is `#` are ignored.
- Keys are case-sensitive.
- Values are literal data and are never executed. Each key still has the narrow
  value shape documented below; quotes, `$()`, backticks, semicolons, control
  characters, and other unsupported characters are diagnosed and rejected
  rather than copied into ambient context.
- A malformed line, empty key/value, unknown key, unauthorized key, or invalid
  enum value is diagnosed on stderr and ignored.
- Each config is limited to 65,536 bytes and 200 lines. A config over either
  limit is diagnosed once and ignored in full.
- Symlinked target-repository configs are rejected. PowerShell also rejects
  unresolved or reparse-point custom-instruction directories and configs rather
  than trusting a path that Windows PowerShell 5.1 cannot robustly resolve.

## Keys

| Key | Authority | Repeatable | Meaning |
|-----|-----------|------------|---------|
| `disclosure=third-party` | Operator only | No | Default: require disclosure for another party's repo; verified operator-owned repos omit it unless explicitly requested for the contribution. |
| `disclosure=always` | Operator only | No | Tightening: require disclosure for every contribution. Once selected by a discovered layer, a later layer cannot weaken it. |
| `owned_account=<public host>/<public account>` | Operator only | Yes | Host-qualified public forge account used as a local remote hint, for example `github.com/example-owner`. Host and owner must both match the remote, case-insensitively; the value remains a hint, not proof. |
| `contribution_guide=<repo-relative path>` | Target repo only | Yes | Additive pointer to an existing regular file under the target repository, with no symlink/reparse-point path component. It cannot override plugin/operator policy. |

Contribution-guide paths use `/` separators and narrow ASCII path segments
containing only letters, digits, `.`, `_`, and `-`. Absolute paths, empty
segments, `.` or `..` segments, traversal, backslashes, Markdown syntax,
controls, non-ASCII text, missing files, and values longer than 160 characters
are rejected. At most four valid paths are accepted, so repository data cannot
consume the shared ambient-context budget.

There is deliberately no key for literal private identifiers, credentials,
hosts, accounts other than public ownership hints, or policy replacement.

## Discovery and precedence

The hook starts with safe plugin defaults, then reads:

1. Canonical personal config: `$HOME/.copilot/ai-attribution.conf`.
2. Platform config home:
   - `$XDG_CONFIG_HOME/ai-attribution/config.conf` when
     `XDG_CONFIG_HOME` is set;
   - `%APPDATA%\ai-attribution\config.conf` on Windows otherwise;
   - `$HOME/.config/ai-attribution/config.conf` on POSIX otherwise.
3. `ai-attribution.conf` directly under each path in
   `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`, in environment order. The platform path
   separator (`:` on POSIX, `;` on Windows) and comma are both accepted,
   matching the customization scanner convention. Each directory is resolved
   without shell evaluation. Entries at or beneath the current repository root
   are diagnosed and skipped, so a target repository cannot self-promote its
   own data to operator authority through the environment.
4. `<session-start-repo>/.github/ai-attribution.conf`, with only
   repo-delegable keys.

Operator values are additive/tightening: accounts accumulate and
`disclosure=always` cannot be reset. Target-repo configuration is considered
last only so its additive contribution-guide facts appear in the emitted
kernel. Safety, publication, attribution, ownership, and sanitization keys are
not repo-delegable and are ignored there.

## Ownership inference

The hook parses both host and owner from common HTTPS and SSH local git remotes,
preferring `origin`. It makes no network call and performs no authentication.
Both host and owner must match one host-qualified `owned_account`,
case-insensitively. A bare owner shared across forges never unlocks the own-repo
exception.

The resulting hint is anchored only to the repository named by the
`sessionStart` payload. Ownership must be re-derived before publishing to any
other repository. A validated configured host/account match may be named in
ambient context; an unconfigured or private remote owner is never echoed there.
Missing, invalid, or nonmatching ownership is classified as third-party without
copying the literal owner. Even a match is only a hint, and the emitted guidance
requires verification before using the disclosure-only own-repo exception.

## Static fallback for launchers without plugin hooks

Some launch paths do not execute plugin hooks. The canonical minimal fallback is
`../instructions/publication-safety.instructions.md`, declared by
`../instruction-projections.json`. The `ai-attribution-setup` skill routes
adopters through the projection sync and scan commands owned by
`customizing-copilot:reviewing-customizations`; setup guidance must not carry a
second prose copy.

Do not remove or shrink a fuller pre-existing static publication policy until
the projected fallback is installed and every known hook-less launch path for
the adopting repository has been validated. The scanner reports old
`ai-attribution:static-fallback` regions as legacy and never removes them.
Once both preconditions hold, remove only redundant prose manually and preserve
stricter repository-owned invariants.

## Failure behavior

Malformed or unreadable optional config never blocks startup. The hook writes
one concise diagnostic per rejected bounded config and retains the safe generic
policy. Malformed or missing launch payload emits `{}` with a concise
diagnostic. The hook always writes exactly one JSON object to stdout.
