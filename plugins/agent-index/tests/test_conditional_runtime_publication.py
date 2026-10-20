"""Conditional POSIX publication keeps the prior operative runtime on failure."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import stat
import subprocess
import re
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "_index_conditional_publication_fixtures",
    Path(__file__).with_name("test_retained_host_runtime_health.py"),
)
assert SPEC and SPEC.loader
FIXTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURES)
source = FIXTURES.source
function = FIXTURES.function

INJECTION = r'''
if [[ -n "${FIXTURE_PEER_PYTHON:-}" ]]; then
    _bootstrap_python() { printf '%s' "$FIXTURE_PEER_PYTHON"; }
fi
_fixture_fail() {
    [[ "$FIXTURE_FAILURE" == "$1" && ! -e "$FIXTURE_FAILURE_LOG" ]]
}
_fixture_record() { builtin printf '%s\n' "$1" > "$FIXTURE_FAILURE_LOG"; }
mkdir() {
    if _fixture_fail mkdir; then
        local arg
        for arg in "$@"; do
            if [[ "$arg" == "$LOCAL_BIN" ]]; then _fixture_record mkdir; return 73; fi
        done
    fi
    command mkdir "$@"
}
cp() {
    if _fixture_fail copy && [[ "${@: -1}" == "$INSTALL_DIR/bin/.resolve-runtime.ps1.publication-stage."*/resolve-runtime.ps1 ]]; then
        command head -c 12 "${@: -2:1}" > "${@: -1}"
        _fixture_record copy
        return 73
    fi
    command cp "$@"
}
mv() {
    if _fixture_fail manifest &&
        [[ "${@: -2:1}" == "$INSTALL_DIR/.deploy-manifest.json.publication-stage."*/deploy-manifest.json ]]; then
        _fixture_record manifest
        return 73
    fi
    command mv "$@"
}
cat() {
    if _fixture_fail write && [[ /dev/fd/1 -ef "${STUB_STAGE:-$STUB}" ]]; then
        command head -c 12
        _fixture_record write
        return 73
    fi
    command cat "$@"
}
'''


def snapshot(paths: list[Path]) -> dict[Path, tuple[bytes, int] | None]:
    return {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
        for path in paths
    }


@pytest.mark.parametrize(("failure", "action"), [
    ("mkdir", "install"), ("copy", "install"), ("write", "install"),
    ("manifest", "install"), ("copy", "update"), ("mkdir", "provision"),
    ("copy", "stamp"), ("write", "stamp"), ("peer", "stamp"),
])
def test_conditional_publication_failure_keeps_healthy_selection(source, tmp_path, failure, action):
    if os.name == "nt":
        pytest.skip("POSIX failure injection")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    api, payload, identity = source
    root = tmp_path / "runtime"
    previous = root / "versions" / "1.0.0"
    candidate = root / "versions" / identity.runtime_version
    _, old_site = FIXTURES.venv(previous)
    FIXTURES.venv(candidate)
    (old_site / "agent_index" / "__main__.py").write_text(
        'print("agent-index 1.0.0")\n', encoding="utf-8",
    )
    runtime = api._runtime()
    runtime.mark_complete(root, "1.0.0", payload_hash="e" * 64)
    runtime.activate(root, "1.0.0", link_free=True)
    runtime.mark_complete(
        root, identity.runtime_version,
        payload_hash=hashlib.sha256(json.dumps(api.manifest_fields(identity), sort_keys=True).encode()).hexdigest(),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reader_dir = root / "bin"
    reader_dir.mkdir()
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
        shutil.copy2(FIXTURES.SCRIPTS / name, reader_dir / name)
    stub = bin_dir / "agent-index"
    stub.write_text(
        f"#!/usr/bin/env bash\nexport AGENT_RT_ROOT='{root}'\n"
        f". '{reader_dir / 'resolve-runtime.sh'}'\n"
        '[[ -n "$AGENT_RT_PY" ]] || exit 125\nexec "$AGENT_RT_PY" -m agent_index "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    previous_payload = tmp_path / "previous-payload"
    previous_payload.mkdir()
    (root / "payload-dir").write_text(str(previous_payload), encoding="utf-8")
    (root / "stamped-version").write_text("1.0.0", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({"schema_version": 3, "runtime_version": "1.0.0", "venv": str(previous)}),
        encoding="utf-8",
    )
    paths = [root / name for name in (
        "current-version", "last-known-good", "payload-dir", "stamped-version",
        "deploy-manifest.json", "bin/resolve-runtime.sh", "bin/resolve-runtime.ps1",
    )] + [stub]
    root.chmod(0o700)
    bin_dir.chmod(0o700)
    reader_dir.chmod(0o700)
    for path in paths[:-1]:
        if path.exists():
            path.chmod(0o600)
    before = snapshot(paths)
    completion = (candidate / ".install-complete.json").read_bytes()
    env = FIXTURES.FIXTURES._FIXTURES.environment(tmp_path)
    env["FIXTURE_FAILURE"] = failure
    env["FIXTURE_FAILURE_LOG"] = str(tmp_path / "injected")
    if failure == "peer":
        peer_spec = importlib.util.spec_from_file_location(
            "_index_peer_failure_fixture",
            Path(__file__).parents[2] / "agent-dispatch/tests/source_publication_fixture.py",
        )
        assert peer_spec and peer_spec.loader
        peer_fixture = importlib.util.module_from_spec(peer_spec)
        peer_spec.loader.exec_module(peer_fixture)
        env["FIXTURE_PEER_PYTHON"] = str(peer_fixture.peer_failure_python(tmp_path))
    definitions = "\n".join(function(name, "sh") for name in (
        "_ensure_runtime", "_test_index_venv", "_runtime_origin_under",
        "_with_index_build_lock", "_with_index_publication_lock", "_with_index_advisory_lock",
        "_payload_hash", "_versioned_slot_clean", "_versioned_mark_complete",
        "_version_lt", "_index_publication_fresh", "_versioned_current", "_versioned_activate",
        "deploy_binstub", "_write_manifest", "_write_index_stamp_file", "do_stamp", "_source_kind",
    ))
    harness = tmp_path / "conditional.sh"
    harness.write_bytes(f"""
set -euo pipefail
SCRIPT_DIR='{FIXTURES.SCRIPTS}'
PLUGIN_DIR='{payload}'
PKG_SRC_DIR="$PLUGIN_DIR/src"
INSTALL_DIR='{root}'
LOCAL_BIN='{bin_dir}'
STUB='{stub}'
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION='{FIXTURES.FIXTURES.PACKAGE}'
VERSIONED_RUNTIME=1
FORCE=0
. '{FIXTURES.ENGINE / "installer-engine.sh"}'
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}
_warn() {{ printf '%s\\n' "$*" >&2; }}; _fail() {{ printf '%s\\n' "$*" >&2; }}
{definitions}
_bootstrap_python() {{ printf '%s' '{FIXTURES.BASE}'; }}
_find_python() {{ printf '%s' '{FIXTURES.BASE}'; }}
_ensure_uv() {{ :; }}; _ensure_uv_index() {{ :; }}
_activation_role() {{ printf client; }}
_versioned_gc() {{ :; }}
{INJECTION}
_initialize_source_runtime
if [[ '{action}' == stamp ]]; then
    if do_stamp; then exit 0; else exit $?; fi
else
    if _ensure_runtime; then exit 0; else exit $?; fi
fi
""".encode("utf-8"))
    result = subprocess.run(
        [bash, str(harness)], env=env, capture_output=True, text=True, timeout=25,
    )
    assert Path(env["FIXTURE_FAILURE_LOG"]).read_text().strip() == failure
    assert result.returncode != 0, result.stdout + result.stderr
    if failure == "peer":
        assert (root / "payload-dir").read_bytes() == b"peer-owned-redirect\n"
        assert (root / "current-version").read_bytes() == before[root / "current-version"][0]
        journals = list(root.glob(".publication.????????/journal"))
        assert journals and all(path.stat().st_size for path in journals), result.stdout + result.stderr
        assert any(
            path.read_bytes() == before[root / "payload-dir"][0]
            for journal in journals for path in journal.parent.glob("before.*")
        )
        assert {path: value for path, value in snapshot(paths).items() if path != root / "payload-dir"} == {
            path: value for path, value in before.items() if path != root / "payload-dir"
        }
        assert (candidate / ".install-complete.json").read_bytes() == completion
        assert "retained" in result.stderr.lower()
        return
    assert snapshot(paths) == before
    backups = [path for path in root.rglob("*.publication.*")
               if re.search(r"\.publication\.[A-Za-z0-9]{8}$", path.name)]
    assert not backups, result.stdout + result.stderr
    assert not list(bin_dir.glob("*.publication.????????")), result.stdout + result.stderr
    assert (candidate / ".install-complete.json").read_bytes() == completion
    result = subprocess.run(
        [bash, str(stub), "--version"], env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "agent-index 1.0.0"
