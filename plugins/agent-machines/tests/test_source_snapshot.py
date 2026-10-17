"""The Bash source snapshot helper preserves and revalidates an assembled payload."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard
SIBLING = (
    Path(__file__).resolve().parents[2] / "agent-ssh" / "tests"
    / "test_source_install_transactions.py"
)
spec = importlib.util.spec_from_file_location("machines_source_fixture", SIBLING)
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
spec.loader.exec_module(fixture)


@pytest.mark.parametrize("corrupt_existing", (False, True))
def test_source_sh_snapshot_revalidates_reuse_without_clobber(
    tmp_path: Path, corrupt_existing: bool,
) -> None:
    payload, receipt = fixture._payload(tmp_path, "agent-machines")
    previous = fixture._previous(tmp_path)
    harness = tmp_path / "snapshot.sh"
    harness.write_text(
        fixture._sh_setup(tmp_path, "agent-machines", payload)
        + fixture._function("agent-machines", "_source_snapshot", "sh")
        + f"""
_initialize_source_runtime
_source_snapshot > {_quote(tmp_path / "first")}
SNAPSHOT="$INSTALL_DIR/snapshots/$SRC_VERSION"
if [[ {'1' if corrupt_existing else '0'} == 1 ]]; then
    printf corrupted >> "$SNAPSHOT/tests/hashed.txt"
fi
_source_snapshot > {_quote(tmp_path / "second")}
""", encoding="utf-8",
    )
    result = fixture._run([fixture._bash(), str(harness)], tmp_path)
    snapshot = tmp_path / "install" / "snapshots" / receipt["runtime_version"]
    if corrupt_existing:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "identity" in (result.stdout + result.stderr).lower()
        assert (snapshot / "tests" / "hashed.txt").read_text().endswith("corrupted")
        fixture._assert_previous(tmp_path, previous)
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "first").read_bytes() == (tmp_path / "second").read_bytes()
        assert fixture._identity_module("agent-machines").read_source_identity(
            snapshot, fixture.VERSION, plugin="agent-machines",
        )
        assert (snapshot / "node_modules" / "hashed.txt").read_bytes() == (
            payload / "node_modules" / "hashed.txt"
        ).read_bytes()


def _quote(path: Path) -> str:
    return fixture._sh(path)
