#!/usr/bin/env bash
# emit-mesh-pointer -- agent-ssh sessionStart hook (bash).
#
# Emits a SUCCINCT pointer as {"additionalContext": "..."} telling the agent that
# the calling repo declares an SSH machine mesh (machines.yaml) and to run
# the payload command catalog's `mesh-status` action for the full table -- rather
# than dumping the whole mesh
# into every session. cwd-gated: fires only when the current git repo has a
# machines.yaml at its root; otherwise emits {} so a globally-loaded plugin never
# leaks one repo's mesh into an unrelated repo.
#
# Presence-gated only -- it does NOT parse machines.yaml (keeps sessionStart fast
# and robust; all rendering is deferred to the payload-local command).

emit_empty() { printf '%s\n' '{}'; exit 0; }

root=$(git rev-parse --show-toplevel 2>/dev/null | head -n 1)
[ -n "$root" ] || emit_empty
[ -f "$root/machines.yaml" ] || emit_empty

read -r -d '' md <<'MD'
## SSH machine mesh available for this repo

This repo declares an SSH machine mesh in `machines.yaml`. For the per-host
**role + reachability + aliases**, append **`mesh-status`** to the exact `argv`
in the agent-ssh session command catalog (`--summary` for one line, `--json` for
structured). Reach a host interactively with `ssh <alias>` (the aliases are
listed by that action). Reachability is **dtssh** -- live only while the target
is powered on and logged in; `ssh.ready` is the operator's declared state, so
append `verify <alias>` to the same catalog `argv` to probe a host live.
MD

if command -v python3 >/dev/null 2>&1; then
  printf '%s' "$md" | python3 -c 'import json,sys; print(json.dumps({"additionalContext": sys.stdin.read()}))'
else
  esc=$(printf '%s' "$md" | sed ':a;N;$!ba;s/\\/\\\\/g;s/"/\\"/g;s/\n/\\n/g')
  printf '{"additionalContext": "%s"}\n' "$esc"
fi
exit 0
