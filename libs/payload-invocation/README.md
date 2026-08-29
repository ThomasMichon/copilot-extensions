# Payload Invocation

Canonical generator for payload-local plugin command shims.

A runtime plugin declares `payload-invocation.json`; the generator renders
equivalent POSIX, PowerShell, and CMD entry points plus one shared
POSIX/PowerShell session-command-catalog emitter shape. The installed
marketplace payload therefore carries the command that invokes its own runtime
instead of relying on a same-named global command found through `PATH`.

## Contract

Generated shims:

- derive and validate the payload from their own file location;
- reject a conflicting `COPILOT_PLUGIN_ROOT`;
- leave a replaceable payload working directory before provisioning or launch;
- resolve the runtime only through the payload's canonical runtime resolver;
- preserve caller arguments and child exit status;
- self-provision only through the same payload's installer/snapshot;
- never scan installed marketplaces or resolve a sibling command through
  `PATH`.

Catalog entries carry an exact `argv` prefix. Callers append arguments without
joining or re-parsing the prefix. Shell examples quote each prefix element
separately and prepend `&` in PowerShell. On POSIX the prefix names the
canonical payload-local executable. On Windows a ready entry pins the absolute PowerShell
7 host, its non-interactive flags, and the canonical payload-local PowerShell
entry point. Windows PowerShell 5.1 cannot preserve the full argument domain and
therefore emits the entry as unavailable rather than advertising a lossy
invocation. A command with no full-fidelity PowerShell implementation may
explicitly select `cmd`. Catalog guidance forbids replacing any prefix element
with a global or `PATH` binstub.

Catalog emitters read the `sessionStart` payload from stdin. When it contains a
session ID, an atomic per-user temporary marker suppresses an identical catalog
from being emitted more than once in that session. Distinct payload paths,
command inventories, availability states, or sessions remain independent.

`outputDir` defaults to `bin` and may name a nested payload-only directory when
a plugin still uses its historical top-level `bin/` files as legacy global
wrapper sources. Catalog emitters always publish the exact generated command
path. `windowsCatalogShim` defaults to `powershell`; a plugin whose stdio
contract requires entry through a native process may select `cmd`, in which case
the Windows catalog names the generated `.cmd` and reports `shell: "cmd"`.
Callers invoke that path through the platform shell and should pass structured
input through stdin or a request file rather than inline CMD arguments.
`provisionMode` defaults to `snapshot`, where first use stamps the owning
payload and provisions through its published snapshot. A self-staging installer
may select `direct`; the payload shim then invokes that same installer with
`provision` and never consults a shared snapshot pointer.

The initial manifest keeps a legacy runtime-root name because installation-cell
runtime placement belongs to Phase 3. Replacing that root with the installation
context must not change the generated invocation surface.

## Usage

```bash
python libs/payload-invocation/generate.py \
  plugins/agent-index/payload-invocation.json

python libs/payload-invocation/generate.py --all --check
```

Generated files are committed. CI runs `--all --check` so templates, manifests,
and payload copies cannot drift.
