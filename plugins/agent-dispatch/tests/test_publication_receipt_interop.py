"""Actual opaque producers remain native-domain proofs when called from Bash."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from . import source_publication_fixture as fixture
from .test_install_sh_activation_guard import _resolve_bash


@pytest.mark.parametrize("producer", ["redirects", "activate", "session-stamp"])
@pytest.mark.parametrize("peer", [False, True])
def test_native_python_receipt_is_not_a_git_bash_inode_claim(
    tmp_path: Path, peer: bool, producer: str,
) -> None:
    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    root = tmp_path / "runtime"
    (root / "versions/2.0.0").mkdir(parents=True)
    root.chmod(0o700)
    payload = tmp_path / "payload"
    payload.mkdir()
    repository = Path(__file__).parents[3]
    result = subprocess.run(
        [bash, "-c", f"""
set -euo pipefail
. '{(repository / "libs/installer-engine/installer-engine.sh").as_posix()}'
INSTALL_DIR='{root.as_posix()}'; SCRIPT_DIR='{(repository / "libs/versioned-runtime").as_posix()}'
SRC_VERSION=2.0.0
_bootstrap_python() {{ printf '%s' '{Path(os.path.realpath(sys._base_executable)).as_posix()}'; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
payload="$INSTALL_DIR/payload-dir"; stamped="$INSTALL_DIR/stamped-version"
current="$INSTALL_DIR/current-version"; good="$INSTALL_DIR/last-known-good"; launcher="$INSTALL_DIR/launcher"
printf original > "$payload"; printf old > "$stamped"; printf 1.0.0 > "$current"
printf 1.0.0 > "$good"; printf original > "$launcher"; chmod 600 "$launcher"
_runtime_publication_begin "$payload" "$stamped" "$current" "$good" "$launcher"
python="$(_bootstrap_python)"
native_payload="$("$python" -I -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' '{payload.as_posix()}')"
if [[ '{producer}' == redirects ]]; then
    target="$payload"; wanted="$native_payload"
    _runtime_publication_expect_value "$payload" "$native_payload"$'\\n'
    _runtime_publication_expect_value "$stamped" '2.0.0'$'\\n'
elif [[ '{producer}' == activate ]]; then
    target="$current"; wanted=2.0.0
    _runtime_publication_expect_value "$current" '2.0.0'$'\\n'
    _runtime_publication_expect_value "$good" '2.0.0'$'\\n'
else
    target="$launcher"; wanted=candidate
    _runtime_publication_expect_value "$launcher" 'candidate'$'\\n'
fi
_runtime_publication_native_begin receipt_file
if [[ '{producer}' == redirects ]]; then
    "$python" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" publish-redirects \
        --payload-dir "$native_payload" --version 2.0.0 --ownership-receipt "$receipt_file"
elif [[ '{producer}' == activate ]]; then
    "$python" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" activate 2.0.0 \
        --no-link --ownership-receipt "$receipt_file"
else
    "$python" -I -B '{(repository / "plugins/agent-index/scripts/session-stamp.py").as_posix()}' \
        publish "$launcher" metadata --ownership-receipt "$receipt_file" --publication-root "$INSTALL_DIR" <<< candidate
fi
if [[ '{int(peer)}' == 1 ]]; then
    "$python" -I -c 'import os,sys; from pathlib import Path; p=Path(sys.argv[1]); q=p.with_name("peer"); q.write_bytes(p.read_bytes()); os.replace(q,p)' "$target"
    if _runtime_publication_accept "$target"; then exit 90; fi
    if _runtime_publication_restore; then exit 91; fi
    [[ "$(cat "$target")" == "$wanted" && -s "$_RUNTIME_PUBLICATION_EVIDENCE/journal" ]]
else
    _runtime_publication_accept "$target"
    _runtime_publication_restore
    [[ "$(cat "$payload")" == original && "$(cat "$stamped")" == old &&
       "$(cat "$current")" == 1.0.0 && "$(cat "$launcher")" == original ]]
fi
if [[ '{int(peer)}' == 0 || '{producer}' != activate ]]; then [[ "$(cat "$current")" == 1.0.0 ]]; fi
"""],
        env=fixture.environment(tmp_path), cwd=tmp_path, capture_output=True, text=True,
        timeout=25, creationflags=fixture.HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_shell_commit_rejects_real_directory_race(tmp_path: Path) -> None:
    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    engine = Path(__file__).parents[3] / "libs/installer-engine/installer-engine.sh"
    result = subprocess.run(
        [bash, "-c", f"""
set -euo pipefail
. '{engine.as_posix()}'
INSTALL_DIR='{root.as_posix()}'; SRC_VERSION=2.0.0
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
target="$INSTALL_DIR/board"; printf original > "$target"
_runtime_publication_begin "$target"
_runtime_publication_stage "$target" stage
printf candidate > "$stage"
mv() {{
    command rm -f "$target"; command mkdir "$target"; printf peer > "$target/sentinel"
    command mv "$@"
}}
if _runtime_publication_commit_file "$target" "$stage"; then exit 90; fi
[[ -d "$target" && "$(cat "$target/sentinel")" == peer && -f "$stage" ]]
[[ ! -e "$target/board" && -s "$_RUNTIME_PUBLICATION_EVIDENCE/journal" ]]
if _runtime_publication_restore; then exit 91; fi
"""],
        env=fixture.environment(tmp_path), cwd=tmp_path, capture_output=True, text=True,
        timeout=20, creationflags=fixture.HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
