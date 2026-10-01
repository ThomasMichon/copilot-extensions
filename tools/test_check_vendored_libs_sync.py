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
    return tmp_path


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
