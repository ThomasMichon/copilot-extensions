"""Prepared fallback payload roots must support native hook process creation."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from plugin_resolve.preparation import _install, prepare
from plugin_resolve.registration import _installation_home, atomic_json


ORIGIN = {"source": {"source": "github", "repo": "example/fixture"}}
SOURCE = "sample@fixture"


def _launch(payload):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", "import os; print(os.getcwd())"],
        cwd=payload, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _repository(base, *, legacy_length, non_bmp=False):
    digest = hashlib.sha256(json.dumps([SOURCE, ORIGIN], sort_keys=True).encode()).hexdigest()
    relative = (
        Path(".git") / "copilot-venue-plugins" / "marketplaces" / "fixture"
        / "installation" / digest / "installed-plugins" / "fixture" / "sample"
    )
    padding = legacy_length - len(str(base / relative)) - 1
    assert padding > 0, "Contained temporary root leaves no room for the calibrated fixture"
    repo = base / (("\U0001f600" if non_bmp else "v") + "v" * (padding - 1))
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return repo, repo / relative


@pytest.mark.skipif(os.name != "nt", reason="Windows process working-directory contract")
def test_preparation_recovers_long_legacy_payload_and_reuses_short_home(tmp_path_factory):
    base = tmp_path_factory.mktemp("vp")
    repo, legacy = _repository(base, legacy_length=270)
    legacy.mkdir(parents=True)
    atomic_json(legacy / "plugin.json", {"name": "sample", "version": "0.1.0"})
    (legacy.parent.parent.parent / ".installation-complete.json").write_text("{interrupted")
    before = {path: path.read_bytes() for path in legacy.parent.parent.parent.rglob("*") if path.is_file()}
    settings = repo / ".github" / "copilot" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    previous = {"enabledPlugins": {SOURCE: True}, "extraKnownMarketplaces": {"fixture": ORIGIN}}
    atomic_json(settings, {**previous, "unrelated": "preserved"})
    atomic_json(repo / ".git" / "copilot-venue-plugins" / "ownership.json", previous)
    control = _launch(base)
    assert control.returncode == 0, control.stderr
    with pytest.raises(OSError) as failure:
        _launch(legacy)
    assert failure.value.winerror == 267
    call_log = base / "calls.jsonl"
    environment = {
        **os.environ, "VENUE_CALL_LOG": str(call_log),
        "VENUE_EXPECTED_SETTINGS_SHA256": hashlib.sha256(settings.read_bytes()).hexdigest(),
    }
    fixture = (
        Path(__file__).resolve().parents[3] / "libs" / "plugin-resolve"
        / "tests" / "fixtures" / "venue_installer.py"
    )
    command = (sys.executable, str(fixture))
    request = {
        "repo": str(repo),
        "plugins": [
            {"source": source, "enable": True, "marketplace": ORIGIN}
            for source in (SOURCE, "support@fixture")
        ],
    }
    prepared = prepare(request, copilot_command=command, environment=environment)
    payload = Path(prepared[SOURCE])
    assert payload != legacy
    assert payload.is_relative_to(repo / ".git" / "copilot-venue-plugins" / "marketplaces")
    assert len(payload.parent.parent.parent.name) == 64
    assert prepared[SOURCE] != prepared["support@fixture"]
    result = _launch(payload)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == payload.resolve()
    settings_before = settings.read_bytes()
    assert json.loads(settings_before)["extraKnownMarketplaces"]["fixture"] == ORIGIN
    assert json.loads(settings_before)["unrelated"] == "preserved"
    assert prepare(request, copilot_command=command, environment=environment) == prepared
    assert len(call_log.read_text().splitlines()) == 2
    assert settings.read_bytes() == settings_before
    assert {path: path.read_bytes() for path in before} == before

    revised = {"source": {**ORIGIN["source"], "ref": "next"}}
    request["plugins"] = [
        {**item, "marketplace": revised} for item in request["plugins"]
    ]
    environment["VENUE_EXPECTED_SETTINGS_SHA256"] = hashlib.sha256(settings_before).hexdigest()
    replacement = prepare(request, copilot_command=command, environment=environment)
    assert replacement[SOURCE] != prepared[SOURCE]
    assert Path(prepared[SOURCE]).is_dir()
    assert json.loads(settings.read_text())["extraKnownMarketplaces"]["fixture"] == revised
    assert len(call_log.read_text().splitlines()) == 4


@pytest.mark.skipif(os.name != "nt", reason="Windows process working-directory contract")
def test_too_deep_preparation_refuses_before_install_and_publication(tmp_path_factory):
    base = tmp_path_factory.mktemp("vp-deep")
    repo, _legacy = _repository(base, legacy_length=305)
    settings = repo / ".github" / "copilot" / "settings.local.json"
    settings.parent.mkdir(parents=True)
    atomic_json(settings, {"unrelated": "preserved"})
    before = settings.read_bytes()
    staging = repo / ".git" / "copilot-venue-plugins" / "marketplaces"
    home = _installation_home(staging, SOURCE, ORIGIN)
    payload = home / "installed-plugins" / "fixture" / "sample"
    payload.mkdir(parents=True)
    with pytest.raises(OSError) as failure:
        _launch(payload)
    assert failure.value.winerror == 267
    with pytest.raises(ValueError, match="shorter repository/worktree path"):
        prepare({
            "repo": str(repo),
            "plugins": [{"source": SOURCE, "enable": True, "marketplace": ORIGIN}],
        }, copilot_command=(str(base / "must-not-run.exe"),))
    assert settings.read_bytes() == before
    assert not (home / "settings.json").exists()
    assert not (home / ".installation-complete.json").exists()
    assert not (staging.parent / "ownership.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows process working-directory contract")
@pytest.mark.parametrize(("length", "non_bmp"), [(258, False), (259, False), (258, True)])
def test_native_cwd_limit_applies_to_completed_installation_reuse(tmp_path_factory, length, non_bmp):
    base = tmp_path_factory.mktemp("vp-limit")
    repo, _legacy = _repository(
        base, legacy_length=length + len("fixture\\installation\\"), non_bmp=non_bmp,
    )
    home = _installation_home(
        repo / ".git" / "copilot-venue-plugins" / "marketplaces", SOURCE, ORIGIN,
    )
    payload = home / "installed-plugins" / "fixture" / "sample"
    payload.mkdir(parents=True)
    assert len(str(payload).encode("utf-16-le")) // 2 == length + non_bmp
    metadata = {"name": "fixture", "owner": {"name": "Synthetic Publisher"}}
    entry = {"name": "sample", "source": "./sample", "version": "1.0.0"}
    atomic_json(payload / "plugin.json", {"name": "sample", "version": "1.0.0"})
    atomic_json(home / "settings.json", {"extraKnownMarketplaces": {"fixture": ORIGIN}})
    atomic_json(home / ".installation-complete.json", {
        "source": SOURCE, "catalog": metadata, "entry": entry,
    })
    if length == 258 and not non_bmp:
        result = _launch(payload)
        assert result.returncode == 0, result.stderr
        assert _install(SOURCE, ORIGIN, home, repo, ("must-not-run",))[0] == payload
    else:
        with pytest.raises(OSError) as failure:
            _launch(payload)
        assert failure.value.winerror == 267
        with pytest.raises(ValueError, match="shorter repository/worktree path"):
            _install(SOURCE, ORIGIN, home, repo, ("must-not-run",))


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_installation_identity_and_posix_layout_remain_qualified(tmp_path, monkeypatch, platform):
    from plugin_resolve import registration

    monkeypatch.setattr(registration, "os", SimpleNamespace(name=platform))
    staging = tmp_path / "marketplaces"
    home = _installation_home(staging, SOURCE, ORIGIN)
    assert len(home.name) == 64
    if platform == "posix":
        assert home.parent == staging / "fixture" / "installation"
    else:
        assert home.parent == staging
    assert home != _installation_home(staging, "support@fixture", ORIGIN)
    assert home != _installation_home(staging, "sample@other", ORIGIN)
    assert home != _installation_home(staging, SOURCE, {"source": {**ORIGIN["source"], "ref": "next"}})
