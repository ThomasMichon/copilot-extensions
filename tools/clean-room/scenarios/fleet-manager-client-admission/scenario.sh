#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=fleet-manager-client-admission
cr_init
phase 0 "fresh environment and exact candidate"
envdump
INPUT="${CR_HARNESS_MOUNT:-}"
if ! FRESH="$(mktemp -d)"; then
    jam "repo-config" "private scenario workspace could not be created"
    cr_finalize
fi
if [ -z "$INPUT" ] || [ ! -f "$INPUT/candidate.tar" ]; then
    jam "path-binstub" "exact candidate archive is absent" "pass -HarnessMount <archive directory>"
    cr_finalize
fi
if command -v fleet-manager >/dev/null 2>&1 || [ -e "$HOME/.fleet-manager" ]; then
    jam "repo-config" "client state already exists" "use a fresh scenario container"
    cr_finalize
fi
if ! capture "extract-candidate" -- timeout 120 python3 - "$INPUT/candidate.tar" "$FRESH/source" <<'PY'
import sys,tarfile
from pathlib import Path
archive,target=map(Path,sys.argv[1:])
target.mkdir()
with archive.open("rb",buffering=1024*1024) as stream:
    with tarfile.open(fileobj=stream,mode="r|") as source:
        source.extractall(target,filter="data")
PY
then
    jam "path-binstub" "candidate snapshot extraction failed" "inspect extract-candidate.log"
    cr_finalize
fi
SOURCE="$FRESH/source"
if [ ! -f "$SOURCE/fleet-manager/src/fleet_manager/client_admission.py" ]; then
    jam "repo-config" "candidate lacks client admission" "use the exact implementation archive"
    cr_finalize
fi
if ! DIGEST="$(sha256sum "$SOURCE/fleet-manager/src/fleet_manager/client_admission.py" | cut -d' ' -f1)"; then
    jam "repo-config" "candidate identity could not be read"
    cr_finalize
fi
cr_meta "candidate_client_sha256" "$DIGEST"
if [ -n "${CR_UV_INDEX:-}" ]; then
    export UV_DEFAULT_INDEX="$CR_UV_INDEX" UV_INDEX_URL="$CR_UV_INDEX" PIP_INDEX_URL="$CR_UV_INDEX"
fi
pass "fresh client environment and exact candidate"
phase 1 "governed client test environment"
cd "$SOURCE" || { jam "repo-config" "candidate directory unavailable"; cr_finalize; }
if ! capture "track-candidate" -- bash -c 'git init -q && git -c core.safecrlf=false add .'; then
    jam "repo-config" "candidate tracking failed" "inspect track-candidate.log"
    cr_finalize
fi
if ! IGNORE="$(python3 - <<'PY'
from pathlib import Path
print(" ".join('--ignore="tests/'+path.name+'"' for path in sorted(
    Path("fleet-manager/tests").glob("test_*.py"))
    if path.name!="test_artifact_staging.py"))
PY
)"; then
    jam "repo-config" "consumer test selection could not be constructed"
    cr_finalize
fi
if ! capture "declared-test-environment" -- env PYTEST_ADDOPTS="$IGNORE --collect-only" \
    timeout 300 python3 tools/run-standalone-tests.py fleet-manager --prepare --timeout 120; then
    jam "toolchain-uv" "governed client prerequisite preparation failed" "inspect declared-test-environment.log"
    cr_finalize
fi
pass "declared test environment prepared without a policy or feed waiver"
phase 2 "actual installed thin-client console"
SELECT="fresh_installed_console_admission or missing_packaged_resource"
if ! capture "installed-client-consumer" -- env \
    PYTEST_ADDOPTS="$IGNORE -k '$SELECT' --junitxml='$FRESH/consumer.xml'" \
    timeout 180 python3 tools/run-standalone-tests.py fleet-manager --timeout 120; then
    jam "repo-config" "installed client consumer contract failed" "inspect installed-client-consumer.log"
    cr_finalize
fi
phase 3 "non-skipped consumer oracle"
if capture "consumer-oracle" -- python3 "$_SELF_DIR/verify.py" "$FRESH/consumer.xml"; then
    pass "real installed admission, retry, revocation and durable reopen; no hosting or source fallback"
else
    jam "pythonpath" "consumer oracle did not prove both real contracts" "inspect consumer-oracle.log"
fi
cr_finalize
