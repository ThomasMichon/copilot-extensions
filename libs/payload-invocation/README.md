# Payload Invocation

Canonical generator for payload-local plugin command shims.

A runtime plugin declares `payload-invocation.json`; the generator renders
equivalent POSIX, PowerShell, and CMD entry points into that plugin's checked-in
`bin/` directory. The installed marketplace payload therefore carries the
command that invokes its own runtime instead of relying on a same-named global
command found through `PATH`.

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
