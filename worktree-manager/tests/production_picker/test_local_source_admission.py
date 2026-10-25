"""Actual Manager readers must not turn optional maps into local enrollment."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from worktree_manager.production_picker import context, project_config as cfg
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, roster

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("worktree_manager.local_source_admission"),
]

LABELS = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}


def _setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str,
    machines: dict | None, *, configured: str | None = None,
) -> Path:
    root = tmp_path / "checkout"
    state = tmp_path / "project"
    root.mkdir()
    state.mkdir()
    (root / ".agent-worktrees").mkdir()
    (root / ".agent-worktrees" / "config.yaml").write_text(
        "default_branch: dev\n", encoding="utf-8",
    )
    raw = {"repo_name": "example"}
    if configured is not None:
        raw["machine"] = configured
    (state / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    if machines is not None:
        (root / "machines.yaml").write_text(
            yaml.safe_dump({"machines": machines}, sort_keys=False), encoding="utf-8",
        )
    context.set_project("example")
    scalar = configured or ("raw-host-wsl" if platform == "wsl" else "raw-host")
    cfg.bind_bootstrap("example", {
        "config_dir": str(state), "repo_dir": str(root), "machine": scalar,
        "configured_machine": configured,
    })
    monkeypatch.setattr("socket.gethostname", lambda: "raw-host")
    monkeypatch.setattr(roster, "local_execution_platform", lambda: platform)
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: platform)
    monkeypatch.setattr(data_ssh, "_local_identity", lambda: ("raw-host", platform))
    monkeypatch.setattr(data_local, "LOCAL", ("raw-host", LABELS[platform]))
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [])

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("contained bootstrap must not call an engine or launch SSH")

    monkeypatch.setattr(cfg.engine_client, "get_value", unexpected)
    return root


@pytest.fixture(autouse=True)
def _reset_config():
    yield
    cfg.clear_caches(reset_binding=True)


def _machines(kind: str, platform: str) -> dict | None:
    if kind == "absent":
        return None
    if kind == "empty":
        return {}
    if kind == "unrelated":
        return {"remote": {
            "hostname": "remote-host", "execution_platform": "linux",
            "ssh": {"ready": True, "environments": [{"name": "linux", "alias": "remote-ssh"}]},
        }}
    if kind == "unscoped":
        return {"friendly": {"hostname": "raw-host"}}
    if kind == "matching":
        return {"local-space": {
            "hostname": "raw-host", "physical_host": "friendly",
            "execution_platform": platform,
        }}
    sibling = "windows" if platform != "windows" else "wsl"
    return {"sibling": {
        "hostname": "raw-host", "physical_host": "friendly",
        "execution_platform": sibling,
    }}


@pytest.mark.parametrize("platform", LABELS)
@pytest.mark.parametrize("kind", ["absent", "empty", "unrelated", "unscoped", "matching", "sibling"])
def test_actual_readers_keep_one_usable_local_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, kind: str,
) -> None:
    root = _setup(monkeypatch, tmp_path, platform, _machines(kind, platform))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.yaml")}
    assert cfg.load_config().default_repo.anchor == str(root)
    sources = data_ssh._build_sources()
    local = [source for source in sources if source.local]
    assert len(local) == 1
    source = local[0]
    expected = (
        "local-space" if kind == "matching" else
        ("friendly-wsl" if platform == "wsl" else "friendly")
        if kind in ("unscoped", "sibling") else
        ("raw-host-wsl" if platform == "wsl" else "raw-host")
    )
    assert source.key == (expected, LABELS[platform])
    assert source.ready and source.argv is None and not source.alias
    assert roster.local_host() == source.key
    assert data_local.is_local(*source.key)
    raw = {"id": "original-record", "machine": "retained-owner", "title": "Retained"}
    monkeypatch.setattr(
        data_local.engine_client, "list_worktree_rows", lambda *args, **kwargs: [raw],
    )
    rows = data_ssh._fetch(source)
    assert len(rows) == 1
    row = rows[0]
    assert (row["machine"], row["env"]) == source.key
    assert row["source_id"] == source.source_id
    assert row["id"] == raw["id"]
    assert data_local.is_local(row["machine"], row["env"])
    assert raw["machine"] == "retained-owner"
    for other in sources:
        assert data_local.is_local(*other.key) is other.local
    assert not data_local.is_local("unrelated-target", LABELS[platform])
    assert not data_local.is_local(expected, "WSL" if platform != "wsl" else "Win")
    assert {path: path.read_bytes() for path in tmp_path.rglob("*.yaml")} == before


@pytest.mark.parametrize("platform", LABELS)
@pytest.mark.parametrize("kind", ["malformed", "ambiguous", "wrong-platform", "profile-conflict"])
def test_actual_readers_refuse_conflicts_before_local_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, kind: str,
) -> None:
    other = "windows" if platform != "windows" else "wsl"
    configured = None
    if kind == "ambiguous":
        machines = {
            name: {"hostname": "raw-host", "execution_platform": platform}
            for name in ("first", "second")
        }
    else:
        machines = {"remote": {
            "hostname": "remote-host", "execution_platform": other,
            "ssh": {"environments": [{"name": other, "alias": "remote-profile"}]},
        }}
        if kind in ("wrong-platform", "profile-conflict"):
            configured = "remote" if kind == "wrong-platform" else "remote-profile"
    root = _setup(monkeypatch, tmp_path, platform, machines, configured=configured)
    if kind == "malformed":
        (root / "machines.yaml").write_text("machines: [invalid]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        data_ssh._build_sources()
    if kind == "malformed":
        with pytest.raises(RuntimeError, match="registry is unavailable"):
            data_local.is_local("remote", LABELS[platform])
    else:
        assert not data_local.is_local("remote", LABELS[platform])


@pytest.mark.parametrize("binding", ["", " ", 42, False, [], {}])
def test_invalid_bootstrap_binding_is_not_treated_as_detected(binding: object) -> None:
    with pytest.raises(ValueError, match="configured machine binding"):
        cfg.bind_bootstrap("example", {"machine": "host", "configured_machine": binding})


def test_old_bootstrap_scalar_is_not_promoted_to_configured_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = _setup(monkeypatch, tmp_path, "wsl", {})
    cfg.bind_bootstrap("example", {
        "config_dir": str(tmp_path / "project"), "repo_dir": str(root),
        "machine": "foreign-derived-wsl",
    })
    loaded = cfg.load_config()
    assert loaded.machine == "foreign-derived-wsl"
    assert not loaded.machine_binding_known
    assert loaded.configured_machine is None
    assert roster.local_context(loaded, {}, "raw-host", "wsl") is None
    local = [source for source in data_ssh._build_sources() if source.local]
    assert [source.key for source in local] == [("raw-host", "WSL")]
