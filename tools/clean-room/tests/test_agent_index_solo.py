"""Bounded assertion/parser guard for the released Index scenario."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios/agent-index-solo"
SPEC = importlib.util.spec_from_file_location("index_solo_witness", SCENARIO / "witness.py")
assert SPEC and SPEC.loader
WITNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WITNESS)


@pytest.mark.parametrize("prefix", ["", "  $ /payload/bin/agent-index status\n"])
def test_captured_json_state_is_strict(tmp_path: Path, prefix: str) -> None:
    path = tmp_path / "status.log"
    data = {"state": "setup_required", "runtime": {"state": "absent"}, "running": False}
    path.write_text(prefix + json.dumps(data) + "\n", encoding="utf-8")
    WITNESS.main(["state", str(path), "setup_required", "absent"])
    data["state"] = "inactive"
    path.write_text(prefix + json.dumps(data), encoding="utf-8")
    with pytest.raises(AssertionError):
        WITNESS.main(["state", str(path), "setup_required", "absent"])


def test_version_is_exact_and_only_normalizes_dev_spelling() -> None:
    for text in ("0.10.15-dev1\n", "agent-index 0.10.15.dev1\n"):
        assert WITNESS.reports_version(text, "0.10.15-dev1")
    for text in ("0.10.15.dev10\n", "not-agent-index 0.10.15.dev1\n", "inactive"):
        assert not WITNESS.reports_version(text, "0.10.15-dev1")


def test_interpreter_action_returns_only_owned_runtime(tmp_path: Path, capsys) -> None:
    python = tmp_path / "versions/test/bin/python"
    manifest = tmp_path / "deploy-manifest.json"
    manifest.write_text(json.dumps({"runtime": {"interpreter": str(python)}}))
    WITNESS.main(["interpreter", str(tmp_path)])
    assert capsys.readouterr().out.strip() == str(python)
    manifest.write_text(json.dumps({"runtime": {"interpreter": str(tmp_path / "outside")}}))
    with pytest.raises(AssertionError):
        WITNESS.main(["interpreter", str(tmp_path)])


def test_setup_parser_uses_final_role_not_provisioning_receipt(tmp_path: Path) -> None:
    path = tmp_path / "setup.log"
    path.write_text('command\n{"status":"ready","engineProvisioned":false}\n'
                    '{"role":"host","service":{"started_by_setup":false}}\n')
    WITNESS.main(["role", str(path), "host"])


def test_hook_stamp_is_not_runtime_readiness(tmp_path: Path) -> None:
    path = tmp_path / "status.log"
    path.write_text(json.dumps({
        "state": "setup_required", "runtime": {"state": "stamped"}, "running": False,
    }), encoding="utf-8")
    with pytest.raises(AssertionError):
        WITNESS.main(["state", str(path), "setup_required", "absent"])
    WITNESS.main(["state", str(path), "setup_required", "stamped"])


@pytest.mark.parametrize("artifact", ["versions", "deploy-manifest.json", "engine", "run"])
def test_stamped_only_rejects_every_deployment_artifact(tmp_path: Path, artifact: str) -> None:
    root = tmp_path / ".agent-index"
    root.mkdir()
    (root / "stamped-version").write_text("test")
    WITNESS.assert_dormant(tmp_path, None, stamped=True)
    (root / artifact).mkdir()
    with pytest.raises(AssertionError, match="more than metadata"):
        WITNESS.assert_dormant(tmp_path, None, stamped=True)


def test_dormant_rejects_service_or_global_stamp(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cell = home / "cell"
    cell.mkdir(parents=True)
    WITNESS.assert_dormant(home, cell)
    (cell / "current-version").write_text("fake", encoding="utf-8")
    with pytest.raises(AssertionError, match="runtime artifact"):
        WITNESS.assert_dormant(home, cell)


def test_manifest_and_execution_keep_release_and_safety_contract() -> None:
    manifest = json.loads((SCENARIO / "manifest.json").read_text(encoding="utf-8"))
    source = (SCENARIO / "scenario.sh").read_text(encoding="utf-8")
    assert [stage["n"] for stage in manifest["stages"]] == list(range(6))
    assert manifest["tier"] == "P"
    assert "CR_RELEASE_REF" in manifest["config"]
    assert source.count('copilot -p "Reply with the single word: ready."') == 1
    assert "AGENT_INDEX_NO_ENGINE_DEPS=1" in source
    assert 'export AGENT_INDEX_REPO="$REPO"' in source
    assert source.index('export AGENT_INDEX_REPO="$REPO"') < source.index("_state status-inactive")
    assert "::agent-provisioning::" in source
    assert "materialized-candidate-not-shipped" in source
    assert 'printf \'{"version"' not in source
    assert "|| true" not in source
