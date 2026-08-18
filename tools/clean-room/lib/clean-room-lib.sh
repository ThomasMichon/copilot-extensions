#!/usr/bin/env bash
# clean-room-lib.sh -- shared helper API for clean-room SCENARIOS.
#
# This is the substrate half of the "scenario contract" (design doc
# docs/clean-room-test-rig.md Sec.6). It is mounted READ-ONLY into the container
# and sourced by a scenario's scenario.sh, so assertions, reporting, and
# diagnostics stay uniform across scenarios and the runner stays name-free.
#
# Public helper API (see the design doc for the vocabulary):
#   cr_init                      -- initialise accounting + logdir (call once, first)
#   phase <n> <title>            -- start phase <n>; also the --until GATE
#                                   (if <n> exceeds CR_UNTIL, finalize + exit here)
#   pass  <msg>                  -- record a PASS line
#   fail  <msg>                  -- record a FAIL line
#   info  <msg>                  -- record an INFO line
#   capture <label> -- <cmd...>  -- run <cmd>, tee stdout+stderr to
#                                   cr-logs/<label>.log, record exit; returns rc
#   envdump                      -- snapshot PATH / key tools+versions / named
#                                   config files into the report's "env" object
#   jam <category> <evidence> [hint]
#                                -- emit a CLASSIFIED failure (design Sec.7);
#                                   also counts as a FAIL
#   cr_meta <key> <value>        -- add a scenario-specific top-level report field
#   cr_finalize                  -- write cr-report.json + summary, then exit
#                                   (exit 0 iff no FAILs); usually called for you
#                                   by the --until gate or at scenario end
#
# Live-scenario auth shims (Tier-E scenarios that create/connect a real
# CodeSpace -- the rig injects only COPILOT_GITHUB_TOKEN and runs no inner login
# flow, so these provide the generic, product-agnostic shim):
#   cr_ensure_gh                 -- install the gh CLI if the base image lacks it
#   cr_ensure_ssh_client         -- install an ssh client if absent (connect needs one)
#   cr_seed_gh_codespace_auth    -- seed gh KEYRING auth from the token AND keep
#                                   GH_TOKEN exported (both are needed; see below),
#                                   asserting the codespace scope
#
# Caller-controlled signal (the async construct for suspend/resume evals):
#   cr_signal_arm  <name>        -- arm a signal the HARNESS controls the wake edge
#                                   of; writes a truly-blocking wait script (a FIFO
#                                   read -- no CPU spin) whose only release is
#                                   cr_signal_fire. Survives across the
#                                   setup->agent-turn->post_check process boundary
#                                   (state lives under $CR_SIGNAL_DIR on disk).
#   cr_signal_wait_cmd <name>    -- print the blocking wait command (a single
#                                   argv-safe path) to hand to the agent-under-test
#                                   (e.g. `agent-dispatch run --detach --resume <wt>
#                                   -- <this>`); it BLOCKS until the harness fires.
#   cr_signal_fire <name>        -- release the signal (bounded; never hangs). The
#                                   caller owns WHEN this happens, so a resume that
#                                   lands before it is proof of a poll/self-wake.
#   cr_signal_waiter_present <name>
#                                -- 0 iff a live waiter for <name> is running (the
#                                   objective "did it genuinely suspend" evidence).
#   cr_signal_fired <name>       -- 0 iff the signal was fired (breadcrumb).
# Why it exists: an internally-timed wait (a plain `sleep`) can be faked and masks
# whether the agent truly hibernated. A wake edge the TEST CALLER owns makes
# suspend/resume objectively observable -- the flagship consumer is the
# agent-dispatch hibernate-the-wait eval; heavier agentic evals reuse it to test
# externally-timed orchestration rather than self-paced sleeps.
#
# Environment consumed:
#   CR_REPORT        path for the JSON report            (default $HOME/cr-report.json)
#   CR_LOGDIR        per-label command logs              (default $HOME/cr-logs)
#   CR_UNTIL         stop after this phase number        (default 999 = all)
#   CR_SCENARIO_NAME scenario name recorded in the report (default "unnamed")
#   CR_SIGNAL_DIR    caller-controlled signal state dir  (default $HOME/.cr-signals)
#
# Contract notes:
# - The report keeps the historical shape: top-level copilot_version,
#   ran_until_phase, passed, failed, results[] -- plus additive scenario meta
#   (via cr_meta), a jams[] array, and an env{} object. Existing consumers that
#   read those top-level keys keep working.
# - .sh files MUST be LF (the container's bash -lc / shebang break on CRLF).

# ---- configuration -------------------------------------------------------
CR_REPORT="${CR_REPORT:-$HOME/cr-report.json}"
CR_LOGDIR="${CR_LOGDIR:-$HOME/cr-logs}"
CR_UNTIL="${CR_UNTIL:-999}"
CR_SCENARIO_NAME="${CR_SCENARIO_NAME:-unnamed}"
CR_SIGNAL_DIR="${CR_SIGNAL_DIR:-$HOME/.cr-signals}"

# ---- accounting ----------------------------------------------------------
CR_PASS=0
CR_FAIL=0
declare -a CR_RESULTS=()
declare -a CR_JAMS=()
declare -a CR_META_KEYS=()
declare -a CR_META_VALS=()
declare -a CR_ENV_TOOLS=()
declare -a CR_ENV_CONFIGS=()
CR_ENV_PATH=""
CR_ENV_CAPTURED=0
CR_CUR_PHASE=0

# JSON-string-escape helper (quotes, backslashes, newlines, tabs, strip CR).
_cr_esc() {
    local s="$1"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    s=${s//$'\t'/\\t}
    s=${s//$'\r'/}
    printf '%s' "$s"
}

cr_init() {
    mkdir -p "$CR_LOGDIR"
    CR_PASS=0; CR_FAIL=0
    CR_RESULTS=(); CR_JAMS=(); CR_META_KEYS=(); CR_META_VALS=()
    printf '\033[1m### clean-room scenario: %s (until phase %s) ###\033[0m\n' \
        "$CR_SCENARIO_NAME" "$CR_UNTIL"
}

_cr_rec() {  # kind message
    local kind="$1"; shift
    local msg="$*"
    case "$kind" in
        PASS) CR_PASS=$((CR_PASS+1)); printf '  \033[32m[PASS]\033[0m %s\n' "$msg" ;;
        FAIL) CR_FAIL=$((CR_FAIL+1)); printf '  \033[31m[FAIL]\033[0m %s\n' "$msg" ;;
        INFO) printf '  \033[36m[INFO]\033[0m %s\n' "$msg" ;;
    esac
    CR_RESULTS+=("{\"kind\":\"$kind\",\"phase\":$CR_CUR_PHASE,\"msg\":\"$(_cr_esc "$msg")\"}")
}

pass() { _cr_rec PASS "$*"; }
fail() { _cr_rec FAIL "$*"; }
info() { _cr_rec INFO "$*"; }

phase() {  # <n> <title...>  -- start phase n; gate on CR_UNTIL
    local n="$1"; shift
    if [ "$CR_UNTIL" -lt "$n" ] 2>/dev/null; then
        cr_finalize
    fi
    CR_CUR_PHASE="$n"
    printf '\n\033[1m== Phase %s -- %s ==\033[0m\n' "$n" "$*"
}

capture() {  # <label> -- <cmd...>   run, tee to cr-logs/<label>.log, record exit
    local label="$1"; shift
    [ "$1" = "--" ] && shift
    local log="$CR_LOGDIR/${label}.log"
    printf '  $ %s\n' "$*" | tee "$log" >/dev/null
    "$@" >>"$log" 2>&1
    local rc=$?
    printf '  (%s exit=%s, log=%s)\n' "$label" "$rc" "$log"
    return $rc
}

# Record one key=value scenario-specific field, emitted at the report top level
# (preserves the historical shape for scenario-specific consumers).
cr_meta() {  # <key> <value>
    CR_META_KEYS+=("$1"); shift
    CR_META_VALS+=("$*")
}

# Snapshot the environment into the report's env{} object: login-shell PATH, the
# presence+version of key tools, and the presence of named config files.
envdump() {
    CR_ENV_PATH="$(bash -lc 'echo $PATH' 2>/dev/null)"
    CR_ENV_TOOLS=(); CR_ENV_CONFIGS=()
    local t ver where
    for t in copilot uv git node python3 python pip gh; do
        where="$(command -v "$t" 2>/dev/null || echo '')"
        if [ -n "$where" ]; then
            case "$t" in
                copilot) ver="$(copilot --version 2>/dev/null | head -1)" ;;
                uv)      ver="$(uv --version 2>/dev/null | head -1)" ;;
                git)     ver="$(git --version 2>/dev/null | head -1)" ;;
                node)    ver="$(node --version 2>/dev/null | head -1)" ;;
                gh)      ver="$(gh --version 2>/dev/null | head -1)" ;;
                *)       ver="$("$t" --version 2>&1 | head -1)" ;;
            esac
        else
            ver=""
        fi
        CR_ENV_TOOLS+=("{\"tool\":\"$(_cr_esc "$t")\",\"path\":\"$(_cr_esc "$where")\",\"version\":\"$(_cr_esc "$ver")\"}")
    done
    local f
    for f in "$HOME/.config/uv/uv.toml" /etc/pip.conf "$HOME/.pip/pip.conf" "$HOME/.config/pip/pip.conf"; do
        if [ -f "$f" ]; then
            CR_ENV_CONFIGS+=("{\"file\":\"$(_cr_esc "$f")\",\"present\":true}")
        else
            CR_ENV_CONFIGS+=("{\"file\":\"$(_cr_esc "$f")\",\"present\":false}")
        fi
    done
    CR_ENV_CAPTURED=1
    info "envdump: PATH + $(command -v uv >/dev/null && echo uv || echo 'no-uv') + configs captured"
}

# Emit a classified failure (design Sec.7 taxonomy). Counts as a FAIL and is also
# recorded in the jams[] array with its evidence reference + optional unjam hint.
jam() {  # <category> <evidence-ref> [<unjam-hint>]
    local cat="$1" ev="$2" hint="${3:-}"
    CR_FAIL=$((CR_FAIL+1))
    printf '  \033[31m[JAM:%s]\033[0m %s%s\n' "$cat" "$ev" \
        "$( [ -n "$hint" ] && printf ' -- hint: %s' "$hint" )"
    CR_RESULTS+=("{\"kind\":\"FAIL\",\"phase\":$CR_CUR_PHASE,\"msg\":\"jam[$(_cr_esc "$cat")]: $(_cr_esc "$ev")\"}")
    CR_JAMS+=("{\"category\":\"$(_cr_esc "$cat")\",\"phase\":$CR_CUR_PHASE,\"evidence\":\"$(_cr_esc "$ev")\",\"hint\":\"$(_cr_esc "$hint")\"}")
}

# ---- live-scenario auth shims (Tier-E CodeSpace scenarios) ----------------
# The rig injects a single COPILOT_GITHUB_TOKEN and deliberately runs no inner
# login flow. A live scenario that actually creates/connects a CodeSpace needs
# more than that raw token, so these helpers provide the generic,
# product-agnostic shim. (Product/tenant-specific credential relaying -- e.g. an
# ADO/az inner-loop token -- layers ON TOP of these in the consuming harness,
# which can hold the fuller credential stack; it does not belong in this public
# substrate.)

# Install the GitHub CLI if the base image lacks it (from the GitHub releases
# tarball). Idempotent; puts gh on PATH. Honors CR_GH_VERSION (default 2.62.0).
cr_ensure_gh() {
    command -v gh >/dev/null 2>&1 && return 0
    if [ -x "$HOME/.local/bin/gh" ]; then export PATH="$HOME/.local/bin:$PATH"; return 0; fi
    local ver="${CR_GH_VERSION:-2.62.0}"
    ( cd /tmp && curl -fsSL -o gh.tgz "https://github.com/cli/cli/releases/download/v${ver}/gh_${ver}_linux_amd64.tar.gz" \
        && tar xzf gh.tgz && mkdir -p "$HOME/.local/bin" && cp "gh_${ver}_linux_amd64/bin/gh" "$HOME/.local/bin/gh" ) >/dev/null 2>&1
    export PATH="$HOME/.local/bin:$PATH"
    command -v gh >/dev/null 2>&1
}

# Install an ssh client if absent (agent-codespaces connect needs one). Records a
# PASS/JAM. Returns non-zero if it could not be provided.
cr_ensure_ssh_client() {
    if command -v ssh >/dev/null 2>&1; then pass "openssh-client present ($(ssh -V 2>&1))"; return 0; fi
    if command -v sudo >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y -qq openssh-client
    else
        apt-get update -qq && apt-get install -y -qq openssh-client
    fi >/dev/null 2>&1
    if command -v ssh >/dev/null 2>&1; then
        pass "openssh-client installed ($(ssh -V 2>&1))"; return 0
    fi
    jam "codespace-config" "no ssh client and could not install openssh-client" "agent-codespaces connect needs an ssh client"
    return 1
}

# Seed gh auth for a live CodeSpace scenario from the injected token. Records a
# PASS/JAM and returns non-zero on failure. Call once in a live scenario's setup.
#
# Two facts drive this shim, and BOTH bite silently without it:
#  (1) agent-codespaces' create scope-check reads the gh KEYRING / `gh auth
#      status`, NOT a raw GH_TOKEN env var -- so the token must be logged in via
#      `gh auth login --with-token`, or `create` refuses ("gh is not
#      authenticated"). This mirrors the real golden path's `gh auth login`.
#  (2) the agent-bridge daemon PROPAGATES GH_TOKEN to the in-CodeSpace copilot at
#      ACP launch (connect stage 7). If GH_TOKEN is unset, `agent-bridge send`
#      fails with "LAUNCH_ACP: Authentication required" -- the missing "inner"
#      auth leg (the CodeSpace-side copilot has no login flow, so the injected
#      token must flow through). So GH_TOKEN must ALSO stay exported.
# The keyring and GH_TOKEN coexist (they resolve the same account); this seeds
# both and asserts the codespace scope.
cr_seed_gh_codespace_auth() {
    if ! cr_ensure_gh; then
        jam "auth-gh" "gh CLI not installed (base image omits it)" "provide gh, or use the golden path's setup.sh prereq phase"
        return 1
    fi
    if [ -n "${COPILOT_GITHUB_TOKEN:-}" ]; then
        printf '%s' "$COPILOT_GITHUB_TOKEN" | gh auth login --hostname github.com --with-token >/dev/null 2>&1 || true
        export GH_TOKEN="$COPILOT_GITHUB_TOKEN"
    fi
    if gh auth status 2>&1 | grep -q "codespace"; then
        pass "gh authenticated with the codespace scope (keyring + GH_TOKEN)"
        return 0
    fi
    jam "auth-gh" "gh not authenticated with the codespace scope" "inject a codespace-scoped COPILOT_GITHUB_TOKEN (run.ps1 -TokenAccount <acct>)"
    return 1
}

# ---- caller-controlled signal (async construct for suspend/resume evals) --
# A "signal" whose wake edge the TEST CALLER owns. The wait it produces is a real
# block (a FIFO read -- no CPU spin), so a worker that hands it to a hibernation
# layer genuinely costs nothing while suspended; the ONLY thing that releases it is
# cr_signal_fire, called by the harness at a moment of its choosing. Any forward
# progress observed before that fire is proof the agent polled / self-woke instead
# of truly hibernating. State lives on disk under $CR_SIGNAL_DIR so it survives the
# setup -> agent-turn -> post_check process boundary.

# Arm a signal: create its FIFO + a blocking wait script. Falls back to a
# sentinel-file poll only if mkfifo is unavailable (still caller-released).
cr_signal_arm() {  # <name>
    local name="$1"
    mkdir -p "$CR_SIGNAL_DIR"
    local fifo="$CR_SIGNAL_DIR/$name.fifo"
    local fired="$CR_SIGNAL_DIR/$name.fired"
    local waitsh="$CR_SIGNAL_DIR/$name-wait.sh"
    rm -f "$fifo" "$fired"
    if mkfifo "$fifo" 2>/dev/null; then
        cat > "$waitsh" <<EOF
#!/usr/bin/env bash
# Caller-controlled signal '$name': block with NO CPU spin until the harness
# fires it (cr_signal_fire). A FIFO read blocks at open until a writer appears.
read -r _ < "$fifo"
EOF
    else
        cat > "$waitsh" <<EOF
#!/usr/bin/env bash
# Caller-controlled signal '$name' (poll fallback -- no mkfifo): block until the
# harness drops the fired breadcrumb (cr_signal_fire).
while [ ! -e "$fired" ]; do sleep 1; done
EOF
    fi
    chmod +x "$waitsh"
    info "signal '$name' armed (wait: $waitsh)"
}

# Print the blocking wait command -- a single argv-safe path -- to hand to the
# agent-under-test (e.g. `agent-dispatch run --detach --resume <wt> -- <this>`).
cr_signal_wait_cmd() {  # <name>
    printf '%s' "$CR_SIGNAL_DIR/$1-wait.sh"
}

# Release the signal (bounded -- never hangs even if no reader is present).
cr_signal_fire() {  # <name>
    local name="$1"
    local fifo="$CR_SIGNAL_DIR/$name.fifo"
    touch "$CR_SIGNAL_DIR/$name.fired"
    if [ -p "$fifo" ]; then
        if command -v timeout >/dev/null 2>&1; then
            timeout 10 bash -c "printf 'go\n' > '$fifo'" 2>/dev/null || true
        else
            ( printf 'go\n' > "$fifo" ) 2>/dev/null &
            sleep 2; kill %1 2>/dev/null || true
        fi
    fi
    info "signal '$name' fired"
}

# 0 iff a live waiter for <name> is running (objective "did it suspend" evidence).
cr_signal_waiter_present() {  # <name>
    local waitsh="$CR_SIGNAL_DIR/$1-wait.sh"
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f "$waitsh" >/dev/null 2>&1
    else
        ps -ef 2>/dev/null | grep -F "$waitsh" | grep -qv grep
    fi
}

# 0 iff the signal was fired (breadcrumb).
cr_signal_fired() {  # <name>
    [ -e "$CR_SIGNAL_DIR/$1.fired" ]
}

_cr_join() {  # print array elements joined by ",\n    " with a leading indent
    local first=1 e
    for e in "$@"; do
        [ $first -eq 1 ] && first=0 || printf ',\n'
        printf '    %s' "$e"
    done
}

cr_finalize() {
    printf '\n\033[1m== Summary ==\033[0m\n'
    printf '  \033[1m%d passed, %d failed\033[0m (scenario=%s, ran through phase %s)\n' \
        "$CR_PASS" "$CR_FAIL" "$CR_SCENARIO_NAME" "$CR_UNTIL"
    [ ${#CR_JAMS[@]} -gt 0 ] && printf '  \033[31m%d jam(s) classified\033[0m\n' "${#CR_JAMS[@]}"
    {
        printf '{\n'
        printf '  "scenario": "%s",\n' "$(_cr_esc "$CR_SCENARIO_NAME")"
        printf '  "copilot_version": "%s",\n' "$(_cr_esc "$(copilot --version 2>/dev/null | head -1)")"
        printf '  "ran_until_phase": %s,\n' "$CR_UNTIL"
        # scenario-specific top-level meta (historical-shape fields)
        local i
        for i in "${!CR_META_KEYS[@]}"; do
            printf '  "%s": "%s",\n' "$(_cr_esc "${CR_META_KEYS[$i]}")" "$(_cr_esc "${CR_META_VALS[$i]}")"
        done
        printf '  "passed": %d,\n  "failed": %d,\n' "$CR_PASS" "$CR_FAIL"
        # env{}
        printf '  "env": {\n'
        printf '    "path": "%s",\n' "$(_cr_esc "$CR_ENV_PATH")"
        printf '    "tools": [\n'
        [ ${#CR_ENV_TOOLS[@]} -gt 0 ] && { _cr_join "${CR_ENV_TOOLS[@]}"; printf '\n'; }
        printf '    ],\n'
        printf '    "configs": [\n'
        [ ${#CR_ENV_CONFIGS[@]} -gt 0 ] && { _cr_join "${CR_ENV_CONFIGS[@]}"; printf '\n'; }
        printf '    ]\n'
        printf '  },\n'
        # jams[]
        printf '  "jams": [\n'
        [ ${#CR_JAMS[@]} -gt 0 ] && { _cr_join "${CR_JAMS[@]}"; printf '\n'; }
        printf '  ],\n'
        # results[]
        printf '  "results": [\n'
        [ ${#CR_RESULTS[@]} -gt 0 ] && { _cr_join "${CR_RESULTS[@]}"; printf '\n'; }
        printf '  ]\n'
        printf '}\n'
    } > "$CR_REPORT"
    printf '  report: %s\n  logs:   %s\n' "$CR_REPORT" "$CR_LOGDIR"
    [ "$CR_FAIL" -eq 0 ]; exit $?
}
