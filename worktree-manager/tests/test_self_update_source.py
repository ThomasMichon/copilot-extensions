"""Manager's merged-Dev acquisition remains independent of service source policy."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from worktree_manager import runtime_source_self_update as update
from worktree_manager import self_install as si
from worktree_manager import source_config
from worktree_manager.runtime_source import ResolvedSource, SourceAcquisitionError
from worktree_manager.runtime_source_config import RuntimeSource, set_source


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))


def test_helper_pins_only_manager_source_and_prepares_under_its_held_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "manager"
    repo = str(tmp_path / "manager-repository")
    set_source(RuntimeSource("main", str(tmp_path / "unavailable-service-source")), root=root)
    config = (root / "config.toml").read_bytes()
    auth = {"APPROVED_AUTH": "fixture"}
    calls = []
    tool = tmp_path / "own-tool"
    output = root / "manager-source/runtime-sources/prepared"
    payload = output / "worktree-manager/qualified"
    monkeypatch.setattr(update, "preparer", lambda: tool)

    @contextmanager
    def acquire(selection: RuntimeSource, **kwargs: object):
        assert selection == RuntimeSource("dev", repo, allow_dev=True)
        assert kwargs == {"root": root / "manager-source", "environment": auth}
        calls.append("held")
        yield ResolvedSource(selection, tmp_path / "clean", "a" * 40, "b" * 40, "a" * 40)
        calls.append("released")

    def prepare(actual: Path, resolved: ResolvedSource, name: str, destination: Path) -> dict:
        assert calls == ["held"]
        assert actual == tool and resolved.commit == "a" * 40
        assert name == "worktree-manager" and destination == output
        return {"payload": str(payload)}

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare", prepare)
    assert update.fetch(repo, root, environment=auth) == payload
    assert calls == ["held", "released"]
    assert (root / "config.toml").read_bytes() == config


@pytest.mark.parametrize("dry_run", [False, True])
def test_self_update_uses_existing_publication_and_cutover_with_qualified_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool,
) -> None:
    root = tmp_path / "manager"
    repo = str(tmp_path / "chosen-manager")
    source_config.set_source(repo=repo, ref="dev", root=root)
    before = (root / "config.toml").read_bytes()
    payload = tmp_path / "qualified"
    payload.mkdir()
    (payload / "pyproject.toml").write_text("[project]\nversion='1'\n")
    auth = {"APPROVED_AUTH": "fixture"}
    seen = []
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")

    def auth_environment(source: str, *, cwd: Path) -> dict[str, str]:
        assert source == repo and cwd == root / "manager-source"
        assert cwd.is_dir()
        return auth

    def fetch(source: str, destination: Path, *, environment: dict[str, str]) -> Path:
        assert (source, destination, environment) == (repo, root, auth)
        seen.append("qualified")
        return payload

    def install(**kwargs: object) -> si.SelfInstallResult:
        assert kwargs["payload_dir"] == payload and kwargs["root"] == root
        assert kwargs["dry_run"] is dry_run and seen == ["qualified"]
        if dry_run:
            assert kwargs["_after_activate"] is None
        else:
            kwargs["_after_activate"](root / "versions/1+source.qualified")
        return si.SelfInstallResult(
            action="planned" if dry_run else "installed", version="1", root=str(root),
        )

    def cutover(**kwargs: object) -> dict:
        assert kwargs == {
            "root": root, "slot": root / "versions/1+source.qualified",
            "version": "1+source.qualified",
        }
        seen.append("cutover")
        return {"action": "fixture"}

    monkeypatch.setattr("worktree_manager.update_auth.environment", auth_environment)
    monkeypatch.setattr(update, "fetch", fetch)
    monkeypatch.setattr(si, "self_install", install)
    monkeypatch.setattr("worktree_manager.mux_daemon_cutover.activate_after_update", cutover)
    result = si.self_update(root=root, dry_run=dry_run)
    assert result.action == "updated" and result.version == "1"
    assert seen == (["qualified"] if dry_run else ["qualified", "cutover"])
    assert (root / "config.toml").read_bytes() == before
    assert not (root / si.STAGING_DIR).exists()


@pytest.mark.parametrize("fault", ["acquire", "revalidate", "prepare"])
def test_source_failures_never_install_or_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    root = tmp_path / "manager"
    source_config.set_source(repo=str(tmp_path / "upstream"), ref="dev", root=root)
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *a, **kw: None)
    monkeypatch.setattr(update, "preparer", lambda: tmp_path / "own-tool")
    monkeypatch.setattr(si, "self_install", lambda **kw: pytest.fail("no installation"))
    monkeypatch.setattr(si, "_fetch_via_tarball", lambda *a: pytest.fail("no fallback"))

    @contextmanager
    def acquire(selection: RuntimeSource, **kwargs: object):
        if fault == "acquire":
            raise SourceAcquisitionError("fixture acquisition refusal")
        yield ResolvedSource(selection, tmp_path, "a" * 40, "b" * 40, "a" * 40)
        if fault == "revalidate":
            raise SourceAcquisitionError("fixture revalidation refusal")

    def prepare(*args: object) -> dict:
        if fault == "prepare":
            raise ValueError("fixture preparation refusal")
        return {"payload": str(tmp_path / "unpublished")}

    monkeypatch.setattr(update, "acquire", acquire)
    monkeypatch.setattr(update, "prepare", prepare)
    result = si.self_update(root=root)
    diagnostic = {"acquire": "acquisition", "revalidate": "revalidation", "prepare": "preparation"}
    assert result.action == "error" and f"fixture {diagnostic[fault]} refusal" in result.reason


def test_dev_without_git_is_explicitly_unsupported_before_auth_or_tarball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "manager"
    source_config.set_source(ref="dev", root=root)
    monkeypatch.setattr(si.shutil, "which", lambda name: None)
    monkeypatch.setattr("worktree_manager.update_auth.environment", lambda *a, **kw: pytest.fail("no auth"))
    monkeypatch.setattr(si, "_fetch_via_tarball", lambda *a: pytest.fail("no unqualified fallback"))
    result = si.self_update(root=root)
    assert result.action == "skipped" and "requires git" in result.reason
    assert not (root / "manager-source").exists()


def test_dev_auth_required_is_reported_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager.update_auth import AuthRequired

    root = tmp_path / "manager"
    source_config.set_source(ref="dev", root=root)
    monkeypatch.setattr(si.shutil, "which", lambda name: "git")
    monkeypatch.setattr(update, "fetch", lambda *a, **kw: pytest.fail("no acquisition"))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AuthRequired("fixture auth required")

    monkeypatch.setattr("worktree_manager.update_auth.environment", refuse)
    result = si.self_update(root=root)
    assert result.action == "auth_required" and result.reason == "fixture auth required"
