# Ordinary Bridge CLI launch debugging

This is a **default-off, user-level observation switch**, not another launcher,
pause mechanism or permission check. It observes the existing `ordinary_bridge`
create/status/resume/send `subprocess.run` calls. It does not trace other
Dispatch backends, change their flags, input, environment, result parsing,
ownership, retries or task transitions. No Bridge package import is required.

## Enable and find traces

Put this file at `<dispatch-install-dir>/launch-debug.json`:

```json
{"bridge_cli_trace": true}
```

The root is `AGENT_DISPATCH_INSTALL_DIR`, otherwise `~/.agent-dispatch`;
this is **not repository configuration**. Remove the file or set `false` to
disable. It is read at each observed call, so existing supervisors see edits
without restarting. Invalid JSON/schema or unreadable configuration logs a
warning and disables optional tracing, never launch.
UTF-8 with or without a Windows byte-order mark is accepted.

For an already-planned foreground invocation, the conventional environment
override takes precedence:

```powershell
$env:AGENT_DISPATCH_BRIDGE_CLI_TRACE = '1'
# Run the same Dispatch command you were already diagnosing.
# '0' forces tracing off even when the user file enables it.
Remove-Item Env:\AGENT_DISPATCH_BRIDGE_CLI_TRACE
```

POSIX: `AGENT_DISPATCH_BRIDGE_CLI_TRACE=1 <the-existing-dispatch-command>`.
This changes the caller's tracing setting only; it does not restart a daemon
or propagate a new setting into an already-running process.

Each call writes `<dispatch-install-dir>/logs/bridge-cli/<random-id>.jsonl`,
with a flushed/closed `started` record before execution and a `finished`
record after return, `OSError`, or timeout. It survives the observing caller's
exit. Files/directories request POSIX modes 0600/0700; Windows inherits the
installation's user-private ACLs. Keep this runtime root private. Traces
are opt-in diagnostic artifacts with **manual retention**: disable the switch
after diagnosis and remove old trace files when no longer needed. Sink errors
warn through the existing `agent-dispatch` logging hierarchy; they do not veto
launch. No trace data goes to CLI stdout.

Recorded fields:

- Actual resolved native `prefix`, argument array, inherited `cwd`, configured
  timeout, wall start time, monotonic elapsed time and observed return code.
- An allowlisted non-secret caller context: machine hostname, installation root,
  and existing session, owner, pane and terminal-session references when present.
  No environment dictionary or authentication values are recorded.
- `argv_exact` / `cwd_exact` explicitly distinguish unchanged metadata from
  redacted or truncated values. Quotes, apostrophes, punctuation and repeated
  spaces are ordinary JSON argument/path content, not shell syntax to discard.
  Credential patterns and known credential values use the shared subprocess
  diagnostic redactor extracted from Bridge; actual changes mark metadata
  non-exact instead of silently reconstructing it.
- Stdin source (`none` or `frozen_startup_invocation`), retained reservation
  reference, UTF-8 byte size and SHA-256. **Never stdin/prompt content.**
  These raw byte fields remain unchanged. Additive `json_status` is `valid`,
  `invalid` or `absent`; `json_sha256` is null unless valid. The JSON digest
  hashes UTF-8 `json.dumps(json.loads(input), sort_keys=True, separators=(",", ":"),
  ensure_ascii=False, allow_nan=False)`. It compares parsed JSON values across
  object-key ordering/formatting, not historical byte identity; it is not an
  independent authorization or receipt.
- **Separate stdout and stderr** sizes/digests and causal diagnostics.
  Each stream declares `capture`: `complete` for observed completed-process
  output, `partial` for bytes captured before a timeout, or `unavailable` when
  no stream was captured. Unavailable streams have null size/digest; an actual
  observed empty stream has size zero and the SHA-256 of empty bytes.
  Stdout selects bounded, redacted `error`/`message` strings (800 characters)
  and `reason`/`stage`/`code` strings (160 characters), including nested error
  objects. Unknown reason codes are retained, not reduced to fixed phrases.
  No opaque stdout envelope or launch-plan environment is dumped. Parsing
  examines at most 32 independently framed JSON values and refuses oversized
  (>65,536-character) or malformed outer envelopes.
  Stderr retains separately redacted, control-normalized text (1,600 characters);
  JSON envelopes select the same diagnostic fields. If prompt/environment
  context is detected anywhere in stderr, only selected structured diagnostic
  fields remain; the entire raw text is omitted, including warning-prefixed or
  malformed multiline dumps. Existing ordinary status labels
  and phrase indicators remain supplemental, not error classification.
  The shared redactor strips ANSI/control characters and known credential
  assignments, provider-token shapes, URL credentials, authorization values and
  credential environment values. Known frozen stdin/prompt values are also
  redacted. This is established best-effort sanitization, not a proof that
  arbitrary third-party output cannot contain an unfamiliar secret encoding.
  Keep traces private and inspect before sharing them.
- A timeout records `unknown_timeout` with no exit code. It does **not**
  assert the remote launch failed, terminate a retained agent or replay a call.
  A started record without a finished record is also an unknown outcome,
  not proof the command did not run.

## Reproduce the actual calls, not a guessed shell wrapper

First inspect the trace and the existing task/reservation without mutating it:

```text
<dispatch-command> show <task-id>
<dispatch-command> reservations list --task <task-id>
```

Use the exact attributable Dispatch command from the session catalog. Do not
paste full reservation output into logs or reports: it can contain the frozen
prompt. The trace's stdin reference selects the retained reservation; its
`agent_info.startup_invocation` is the source of truth.
Dispatch prints JSON by default and has no global `--json` switch; Bridge's
separate `--json` option below is correct for Bridge.

Run each chosen stage **once, deliberately**, in the recorded cwd, with the
recorded prefix and argument array, inherited credentials/environment, and
the recorded timeout. The trace does not save the environment, so matching it
is an operator responsibility; a different current environment is not a
historically identical call. Do not replace the resolved prefix with a
guessed `.cmd`, PATH lookup, or shell command string.

For no-stdin calls, PowerShell can invoke an exact trace like this:

```powershell
$call = Get-Content -LiteralPath '<trace-file>' -First 1 | ConvertFrom-Json
if (!$call.argv_exact -or !$call.cwd_exact -or $call.stdin.source -ne 'none') {
    throw 'Omitted metadata or stdin: use the original retained source, not this recipe'
}
Set-Location -LiteralPath $call.cwd
$command = @($call.prefix) + @($call.argv)
$exe = $command[0]
$rest = $command[1..($command.Count - 1)]
& $exe @rest
```

This foreground shell example exposes both streams directly and does not
implement Python's recorded timeout; interrupting it does not prove the remote
outcome. Normal automated calls still retain their existing headless,
bounded subprocess behavior. Typically the recorded stages are:

```text
<prefix...> --json create <selector> --creation-key <retained-key> --min-daemon-protocol <recorded-version> [--charter <name>]
<prefix...> --json status <retained-session> [--full]
<prefix...> --json resume <retained-session-or-worktree> --strict
<prefix...> --json send <retained-session> --expected-native-session-id <retained-native> --native-invocation-file - [--receipt-only] --no-wait
```

These are explanatory shapes; only the trace contains the historically
executed argument array. Do not run every shape in sequence: follow the
actual observed branch. A new create needs ordinary coordinator storage/readback
before sending, not manual database edits.

For a send, use the existing frozen invocation from an authenticated coordinator
reservation read, **in memory** in the Dispatch runtime. The serialization used
at the boundary uses `json.dumps(..., ensure_ascii=False)`, but the initial
send can serialize the locally prepared mapping while storage sorts its keys.
Retained readback therefore preserves the JSON values without necessarily
preserving the original insertion order or raw digest. Distinguish those two
claims explicitly:

```python
import hashlib
import json
import subprocess
from agent_dispatch.ordinary_invocation import native_payload
from agent_dispatch.procutil import no_window_kwargs

# call = the chosen trace's started record
# reservation = the current authenticated coordinator read for call["stdin"]["reference"]
payload = json.dumps(
    native_payload(reservation["agent_info"]["startup_invocation"]), ensure_ascii=False,
)
assert call["argv_exact"] and call["cwd_exact"]
raw = payload.encode("utf-8")
observed = call["stdin"]
if (len(raw) == observed["bytes"]
        and hashlib.sha256(raw).hexdigest() == observed["sha256"]):
    reconstruction = "byte-exact"
else:
    assert observed.get("json_status") == "valid" and observed.get("json_sha256"), (
        "Raw mismatch: old/invalid trace has no JSON-value digest; exactness is unproven"
    )
    canonical = json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == observed["json_sha256"], (
        "Retained JSON values differ from the observed input"
    )
    reconstruction = "JSON-value-exact (not byte-exact)"
print(reconstruction)  # Classification only; never print the payload.
# Only after choosing to perform this real operation, not as an automatic replay:
result = subprocess.run(
    [*call["prefix"], *call["argv"]], cwd=call["cwd"], input=payload,
    capture_output=True, text=True, encoding="utf-8", check=False,
    timeout=call["timeout_seconds"], **no_window_kwargs(),
)
# Inspect result.returncode, result.stdout and result.stderr separately, privately.
```

This reuses `native_payload`: do not copy a prompt into a debug log, serialize
the entire stored envelope, mint a new invocation key or recreate a reservation.
A raw digest mismatch must never be described as byte-exact, even when the
additive JSON-value digest matches. An old trace without that digest cannot
silently waive a failed raw check: recover the original byte order/source, or
leave the reconstruction unproven. A JSON-value mismatch, missing frozen source
or omitted metadata also leaves it unproven. This is diagnostic evidence, not a
change to execution, admission, ownership or receipt checks. Do not change the
frozen invocation or actual send serializer just to force a hash match.
After timeout inspect the retained
session/status and ordinary receipt before deciding what to do; tracing adds
no automatic retry.

## Manually pull and start ordinary work

Inside the **actual worker's own worktree/session**, with its normal Dispatch
route and credentials, use the ordinary CLI:

```text
<dispatch-command> worktree-status --repo <lane>
<dispatch-command> claim --task <task-id> --evaluation --repo <lane>
<dispatch-command> start <task-id> --owner-session-id <actual-native-session-id>
<dispatch-command> show <task-id>
```

Add the task's required `--capability <name>` flags to `claim` when applicable.
Omitting `--task <task-id>` pulls one eligible task in that lane. Identity resolves from
the actual CWD; do not spoof a different worker or invent a native session id.
Only start after the claim succeeded as that worker. Existing targeting, holds,
reservation/native ownership and concurrency fences still apply. `consume` is
the handoff lifecycle shortcut, **not** the ordinary-task pull command.

There is no new pause-before-send switch. If stepwise inspection requires
pausing an active supervisor between create and send, that unsupported gap
remains explicit; the trace adds neither a pause state machine nor authority to
change or steal a retained allocation.
