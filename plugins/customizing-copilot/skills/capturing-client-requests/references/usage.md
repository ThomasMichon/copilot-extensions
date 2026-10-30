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

| Argument | Default / meaning |
|----------|-------------------|
| `--copilot-command` | `copilot` resolved through PATH; an explicit native executable pins the client |
| `--working-directory` | Required harness directory |
| `--output` | Required new local file; selecting it requests raw persistence |
| `--prompt` | A short no-tool acknowledgement request; use synthetic text for composition debugging |
| `--model` | `gpt-4.1`, a fake-provider model identity, not a paid inference request |
| `--timeout-seconds` | 120; finite positive overall client/request deadline |

The tool starts a fresh noninteractive `-p` session with custom instructions,
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

This does not expose server-side additions by GitHub's normal hosted provider,
nor prove which locally inventoried instruction sources were admitted without
examining the actual artifact.
