"""Bounded assertion/parser guard for the released Index scenario."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
import venv

import pytest

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios/agent-index-solo"
SPEC = importlib.util.spec_from_file_location("index_solo_witness", SCENARIO / "witness.py")
assert SPEC and SPEC.loader
WITNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WITNESS)


@pytest.mark.parametrize("returncode", [0, 2])
def test_managed_host_mcp_witness_requires_missing_extra_refusal(monkeypatch, returncode):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0 if len(calls) == 1 else returncode,
            "", "explicit [stdio-mcp] extra required"
        )

    monkeypatch.setattr(WITNESS.subprocess, "run", run)
    if returncode == 2:
        WITNESS.main(["mcp", "fixture-python"])
    else:
        with pytest.raises(AssertionError):
            WITNESS.main(["mcp", "fixture-python"])
    assert "find_spec('mcp') is None" in calls[0][-1]
    assert calls[1] == ["fixture-python", "-I", "-m", "agent_index", "mcp"]


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
    slot = tmp_path / "versions/test"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    manifest = tmp_path / "deploy-manifest.json"
    manifest.write_text(json.dumps({"runtime": {"interpreter": str(python)}}))
    WITNESS.main(["interpreter", str(tmp_path)])
    assert capsys.readouterr().out.strip() == str(python)
    manifest.write_text(json.dumps({"runtime": {"interpreter": str(tmp_path / "outside")}}))
    with pytest.raises(AssertionError):
        WITNESS.main(["interpreter", str(tmp_path)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv executable/directory links")
@pytest.mark.parametrize("fault", ["traversal", "versions-link", "slot-link", "bin-link"])
def test_interpreter_rejects_escaping_directory_identity(tmp_path: Path, fault: str) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    foreign = tmp_path / "foreign"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(foreign)
    versions = root / "versions"
    if fault == "versions-link":
        versions.symlink_to(tmp_path, target_is_directory=True)
        python = versions / "foreign/bin/python"
    else:
        versions.mkdir()
        if fault == "traversal":
            python = versions / "../../foreign/bin/python"
        elif fault == "slot-link":
            (versions / "test").symlink_to(foreign, target_is_directory=True)
            python = versions / "test/bin/python"
        else:
            (versions / "test").mkdir()
            (versions / "test/bin").symlink_to(foreign / "bin", target_is_directory=True)
            python = versions / "test/bin/python"
    assert python.is_file(), "adversary must name a genuine, reachable interpreter"
    (root / "deploy-manifest.json").write_text(
        json.dumps({"runtime": {"interpreter": str(python)}})
    )
    with pytest.raises(AssertionError, match="traversal|directory link"):
        WITNESS.main(["interpreter", str(root)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv final executable link")
def test_interpreter_accepts_real_venv_final_python_link(tmp_path: Path, capsys) -> None:
    slot = tmp_path / "versions/test"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(slot)
    python = slot / "bin/python"
    assert python.is_symlink()
    assert not python.resolve(strict=True).is_relative_to(tmp_path)
    (tmp_path / "deploy-manifest.json").write_text(
        json.dumps({"runtime": {"interpreter": str(python)}})
    )
    WITNESS.main(["interpreter", str(tmp_path)])
    assert capsys.readouterr().out.strip() == str(python)


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


def test_stamped_only_accepts_empty_resolver_logs(tmp_path: Path) -> None:
    (tmp_path / ".agent-index/logs").mkdir(parents=True)
    WITNESS.assert_dormant(tmp_path, None, stamped=True)


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
    assert source.count('cr_meta release_sha "$RELEASE_SHA"') == 1
    assert source.index('cr_meta release_sha "$RELEASE_SHA"') > source.index(
        'release pin did not resolve to the requested commit'
    )
    assert source.index("_run candidate-source") < source.index("_run marketplace-add")
    assert 'printf \'{"version"' not in source
    assert "|| true" not in source


def test_candidate_witness_uses_trusted_whole_tree_receipt(tmp_path: Path, capsys) -> None:
    from materialized_provenance import content_manifest, RECEIPT, SCHEMA

    head = "a" * 40
    (tmp_path / "plugin.json").write_text('{"version":"test"}')
    receipt = {"schema": SCHEMA, "version": 1, "source_commit": head,
               "paths": content_manifest(tmp_path)}
    (tmp_path / RECEIPT).write_text(json.dumps(receipt))
    WITNESS.main(["candidate", str(tmp_path), head])
    assert capsys.readouterr().out.strip() == head
    with pytest.raises(ValueError, match="expected HEAD"):
        WITNESS.main(["candidate", str(tmp_path), "b" * 40])
    (tmp_path / "plugin.json").write_text('{"version":"tampered"}')
    with pytest.raises(ValueError, match="content manifest"):
        WITNESS.main(["candidate", str(tmp_path), head])


def test_proposal_links_resolve_and_scenario_docs_are_timeless() -> None:
    root = SCENARIO.parents[3]
    proposal = root / "efforts/active/vendored-installer-engine/temporary-container-obligations.md"
    text = proposal.read_text()
    for target in ("../../../visions/agent-fabric/README.md",
                   "../../../docs/patterns/a-la-carte-independence.md"):
        assert target in text
        assert (proposal.parent / target).is_file()
    assert "../../../../" not in text
    readme = (SCENARIO / "README.md").read_text()
    assert "| Contract area | Required behavior and coverage |" in readme
    assert "Previous expectation" not in readme
