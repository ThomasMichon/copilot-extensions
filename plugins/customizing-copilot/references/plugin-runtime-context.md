# Plugin Runtime Context

Use runtime-provided context instead of assuming where a plugin was installed.
The payload may come from a marketplace cache, `--plugin-dir`, an in-repository
marketplace, or a future layout. A script should be able to locate its own
payload and the target repository without coupling those two paths.

## Context matrix

| Scriptable surface | Script / payload location | Copilot target path | Copilot session ID | Child process CWD |
|---|---|---|---|---|
| Plugin command hook | `COPILOT_PLUGIN_ROOT`, `PLUGIN_ROOT`, or `CLAUDE_PLUGIN_ROOT`; `${PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_ROOT}` expand in hook config | Hook JSON on stdin: `cwd` is the session working directory. `COPILOT_PROJECT_DIR` / `CLAUDE_PROJECT_DIR` is the resolved repository root, falling back to the session working directory when no repository root resolves. | Hook JSON on stdin: `sessionId` | Plugin payload root by default |
| Plugin stdio MCP server | `COPILOT_PLUGIN_ROOT`, `PLUGIN_ROOT`, or `CLAUDE_PLUGIN_ROOT`; `${PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_ROOT}` expand in config | **Not supplied automatically.** Use a required tool argument, adopter-provided env value, or project-scoped registration. | `COPILOT_AGENT_SESSION_ID` | Plugin payload root, including when `cwd` is omitted |
| JavaScript session extension | `import.meta.url` is authoritative for the current module; `EXTENSION_PATH` is the absolute entry-module path | `process.cwd()` at launch; then `session.context_changed` event `data.cwd` | `SESSION_ID` | Session working directory **at extension launch**; it does not change after `/cd` |
| Plugin LSP server | `COPILOT_PLUGIN_ROOT`, `PLUGIN_ROOT`, or `CLAUDE_PLUGIN_ROOT`; use `${PLUGIN_ROOT}` in config | `${workspaceFolder}` or `${COPILOT_PROJECT_DIR}` during config expansion | **Not supplied** | Direct `command` launch: project root. `bash` / `powershell` launch: plugin payload root. Set `cwd` explicitly to avoid depending on this difference. |

The plugin data directory is separate from the immutable payload:

- Hooks receive `CLAUDE_PLUGIN_DATA` and `COPILOT_PLUGIN_DATA`.
- Agent Plugins specification MCP servers receive `PLUGIN_DATA`,
  `CLAUDE_PLUGIN_DATA`, and `COPILOT_PLUGIN_DATA`. Legacy plugin MCP servers do
  not receive an automatic data directory.
- LSP servers receive `CLAUDE_PLUGIN_DATA` and `COPILOT_PLUGIN_DATA`.
- JavaScript extensions receive no plugin-data variable; store durable state in
  an explicitly configured user-data location rather than beside the module.

## Command hooks

The runtime expands plugin placeholders in the hook configuration and injects
the root/data variables into the child environment. It also defaults an absent
or empty plugin-hook `cwd` to the plugin payload root. That default makes
relative payload access work, but it means the hook process CWD is **not** the
target repository.

Read the event payload from stdin for the exact session context:

```json
{
  "sessionId": "00000000-0000-0000-0000-000000000000",
  "cwd": "/path/to/the/session/working-directory"
}
```

Use:

- `COPILOT_PLUGIN_ROOT` for neighboring payload files;
- `COPILOT_PLUGIN_DATA` for mutable plugin-owned state;
- stdin `cwd` for the current Copilot working directory;
- `COPILOT_PROJECT_DIR` for the repository root when a root rather than the
  current subdirectory is required (it falls back to stdin `cwd` when no
  repository root resolves, so its presence alone does not prove this is a
  repository); and
- stdin `sessionId` for session identity.

Do not derive the target repository from `process.cwd()` in a plugin hook. Do
not hardcode `~/.copilot/installed-plugins/...`.

Portable hook registration:

```json
{
  "type": "command",
  "bash": "python3 \"${PLUGIN_ROOT}/hooks/context.py\"",
  "powershell": "python \"$env:COPILOT_PLUGIN_ROOT\\hooks\\context.py\""
}
```

The PowerShell command reads the already-injected environment variable at
execution time; the Bash command demonstrates loader-time placeholder
expansion. Either strategy survives a relocated payload.

## Stdio MCP servers

Plugin-backed local MCP configs receive the plugin-root variables in both the
configuration expansion environment and the child process environment. The
plugin loader also defaults an omitted `cwd` to `${PLUGIN_ROOT}`:

```json
{
  "mcpServers": {
    "example": {
      "type": "local",
      "command": "node",
      "args": ["${PLUGIN_ROOT}/server.mjs"]
    }
  }
}
```

The server can then use:

```javascript
const pluginRoot = process.env.COPILOT_PLUGIN_ROOT;
const sessionId = process.env.COPILOT_AGENT_SESSION_ID;
```

`process.cwd()` also identifies the plugin payload root, not Copilot's target
repository. Plugin MCP config receives no `COPILOT_PROJECT_DIR`, does not
advertise the MCP `roots` capability, and cannot request `roots/list`. The
session ID is useful for correlation but is not itself a target path.

Choose an explicit project-context contract when the server needs repository
access:

1. Prefer a required `cwd` / `projectRoot` tool argument and validate that path
   before use.
2. For a server deliberately bound to one project, register it at project scope
   rather than contributing it globally from a plugin; a non-plugin local MCP
   config with no `cwd` uses the session working directory.
3. If an adopter supplies a target path through `env`, name and document that
   setting explicitly. Do not mistake an ambient `PWD` or the plugin process CWD
   for session context.

There is currently no zero-configuration runtime channel that gives a
plugin-contributed MCP child both the plugin payload root and Copilot's target
directory. Treat that as a runtime limitation, not something to reconstruct
from the installation layout.

Remote HTTP/SSE MCP servers have no local script process, payload-root
environment, or process CWD. Forward any required context explicitly through a
designed protocol/header rather than treating the local stdio contract as a
remote one.

## JavaScript session extensions

The extension host launches each module in a child process whose initial working
directory is the session working directory. It injects the absolute entry module
as `EXTENSION_PATH` and the owning session as `SESSION_ID`.

Prefer module-relative asset resolution:

```javascript
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(import.meta.url);
const sessionId = process.env.SESSION_ID;
```

`EXTENSION_PATH` is useful to inspect the entry module from shared bootstrap
code, but `import.meta.url` remains correct for every imported module. There is
no `COPILOT_PLUGIN_ROOT` injection for extensions; resolve assets relative to
their module, or walk upward for a manifest only when the plugin root itself is
required.

`process.cwd()` is only a launch-time snapshot. The extension process remains
alive across `/cd`, and Node does not update its CWD when the session changes.
An extension that must follow the live target should retain its own path state
and subscribe after `joinSession(...)`:

```javascript
let targetCwd = process.cwd();

session.on("session.context_changed", (event) => {
  targetCwd = event.data.cwd;
});
```

The context event may first carry `pendingGitContext: true` and then a settled
event with repository metadata. The `cwd` is usable immediately; wait for the
settled event when `gitRoot` or other git context is required. Prefer absolute
operations rooted at the retained value rather than calling `process.chdir()`.

## LSP servers

Plugin LSP config expansion knows both the plugin payload root and the target
project root:

```json
{
  "lspServers": {
    "example": {
      "command": "node",
      "args": ["${PLUGIN_ROOT}/server.mjs"],
      "fileExtensions": {
        ".js": "javascript"
      },
      "cwd": "${COPILOT_PROJECT_DIR}",
      "env": {
        "COPILOT_PROJECT_DIR": "${COPILOT_PROJECT_DIR}"
      }
    }
  }
}
```

Set `cwd` explicitly because direct command and shell-script launch forms have
different defaults. Forward `COPILOT_PROJECT_DIR` through `env` when the child
must retain both the plugin-root and project-root paths.

The runtime does **not** inject the Copilot session ID into an LSP child. Do not
substitute a process ID or an ambient `COPILOT_AGENT_SESSION_ID`; neither is the
runtime-owned session identity for that LSP launch. A plugin that requires
session identity must currently use a hook, MCP server, or JavaScript extension
instead, or treat native LSP session-ID support as a runtime prerequisite.

## Non-runtime scripts

Installer/setup scripts and shell commands copied into skills are not
session-owned runtime entry points. Their CWD and environment belong to the
caller, and no Copilot session ID is guaranteed. Give those scripts explicit
arguments (for example `--plugin-root` or `--project`) rather than borrowing the
contracts above.
