# Bounded selected-session inspection

Back to [Monitor agent fleet](../SKILL.md) and [fabric readers](fabric-readers.md).
This is an exceptional diagnostic,
**not a recurring fleet status reader**. Continue using native passive roster
and status readers each cycle. Do not sweep guidance bodies or reconstruct this
operation with PowerShell objects, recursive listings or deep serializers.

Use the already available Python 3.10+ interpreter and this skill's
`scripts/read_session_guidance.py` on the host that owns the selected session.
There is no runtime installation, service, transport or arbitrary-command API.
If the payload/interpreter is unavailable there, report a coverage gap; do not
copy a collector remotely, install anything, autojoin or repair the host.

```text
<python> <skill-directory>/scripts/read_session_guidance.py --session-state-root <absolute-session-state-directory> --session-id 00000000-0000-0000-0000-000000000001
<python> <skill-directory>/scripts/read_session_guidance.py --session-state-root <absolute-session-state-directory> --session-id 00000000-0000-0000-0000-000000000001 --guidance example-plugin
```

Default behavior lists only the selected session directory and its immediate
`instructions` directory. Each repeated `--guidance <provider>` explicitly
allowlists one `instructions/<provider>/session-guidance.instructions.md`.
The reader lists that provider directory **before** reading the named file.
It never recursively descends or reads other files, history or other sessions.
Nested directories inside a selected provider are reported as depth gaps, not
followed. Up to eight providers are accepted. Provider identifiers are lowercase
ASCII letters/digits, underscores or hyphens, beginning with a letter/digit,
at most 64 characters. Sessions must have lowercase canonical UUID syntax.

Only when requested, `--maintenance-file <absolute-path-to-maintenance.json>`
adds that exact notice, listing its parent first. The filename must be
`maintenance.json`. Its UTF-8 bytes are returned as text, **not parsed as a
nested JSON object**. This option is not a generic arbitrary-file reader.
Names and selected bodies may be private; keep output in the audit owner's
approved private destination.

## Enforced limits (not tunable upward)

| Resource | Default and maximum |
|---|---|
| Execution | 5 seconds from `main` entry through output flush; `--timeout` may reduce it to 0.001–5 seconds |
| Directory enumeration | 64 yielded entries per directory, 256 total; at most 32 directory attempts |
| Instruction traversal | Depth 2 below `instructions`: provider directory and its immediate entries; no deeper traversal |
| Input | 8,192 bytes per file and 24,576 bytes across all actual reads, including growth checks |
| Output | 32,768 ASCII JSON bytes including final newline; 2,048 bytes reserved for summary/reasons |
| Arguments | 32 arguments, 8,192 characters combined; absolute paths at most 4,096 characters / 32 components |
| Entry names | 128 characters; control characters refused |

Enumeration is streamed, not loaded/sorted and then truncated. Reaching an
entry boundary conservatively reports partial coverage, even if it happens to
be the final entry: there is no extra unbudgeted lookahead. Huge files are
refused before opening. Growth, invalid UTF-8, special files, symlinks and
Windows junctions/other reparse points are coverage gaps. Every existing
ancestor is checked for links. This is a read-only, non-atomic snapshot for
cooperative local state, not protection against a hostile concurrent path
replacement by an equally privileged writer.

Memory is **algorithmically bounded**, not a promised OS working-set/RSS cap:
all input-dependent buffers, directory maps, names and serialized records have
fixed limits; the largest single text encoding expansion is bounded by the
per-file limit. Python's interpreter/allocator baseline is additional. No
PowerShell objects, arbitrary-depth JSON parsing, external readers or child
processes are used. No OS-specific containment fallback is needed because
there is no child tree to contain.

A host-local daemon watchdog thread calls `os._exit(124)` at the deadline,
including while the main thread blocks in filesystem or stdout I/O. It is
armed before argument parsing and remains armed through output flush. This
deadline lives on the **collecting host**, not in the SSH client; losing the
client cannot leave an indefinite collector. The process starts no children
and natural exit leaves no watcher running. Interpreter startup/import time
and OS scheduling/uninterruptible kernel delays are not real-time guarantees.
An outer caller/SSH deadline is useful transport protection, never a
replacement for this host-local bound.

## Consumer contract

Contract `agent-roles.selected-session-guidance/v1` emits only JSON primitives
in a bounded envelope with `records`, `coverage`, `gaps`, `entries`,
`directories` and `bytes_read`. Consumers must recognize this contract and
tolerate additive fields; unknown contracts are unavailable evidence.

- Exit **0**, `coverage: "selected-scope"`: the selected scope was read, not
  evidence of fleet health or complete session context.
- Exit **2**, `coverage: "partial"`: refused, unavailable, changed or truncated
  evidence. Record the reason codes and retain the coverage gap.
- Exit **124**: host-local deadline; output may be empty/incomplete. Discard it
  as evidence, report timeout/unknown, and do not infer health.
- Missing/invalid JSON, any other nonzero exit or transport failure is likewise
  unavailable evidence, never a healthy/empty result.

Errors contain fixed reason codes, not exception messages or file bodies.
There is no retry or limit escalation. The command's own help/argument errors
are bounded too. A missing command on an older payload is an explicit coverage
gap; do not downgrade to an unbounded shell recipe.
