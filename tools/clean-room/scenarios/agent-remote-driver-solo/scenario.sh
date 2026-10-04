#!/usr/bin/env bash
# agent-remote-driver-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-remote-driver on a fresh box and proves its launch-time
# baseline-drivability contract end-to-end: a live `copilot` session writes a
# discovery descriptor, the driver's dependency-free HTTP surface answers over
# loopback with the descriptor's bearer token, the one aggregation CLI
# (bin/list-sessions.mjs) reflects the live session, and SIGTERM cleanly
# removes the descriptor (installSignalCleanup). No agent-worktrees or
# agent-bridge is installed in this venue -- that absence IS the point (the
# plugin's whole premise is launch-time presence independent of any other
# coordination plugin).
#
# GENUINELY TIER P (no model in the loop, no AI credits): the live session is
# kept alive with NO prompt ever submitted -- a bare `copilot` process under a
# pty (via `script`, so it never hits the CLI's "no prompt provided" exit) is
# enough for the extension to join, assign a session id, and write its
# descriptor. This is a deterministic filesystem/network assertion, not a
# judged eval: it never needs a model turn or inspects any reply content, only
# the extension's observable side effects (descriptor file, HTTP responses,
# aggregation CLI output, signal-cleanup behavior).
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME + the
# lib's vars. CR_MARKETPLACE_REPO may be a container-local directory (e.g.
# /harness via -HarnessMount) for uncommitted-worktree validation of a plugin
# not yet promoted to the marketplace repo's default branch. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
PLUGIN="agent-remote-driver"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
DISCOVERY_DIR="$HOME/.copilot/remote-driver/sessions"
MARKETPLACE_REPO_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$MARKETPLACE_REPO")"
if [ -d "$MARKETPLACE_REPO" ]; then
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"directory\", \"path\": $MARKETPLACE_REPO_JSON } }"
else
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"github\", \"repo\": $MARKETPLACE_REPO_JSON } }"
fi

: "${CR_SCENARIO_NAME:=agent-remote-driver-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base"   "standalone"

# capture() (the shared lib) always tees the full invoked argv into its log
# BEFORE running it -- fine for most commands, but every HTTP check below
# carries the descriptor's live bearer token in its argv (an Authorization
# header). Use this instead for any curl call so the token never lands in a
# persisted log: it logs a static, redacted description, never the real argv.
_http_capture() {  # <label> <description> -- <curl argv...>
    local label="$1" desc="$2"; shift 2
    [ "$1" = "--" ] && shift
    local log="$CR_LOGDIR/${label}.log"
    printf '  $ %s (argv redacted -- carries the live bearer token)\n' "$desc" > "$log"
    "$@" >>"$log" 2>&1
    local rc=$?
    printf '  (%s exit=%s, log=%s)\n' "$label" "$rc" "$log"
    return $rc
}

SESSION_PID=""
DESCRIPTOR=""
# Registered immediately once the live session starts (phase 2), BEFORE any
# later phase's own HTTP/jam logic runs -- `phase()` calls cr_finalize + exit
# the moment CR_UNTIL is reached, and a plain signal/interrupt can land at any
# point too. Without this trap, a `-Until 2`/`3`/`4` run (or an interruption)
# would leave this Copilot process and its descriptor running in the
# persistent clean-room container with nothing left to reap it.
#
# Stop the EXTENSION's own process first (parsed fresh from the descriptor,
# not from a $D_PID that may not be set yet this early) -- signaling only the
# outer script/copilot wrapper is exactly the bug phase 5 below exists to
# avoid: the wrapper can exit cleanly while orphaning the extension child (and
# its descriptor) behind it. Best-effort throughout; this is cleanup, not a
# pass/fail assertion.
_cleanup_session() {
    local victim_pid=""
    if [ -n "$DESCRIPTOR" ] && [ -f "$DESCRIPTOR" ]; then
        victim_pid="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("pid", ""))
except Exception:
    pass
' "$DESCRIPTOR" 2>/dev/null)"
    fi
    if [ -n "$victim_pid" ] && kill -0 "$victim_pid" 2>/dev/null; then
        kill -TERM "$victim_pid" 2>/dev/null || true
        for _ in $(seq 1 5); do
            kill -0 "$victim_pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$victim_pid" 2>/dev/null && kill -KILL "$victim_pid" 2>/dev/null || true
    fi
    if [ -n "$SESSION_PID" ] && kill -0 "$SESSION_PID" 2>/dev/null; then
        kill -TERM "$SESSION_PID" 2>/dev/null || true
        sleep 1
        kill -0 "$SESSION_PID" 2>/dev/null && kill -KILL "$SESSION_PID" 2>/dev/null || true
    fi
}
trap _cleanup_session EXIT

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$DISCOVERY_DIR" ] || [ -d "$HOME/.agent-worktrees" ] || [ -d "$HOME/.agent-bridge" ]; then
    fail "environment is NOT clean -- pre-existing remote-driver/worktrees/bridge state"
else
    pass "clean slate: no ~/.copilot/remote-driver, no ~/.agent-worktrees, no ~/.agent-bridge"
fi

# =========================================================================
phase 1 "install ONLY $PLUGIN"
mkdir -p "$HOME/.copilot"
# "experimental": true is required for the CLI to load ANY SDK extension at
# all (an ambient, suite-wide prerequisite -- see e.g. agent-worktrees'
# copilot-extensions-setup skill / install.sh), not something this scenario
# or this plugin's own hook can set on the operator's behalf.
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "sandbox": { "enabled": false },
  "experimental": true,
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": $MARKETPLACE_SOURCE },
  "enabledPlugins": { "$PLUGIN@$MARKETPLACE_NAME": true }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install" -- copilot plugin install "$PLUGIN@$MARKETPLACE_NAME" || true
# A "directory" marketplace source (a local/uncommitted-worktree checkout, e.g.
# via -HarnessMount) is loaded LIVE from its source path -- nothing is copied
# into $INSTALLED_ROOT. Detect that mode from the CLI's own message so the rest
# of this scenario points at wherever the payload actually lives.
LIVE_SRC="$(grep -oE 'loaded live from [^,]+' "$CR_LOGDIR/install.log" 2>/dev/null | sed -E 's/^loaded live from //' | head -n1)"
if [ -n "$LIVE_SRC" ]; then
    PLUGIN_DIR="$LIVE_SRC"
elif [ -d "$INSTALLED_ROOT/$PLUGIN" ]; then
    PLUGIN_DIR="$INSTALLED_ROOT/$PLUGIN"
else
    PLUGIN_DIR=""
fi
if [ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ]; then
    pass "$PLUGIN payload present on disk ($PLUGIN_DIR)"
else
    jam "npm-registry" "$PLUGIN payload NOT installed (see cr-logs/install.log)" \
        "check marketplace source (CR_MARKETPLACE_REPO) + node/npm feed reachability"
fi
# Standalone by design: no agent-worktrees, no agent-bridge is installed or
# needed for this plugin to provide baseline drivability.
if bash -lc 'command -v agent-worktrees >/dev/null || command -v agent-bridge >/dev/null'; then
    info "an unexpected coordination plugin is present -- not injected by this scenario"
else
    pass "no agent-worktrees / agent-bridge present -- $PLUGIN composes with neither"
fi

# =========================================================================
phase 2 "live session writes a discovery descriptor (no model turn)"
COPILOT_ARGV=(copilot --allow-all --experimental)
[ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ] && COPILOT_ARGV+=(--plugin-dir "$PLUGIN_DIR")
# Properly quote the argv into one string for `script -c` (which takes a
# single shell command, not an argv array).
COPILOT_CMD="$(printf '%q ' "${COPILOT_ARGV[@]}")"
mkdir -p "$HOME/driver-repo" && ( cd "$HOME/driver-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# driver' > README.md && git add -A && git commit -qm init )
SESSION_LOG="$CR_LOGDIR/live-session.log"
# `copilot` refuses outright ("No prompt provided...") without -p/-i UNLESS
# given a real pty -- a plain background subshell with redirected stdio is
# non-interactive and hits that refusal immediately. `script` allocates a
# pty via forkpty() without needing a real controlling terminal, so the CLI
# starts its ordinary interactive session (joins, gets a session id, the
# extension writes its descriptor) and then simply waits at the prompt --
# NO prompt is ever typed or submitted, so NO model turn ever fires. This is
# what makes the whole scenario genuinely Tier P: deterministic and
# credit-free, not reliant on the model choosing to run a shell `sleep`.
(
  cd "$HOME/driver-repo" && exec script -qec "$COPILOT_CMD" /dev/null
) </dev/null >"$SESSION_LOG" 2>&1 &
SESSION_PID=$!
info "started live session in background (pid=$SESSION_PID, no prompt submitted), polling for a discovery descriptor"

DESCRIPTOR=""
for _ in $(seq 1 20); do
    if [ -d "$DISCOVERY_DIR" ]; then
        DESCRIPTOR="$(find "$DISCOVERY_DIR" -maxdepth 1 -name '*.json' -print -quit 2>/dev/null)"
        [ -n "$DESCRIPTOR" ] && break
    fi
    sleep 1
done

if [ -n "$DESCRIPTOR" ]; then
    pass "discovery descriptor appeared: $DESCRIPTOR"
    # Persist a diagnostic snapshot with the live bearer token REDACTED -- the
    # production writer deliberately protects the real file with mode 0600;
    # a run artifact outside that protection must not carry a working token.
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
if "token" in d:
    d["token"] = "REDACTED"
json.dump(d, open(sys.argv[2], "w"), indent=2)
' "$DESCRIPTOR" "$CR_LOGDIR/descriptor.json" 2>/dev/null || true
else
    jam "path-binstub" "no descriptor appeared under $DISCOVERY_DIR within 20s (see cr-logs/live-session.log)" \
        "confirm the plugin loaded (--plugin-dir / enabledPlugins) and extension.mjs's sessionStart hook ran"
fi

# =========================================================================
phase 3 "drive the session over its HTTP surface (/health, /events, /send)"
if [ -n "$DESCRIPTOR" ]; then
    read -r D_HOST D_PORT D_TOKEN D_PID D_SID <<EOF2
$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("host",""), d.get("port",""), d.get("token",""), d.get("pid",""), d.get("sessionId",""))
' "$DESCRIPTOR")
EOF2
    BASE_URL="http://${D_HOST}:${D_PORT}"
    AUTHZ="Authorization: Bearer ${D_TOKEN}"

    if _http_capture health "GET $BASE_URL/health" -- curl -fsS -m 5 -H "$AUTHZ" "$BASE_URL/health"; then
        if grep -q '"ok":true' "$CR_LOGDIR/health.log" && grep -q "\"pid\":$D_PID" "$CR_LOGDIR/health.log"; then
            pass "/health reports ok:true with the descriptor's own pid ($D_PID)"
        else
            jam "network" "/health responded but did not confirm ok:true + matching pid (see cr-logs/health.log)" \
                "driver-server.mjs's /health handler should echo {ok, sessionId, pid}"
        fi
    else
        jam "network" "/health request failed (see cr-logs/health.log)" \
            "confirm the server bound $BASE_URL and the bearer token matches the descriptor"
    fi

    if _http_capture events "GET $BASE_URL/events (SSE, 3s)" -- curl -fsS -m 3 -N -H "$AUTHZ" "$BASE_URL/events"; then
        if grep -q ': connected' "$CR_LOGDIR/events.log" 2>/dev/null; then
            pass "/events SSE stream connected and received the initial ': connected' marker"
        else
            jam "network" "/events connected but never sent the ': connected' marker (see cr-logs/events.log)" \
                "driver-server.mjs's /events handler should write an initial SSE comment on connect"
        fi
    else
        # curl -m exits 28 on a timeout; an SSE stream that stays open with no
        # further event in the short window is NOT itself a failure -- only a
        # connect failure, or never receiving the initial marker, is.
        _rc=$?
        if [ "$_rc" -eq 28 ] && grep -q ': connected' "$CR_LOGDIR/events.log" 2>/dev/null; then
            pass "/events SSE stream connected and received the initial ': connected' marker (held open until the 3s timeout, as expected)"
        else
            jam "network" "/events did not connect or never sent the ': connected' marker (see cr-logs/events.log)" \
                "confirm the bounded MAX_SSE_CLIENTS gate isn't refusing a fresh connection"
        fi
    fi

    if _http_capture send "POST $BASE_URL/send" -- curl -fsS -m 5 -H "$AUTHZ" -H 'content-type: application/json' \
        -d '{"content":"(clean-room probe, no reply needed)"}' "$BASE_URL/send"; then
        pass "/send accepted a message (no model turn is expected to result -- the session has none pending)"
    else
        jam "network" "/send request failed (see cr-logs/send.log)" \
            "confirm the /send handler accepts a message"
    fi
else
    info "skipping phase 3 HTTP checks -- no descriptor from phase 2"
fi

# =========================================================================
phase 4 "bin/list-sessions.mjs aggregated view reflects the live session"
if [ -n "$PLUGIN_DIR" ] && [ -f "$PLUGIN_DIR/bin/list-sessions.mjs" ]; then
    if capture "list-sessions" -- node "$PLUGIN_DIR/bin/list-sessions.mjs" --json; then
        if [ -n "$DESCRIPTOR" ] && grep -q "$D_SID" "$CR_LOGDIR/list-sessions.log" 2>/dev/null; then
            pass "list-sessions.mjs --json lists the live session ($D_SID)"
        elif [ -z "$DESCRIPTOR" ]; then
            info "no descriptor from phase 2 -- cannot assert the live session is listed"
        else
            jam "path-binstub" "list-sessions.mjs ran but did not list the live session (see cr-logs/list-sessions.log)" \
                "registry.mjs's aggregated view should include every heartbeat-fresh descriptor"
        fi
    else
        jam "path-binstub" "list-sessions.mjs failed to run (see cr-logs/list-sessions.log)" \
            "the aggregation CLI should run with only node, no other dependency"
    fi
else
    jam "npm-registry" "bin/list-sessions.mjs not found under the installed plugin payload" \
        "confirm the plugin payload ships its bin/ directory"
fi

# =========================================================================
phase 5 "SIGTERM is handled cleanly and removes the discovery descriptor"
# Nothing makes this session exit on its own (no prompt was ever submitted,
# so there is no turn to complete) -- termination here is the DESIGNED way
# this phase exercises extension.mjs's installSignalCleanup path.
#
# Signal the EXTENSION'S OWN process (D_PID from the descriptor), not the
# outer `script`/`copilot` wrapper (SESSION_PID): the extension runs as its
# own dedicated child process (see `extension-bootstrap` in the CLI's debug
# log), and installSignalCleanup registers its SIGINT/SIGTERM handlers on
# THAT process specifically. SIGTERM-ing the top-level wrapper only proves
# the wrapper itself exits promptly -- it does not prove the OS actually
# delivers that signal down to the extension child (it may not, depending on
# how the main CLI shuts down its own children), which is exactly what was
# observed: the wrapper exited cleanly while the descriptor remained. A
# forced SIGKILL fallback here is a genuine FAIL, not silently treated the
# same as a graceful SIGTERM exit -- it would mean installSignalCleanup
# itself is broken or hung.
TERMINATED_CLEANLY=0
if [ -n "$DESCRIPTOR" ] && [ -n "${D_PID:-}" ] && kill -0 "$D_PID" 2>/dev/null; then
    kill -TERM "$D_PID" 2>/dev/null || true
    for _ in $(seq 1 15); do
        kill -0 "$D_PID" 2>/dev/null || { TERMINATED_CLEANLY=1; break; }
        sleep 1
    done
    if [ "$TERMINATED_CLEANLY" -eq 1 ]; then
        pass "the extension's own process (pid $D_PID) exited within 15s of SIGTERM (installSignalCleanup's signal-cleanup path)"
    else
        jam "path-binstub" "the extension's own process (pid $D_PID) did not exit within 15s of SIGTERM -- forcing SIGKILL" \
            "extension.mjs's installSignalCleanup should let SIGTERM terminate the process promptly"
        kill -KILL "$D_PID" 2>/dev/null || true
    fi
elif [ -z "$DESCRIPTOR" ]; then
    info "no descriptor from phase 2 -- cannot assert signal cleanup"
else
    jam "path-binstub" "the extension's own process (pid ${D_PID:-unknown}, from the descriptor) was not alive to signal" \
        "confirm the descriptor's pid field actually identifies the running extension process"
fi
sleep 1
if [ -n "$DESCRIPTOR" ] && [ ! -e "$DESCRIPTOR" ]; then
    if [ "$TERMINATED_CLEANLY" -eq 1 ]; then
        pass "discovery descriptor removed after a clean SIGTERM exit"
    else
        jam "path-binstub" "discovery descriptor was removed, but only after a forced SIGKILL (see above) rather than a clean SIGTERM exit" \
            "a descriptor surviving to SIGKILL time, removed by some other path, is not the same proof as installSignalCleanup working"
    fi
elif [ -z "$DESCRIPTOR" ]; then
    info "no descriptor from phase 2 -- cannot assert cleanup"
else
    jam "path-binstub" "discovery descriptor still present after signaling the extension process ($DESCRIPTOR)" \
        "extension.mjs should remove its descriptor on SIGTERM (installSignalCleanup) or process exit"
fi
# Regardless of the above, the overall session wrapper (SESSION_PID) is
# always reaped by the EXIT trap (_cleanup_session) so the persistent
# clean-room container never accumulates an orphaned copilot/script process.

# =========================================================================
cr_finalize
