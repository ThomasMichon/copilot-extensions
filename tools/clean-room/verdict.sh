#!/usr/bin/env bash
# verdict.sh -- reduce a clean-room cr-report.json to a UNIFORM machine verdict.
#
# The scenarios emit a rich cr-report.json (phases + jams + env + results). A
# consuming gate (the odsp-web-harness re-blit sync, the agent-harness-plugins
# mirror, a CI job) just needs a single, stable PASS/FAIL verdict + exit code,
# without parsing the whole report. This is that thin adapter -- the "consistent
# way to handle things" across partners.
#
# Usage:
#   verdict.sh --report <cr-report.json> [--pretty]
#
# Output (one line of JSON on stdout unless --pretty):
#   { "ok": bool, "scenario": str, "passed": int, "failed": int,
#     "degraded": bool, "jams": [ { "category", "evidence", "hint" } ] }
#
# Exit: 0 iff ok (failed == 0). 1 on validation failure. 2 on usage/parse error.
#
# `degraded` is true when the only reason the run is not a clean, fully-exercised
# PASS is an environment gap (a `validator-env` jam) rather than a real product
# failure -- a caller running with --require-validation treats degraded as a hold.
set -uo pipefail

REPORT=""
PRETTY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --report) REPORT="$2"; shift 2 ;;
        --pretty) PRETTY=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) printf 'verdict.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
    printf 'verdict.sh: --report <cr-report.json> required (got: %s)\n' "${REPORT:-<none>}" >&2
    exit 2
fi

PY="$(command -v python3 || command -v python || echo '')"
if [ -n "$PY" ]; then
    "$PY" - "$REPORT" "$PRETTY" <<'PY'
import json, sys
report, pretty = sys.argv[1], sys.argv[2] == "1"
try:
    with open(report) as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write("verdict.sh: cannot parse %s: %s\n" % (report, e))
    sys.exit(2)
failed = int(d.get("failed", 0) or 0)
jams = [
    {"category": j.get("category", ""), "evidence": j.get("evidence", ""), "hint": j.get("hint", "")}
    for j in d.get("jams", []) or []
]
# environment-only gaps (not a product failure) -> degraded, not a hard fail signal
env_cats = {"validator-env"}
degraded = failed > 0 and all(j["category"] in env_cats for j in jams) and len(jams) == failed
verdict = {
    "ok": failed == 0,
    "scenario": d.get("scenario", ""),
    "passed": int(d.get("passed", 0) or 0),
    "failed": failed,
    "degraded": bool(degraded),
    "jams": jams,
}
sys.stdout.write(json.dumps(verdict, indent=2 if pretty else None) + "\n")
sys.exit(0 if verdict["ok"] else 1)
PY
    exit $?
fi

# --- python-less fallback: grep the two top-level counters ------------------
passed="$(grep -oE '"passed"[[:space:]]*:[[:space:]]*[0-9]+' "$REPORT" | grep -oE '[0-9]+' | head -1)"
failed="$(grep -oE '"failed"[[:space:]]*:[[:space:]]*[0-9]+' "$REPORT" | grep -oE '[0-9]+' | head -1)"
passed="${passed:-0}"; failed="${failed:-0}"
scenario="$(grep -oE '"scenario"[[:space:]]*:[[:space:]]*"[^"]*"' "$REPORT" | sed -E 's/.*:"([^"]*)"/\1/' | head -1)"
ok=false; [ "$failed" = "0" ] && ok=true
printf '{"ok":%s,"scenario":"%s","passed":%s,"failed":%s,"degraded":false,"jams":[]}\n' \
    "$ok" "$scenario" "$passed" "$failed"
[ "$failed" = "0" ]
