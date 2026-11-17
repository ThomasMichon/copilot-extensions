"""Real pytest progress remains diagnosable after bounded caller failure."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import bridge_capture_progress as progress
import capture_bridge_http48 as common
import capture_bridge_http52 as capture

ROOT = Path(__file__).resolve().parents[1]


def _last_node(observer: progress.ObserverSummary) -> str:
    event = observer["last_started"]
    assert event is not None
    node = event["nodeid"]
    assert isinstance(node, str)
    return node


def _fixture(tmp_path: Path, *, valid_source: bool = True) -> tuple[Path, dict[str, str], list[str]]:
    root = tmp_path / "source"
    tools = root / "tools"
    tools.mkdir(parents=True)
    for name in ("bridge_capture_progress.py", "bridge_http47_runtime.py"):
        shutil.copyfile(ROOT / "tools" / name, tools / name)
    owner = root / ".test-venvs" / "http47-capture-control"
    owner.mkdir(parents=True)
    request = owner / "request.json"
    content = (tools / "bridge_capture_progress.py").read_bytes()
    digest = hashlib.sha256(content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()
    payload = json.dumps({
        "root": str(root), "capture_generation": 52,
        "source_proof": {progress.SOURCE: {"sha256": digest if valid_source else "0" * 64}},
    }).encode()
    request.write_bytes(payload)
    test = root / "test_probe.py"
    test.write_text(
        "import os, time\n"
        "def test_controlled_probe():\n"
        "    if os.environ.get('PAUSE_CAPTURE_PROBE'):\n"
        "        time.sleep(30)\n"
        "    assert True\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(tools), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "BRIDGE_HTTP47_CAPTURE_REQUEST": str(request),
        "BRIDGE_HTTP47_CAPTURE_REQUEST_SHA256": hashlib.sha256(payload).hexdigest(),
    })
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    argv = [
        sys.executable, "-B", "-m", "pytest", "-q", "--noconftest", "-p", "no:cacheprovider",
        "--basetemp", str(root / "pytest-tmp"), str(test),
    ]
    return root, env, argv


def test_actual_pytest_success_restores_environment_and_cleans_only_its_journals(tmp_path: Path) -> None:
    root, env, argv = _fixture(tmp_path)
    prior = os.environ.get(progress.ENVIRONMENT)
    with progress.observe(root):
        directory = Path(os.environ[progress.ENVIRONMENT])
        env[progress.ENVIRONMENT] = str(directory)
        env["PYTEST_PLUGINS"] = os.environ["PYTEST_PLUGINS"]
        result = subprocess.run(
            argv, env=env, cwd=root, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        summary = progress.summarize(directory)
        assert summary["scope"] == "diagnostics-only; not capture evidence"
        observer, = summary["observers"]
        assert observer["started"] == observer["completed"] == 1
        assert _last_node(observer).endswith("::test_controlled_probe")
    assert not directory.exists()
    assert (root / ".test-venvs/http47-capture-control/request.json").is_file()
    assert os.environ.get(progress.ENVIRONMENT) == prior


def test_actual_pytest_timeout_preserves_last_started_probe_without_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root, env, argv = _fixture(tmp_path)
    env["PAUSE_CAPTURE_PROBE"] = "1"
    with pytest.raises(subprocess.TimeoutExpired):
        with progress.observe(root):
            directory = Path(os.environ[progress.ENVIRONMENT])
            env[progress.ENVIRONMENT] = str(directory)
            env["PYTEST_PLUGINS"] = os.environ["PYTEST_PLUGINS"]
            subprocess.run(
                argv, env=env, cwd=root, capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    assert directory.is_dir()
    summary = progress.summarize(directory)
    observer, = summary["observers"]
    assert observer["started"] == 1 and observer["completed"] == 0
    assert _last_node(observer).endswith("::test_controlled_probe")
    assert "diagnostics-only; not capture evidence" in capsys.readouterr().err


def test_actual_pytest_refuses_unbound_progress_source_before_probe(tmp_path: Path) -> None:
    root, env, argv = _fixture(tmp_path, valid_source=False)
    with pytest.raises(subprocess.CalledProcessError):
        with progress.observe(root):
            directory = Path(os.environ[progress.ENVIRONMENT])
            env[progress.ENVIRONMENT] = str(directory)
            env["PYTEST_PLUGINS"] = os.environ["PYTEST_PLUGINS"]
            subprocess.run(
                argv, env=env, cwd=root, capture_output=True, text=True, timeout=15, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    assert progress.summarize(directory)["observers"] == []


def test_progress_summary_refuses_aliased_journal_before_reading(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text('{"kind":"start","nodeid":"untrusted"}\n', encoding="utf-8")
    os.link(source, tmp_path / "observer-1.jsonl")
    with pytest.raises(ValueError, match="ordinary owned"):
        progress.summarize(tmp_path)


def test_http52_timeout_does_not_replay_probes_or_expand_existing_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    proof: dict[str, object] = {"selected": {"sha256": "fixture"}}
    vector: dict[str, object] = {"expected": "fixture"}

    def run(
        root: Path, selected: dict[str, object], inputs: dict[str, object], **options: object,
    ) -> dict[str, object]:
        assert root == tmp_path and selected is proof and inputs is vector
        calls.append(options)
        raise subprocess.TimeoutExpired(["controlled-probes"], 1)

    monkeypatch.setattr(common, "run_probes", run)
    with pytest.raises(subprocess.TimeoutExpired):
        capture.run_probes(tmp_path, proof, vector)
    assert len(calls) == 1
    assert calls[0]["aggregate_timeout"] == (1200 if os.name == "nt" else 240)
    assert calls[0]["expected_roles"] == frozenset({"cli", "record-writer", "roster-source"})
    assert calls[0]["isolated_probes"] == frozenset({
        "test_retained_acquisition_and_later_lineage_use_actual_owner_processes",
        "test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim",
    })
