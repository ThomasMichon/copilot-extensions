#!/usr/bin/env bash
# partner-harness-setup/scenario.sh -- Tier-P validation of a DOWNSTREAM partner
# harness's setup flow, run INSIDE the disposable clean-room box.
#
# Purpose: a re-blit / vendored-plugin sync must never publish a drop that breaks
# the partner harness's setup flow. This scenario turns "does the partner still
# set up?" into a hard PASS/FAIL by asserting on the to-be-published tree:
#   1. drop structure  -- each vendored plugin parses + is marketplace-listed +
#      ships its installer entrypoints; the partner's setup entrypoint + golden
#      path doc exist.
#   2. read-only check -- the partner's `setup <check>` runs without CRASHING
#      (it may report "manual guidance" -- that is not a failure).
#   3. partner's own tests -- the partner's OWN setup/update test suite passes
#      (stdlib unittest; hermetic, stubbed -- touches nothing real).
#
# Name-free of any specific partner: everything is CR_PARTNER_* configurable. A
# consuming gate is a downstream vendored-plugin sync that runs this before it
# publishes a re-blit; it sets CR_PARTNER_* for its own partner harness.
#
# This is Tier P: deterministic, agent-free, no credits. It sources the shared
# lib and speaks only the helper API (phase/pass/fail/info/jam), so it reports
# uniformly and the runner stays name-free.
#
# Platform note: this runs the partner's NATIVE-unix flow (setup.sh + *_sh tests)
# in a Linux container. The Windows-native flow (setup.ps1 + *_ps1 tests) runs on
# a Windows container host. A partner is validated on the platform whose native
# suite is faithful.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Name the scenario BEFORE sourcing the lib: the lib pins CR_SCENARIO_NAME at
# source time (defaulting to "unnamed"), so a later ":=" would no-op.
: "${CR_SCENARIO_NAME:=partner-harness-setup}"
export CR_SCENARIO_NAME
# shellcheck source=../../lib/clean-room-lib.sh
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

PARTNER_NAME="${CR_PARTNER_NAME:-partner-harness}"
PARTNER_PATH="${CR_PARTNER_PATH:-}"
PARTNER_REPO="${CR_PARTNER_REPO:-}"
PARTNER_PLUGINS="${CR_PARTNER_PLUGINS:-agent-bridge agent-codespaces}"
PARTNER_MARKETPLACE="${CR_PARTNER_MARKETPLACE:-.github/plugin/marketplace.json}"
PARTNER_GOLDEN_DOC="${CR_PARTNER_GOLDEN_DOC:-docs/golden-path/setup.md}"
PARTNER_SETUP_ENTRY="${CR_PARTNER_SETUP_ENTRY:-setup.sh}"
PARTNER_CHECK_ARGS="${CR_PARTNER_CHECK_ARGS:-check --non-interactive}"
PARTNER_CHECK_OK_CODES="${CR_PARTNER_CHECK_OK_CODES:-0 1 2}"
PARTNER_SETUP_TESTS="${CR_PARTNER_SETUP_TESTS:-tests.test_setup_sh tests.test_update_sh}"

cr_init
cr_meta "partner" "$PARTNER_NAME"

_PY="$(command -v python3 || command -v python || echo '')"

# Read JSON fields with python (stdlib); values are passed as ARGV, never eval'd,
# so untrusted CR_PARTNER_* inputs never become code. Prints empty on any error.
_json_field() {  # <file> <top-level-key>   -> d[key] as a string
    [ -n "$_PY" ] || { printf ''; return 1; }
    "$_PY" - "$1" "$2" <<'PY' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f: d = json.load(f)
    v = d.get(sys.argv[2])
    sys.stdout.write("" if v is None else str(v))
except Exception:
    sys.stdout.write("")
PY
}

_json_plugin_listed() {  # <marketplace-file> <plugin-name> -> name if listed, else ''
    [ -n "$_PY" ] || { printf ''; return 1; }
    "$_PY" - "$1" "$2" <<'PY' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f: d = json.load(f)
    name = sys.argv[2]
    hit = next((x for x in d.get("plugins", []) if x.get("name") == name), None)
    sys.stdout.write(name if hit else "")
except Exception:
    sys.stdout.write("")
PY
}

# =========================================================================
phase 0 "environment + partner tree present"
envdump
info "partner=$PARTNER_NAME plugins='$PARTNER_PLUGINS'"
info "python3: ${_PY:-MISSING}  bash: $(command -v bash)  git: $(command -v git || echo MISSING)"

ROOT=""
if [ -n "$PARTNER_PATH" ] && [ -d "$PARTNER_PATH" ]; then
    ROOT="$PARTNER_PATH"
    info "partner tree: mounted at $ROOT"
elif [ -n "$PARTNER_REPO" ]; then
    ROOT="$HOME/partner-src"
    capture "clone-partner" -- git clone --depth 1 "$PARTNER_REPO" "$ROOT" || true
    [ -d "$ROOT/.git" ] || ROOT=""
    info "partner tree: cloned $PARTNER_REPO -> ${ROOT:-FAILED}"
fi

if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
    jam "repo-config" "no partner tree (set CR_PARTNER_PATH to a mount, or CR_PARTNER_REPO to clone)" \
        "the sync gate should mount the re-blitted scratch tree at CR_PARTNER_PATH"
    cr_finalize
fi
if [ -z "$_PY" ]; then
    jam "validator-env" "python3 not found in the box (needed to parse manifests + run the partner test suite)" \
        "use an image that ships python3 (the base image does)"
    cr_finalize
fi
pass "partner tree present at $ROOT; python3 available"

# Defensive LF-normalization: a partner tree checked out on Windows (autocrlf) or
# re-blitted through a Windows sync box can carry CRLF in its *.sh, which makes a
# Linux `bash setup.sh` fail on $'\r' -- a checkout artifact, never the repo's
# intent (partner harnesses enforce LF via .gitattributes). The box's copy is
# disposable, so normalize it here and validate the partner's TRUE content. This
# keeps the Linux gate robust regardless of how the caller staged the tree.
if command -v sed >/dev/null 2>&1; then
    _crlf_n=0
    while IFS= read -r -d '' f; do
        if grep -qU $'\r' "$f" 2>/dev/null; then sed -i 's/\r$//' "$f" 2>/dev/null && _crlf_n=$((_crlf_n+1)); fi
    done < <(find "$ROOT" -type f -name '*.sh' -print0 2>/dev/null)
    [ "$_crlf_n" -gt 0 ] && info "normalized CRLF->LF in $_crlf_n *.sh file(s) (Windows-checkout artifact; box copy is disposable)"
fi

# =========================================================================
phase 1 "drop structure (plugins parse, marketplace-listed, installers + golden-path present)"
MKT="$ROOT/$PARTNER_MARKETPLACE"
if [ ! -f "$MKT" ]; then
    jam "drop-structural" "marketplace manifest missing: $PARTNER_MARKETPLACE" "a drop must not remove the partner marketplace manifest"
else
    pass "marketplace manifest present: $PARTNER_MARKETPLACE"
fi

for p in $PARTNER_PLUGINS; do
    pdir="$ROOT/plugins/$p"
    if [ ! -d "$pdir" ]; then
        jam "drop-structural" "plugins/$p absent" "upstream may no longer ship it, or the re-blit failed"; continue
    fi
    if [ ! -f "$pdir/plugin.json" ]; then
        jam "drop-structural" "plugins/$p/plugin.json missing" "the re-blitted plugin subtree is incomplete"; continue
    fi
    pj_name="$(_json_field "$pdir/plugin.json" name)"
    pj_ver="$(_json_field "$pdir/plugin.json" version)"
    if [ "$pj_name" != "$p" ]; then
        jam "drop-structural" "plugins/$p/plugin.json declares name '$pj_name'" "mismatched name breaks marketplace resolution"; continue
    fi
    if [ -z "$pj_ver" ]; then
        jam "drop-structural" "plugins/$p/plugin.json has no version" "every plugin drop must carry a version"; continue
    fi
    miss=""
    for inst in scripts/install.sh scripts/install.ps1; do
        [ -f "$pdir/$inst" ] || miss="$miss $inst"
    done
    if [ -n "$miss" ]; then
        jam "drop-structural" "plugins/$p missing installer(s):$miss" "setup delegates to these installers; a drop without them breaks setup"; continue
    fi
    listed=""
    [ -f "$MKT" ] && listed="$(_json_plugin_listed "$MKT" "$p")"
    if [ "$listed" != "$p" ]; then
        jam "drop-structural" "plugins/$p not listed in $PARTNER_MARKETPLACE" "the manifest and the drop disagree"; continue
    fi
    pass "plugin $p: plugin.json v$pj_ver, installers present, marketplace-listed"
done

if [ -f "$ROOT/$PARTNER_SETUP_ENTRY" ]; then pass "setup entrypoint present: $PARTNER_SETUP_ENTRY"
else jam "drop-structural" "setup entrypoint missing: $PARTNER_SETUP_ENTRY" "the drop is not a valid partner harness tree"; fi
if [ -f "$ROOT/$PARTNER_GOLDEN_DOC" ]; then pass "golden-path doc present: $PARTNER_GOLDEN_DOC"
else jam "drop-structural" "golden-path doc missing: $PARTNER_GOLDEN_DOC" "the partner's golden path is undocumented in this drop"; fi

# =========================================================================
phase 2 "read-only \`setup ${PARTNER_CHECK_ARGS}\` runs without crashing"
# The read-only check must RUN and report a coherent diagnostic code -- it may
# find missing prereqs on a fresh box (a non-zero "here is what to do" code); that
# is the check working, NOT a crash. What we reject is the script actually
# breaking: an unexpected exit code, or interpreter-level errors (syntax / CRLF /
# unbound var / a command the script itself failed to find).
if [ -f "$ROOT/$PARTNER_SETUP_ENTRY" ]; then
    ( cd "$ROOT" && capture "setup-check" -- bash "$PARTNER_SETUP_ENTRY" $PARTNER_CHECK_ARGS ) ; chk_rc=$?
    ok_code=1; for c in $PARTNER_CHECK_OK_CODES; do [ "$chk_rc" = "$c" ] && ok_code=0; done
    # interpreter-crash signatures: the SCRIPT itself is broken/mis-staged (a
    # syntax error, an unset-var abort, a bad `set` option -- CRLF trips all
    # three). NOT "<tool>: command not found" -- that is the check correctly
    # probing an absent prereq on a fresh box and reporting [MANUAL].
    crash=""
    if grep -qE "syntax error|unbound variable|invalid option name" "$CR_LOGDIR/setup-check.log" 2>/dev/null; then
        crash="$(grep -nE "syntax error|unbound variable|invalid option name" "$CR_LOGDIR/setup-check.log" 2>/dev/null | head -1)"
    fi
    if [ -n "$crash" ]; then
        jam "drop-structural" "read-only check hit an interpreter error: $crash -- see cr-logs/setup-check.log" \
            "the setup script is broken/mis-staged (syntax/CRLF/unbound); fix upstream before publishing"
    elif [ "$ok_code" = 0 ]; then
        pass "read-only check ran + reported a coherent diagnostic (exit $chk_rc; no interpreter error)"
    else
        jam "repo-config" "read-only check exited $chk_rc (expected one of: $PARTNER_CHECK_OK_CODES) -- see cr-logs/setup-check.log" \
            "the setup script's read-only check crashed or changed its exit contract; fix upstream before publishing"
    fi
else
    info "skipping check: no $PARTNER_SETUP_ENTRY"
fi

# =========================================================================
phase 3 "partner's OWN setup/update test suite passes"
# These are the partner's hermetic, stdlib-only setup-script tests -- the honest
# "validate the setup script" contract, run against THIS drop.
have_tests=1
for m in $PARTNER_SETUP_TESTS; do
    f="$ROOT/$(printf '%s' "$m" | tr '.' '/').py"
    [ -f "$f" ] || have_tests=0
done
if [ "$have_tests" = 1 ]; then
    ( cd "$ROOT" && capture "setup-tests" -- "$_PY" -m unittest $PARTNER_SETUP_TESTS ) ; t_rc=$?
    ran="$(grep -E '^Ran [0-9]+ tests' "$CR_LOGDIR/setup-tests.log" 2>/dev/null | tail -1)"
    if [ "$t_rc" = 0 ]; then
        pass "partner setup/update tests passed (${ran:-ok})"
    else
        jam "setup-script-contract" "partner setup/update tests FAILED (${ran:-see cr-logs/setup-tests.log}) -- $PARTNER_SETUP_TESTS" \
            "the drop breaks the partner's setup-script contract; fix upstream, do not publish"
    fi
else
    jam "setup-script-contract" "partner has no native setup tests ($PARTNER_SETUP_TESTS)" \
        "the partner cannot be behaviorally validated on this platform; validate on its native platform (see manifest notes)"
fi

# =========================================================================
cr_finalize
