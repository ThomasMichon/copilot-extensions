from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def generation():
    path = Path(__file__).resolve().parents[1] / "scripts" / "runtime-generation.py"
    spec = importlib.util.spec_from_file_location("codespaces_generation_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime(root, *, current=None, previous=None, admission="construct"):
    return SimpleNamespace(
        ADMIT_REUSE="reuse",
        current_version=lambda _: current,
        read_last_known_good=lambda _: previous,
        version_dir=lambda _, version: root / version,
        check_admission=lambda *_, **__: admission,
        slot_python=lambda _, version: root / version / "python",
    )


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3+source." + "a" * 40 + "." + "b" * 16])
def test_occupied_unusable_generation_is_not_reconstructed(tmp_path, monkeypatch, generation, version):
    slot = tmp_path / version
    slot.mkdir()
    (slot / "preserve").write_bytes(b"old")
    monkeypatch.setattr(generation, "_runtime", lambda: _runtime(tmp_path))

    selected = generation.select_generation(tmp_path, version, "payload")

    assert selected.startswith(version + (".repair." if "+" in version else "+repair."))
    assert not (tmp_path / selected).exists()
    assert (slot / "preserve").read_bytes() == b"old"


def test_exact_healthy_selected_repair_reused_without_base_probe(tmp_path, monkeypatch, generation):
    selected = "1.2.3+repair." + "a" * 32
    runtime = _runtime(tmp_path, current=selected, admission="reuse")
    probes = []
    monkeypatch.setattr(generation, "_runtime", lambda: runtime)
    monkeypatch.setattr(generation.subprocess, "run", lambda args, **_: (
        probes.append(args) or SimpleNamespace(returncode=0)
    ))

    assert generation.select_generation(tmp_path, "1.2.3", "payload") == selected
    assert len(probes) == 1
    assert Path(probes[0][0]) == tmp_path / selected / "python"


def test_other_source_repair_is_not_reused(tmp_path, monkeypatch, generation):
    runtime = _runtime(tmp_path, current="1.2.3+source.other.repair." + "a" * 32, admission="reuse")
    monkeypatch.setattr(generation, "_runtime", lambda: runtime)
    monkeypatch.setattr(generation.subprocess, "run", lambda *_a, **_kw: pytest.fail("foreign probe"))

    assert generation.select_generation(tmp_path, "1.2.3+source.incoming", "payload") == (
        "1.2.3+source.incoming"
    )


def test_malformed_old_selector_does_not_veto_incoming(tmp_path, monkeypatch, generation):
    runtime = _runtime(tmp_path)

    def unreadable(_):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid old selector")

    runtime.current_version = unreadable
    monkeypatch.setattr(generation, "_runtime", lambda: runtime)
    assert generation.select_generation(tmp_path, "1.2.3", "payload") == "1.2.3"


@pytest.mark.parametrize("record", ["matching", "changed-receipt", "missing"])
def test_source_repair_reuse_requires_exact_deployment_receipt(
    tmp_path, monkeypatch, generation, record,
):
    base = "1.2.3+source.incoming"
    selected = base + ".repair." + "a" * 32
    expected = {"runtime_source": {"source_tree": "original"}}
    runtime = _runtime(tmp_path, current=selected, admission="reuse")
    runtime.check_admission = lambda _, version, **__: "reuse" if version == selected else "construct"
    (tmp_path / base).mkdir()
    if record != "missing":
        (tmp_path / "deploy-manifest.json").write_text(json.dumps({
            "runtime_version": selected,
            "runtime_source": {"source_tree": "original" if record == "matching" else "changed"},
        }))
    monkeypatch.setattr(generation, "_runtime", lambda: runtime)
    monkeypatch.setattr(generation.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(returncode=0))

    candidate = generation.select_generation(tmp_path, base, "payload", expected)

    assert (candidate == selected) is (record == "matching")
    assert candidate.startswith(base + ".repair.")


@pytest.mark.parametrize("failure", ["exit", "timeout", "spawn"])
def test_failed_old_health_never_selects_incumbent(tmp_path, monkeypatch, generation, failure):
    (tmp_path / "1.2.3").mkdir()
    selected = "1.2.3+repair." + "a" * 32
    runtime = _runtime(tmp_path, current=selected, previous=selected, admission="reuse")
    monkeypatch.setattr(generation, "_runtime", lambda: runtime)
    probes = []

    def probe(*args, **kwargs):
        probes.append(args[0])
        if failure == "timeout":
            raise generation.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        if failure == "spawn":
            raise OSError("old interpreter cannot launch")
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(generation.subprocess, "run", probe)
    candidate = generation.select_generation(tmp_path, "1.2.3", "payload")
    assert candidate.startswith("1.2.3+repair.") and candidate != selected
    assert len(probes) == 2
