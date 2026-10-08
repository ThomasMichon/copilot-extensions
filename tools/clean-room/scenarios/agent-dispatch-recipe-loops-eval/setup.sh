#!/usr/bin/env bash
# agent-dispatch-recipe-loops-eval/setup.sh -- establish the STARTING STATE for
# the Tier-E live-forge recipe-loops eval (agent-dispatch-recipe-library effort,
# ThomasMichon/copilot-extensions#4691 Validation Plan's live-fixture item).
#
# This is SETUP, not the thing under test: it installs agent-dispatch plus its
# two genuine documented headless-embody runtime dependencies (agent-bridge,
# agent-worktrees), first-session-provisions all three, authenticates `gh` from
# the SAME injected Copilot token (no separate credential), and clones the REAL
# fixture repository named by $CR_FIXTURE_REPO -- a repo that ALREADY carries
# the four recipe declarations under .copilot-extensions/agent-dispatch/registrar/
# plus real fixture issues and efforts. It never authors or mutates that
# content, and it never registers the repo with agent-dispatch's own
# registrar -- that registration (and everything after it) is the eval itself.
# Its phases are setup TELEMETRY (pass/info/jam), never the eval verdict -- the
# verdict comes from the driven-agent transcript + clean-room-judge. Sources
# the shared lib for uniform legibility. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-dispatch"
# agent-bridge is a genuine RUNTIME DEPENDENCY of headless embody, not an
# incidental extra: repository-issue-loop's own docs (spawn-supervisor.md)
# document that a headless lane's `--headless-agent` (default `task-worker`)
# must be "actually registered with agent-bridge on the host where the body
# will spawn" -- so installing+registering it here is legitimate STARTING-
# STATE arrangement (same category as installing `gh`), not a shortcut around
# the thing under test (the repository-issue-loop/effort-driver-loop CLI
# lifecycle itself).
BRIDGE_PLUGIN="agent-bridge"
# agent-worktrees is likewise a genuine RUNTIME DEPENDENCY of headless embody:
# the supervisor's spawn path (embody.py's create_worktree) always shells out
# to `agent-worktrees create` to stand up a fresh parallel worktree before
# launching the headless Copilot session there -- this is true for EVERY
# headless lane, not specific to this fixture, and mirrors how a real
# production adopter repo already uses agent-worktrees as its standing
# worktree-session manager long before adopting repository-issue-loop.
WORKTREES_PLUGIN="agent-worktrees"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
INSTALL_DIR="$HOME/.agent-dispatch"
FIXTURE_DIR="$HOME/recipe-fixture"
FIXTURE_REPO="${CR_FIXTURE_REPO:-}"
FIXTURE_PRODUCER_LOGIN="${CR_FIXTURE_PRODUCER_LOGIN:-}"

: "${CR_SCENARIO_NAME:=agent-dispatch-recipe-loops-eval}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "role" "starting-state-setup"
cr_meta "fixture_repo" "$FIXTURE_REPO"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_installer_path() {
    local p=""
    if [ -f "$INSTALL_DIR/payload-dir" ]; then
        p="$(tr -d ' \t\r\n' < "$INSTALL_DIR/payload-dir")/scripts/install.sh"
        [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    fi
    p="$INSTALLED_ROOT/$PLUGIN/scripts/install.sh"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/"$PLUGIN"/scripts/install.sh 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

# =========================================================================
phase 0 "environment (fresh machine) + required env"
envdump
if [ -z "$FIXTURE_REPO" ] || [ -z "$FIXTURE_PRODUCER_LOGIN" ]; then
    jam "dispatch-config" "CR_FIXTURE_REPO and/or CR_FIXTURE_PRODUCER_LOGIN not set" "pass both via -PassEnv (or an equivalent host env forward) -- this scenario names no fixture repo of its own"
    cr_finalize
fi
pass "required env present: CR_FIXTURE_REPO=$FIXTURE_REPO, CR_FIXTURE_PRODUCER_LOGIN=$FIXTURE_PRODUCER_LOGIN"
if [ -d "$INSTALL_DIR" ] || [ -d "$HOME/.local/bin" ]; then
    info "pre-existing ~/.agent-dispatch or ~/.local/bin (box not pristine, continuing)"
else
    pass "clean slate: no ~/.agent-dispatch, no ~/.local/bin"
fi

# =========================================================================
phase 1 "install $PLUGIN + $BRIDGE_PLUGIN + $WORKTREES_PLUGIN (headless embody's own documented runtime dependencies)"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "sandbox": { "enabled": false },
  "experimental": true,
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } } },
  "enabledPlugins": { "$PLUGIN@$MARKETPLACE_NAME": true, "$BRIDGE_PLUGIN@$MARKETPLACE_NAME": true, "$WORKTREES_PLUGIN@$MARKETPLACE_NAME": true }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install" -- copilot plugin install "$PLUGIN@$MARKETPLACE_NAME" || true
capture "install-bridge" -- copilot plugin install "$BRIDGE_PLUGIN@$MARKETPLACE_NAME" || true
capture "install-worktrees" -- copilot plugin install "$WORKTREES_PLUGIN@$MARKETPLACE_NAME" || true
if [ -d "$INSTALLED_ROOT/$PLUGIN" ]; then
    pass "$PLUGIN payload present on disk"
else
    jam "npm-registry" "$PLUGIN payload NOT installed (see cr-logs/install.log)" "check marketplace source + node/npm feed"
fi
if [ -d "$INSTALLED_ROOT/$BRIDGE_PLUGIN" ]; then
    pass "$BRIDGE_PLUGIN payload present on disk"
else
    jam "npm-registry" "$BRIDGE_PLUGIN payload NOT installed (see cr-logs/install-bridge.log)" "check marketplace source + node/npm feed"
fi
if [ -d "$INSTALLED_ROOT/$WORKTREES_PLUGIN" ]; then
    pass "$WORKTREES_PLUGIN payload present on disk"
else
    jam "npm-registry" "$WORKTREES_PLUGIN payload NOT installed (see cr-logs/install-worktrees.log)" "check marketplace source + node/npm feed"
fi

# =========================================================================
phase 2 "first-session provision (all three binstubs on PATH)"
_apply_uv_index_fixture
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG+=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
[ -d "$INSTALLED_ROOT/$BRIDGE_PLUGIN" ] && PLUGIN_ARG+=( --plugin-dir "$INSTALLED_ROOT/$BRIDGE_PLUGIN" )
[ -d "$INSTALLED_ROOT/$WORKTREES_PLUGIN" ] && PLUGIN_ARG+=( --plugin-dir "$INSTALLED_ROOT/$WORKTREES_PLUGIN" )
capture "session-provision" -- copilot -p "Reply with the single word: ready." --allow-all --experimental "${PLUGIN_ARG[@]}" || true
sleep 8
if ! bash -lc 'command -v agent-dispatch >/dev/null 2>&1'; then
    installer="$(_installer_path || true)"
    [ -n "$installer" ] && capture "installer-provision" -- bash "$installer" provision || true
fi
if bash -lc 'command -v agent-dispatch >/dev/null 2>&1'; then
    capture "binstub-version" -- bash -lc 'agent-dispatch --version' || true
    pass "agent-dispatch binstub resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-dispatch binstub NOT on PATH after provision" "see agent-dispatch-solo (#649) -- setup cannot proceed"
fi
if ! bash -lc 'command -v agent-bridge >/dev/null 2>&1'; then
    _bridge_installer="$INSTALLED_ROOT/$BRIDGE_PLUGIN/scripts/install.sh"
    [ -f "$_bridge_installer" ] && capture "bridge-installer-provision" -- bash "$_bridge_installer" provision || true
fi
if bash -lc 'command -v agent-bridge >/dev/null 2>&1'; then
    capture "bridge-binstub-version" -- bash -lc 'agent-bridge --version' || true
    pass "agent-bridge binstub resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-bridge binstub NOT on PATH after provision" "headless embody cannot preflight/spawn without it"
fi
if ! bash -lc 'command -v agent-worktrees >/dev/null 2>&1'; then
    _worktrees_installer="$INSTALLED_ROOT/$WORKTREES_PLUGIN/scripts/install.sh"
    [ -f "$_worktrees_installer" ] && capture "worktrees-installer-provision" -- bash "$_worktrees_installer" provision || true
fi
if bash -lc 'command -v agent-worktrees >/dev/null 2>&1'; then
    capture "worktrees-binstub-version" -- bash -lc 'agent-worktrees --version' || true
    pass "agent-worktrees binstub resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-worktrees binstub NOT on PATH after provision" "headless embody's spawn path (embody.py create_worktree) always shells out to 'agent-worktrees create'"
fi

# =========================================================================
phase 3 "authenticate gh from the SAME injected Copilot token (no separate credential)"
if cr_ensure_gh; then
    pass "gh CLI present ($(gh --version 2>/dev/null | head -1))"
else
    jam "auth-gh" "gh CLI not installed and could not be installed" "see cr_ensure_gh"
    cr_finalize
fi
if [ -n "${COPILOT_GITHUB_TOKEN:-}" ]; then
    # The ORCHESTRATOR's own later tool calls are separate `docker exec`
    # invocations that do NOT inherit this setup process's shell exports (only
    # container-launch-time `-e` vars and ON-DISK credentials persist across
    # them). A plain `export`/`.bashrc` append is therefore not enough -- `gh
    # auth login --with-token` persists the credential to
    # ~/.config/gh/hosts.yml, which every later `gh` invocation (including
    # agent-dispatch's own github_provider_adapter, which shells out to `gh`)
    # resolves automatically regardless of env-var inheritance.
    printf '%s' "$COPILOT_GITHUB_TOKEN" | gh auth login --hostname github.com --with-token >/dev/null 2>&1 || true
    export GH_TOKEN="$COPILOT_GITHUB_TOKEN"
    export GITHUB_TOKEN="$COPILOT_GITHUB_TOKEN"
    # Best-effort defense in depth for any later shell that DOES happen to be a
    # fresh login/interactive shell sourcing one of these files. Deliberately
    # appends only to files that ALREADY EXIST -- creating a fresh
    # ~/.bash_profile here would otherwise shadow ~/.profile in bash's login-
    # shell startup chain (bash reads the FIRST of .bash_profile/.bash_login/
    # .profile that exists) and silently break whatever PATH setup the
    # binstub's own installer already placed there.
    for _rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
        [ -f "$_rc" ] || continue
        { printf 'export GH_TOKEN=%q\n' "$COPILOT_GITHUB_TOKEN"; printf 'export GITHUB_TOKEN=%q\n' "$COPILOT_GITHUB_TOKEN"; } >> "$_rc"
    done
else
    jam "auth-gh" "COPILOT_GITHUB_TOKEN not set in the container env" "run.ps1 -TokenAccount <acct> (or -NoToken is incompatible with this scenario)"
    cr_finalize
fi
_whoami_out="$CR_LOGDIR/gh-whoami.log"
if ( gh api user --jq .login ) > "$_whoami_out" 2>&1; then
    _actual_login="$(tr -d ' \t\r\n' < "$_whoami_out")"
    if [ "$_actual_login" = "$FIXTURE_PRODUCER_LOGIN" ]; then
        pass "gh authenticated as the expected fixture producer login ($_actual_login)"
    else
        jam "auth-gh" "gh authenticated as '$_actual_login', expected '$FIXTURE_PRODUCER_LOGIN'" "the injected Copilot token account must match CR_FIXTURE_PRODUCER_LOGIN (run.ps1 -TokenAccount)"
        cr_finalize
    fi
else
    jam "auth-gh" "gh api user failed (see cr-logs/gh-whoami.log)" "verify the injected token is live and has 'repo' scope"
    cr_finalize
fi

# =========================================================================
phase 4 "clone the real fixture repo (content already seeded there, never authored here)"
_clone_out="$CR_LOGDIR/clone-fixture.log"
if ( bash -lc "gh repo clone '$FIXTURE_REPO' '$FIXTURE_DIR'" ) > "$_clone_out" 2>&1; then
    pass "cloned $FIXTURE_REPO to $FIXTURE_DIR"
else
    jam "dispatch-config" "gh repo clone of '$FIXTURE_REPO' failed (see cr-logs/clone-fixture.log)" "verify CR_FIXTURE_REPO is correct and the authenticated identity can read it"
    cr_finalize
fi
_registrar_dir="$FIXTURE_DIR/.copilot-extensions/agent-dispatch/registrar"
_missing=()
for _name in backlog-triager issue-reproducer effort-builder effort-driver; do
    [ -f "$_registrar_dir/$_name.yaml" ] || _missing+=("$_name.yaml")
done
if [ "${#_missing[@]}" -eq 0 ]; then
    pass "all four recipe declarations present in the cloned fixture repo's registrar/ directory"
else
    jam "dispatch-config" "fixture repo is missing declaration(s): ${_missing[*]}" "seed the fixture repo with all four .yaml declarations before running this scenario"
    cr_finalize
fi
info "deliberately NOT registering the repo with agent-dispatch's registrar here -- that registration is itself part of what this eval audits"

# =========================================================================
phase 5 "register the fixture repo with agent-worktrees (headless embody's documented spawn prerequisite)"
# embody.py's create_worktree() always shells out to `agent-worktrees create`
# for a headless lane -- this is host-level worktree-management adoption, the
# same one-time step a real production repo already performs long before
# ever declaring a repository-issue-loop, not a per-loop adoption step.
#
# project_for_task() resolves a task's --project in two steps: (1) reverse the
# task's `repo` field (here a bare "owner/name" forge string) through
# identity.name_for_repo() against agent-worktrees' own CANONICAL remote
# registry (which always carries a host prefix, e.g.
# "github.com/owner/name") -- a bare "owner/name" never matches that, so this
# always misses; (2) FALL BACK to the repo string's own final path segment
# (e.g. "owner/name" -> "name"). So the agent-worktrees project name must be
# registered as exactly that trailing segment for the fallback to resolve it.
PROJECT_NAME="${FIXTURE_REPO##*/}"
_aw_register_out="$CR_LOGDIR/worktrees-register.log"
if ( cd "$FIXTURE_DIR" && bash -lc "agent-worktrees register '$PROJECT_NAME'" ) > "$_aw_register_out" 2>&1; then
    pass "fixture repo registered as agent-worktrees project '$PROJECT_NAME' (see cr-logs/worktrees-register.log)"
else
    jam "dispatch-config" "agent-worktrees register failed (see cr-logs/worktrees-register.log)" "headless embody's spawn step will fail without a registered project"
    cr_finalize
fi
_aw_list_out="$CR_LOGDIR/worktrees-repos-list.log"
# Verify against `repos list --json` directly rather than `repos find` --
# `find`'s own platform-key matching (wsl/linux/windows) is a presentation
# nicety independent of whether the registry entry itself (what
# agent_dispatch.identity._repo_registry()/name_for_repo() actually reads)
# carries a usable path.
if ( bash -lc 'agent-worktrees repos list --json' ) > "$_aw_list_out" 2>&1 && grep -q "$FIXTURE_DIR" "$_aw_list_out"; then
    pass "agent-worktrees registry carries a path for 'recipe-fixture' ($FIXTURE_DIR)"
else
    jam "dispatch-config" "agent-worktrees repos list does not show a path for 'recipe-fixture' (see cr-logs/worktrees-repos-list.log)" "check the register step above"
fi

# =========================================================================
phase 6 "register the 'task-worker' local agent-bridge venue (headless embody's documented host prerequisite)"
# repository-issue-loop's own docs (spawn-supervisor.md) document that a
# headless lane's default --headless-agent ("task-worker") must be a "real,
# addressable agent-bridge machine/repo identity" already registered on the
# host -- this is host-level agent-bridge topology, analogous to a real
# deployment's already-adopted machines.yaml/acp-agents.json, not a step the
# repository-issue-loop adoption docs ask an adopter to perform per-loop.
cat > "$HOME/agents.json" <<JSON
{
  "task-worker": {
    "cwd": "$FIXTURE_DIR",
    "copilot_args": ["--allow-all", "--experimental"],
    "description": "Local headless worker venue for the recipe-loops eval"
  }
}
JSON
mkdir -p "$HOME/.agent-bridge"
_bridge_config="$HOME/.agent-bridge/config.yaml"
if [ ! -f "$_bridge_config" ]; then
    cat > "$_bridge_config" <<YAML
port: 0
bind: 127.0.0.1
log_level: info
YAML
fi
# Append the topology block without disturbing whatever the already-
# provisioned config.yaml (schema_version, port, bind, log_level) already
# carries -- adds a top-level "topologies:" key only if one is not already
# present, so this is safe to run against either a brand-new or an
# already-migrated config file.
python3 - "$_bridge_config" "$HOME/agents.json" <<'PY'
import sys
path, agents_path = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines()
new_entry = ["  local-only:", f"    agents_config: {agents_path}"]
out = []
replaced = False
for line in lines:
    stripped = line.rstrip()
    if stripped.startswith("topologies:"):
        # Replace an empty flow mapping ("topologies: {}") or a bare block
        # starter ("topologies:") with the key plus our one entry; any
        # pre-existing nested topology entries (indented lines following a
        # bare "topologies:") are preserved by simply not consuming them here.
        out.append("topologies:")
        out.extend(new_entry)
        replaced = True
        continue
    out.append(line)
if not replaced:
    out.append("topologies:")
    out.extend(new_entry)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
_agents_out="$CR_LOGDIR/bridge-agents.log"
# The already-running agent-bridge daemon (started during first-session
# provisioning, before this config edit) caches its config at startup and
# does not live-reload -- restart it (zero-downtime cutover) so it picks up
# the newly-registered 'task-worker' topology.
capture "bridge-service-restart" -- bash -lc 'agent-bridge service restart' || true
if ( bash -lc 'agent-bridge agents' ) > "$_agents_out" 2>&1 && grep -q 'task-worker' "$_agents_out"; then
    pass "agent-bridge resolves the 'task-worker' local venue (see cr-logs/bridge-agents.log)"
else
    jam "dispatch-config" "agent-bridge does not resolve 'task-worker' (see cr-logs/bridge-agents.log)" "check ~/.agent-bridge/config.yaml topology wiring"
fi

# =========================================================================
phase 7 "start the singleton supervisor daemon (host infrastructure, not the eval itself)"
# On a real deployed host this is a long-running systemd --user service
# (scripts/install.sh's `agent-dispatch-supervisor.service`) started ONCE,
# well before any adopter ever runs `repository-issue-loop setup` -- i.e. it
# is pre-existing host infrastructure, not a step the repository-issue-loop
# adoption docs ask an adopter to perform themselves. This container has no
# systemd, so this phase stands in for that already-running host service
# (never telling the ORCHESTRATOR to discover or start it itself -- that
# would conflate genuine host infrastructure with the documented declaration
# lifecycle this eval actually audits).
_daemon_log="$CR_LOGDIR/supervisor-serve.log"
nohup bash -lc 'agent-dispatch supervise serve --interval 5' > "$_daemon_log" 2>&1 < /dev/null &
disown || true
sleep 5
_daemon_status_out="$CR_LOGDIR/supervisor-daemon-status.log"
if ( bash -lc 'agent-dispatch supervise daemon-status' ) > "$_daemon_status_out" 2>&1; then
    if grep -q '"running": *true' "$_daemon_status_out" 2>/dev/null || grep -q '"holds_scope": *true' "$_daemon_status_out" 2>/dev/null; then
        pass "singleton supervisor daemon is running (see cr-logs/supervisor-daemon-status.log)"
    else
        info "supervisor daemon-status did not clearly report running -- see cr-logs/supervisor-daemon-status.log and supervisor-serve.log"
    fi
else
    jam "dispatch-config" "agent-dispatch supervise daemon-status failed (see cr-logs/supervisor-daemon-status.log)" "check cr-logs/supervisor-serve.log for why the daemon did not start"
fi

# =========================================================================
cr_finalize
