"""Opt-in real long-prefix loading without copying or modifying backend DLLs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

_PYTHON_ENV = "COPILOT_EXTENSIONS_TEST_LONG_PREFIX_VAULT_PYTHON"


@pytest.fixture(scope="module")
def long_prefix_python() -> Path:
    value = os.environ.get(_PYTHON_ENV)
    if not value:
        pytest.skip("select a disposable, canonically prepared long-prefix Vault SSH runtime")
    python = Path(value)
    assert python.is_absolute() and python.is_file(), "selected fixture interpreter is unavailable"
    return python


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows long-filename DLL loading")
@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("first_consumer", ["ssh", "protected"])
def test_real_long_prefix_crypto_consumers(
    long_prefix_python: Path, extended: bool, first_consumer: str,
) -> None:
    executable = str(long_prefix_python)
    if not executable.startswith("\\\\?\\"):
        executable = (
            "\\\\?\\UNC\\" + executable[2:] if executable.startswith("\\\\")
            else "\\\\?\\" + executable
        )
    result = subprocess.run(
        [executable, "-I", "-B", "-c", """
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

def ordinary(value):
    if value.startswith('\\\\\\\\?\\\\UNC\\\\'):
        return '\\\\\\\\' + value[8:]
    return value[4:] if value.startswith('\\\\\\\\?\\\\') else value

spec = importlib.util.find_spec('Cryptodome')
assert spec is not None and spec.origin is not None
backend = Path(spec.origin).parent
libraries = tuple(backend.rglob('*.pyd'))
assert libraries, 'selected fixture has no native crypto libraries'
units = [len(ordinary(str(path)).encode('utf-16-le', 'surrogatepass')) // 2 for path in libraries]
assert min(units) >= 260, 'fixture does not exercise ordinary Win32 long-filename loading'
before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in libraries}
root = ordinary(str(backend.parent))
if sys.argv[1] == 'extended':
    root = '\\\\\\\\?\\\\UNC\\\\' + root[2:] if root.startswith('\\\\\\\\') else '\\\\\\\\?\\\\' + root
sys.path.insert(0, root)
if sys.argv[2] == 'ssh':
    from agent_vault.ssh_key import _crypto
    _crypto()
from agent_vault.protected_crypto import AesGcm, Fernet
from agent_vault.ssh_key import _crypto
from agent_vault import native_crypto
import Cryptodome
assert Cryptodome.__file__.startswith(root)
selected_site = Path(sys.prefix) / 'Lib' / 'site-packages'
assert Path(Cryptodome.__file__).samefile(selected_site / 'Cryptodome' / '__init__.py')
assert Path(native_crypto.__file__).samefile(selected_site / 'agent_vault' / 'native_crypto.py')
ECC = _crypto()
key = ECC.generate(curve='Ed25519')
assert ECC.import_key(key.export_key(format='PEM')).public_key() == key.public_key()
cipher = AesGcm(bytes(32))
sealed = cipher.encrypt(bytes(12), b'synthetic-value', b'fixture')
assert cipher.decrypt(bytes(12), sealed, b'fixture') == b'synthetic-value'
fernet = Fernet(Fernet.generate_key())
assert fernet.decrypt(fernet.encrypt(b'synthetic-value')) == b'synthetic-value'
assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in libraries} == before
print(json.dumps({'shortest_dll_utf16_units': min(units), 'unchanged_dlls': len(libraries)}))
""", "extended" if extended else "ordinary", first_consumer],
        executable=executable, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["shortest_dll_utf16_units"] >= 260
    assert observed["unchanged_dlls"] > 0
