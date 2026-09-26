"""Tests for the Manager's `update` command — the plugin updater/aligner seam.

`<project> update` hands off to `worktree-manager update`, which (1) self-updates
the Manager and (2) orchestrates the harness update by driving the engine's own
mechanics via `agent-worktrees update --no-manager` (the seam bypass). These pin
that sequencing, the flag forwarding, the bypass boundary, and self_update's
best-effort behavior — all without a real engine or network.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stdout

import pytest

from worktree_manager import __main__ as wm
from worktree_manager import engine_client as ec
from worktree_manager import self_install


def _run_update(rest, monkeypatch, *, su_action="already-current", su_kwargs=None):
    from worktree_manager.self_install import SelfUpdateResult
    calls = {}
    monkeypatch.setattr(
        wm, "_cmd_update", wm._cmd_update)  # ensure real function under test

    def fake_self_update(**kw):
        calls["self_update"] = kw
        return SelfUpdateResult(action=su_action, version="0.1.0-dev9",
                                previous="0.1.0-dev8", **(su_kwargs or {}))

    monkeypatch.setattr(self_install, "self_update", fake_self_update)

    def fake_passthrough(project, args, **kw):
        calls["passthrough"] = {"project": project, "args": args}
        return 0

    monkeypatch.setattr(ec, "run_engine_passthrough", fake_passthrough)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wm._cmd_update(rest)
    return rc, calls, buf.getvalue()


def test_update_self_updates_then_orchestrates_bypass(monkeypatch):
    rc, calls, out = _run_update([], monkeypatch)
    assert rc == 0
    # Self-update ran first…
    assert "self_update" in calls
    # …then the harness update was driven through the engine with the bypass flag.
    assert calls["passthrough"]["project"] is None
    assert calls["passthrough"]["args"] == ["update", "--no-manager"]


def test_update_forwards_project_and_flags(monkeypatch):
    rc, calls, out = _run_update(
        ["--force", "--project", "dotfiles", "--skip-modules", "agent-bridge"],
        monkeypatch)
    assert rc == 0
    # --project is forwarded through run_engine_passthrough's own project param
    # (placed before the verb), not appended as a flag after it; other flags
    # forwarded after the bypass.
    assert calls["passthrough"]["project"] == "dotfiles"
    assert calls["passthrough"]["args"] == [
        "update", "--no-manager", "--force", "--skip-modules", "agent-bridge"]


def test_update_reports_self_update_and_continues(monkeypatch):
    rc, calls, out = _run_update([], monkeypatch, su_action="updated")
    assert rc == 0
    assert "updated" in out and "active on next run" in out
    assert "passthrough" in calls  # continues to the harness update regardless


def test_update_engine_absent_hints_setup(monkeypatch):
    from worktree_manager.self_install import SelfUpdateResult
    monkeypatch.setattr(self_install, "self_update",
                        lambda **kw: SelfUpdateResult(action="skipped", reason="git not found"))

    def boom(project, args, **kw):
        raise ec.EngineError("not installed", install_hint=True)

    monkeypatch.setattr(ec, "run_engine_passthrough", boom)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wm._cmd_update([])
    assert rc == 1
    assert "worktree-manager setup" in buf.getvalue()


def test_extract_project_pulls_pair_and_leaves_rest():
    project, rest = wm._extract_project(["--force", "--project", "x", "--skip-modules"])
    assert project == "x"
    assert rest == ["--force", "--skip-modules"]


def test_extract_project_none_when_absent():
    project, rest = wm._extract_project(["--force", "--skip-modules"])
    assert project is None
    assert rest == ["--force", "--skip-modules"]


# ── run_engine_passthrough ────────────────────────────────────────────────────

def test_passthrough_builds_command_and_returns_code(monkeypatch):
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )
    ec.set_engine_command(None)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    rc = ec.run_engine_passthrough(None, ["update", "--no-manager"])
    assert rc == 0
    assert seen["cmd"][-2:] == ["update", "--no-manager"]
    assert "/fake/agent-worktrees" in seen["cmd"][0]


def test_passthrough_absent_engine_hints_install(monkeypatch):
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "installed_engine_command", lambda: None)
    ec.set_engine_command(None)
    with pytest.raises(ec.EngineError) as ei:
        ec.run_engine_passthrough(None, ["update"])
    assert ei.value.install_hint is True


# ── self_update (best-effort, git-fetch + version-install) ────────────────────

def test_self_update_without_git_non_github_skips(monkeypatch, tmp_path):
    """Without git AND a non-GitHub source, there is no tarball endpoint — skip."""
    from worktree_manager import source_config as sc
    monkeypatch.setattr(self_install.shutil, "which", lambda name: None)
    sc.set_source(repo=str(tmp_path / "local-remote"), root=tmp_path)  # non-GitHub
    res = self_install.self_update(root=tmp_path, dry_run=True)
    assert res.action == "skipped"
    assert "git" in (res.reason or "")


def test_self_update_without_git_falls_back_to_tarball(monkeypatch, tmp_path):
    """Without git but a GitHub source, self_update fetches the codeload tarball."""
    from worktree_manager import source_config as sc
    monkeypatch.setattr(self_install.shutil, "which", lambda name: None)
    monkeypatch.setattr(self_install, "local_bin", lambda: tmp_path / "localbin")
    sc.set_source(repo="https://github.com/acme/widgets.git", ref="main", root=tmp_path)
    seen = {}

    def fake_fetch(staging, url, **kw):
        seen["url"] = url
        pkg = staging / "worktree-manager" / "src" / "worktree_manager"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text('__version__ = "9.9.9"\n')
        (staging / "worktree-manager" / "pyproject.toml").write_text(
            "[project]\nname='x'\nversion='9.9.9'\n")

    monkeypatch.setattr(self_install, "_fetch_via_tarball", fake_fetch)
    res = self_install.self_update(root=tmp_path, dry_run=False)
    assert seen["url"] == "https://codeload.github.com/acme/widgets/tar.gz/main"
    assert res.action == "updated"
    assert res.version == "9.9.9"


@pytest.mark.parametrize("repo,ref,expected", [
    ("https://github.com/ThomasMichon/copilot-extensions.git", "main",
     "https://codeload.github.com/ThomasMichon/copilot-extensions/tar.gz/main"),
    ("https://github.com/acme/widgets", "canary",
     "https://codeload.github.com/acme/widgets/tar.gz/canary"),
    ("git@github.com:acme/widgets.git", "main",
     "https://codeload.github.com/acme/widgets/tar.gz/main"),
])
def test_manager_tarball_url_github(tmp_path, repo, ref, expected):
    from worktree_manager import source_config as sc
    sc.set_source(repo=repo, ref=ref, root=tmp_path)
    assert self_install.manager_tarball_url(tmp_path) == expected


def test_manager_tarball_url_non_github_is_none(tmp_path):
    from worktree_manager import source_config as sc
    sc.set_source(repo=str(tmp_path / "local"), root=tmp_path)
    assert self_install.manager_tarball_url(tmp_path) is None


def test_fetch_via_tarball_also_fetches_the_libs_sibling(tmp_path, monkeypatch):
    """A tarball-only fetch previously grabbed ONLY the worktree-manager/
    subtree, silently dropping the sibling libs/ that any src-passthrough
    vendor pointer inside the payload needs to resolve canonical content
    from -- the resulting staging tree must carry both, the same
    monorepo-shaped layout a git clone already produces for free.
    (tools/materialize_main.py is deliberately never fetched at all --
    _materialize_payload_pointers uses the LOCAL, already-trusted
    _trusted_pointer_materializer module instead, never dynamically
    executing anything from the fetched, potentially untrusted source.)"""
    import tarfile

    archive_root = tmp_path / "archive-src"
    (archive_root / "copilot-extensions-main" / "worktree-manager" / "src" /
     "worktree_manager").mkdir(parents=True)
    (archive_root / "copilot-extensions-main" / "worktree-manager" / "src" /
     "worktree_manager" / "__init__.py").write_text('__version__ = "9.9.9"\n')
    (archive_root / "copilot-extensions-main" / "worktree-manager" /
     "pyproject.toml").write_text("[project]\nname='x'\nversion='9.9.9'\n")
    (archive_root / "copilot-extensions-main" / "libs" / "shared-lib" / "src" /
     "shared_lib").mkdir(parents=True)
    (archive_root / "copilot-extensions-main" / "libs" / "shared-lib" / "src" /
     "shared_lib" / "__init__.py").write_text("value = 1\n")

    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(archive_root / "copilot-extensions-main", arcname="copilot-extensions-main")

    class _FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self._data

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse(archive_path.read_bytes()),
    )

    staging = tmp_path / "staging"
    self_install._fetch_via_tarball(staging, "https://codeload.example/fake.tar.gz")

    assert (staging / "worktree-manager" / "pyproject.toml").is_file()
    assert (staging / "libs" / "shared-lib" / "src" / "shared_lib" / "__init__.py").read_text() == "value = 1\n"
    assert not (staging / "tools").exists()


def test_fetch_via_tarball_refuses_a_symlink_pointing_outside_the_archive(tmp_path, monkeypatch):
    """Round-8 review finding (superseded by round-11's stronger fix):
    shutil.copytree's default (symlinks=False) DEREFERENCES a symlink
    anywhere in the source tree, silently copying whatever it points to --
    a malicious/corrupted tarball could embed a symlink entry pointing
    OUTSIDE the extracted archive, and copytree would smuggle that
    external file's content into staging before materialize_main ever
    gets a chance to reject it. Round-8 fixed this with symlinks=True
    (preserve, don't dereference) so a LATER check could still catch it;
    round-11 replaced the hand-rolled extraction validator with stdlib's
    own filter="data", which rejects an absolute-target symlink like this
    one OUTRIGHT at extraction time -- a strictly stronger guarantee than
    merely preserving it for a downstream check to maybe catch."""
    import tarfile

    outside_target = tmp_path / "outside-secret.txt"
    outside_target.write_text("should never be smuggled in\n")

    archive_root = tmp_path / "archive-src"
    top = archive_root / "copilot-extensions-main"
    (top / "worktree-manager" / "src" / "worktree_manager").mkdir(parents=True)
    (top / "worktree-manager" / "src" / "worktree_manager" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n'
    )
    (top / "worktree-manager" / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='9.9.9'\n"
    )
    (top / "libs" / "shared-lib").mkdir(parents=True)
    # A real symlink under libs/ pointing OUTSIDE the archive tree entirely --
    # tarfile.add() (default dereference=False) stores this as an actual
    # symlink tar entry, not the target's dereferenced content.
    (top / "libs" / "shared-lib" / "evil-link").symlink_to(outside_target)

    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(top, arcname="copilot-extensions-main")

    class _FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self._data

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse(archive_path.read_bytes()),
    )

    staging = tmp_path / "staging"
    with pytest.raises(OSError, match="symlink|link to"):
        self_install._fetch_via_tarball(staging, "https://codeload.example/fake.tar.gz")

    copied_link = staging / "libs" / "shared-lib" / "evil-link"
    assert not copied_link.exists() and not copied_link.is_symlink(), (
        "nothing should have been extracted from a rejected tarball"
    )


def test_fetch_via_tarball_refuses_a_symlinked_libs_root(tmp_path, monkeypatch):
    """Round-9 review finding: symlinks=True on copytree does NOT protect
    the copytree's own SOURCE ROOT -- shutil.copytree always creates dst
    as a real directory, so if libs_source itself were a symlink,
    os.scandir would transparently follow it (there's nowhere for a
    preserved-symlink object to even go at the copy root). A tarball
    providing libs/ as a symlink must be refused before copytree ever
    runs on it."""
    import tarfile

    outside = tmp_path / "outside-libs"
    (outside / "shared-lib" / "src" / "shared_lib").mkdir(parents=True)
    (outside / "shared-lib" / "src" / "shared_lib" / "__init__.py").write_text(
        "smuggled = True\n"
    )

    archive_root = tmp_path / "archive-src"
    top = archive_root / "copilot-extensions-main"
    (top / "worktree-manager" / "src" / "worktree_manager").mkdir(parents=True)
    (top / "worktree-manager" / "src" / "worktree_manager" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n'
    )
    (top / "worktree-manager" / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='9.9.9'\n"
    )
    top.mkdir(parents=True, exist_ok=True)
    (top / "libs").symlink_to(outside, target_is_directory=True)

    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(top, arcname="copilot-extensions-main")

    class _FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self._data

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse(archive_path.read_bytes()),
    )

    staging = tmp_path / "staging"
    with pytest.raises(OSError, match="symlink|link to"):
        self_install._fetch_via_tarball(staging, "https://codeload.example/fake.tar.gz")



def test_fetch_via_tarball_refuses_a_symlinked_extraction_top_dir(tmp_path, monkeypatch):
    """Round-10 review finding: checking only payload.is_symlink() misses a
    symlinked TOP-LEVEL extraction dir (the codeload <hash>-<ref>/ dir)
    whose own worktree-manager/ subpath is a real (non-symlink) file
    within the symlinked-to target -- rdir.is_dir() already follows the
    symlink to find it, so payload itself would never be a symlink even
    though the whole tree was reached via a symlinked parent."""
    import tarfile

    outside = tmp_path / "outside-extraction-root"
    (outside / "worktree-manager" / "src" / "worktree_manager").mkdir(parents=True)
    (outside / "worktree-manager" / "src" / "worktree_manager" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n'
    )
    (outside / "worktree-manager" / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='9.9.9'\n"
    )

    archive_path = tmp_path / "payload.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        # Nest the real content one level deeper ("nested/real-target",
        # not a bare top-level "real-target" sibling) so it can NEVER
        # independently satisfy the loop's own payload search: extract/
        # would otherwise contain TWO candidate top-level dirs (the real
        # one AND the symlink), and since extract.iterdir()'s enumeration
        # order is filesystem-dependent (not guaranteed), the loop could
        # non-deterministically pick the real, non-symlinked "real-target"
        # entry FIRST and never even reach the symlinked entry -- exactly
        # the flake this nesting eliminates: "nested" itself has no
        # worktree-manager/pyproject.toml directly inside it (only
        # nested/real-target/worktree-manager/... does), so it can never
        # satisfy the search on its own.
        tf.add(outside, arcname="nested/real-target")
        link_info = tarfile.TarInfo(name="copilot-extensions-main")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "nested/real-target"
        tf.addfile(link_info)

    class _FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return self._data

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse(archive_path.read_bytes()),
    )

    staging = tmp_path / "staging"
    with pytest.raises(OSError, match="symlink|link to"):
        self_install._fetch_via_tarball(staging, "https://codeload.example/fake.tar.gz")


@pytest.mark.parametrize("repo,ref,expected", [
    ("https://github.com/ThomasMichon/copilot-extensions.git", "main",
     "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main"
     "/worktree-manager/src/worktree_manager/__init__.py"),
    ("https://github.com/acme/widgets", "canary",
     "https://raw.githubusercontent.com/acme/widgets/canary"
     "/worktree-manager/src/worktree_manager/__init__.py"),
    ("git@github.com:acme/widgets.git", "main",
     "https://raw.githubusercontent.com/acme/widgets/main"
     "/worktree-manager/src/worktree_manager/__init__.py"),
])
def test_remote_init_url_github(tmp_path, repo, ref, expected):
    from worktree_manager import source_config as sc
    sc.set_source(repo=repo, ref=ref, root=tmp_path)
    assert self_install.remote_init_url(tmp_path) == expected


def test_remote_init_url_non_github_is_none(tmp_path):
    from worktree_manager import source_config as sc
    sc.set_source(repo=str(tmp_path / "local"), root=tmp_path)
    assert self_install.remote_init_url(tmp_path) is None


def test_fetch_remote_version_parses_the_fetched_init_py(monkeypatch, tmp_path):
    """A successful GET returns the parsed ``__version__`` -- no git, no
    clone/tarball, a single small file read."""
    from worktree_manager import source_config as sc

    sc.set_source(
        repo="https://github.com/acme/widgets.git", ref="main", root=tmp_path)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'__version__ = "9.9.9"\n'

    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen)
    assert self_install.fetch_remote_version(tmp_path) == "9.9.9"
    assert seen["url"] == (
        "https://raw.githubusercontent.com/acme/widgets/main"
        "/worktree-manager/src/worktree_manager/__init__.py")


def test_fetch_remote_version_degrades_to_none_on_any_failure(monkeypatch, tmp_path):
    """Network error, timeout, unparsable content -- all degrade to ``None``,
    never raise. This is the property a background poll depends on."""
    from worktree_manager import source_config as sc

    sc.set_source(
        repo="https://github.com/acme/widgets.git", ref="main", root=tmp_path)

    def boom(url, timeout=None):
        raise OSError("network is down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert self_install.fetch_remote_version(tmp_path) is None


def test_fetch_remote_version_non_github_source_is_none(tmp_path):
    from worktree_manager import source_config as sc
    sc.set_source(repo=str(tmp_path / "local"), root=tmp_path)
    assert self_install.fetch_remote_version(tmp_path) is None



def test_self_update_reports_updated(monkeypatch, tmp_path):
    from worktree_manager.self_install import SelfInstallResult
    monkeypatch.setattr(self_install.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(self_install.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    # Pretend the fetched payload exists and self_install reports an install.
    monkeypatch.setattr(self_install.Path, "is_file", lambda self: True)
    monkeypatch.setattr(self_install, "self_install",
                        lambda **kw: SelfInstallResult(version="0.1.0-dev9",
                                                       action="installed", root=str(tmp_path)))
    res = self_install.self_update(root=tmp_path, dry_run=False)
    assert res.action == "updated"
    assert res.version == "0.1.0-dev9"


def test_safe_extract_refuses_the_classic_tar_symlink_traversal_attack(tmp_path):
    """Round-11 review finding: a path-only pre-check computed against
    getmembers() BEFORE any extraction happens cannot catch the classic
    tar symlink attack -- a symlink member (evil -> /tmp/outside) followed
    by a second member using it as a path prefix (evil/payload.py) --
    because at pre-check time neither path exists on disk yet, so a
    lexical .resolve() sees no symlink to follow and both members pass;
    only DURING extractall's own sequential member-by-member write does
    the second member traverse through the just-created symlink and land
    outside dest entirely (verified against the standard library by the
    reviewer). filter="data" is stdlib's own defense against exactly this,
    validating each member's resolved destination live as extraction
    proceeds."""
    import tarfile

    outside = tmp_path / "outside-target"
    outside.mkdir()

    archive_path = tmp_path / "attack.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tf:
        link_info = tarfile.TarInfo(name="evil")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = str(outside)
        tf.addfile(link_info)
        payload_info = tarfile.TarInfo(name="evil/payload.py")
        payload_data = b"import os; os.system('echo pwned')\n"
        payload_info.size = len(payload_data)
        import io
        tf.addfile(payload_info, io.BytesIO(payload_data))

    dest = tmp_path / "dest"
    dest.mkdir()
    with tarfile.open(archive_path, "r:gz") as tf:
        with pytest.raises(OSError, match="symlink|link to"):
            self_install._safe_extract(tf, dest)

    # Nothing must have been written outside dest -- the attack's whole
    # point was landing payload.py at outside/payload.py.
    assert not (outside / "payload.py").exists()


def test_safe_extract_never_uses_the_filter_kwarg(tmp_path):
    """Round-12 review finding: the project declares
    requires-python >= 3.10 (worktree-manager/pyproject.toml), but
    TarFile.extractall(filter=...)/.extract(filter=...) is only available
    in newer stdlib patch versions -- calling it unconditionally would
    raise TypeError on an unpatched 3.10/3.11 host, breaking the
    documented git-optional tarball fallback instead of updating. Proves
    _safe_extract() never passes filter= at all (relying instead on its
    own hand-rolled, version-independent sequential validate-then-extract
    loop), by monkeypatching TarFile.extract itself to fail loudly if
    ever called with a filter kwarg."""
    import tarfile

    original_extract = tarfile.TarFile.extract

    def _spy_extract(self, member, path="", set_attrs=True, *, numeric_owner=False, filter=None):
        assert filter is None, "filter= was passed -- must stay version-independent"
        return original_extract(self, member, path, set_attrs=set_attrs,
                                numeric_owner=numeric_owner)

    import unittest.mock
    with unittest.mock.patch.object(tarfile.TarFile, "extract", _spy_extract):
        archive_root = tmp_path / "archive-src"
        archive_root.mkdir()
        (archive_root / "a.txt").write_text("hello\n")
        archive_path = tmp_path / "plain.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(archive_root, arcname="stuff")

        dest = tmp_path / "dest"
        dest.mkdir()
        with tarfile.open(archive_path, "r:gz") as tf:
            self_install._safe_extract(tf, dest)

    assert (dest / "stuff" / "a.txt").read_text() == "hello\n"
