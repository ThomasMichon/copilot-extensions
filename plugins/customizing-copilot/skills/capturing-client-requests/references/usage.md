# Local client request capture

Back to [capturing-client-requests](../SKILL.md).

## What is captured

`capture-client-request.py` writes the complete received HTTP JSON **body bytes**
to the explicitly selected new file. Messages, instructions, tools, options,
Unicode, whitespace and ordering remain as the client sent them; the tool
does not reconstruct, redact or tokenize the request. HTTP headers, including
the fresh per-run dummy credential, are not recorded.

The file may contain private information or credentials embedded in harness
content. Use a private, nonsynced local directory outside a repository.
On POSIX the new file is created with mode `0600`; on Windows it inherits the
selected directory's ACL, so choose an operator-private directory. UNC and
mapped Windows network drives are refused. The operator must also avoid
network-mounted or synced output directories on other platforms. No encryption
or automatic deletion/retention policy is imposed.

The command never transmits the captured body to a real inference provider.
Do not paste it back into an online assistant merely to inspect it: that would
send it off-machine. Use a local editor or explicitly approved local analysis.

## Invocation

Enable the `customizing-copilot` plugin and load `capturing-client-requests` to
obtain its actual skill base. The script runs directly from the marketplace
payload with Python 3.11+; it does not depend on a source checkout, a registered
harness, a sibling plugin, a tokenizer or a service install.

PowerShell (replace both executable paths with the actual installed paths):

```powershell
& $python "$skillBase\scripts\capture-client-request.py" `
  --copilot-command $copilotExe `
  --working-directory $harnessDirectory `
  --output "$privateCaptureDirectory\request.json"
```

POSIX:

```bash
"$python" "$skill_base/scripts/capture-client-request.py" \
  --copilot-command "$copilot_executable" \
  --working-directory "$harness_directory" \
  --output "$private_capture_directory/request.json"
```

The working directory and output parent must already exist. Existing output
files, including symbolic links, are never overwritten. On Windows choose the
native CLI executable rather than a `.cmd`, `.bat` or PowerShell shim.
The selected client must support `--session-id` and the resolved
`--model`/`--reasoning-effort`/`--context` flags. Native CLI `1.0.100-1` is
qualified by the synthetic integration case; unsupported flags fail rather
than being silently removed.

| Argument | Default / meaning |
|----------|-------------------|
| `--copilot-command` | `copilot` resolved through PATH; an explicit native executable pins the client |
| `--working-directory` | Required harness directory |
| `--output` | Required new local file; selecting it requests raw persistence |
| `--prompt` | A short no-tool acknowledgement request; use synthetic text for composition debugging |
| `--model` | Override the operator's current model; there is no invented fixed-model fallback |
| `--reasoning-effort` | Override the persisted `effortLevel` |
| `--context` | Override the persisted `contextTier` |
| `--timeout-seconds` | 300; finite positive overall client/request deadline |
| `--agent-worktrees-command` | Optional exact catalog command for registry-backed marketplace inventory; never a bare global command lookup |

Model precedence is explicit `--model`, then inherited `COPILOT_MODEL`, then
an explicit `COPILOT_PROVIDER_MODEL_ID`, then the operator's `settings.json`
model for an ordinary launch. A selected custom provider without an identified
model requires `--model`; it must not silently borrow an ordinary cloud-model
default. Effort/context use explicit overrides
then persisted `effortLevel`/`contextTier`, expressed as native CLI flags.
The settings root is `COPILOT_HOME` when selected, otherwise `~/.copilot`.
Missing or malformed preferences fail clearly if the required model cannot be
resolved; supply the current model explicitly rather than guessing a small
replacement. A launcher/profile or ephemeral interactive model selection that
is not represented by these inputs must be expressed through the overrides.
No settings file is edited. The model still points to the local fake provider,
not a paid inference request.
All three explicit preference overrides can be resolved without depending on
the persisted preference file. Other native CLI startup/configuration checks
still apply.

The tool starts a fresh noninteractive `-p` session with an owned session ID,
custom instructions,
plugins and builtin MCPs left enabled. It keeps the operator's ordinary home,
settings and custom-instruction environment. Repository trust and configuration
therefore still affect what loads. Pre-trust the intended harness through the
CLI's normal workflow; the tool does not silently grant blanket tool or path
permissions or bypass trust. It sets an OpenAI Chat Completions fake provider,
offline mode, `--no-remote` and `--no-auto-update` only for the child.
Inherited provider overrides, ambient GitHub token variables and proxy
variables are removed from that child, and loopback bypasses proxies.
These differences, the selected model and `-p` mode can change composition;
the artifact is evidence for this invocation, not every headed/resumed launch.

The listener is exclusively bound to an OS-assigned IPv4 loopback port, accepts
one authenticated `POST /v1/chat/completions` of at most 16 MiB and returns
`LOCAL_REQUEST_CAPTURE_COMPLETE` as a terminal streaming or nonstreaming
completion. There is no bind override, endpoint attachment, forwarding,
shared-service mode or daemon mode. A different local client does not know the
fresh per-run credential. Equally privileged local software is not an
adversarial security boundary.

The selected harness can execute its usual startup hooks and MCP servers.
They retain their ordinary filesystem/network capabilities and may create
their own logs. **Offline mode and loopback binding do not impose an OS
network sandbox.** This tool does not alter firewall settings or promise that
arbitrary harness code cannot transmit data. Use only a trusted harness;
independent OS isolation is appropriate when stronger guarantees are needed.

## Results and failures

Stdout contains one JSON metadata object with schema
`copilot-extensions.local-request-capture`, version `1`, status,
`capture_path`, `captured_bytes` and, when available, `client_exit_code`.
It never contains the prompt, body, request headers or raw CLI output.
Exit `0` requires `status=captured`; failures exit `1`, and argument parsing
errors exit `2`.

The CLI's stdout/stderr are discarded rather than copied to a public log.
CLI-managed local session state/logs still follow the CLI's own behavior.
On timeout the tool closes its listener and terminates/reaps only the CLI
process it launched, not existing services or unrelated processes. It does
not claim to retire independently spawned hook/MCP services.

No-body failures remove only the tool's newly reserved empty output. A complete
request remains available even if completion delivery or subsequent client exit
fails; inspect `captured_bytes` separately from overall success. Unexpected
credentials/routes/framing, invalid or incomplete JSON, oversize bodies,
timeouts and nonzero client exit fail explicitly without echoing private data.
Infrastructure exceptions report their class rather than potentially sensitive
exception text. Do not repeatedly rerun a failed real-harness capture without
diagnosing the selected CLI's supported custom-provider contract.

## Default local interpretation

Successful captures also write an exclusive companion file: `request.json`
produces `request.analysis.json`. Stdout adds its path and resolved launch
preferences, not the contents of either artifact. Both filenames must be
unused before launch. An analysis failure is nonzero and preserves a complete
raw request, while removing only the tool's incomplete companion output.
`analysis_warning_count` exposes partial inventory/interpretation warnings
without disclosing their private paths; detailed reasons remain in the report.

The report schema is `copilot-extensions.client-request-analysis`, version `1`.
It distinguishes original wire JSON bytes from decoded UTF-8 message bytes
and documented canonical-JSON metrics. Names and filesystem paths are still
private metadata; descriptions, instruction bodies and memory bodies are not
echoed into the report.

| Section | Interpretation |
|---------|----------------|
| `instruction_files` | Candidate file/body content matches and selector references, with pointers, transformations and ambiguous aliases; not proof a particular file was read |
| `skills` | Complete rendered name/description/location records versus names-only advertisements, with source metadata correlations when available; not loaded skill bodies |
| `mcp_servers` / `tools` | Observed full schemas or advertisements and configured-name/namespace correlations; no claim that a server was connected or that naming authenticates ownership |
| `builtin_tools` | Conservative public builtin-name matches, with collisions and unknown ownership retained; the allowlist is not a completeness proof |
| `subagents` | Rendered delegation records and enum choices, distinguished from source frontmatter, permissions, model configuration and execution |
| `discovery` | Observed tool/task/agent/skill/catalog search or selection surfaces, parameter types, enum/free-string capabilities; not search results or the whole deferred catalog |
| `memories` | Observed marker candidates separately from exact totals; absence of a supported serialization boundary means unknown, not zero |
| `builtin_prompt` | Exact system-message bytes, source-matched union and unclassified remainder; exact builtin bytes remain unknown without authenticated origin boundaries |

Instruction matching accepts full text or frontmatter-stripped bodies and
explicitly labels CRLF normalization and trimmed edge newlines. It includes captured message text and
tool descriptions. Overlapping and identical matches do not double-count
system attribution. Match/evidence limits and truncated accounting are visible
in warnings and completeness fields; truncated occurrence counts are lower
bounds rather than additional full scans merely to count omitted evidence.
Incomplete unions are lower bounds and
their remainders are upper bounds, not exact measurements.

Candidate discovery reuses the customization scanner's repository, personal,
custom-instruction-directory and enabled-plugin paths. An owned capture's
session ID additionally selects its session-scoped instruction files.
Filesystem candidates are observed at analysis time, not an immutable record
of what the client loaded. Reads are limited to 8,192 candidate files, 2 MiB per
file and 64 MiB total, with explicit skipped/truncated coverage.
Inventory never runs hooks or contacts an MCP server. Registry-backed
marketplaces that require a catalog command are reported unavailable unless
`--agent-worktrees-command` supplies that exact command; there is no implicit
PATH fallback or sibling-service requirement.
Relative custom-instruction paths are resolved from the selected harness,
not the analyzer's caller directory. MCP candidates include wrapped or bare
project maps along the working-directory/repository chain, user configuration
and enabled-plugin inline or declared-file sources. Closest project definitions
win, and `.mcp.json` takes precedence over `.github/mcp.json` in the same folder.
Candidate MCP configuration and plugin-manifest reads share the content-read
budget. MCP names/correlations also have collection and expansion quotas;
unexamined candidates leave ambiguity unknown rather than proving uniqueness.

Interpret a saved request without another client/provider invocation:

```powershell
& $python "$skillBase\scripts\analyze-client-request.py" `
  --request "$privateCaptureDirectory\request.json" `
  --working-directory $harnessDirectory `
  --output "$privateCaptureDirectory\request.analysis.json"
```

The offline command accepts optional `--session-id` (the captured client's
session ID) and `--agent-worktrees-command`. Without an explicitly identified
session, it does not guess among other sessions by recency. It does not modify
the input request. Its output parent must exist and its output file must be
new; exit zero reports `status=analyzed`.
The offline entrypoint requires a Chat Completions `messages` array; it does
not reinterpret another protocol's input as an empty conversation.

Neither capture nor interpretation exposes server-side additions by GitHub's
normal hosted provider. Unknown memory or builtin-prompt attribution cannot be
repaired by relabeling arbitrary system text or by assuming inventory proves
model admission.
