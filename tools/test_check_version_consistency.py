from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-version-consistency.py"
_spec = importlib.util.spec_from_file_location("check_version_consistency", SCRIPT)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def _write(tmp_path: Path, text: str, name: str = "_build_info.py") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_source_fallback_accepts_init_and_build_info_literals(
    tmp_path: Path,
) -> None:
    versions, errors = checker._source_fallback_versions(
        _write(tmp_path, "__version__ = '1.2.3-dev4'\n", "__init__.py")
    )
    assert list(versions.values()) == ["1.2.3-dev4"]
    assert errors == []

    versions, errors = checker._source_fallback_versions(
        _write(tmp_path, '__version__ = "1.2.3"\n')
    )
    assert list(versions.values()) == ["1.2.3"]
    assert errors == []


def test_source_fallback_matching_and_mismatched_dev_versions_are_exact(
    tmp_path: Path,
) -> None:
    versions, errors = checker._source_fallback_versions(
        _write(tmp_path, '__version__ = "1.2.3-dev4"\n')
    )
    assert errors == []
    assert set(versions.values()) | {"1.2.3-dev4"} == {"1.2.3-dev4"}
    assert set(versions.values()) | {"1.2.3-dev5"} == {
        "1.2.3-dev4",
        "1.2.3-dev5",
    }


def test_source_fallback_accepts_fallback_variable_and_exact_dev_literal(
    tmp_path: Path,
) -> None:
    versions, errors = checker._source_fallback_versions(
        _write(tmp_path, '_FALLBACK_VERSION = "1.2.3-dev4"\n')
    )
    assert list(versions.values()) == ["1.2.3-dev4"]
    assert errors == []


def test_source_fallback_records_pep440_and_malformed_literals_but_rejects_them(
    tmp_path: Path,
) -> None:
    for literal in ("1.2.3.dev4", "not-a-version"):
        versions, errors = checker._source_fallback_versions(
            _write(tmp_path, f'__version__ = "{literal}"\n')
        )
        assert list(versions.values()) == [literal]
        assert errors and "invalid version literal" in errors[0]
    assert "1.2.3.dev4" != "1.2.3-dev4"  # exact comparison; no normalization


def test_source_fallback_rejects_nonconstant_assignment(
    tmp_path: Path,
) -> None:
    versions, errors = checker._source_fallback_versions(
        _write(tmp_path, "__version__ = resolve()\n")
    )
    assert versions == {}
    assert any("not a constant string" in error for error in errors)


def test_source_fallback_rejects_duplicate_monitored_assignments(
    tmp_path: Path,
) -> None:
    versions, errors = checker._source_fallback_versions(
        _write(
            tmp_path,
            '__version__ = "1.2.3-dev3"\n__version__ = "1.2.3-dev4"\n',
        )
    )
    assert list(versions.values()) == ["1.2.3-dev3", "1.2.3-dev4"]
    assert any("2 monitored assignments" in error for error in errors)


def test_explicit_dynamic_primary_contract_still_tracks_fallback(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        '__version__ = package_version()\n__version__ = "1.2.3-dev4"\n',
        "__init__.py",
    )
    versions, errors = checker._source_fallback_versions(
        path, allow_dynamic_primary=True
    )
    assert list(versions.values()) == ["1.2.3-dev4"]
    assert errors == []


def test_source_fallback_without_monitored_assignment_is_ignored(
    tmp_path: Path,
) -> None:
    assert checker._source_fallback_versions(
        _write(tmp_path, 'BUILD_INFO = {"version": ""}\n')
    ) == ({}, [])


def test_source_fallback_exact_sentinel_exemption(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '__version__ = resolve()\n__version__ = "0.0.0-unknown"\n',
    )
    contract = (
        ("__version__", None),
        ("__version__", "0.0.0-unknown"),
    )
    assert checker._source_fallback_versions(
        path, exemption=contract
    ) == ({}, [])

    versions, errors = checker._source_fallback_versions(
        path,
        exemption=(("__version__", None), ("__version__", "different")),
    )
    assert list(versions.values()) == ["0.0.0-unknown"]
    assert errors


def test_unreadable_source_fallback_reports_read_failure(tmp_path: Path) -> None:
    unreadable = tmp_path / "_build_info.py"
    unreadable.mkdir()

    assert checker._source_fallback_versions(unreadable) == (
        {},
        ["cannot read file"],
    )


def test_worktree_manager_version_surfaces_must_match(tmp_path: Path) -> None:
    manager = tmp_path / "worktree-manager"
    package = manager / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (manager / "pyproject.toml").write_text(
        '[project]\nversion = "0.1.0-dev28"\n',
        encoding="utf-8",
    )
    init_path = package / "__init__.py"
    init_path.write_text('__version__ = "0.1.0-dev28"\n', encoding="utf-8")

    assert checker._worktree_manager_version_violations(manager) == []

    init_path.write_text('__version__ = "0.1.0-dev27"\n', encoding="utf-8")
    [violation] = checker._worktree_manager_version_violations(manager)
    assert violation.startswith("worktree-manager: version mismatch")
