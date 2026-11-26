"""Native crypto filenames and fresh Windows extended-path consumers."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

from agent_vault import native_crypto


def test_installed_native_controls_require_explicit_file_selection(tmp_path: Path) -> None:
    plugin = Path(__file__).resolve().parents[1]
    shutil.copyfile(plugin / "pyproject.toml", tmp_path / "pyproject.toml")
    tests = tmp_path / "tests"
    native = tests / "native"
    native.mkdir(parents=True)
    (tests / "test_default.py").write_text("def test_control():\n    pass\n", encoding="utf-8")
    target = native / "test_native_crypto_long_prefix.py"
    shutil.copyfile(plugin / "tests/native" / target.name, target)
    environment = {
        **os.environ,
        "COPILOT_EXTENSIONS_TEST_LONG_PREFIX_VAULT_PYTHON": "must-not-be-used-by-discovery",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def collect(selection: str) -> str:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-m", "pytest", "--collect-only", "-q", selection],
            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return result.stdout

    ordinary = collect("tests")
    assert "test_default.py::test_control" in ordinary
    assert target.name not in ordinary
    explicit = collect("tests/native/" + target.name)
    assert explicit.count("test_real_long_prefix_crypto_consumers[") == 4


@pytest.mark.parametrize("root, extended", [
    ("C:\\" + "package\\" * 34, "\\\\?\\C:\\" + "package\\" * 34),
    ("\\\\server\\share\\" + "package\\" * 34,
     "\\\\?\\UNC\\server\\share\\" + "package\\" * 34),
    ("\\\\?\\C:\\package\\", "\\\\?\\C:\\package\\"),
    ("\\\\?\\UNC\\server\\share\\package\\", "\\\\?\\UNC\\server\\share\\package\\"),
])
def test_native_filename_normalizes_before_extended_loading(root: str, extended: str) -> None:
    assert native_crypto._native_filename(root + "Util\\..\\Hash\\_SHA512.pyd") == (
        extended + "Hash\\_SHA512.pyd"
    )


@pytest.mark.parametrize("filename", [
    "C:\\package\\Util\\..\\Hash\\_SHA512.pyd",
    "\\\\server\\share\\package\\Util\\..\\Hash\\_SHA512.pyd",
    "Cryptodome\\Hash\\_SHA512.pyd",
    "\\Hash\\" + "package\\" * 34 + "_SHA512.pyd",
    "\\\\.\\C:\\" + "package\\" * 34 + "_SHA512.pyd",
])
def test_short_relative_and_device_filenames_are_unchanged(filename: str) -> None:
    assert native_crypto._native_filename(filename) == filename


@pytest.mark.parametrize("characters, extended", [(119, False), (120, True)])
def test_non_bmp_filename_uses_windows_utf16_threshold(characters: int, extended: bool) -> None:
    filename = "C:\\" + "\U0001f600" * characters + "\\Hash\\_SHA512.pyd"
    assert len(filename) < 260
    assert native_crypto._native_filename(filename) == ("\\\\?\\" + filename if extended else filename)


def test_windows_preparation_wraps_once_and_preserves_resolver_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Sequence[str], str]] = []

    def original(components: Sequence[str], filename: str) -> str:
        calls.append((components, filename))
        if components[0] != "Cryptodome":
            raise ValueError("foreign package")
        return "\\\\?\\C:\\package\\Util\\..\\Hash\\" + filename

    raw_api = SimpleNamespace(pycryptodome_filename=original)
    imports: list[str] = []

    def load(name: str) -> SimpleNamespace:
        imports.append(name)
        return raw_api

    monkeypatch.setattr(native_crypto, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(native_crypto, "import_module", load)
    native_crypto.prepare_native_crypto()
    wrapped = raw_api.pycryptodome_filename
    native_crypto.prepare_native_crypto()
    assert raw_api.pycryptodome_filename is wrapped
    assert wrapped(["Cryptodome", "Hash"], "_SHA512.pyd") == (
        "\\\\?\\C:\\package\\Hash\\_SHA512.pyd"
    )
    with pytest.raises(ValueError, match="foreign package"):
        wrapped(["other"], "_SHA512.pyd")
    assert len(calls) == 2
    assert imports == ["Cryptodome.Util._raw_api"] * 2


def test_non_windows_preparation_does_not_import_optional_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_name: str) -> NoReturn:
        pytest.fail("optional backend must not be imported")

    monkeypatch.setattr(native_crypto, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(native_crypto, "import_module", forbidden)
    native_crypto.prepare_native_crypto()


def _package_spelling(root: str, extended: bool) -> str:
    if root.startswith("\\\\?\\UNC\\"):
        root = "\\\\" + root[8:]
    elif root.startswith("\\\\?\\"):
        root = root[4:]
    if not extended:
        return root
    return "\\\\?\\UNC\\" + root[2:] if root.startswith("\\\\") else "\\\\?\\" + root


@pytest.mark.parametrize("root, ordinary, extended", [
    ("C:\\package", "C:\\package", "\\\\?\\C:\\package"),
    ("\\\\server\\share\\package", "\\\\server\\share\\package", "\\\\?\\UNC\\server\\share\\package"),
    ("\\\\?\\C:\\package", "C:\\package", "\\\\?\\C:\\package"),
    ("\\\\?\\UNC\\server\\share\\package", "\\\\server\\share\\package", "\\\\?\\UNC\\server\\share\\package"),
])
def test_backend_package_spelling_preserves_drive_and_unc_namespaces(
    root: str, ordinary: str, extended: str,
) -> None:
    assert _package_spelling(root, False) == ordinary
    assert _package_spelling(root, True) == extended


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows DLL loading")
@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("first_consumer", ["ssh", "protected"])
def test_fresh_crypto_consumers_with_native_path_spelling(
    extended: bool, first_consumer: str,
) -> None:
    spec = importlib.util.find_spec("Cryptodome")
    assert spec is not None and spec.origin is not None
    original = Path(spec.origin).parent
    before = {
        str(path.relative_to(original)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in original.rglob("*.pyd")
    }
    assert before
    source = str(Path(native_crypto.__file__).resolve().parents[1])
    root = str(original.parent)
    spelling = _package_spelling(root, extended)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", """
import sys
sys.path[:0] = sys.argv[1:3]
if sys.argv[3] == "ssh":
    from agent_vault.ssh_key import _crypto
    ECC = _crypto()
from agent_vault.protected_crypto import AesGcm, Fernet
from agent_vault.ssh_key import _crypto
ECC = _crypto()
key = ECC.generate(curve="Ed25519")
assert ECC.import_key(key.export_key(format="PEM")).public_key() == key.public_key()
cipher = AesGcm(bytes(32))
sealed = cipher.encrypt(bytes(12), b"synthetic-value", b"fixture")
assert cipher.decrypt(bytes(12), sealed, b"fixture") == b"synthetic-value"
fernet = Fernet(Fernet.generate_key())
assert fernet.decrypt(fernet.encrypt(b"synthetic-value")) == b"synthetic-value"
import Cryptodome
assert Cryptodome.__file__.startswith(sys.argv[1])
""", spelling, source, first_consumer],
        executable=sys.executable,
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert {
        str(path.relative_to(original)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in original.rglob("*.pyd")
    } == before
