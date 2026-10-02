from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "check-vendored-libs-sync.py"

_SPEC = importlib.util.spec_from_file_location("check_vendored_libs_sync", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
check_vendored_libs_sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_vendored_libs_sync)


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(check_vendored_libs_sync, "REPO", tmp_path)
    monkeypatch.setattr(check_vendored_libs_sync, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(check_vendored_libs_sync, "LIBS_DIR", tmp_path / "libs")
    # The module's `uer` (uv_editable_ref) import is a separate module object
    # with its OWN `REPO`/`PLUGINS_DIR` constants computed from its own file
    # location -- redirect those too, or `_editable_pointer_consumers()`
    # would scan the real repo on disk instead of this fake one.
    uer = check_vendored_libs_sync.uer
    monkeypatch.setattr(uer, "REPO", tmp_path)
    monkeypatch.setattr(uer, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(uer, "LIBS_DIR", tmp_path / "libs")
    return tmp_path


def _seed_editable_pointer_consumer(root: Path, consumer_rel: str, lib: str) -> None:
    """A consumer with NO local ``libs/<lib>`` copy at all -- just a
    `uv`-editable canonical-reference entry in its own ``pyproject.toml``
    pointing at the top-level ``libs/<lib>``."""
    consumer_dir = root / consumer_rel
    consumer_dir.mkdir(parents=True, exist_ok=True)
    depth = len(Path(consumer_rel).parts)
    up = "/".join([".."] * depth)
    (consumer_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{Path(consumer_rel).name}"\nversion = "0.1.0"\n\n'
        f'[tool.uv.sources]\n{lib} = {{ path = "{up}/libs/{lib}", editable = true }}\n',
        encoding="utf-8",
    )


def _seed_real_lib(root: Path, rel: str, *, content: str, version: str = "0.1.0-dev1") -> None:
    lib_dir = root / rel
    pkg = lib_dir.name.replace("-", "_")
    (lib_dir / "src" / pkg).mkdir(parents=True, exist_ok=True)
    (lib_dir / "src" / pkg / "__init__.py").write_text(content, encoding="utf-8")
    (lib_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{lib_dir.name}"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def _seed_pointer_copy(root: Path, rel: str, *, version: str = "0.1.0-dev1") -> None:
    lib_dir = root / rel
    pkg = lib_dir.name.replace("-", "_")
    (lib_dir / "src" / pkg).mkdir(parents=True, exist_ok=True)
    (lib_dir / "src" / pkg / "__init__.py").write_text("# pointer stub\n", encoding="utf-8")
    (lib_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{lib_dir.name}"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (lib_dir / "VENDOR_POINTER.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.vendor-pointer",
                "version": 1,
                "source": f"libs/{lib_dir.name}",
                "kind": "src-passthrough",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_verify_accepts_a_real_copy_backed_by_canonical_when_a_pointer_peer_exists(
    fake_repo: Path,
):
    _seed_real_lib(fake_repo, "libs/shared-lib", content="value = 1\n")
    _seed_real_lib(
        fake_repo,
        "plugins/agent-worktrees/libs/shared-lib",
        content="value = 1\n",
    )
    _seed_pointer_copy(fake_repo, "plugins/customizing-copilot/libs/shared-lib")

    assert check_vendored_libs_sync.verify() == []


def test_verify_flags_real_copy_drift_even_when_the_other_copy_is_only_a_pointer(
    fake_repo: Path,
):
    _seed_real_lib(fake_repo, "libs/shared-lib", content="canonical\n")
    _seed_real_lib(
        fake_repo,
        "plugins/agent-worktrees/libs/shared-lib",
        content="drifted\n",
    )
    _seed_pointer_copy(fake_repo, "plugins/customizing-copilot/libs/shared-lib")

    problems = check_vendored_libs_sync.verify()

    assert any(
        "shared-lib: src/shared_lib/__init__.py DIFFERS between libs/shared-lib "
        "and plugins/agent-worktrees/libs/shared-lib" in p
        for p in problems
    )


def test_verify_ignores_pointer_only_libs(fake_repo: Path):
    _seed_pointer_copy(fake_repo, "plugins/one/libs/shared-lib")
    _seed_pointer_copy(fake_repo, "plugins/two/libs/shared-lib")

    assert check_vendored_libs_sync.verify() == []


def test_verify_still_flags_pointer_version_skew_in_a_mixed_set(fake_repo: Path):
    _seed_real_lib(fake_repo, "libs/shared-lib", content="value = 1\n", version="0.1.0-dev1")
    _seed_real_lib(
        fake_repo,
        "plugins/agent-worktrees/libs/shared-lib",
        content="value = 1\n",
        version="0.1.0-dev1",
    )
    _seed_pointer_copy(
        fake_repo,
        "plugins/customizing-copilot/libs/shared-lib",
        version="9.9.9",
    )

    problems = check_vendored_libs_sync.verify()

    assert any("version skew across copies" in p for p in problems)


def test_verify_flags_canonical_drift_against_real_copy_with_only_editable_pointer_consumers(
    fake_repo: Path,
):
    """The actual bug (PR #4942 review): `plugin-activation` has real copies
    in `agent-worktrees`/`customizing-copilot` AND several `uv`-editable-
    pointer-only consumers, no `VENDOR_POINTER.json` copy at all. Before this
    fix, canonical was only ever pulled into the comparison when a
    `VENDOR_POINTER.json` copy existed -- an editable-pointer-only mix (like
    this one) let a canonical-only edit through as "OK" even though the real
    copies were now stale."""
    _seed_real_lib(fake_repo, "libs/shared-lib", content="fixed\n")
    _seed_real_lib(
        fake_repo, "plugins/agent-worktrees/libs/shared-lib", content="still-old\n",
    )
    _seed_editable_pointer_consumer(fake_repo, "plugins/agent-bridge", "shared-lib")

    problems = check_vendored_libs_sync.verify()

    assert any(
        "shared-lib: src/shared_lib/__init__.py DIFFERS between libs/shared-lib "
        "and plugins/agent-worktrees/libs/shared-lib" in p
        for p in problems
    ), problems


def test_verify_accepts_real_copy_matching_canonical_with_editable_pointer_consumer(
    fake_repo: Path,
):
    _seed_real_lib(fake_repo, "libs/shared-lib", content="value = 1\n")
    _seed_real_lib(
        fake_repo, "plugins/agent-worktrees/libs/shared-lib", content="value = 1\n",
    )
    _seed_editable_pointer_consumer(fake_repo, "plugins/agent-bridge", "shared-lib")

    assert check_vendored_libs_sync.verify() == []


def test_verify_ignores_editable_pointer_only_libs_with_a_single_real_copy(
    fake_repo: Path,
):
    # Only ONE real copy plus editable-pointer consumers -- nothing to
    # cross-check canonical against except that single copy, which is
    # already covered the moment a second real/pointer copy appears; a lone
    # real copy is not itself drift.
    _seed_real_lib(fake_repo, "libs/shared-lib", content="value = 1\n")
    _seed_real_lib(
        fake_repo, "plugins/agent-worktrees/libs/shared-lib", content="value = 1\n",
    )
    _seed_editable_pointer_consumer(fake_repo, "plugins/agent-bridge", "shared-lib")
    _seed_editable_pointer_consumer(fake_repo, "worktree-manager", "shared-lib")

    assert check_vendored_libs_sync.verify() == []


def test_verify_fails_closed_on_symlinked_consumer_pyproject(fake_repo: Path):
    """The regression this test exists for (PR #4954 review): if the ONLY
    editable-pointer consumer's `pyproject.toml` is a symlink,
    `find_uv_editable_refs()` returns no references for it (its own explicit
    refusal) -- a caller that doesn't reject the symlink itself first would
    treat that empty result as "no editable-pointer consumers for this lib",
    silently omit canonical from the comparison, and let a stale real copy
    pass. Must fail closed instead (`check-version-bump.py`'s identical
    preflight, mirrored here)."""
    _seed_real_lib(fake_repo, "libs/shared-lib", content="canonical\n")
    _seed_real_lib(
        fake_repo, "plugins/agent-worktrees/libs/shared-lib", content="still-old\n",
    )
    consumer_dir = fake_repo / "plugins" / "agent-bridge"
    consumer_dir.mkdir(parents=True, exist_ok=True)
    real = fake_repo / "plugins" / "agent-bridge" / "real-pyproject.toml"
    real.write_text(
        '[project]\nname = "agent-bridge"\nversion = "0.1.0"\n\n'
        '[tool.uv.sources]\nshared-lib = { path = "../../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    (consumer_dir / "pyproject.toml").symlink_to(real)

    with pytest.raises(SystemExit, match="(?i)symlink"):
        check_vendored_libs_sync.verify()


def test_list_and_main_report_an_editable_pointer_only_lib_too(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
):
    """The regression this test exists for (PR #4954 review): a lib with one
    real copy plus a `uv`-editable-pointer consumer (no VENDOR_POINTER.json
    copy at all) is now cross-checked against canonical by `verify()`, but
    `_print_list()`'s inventory and `main()`'s success count had their own,
    separate predicates that never learned about the editable-pointer form --
    both must count/list this lib too, not just `verify()`."""
    _seed_real_lib(fake_repo, "libs/shared-lib", content="value = 1\n")
    _seed_real_lib(
        fake_repo, "plugins/agent-worktrees/libs/shared-lib", content="value = 1\n",
    )
    _seed_editable_pointer_consumer(fake_repo, "plugins/agent-bridge", "shared-lib")

    check_vendored_libs_sync._print_list()
    list_out = capsys.readouterr().out
    assert "shared-lib" in list_out
    assert "agent-bridge" in list_out

    monkeypatch.setattr("sys.argv", ["check-vendored-libs-sync.py"])
    exit_code = check_vendored_libs_sync.main()
    main_out = capsys.readouterr().out
    assert exit_code == 0
    assert "OK (1 shared libs in sync)" in main_out

