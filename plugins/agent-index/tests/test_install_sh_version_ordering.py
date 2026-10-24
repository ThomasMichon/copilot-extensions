"""Keep package ordering portable at downgrade and publication boundaries."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def function(name: str) -> str:
    text = INSTALL.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


def run(tmp_path: Path, body: str, *args: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    bash = (
        str(Path(git).resolve().parents[1] / "bin" / "bash.exe") if git else None
    ) if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("A native Bash interpreter is unavailable")
    script = tmp_path / "ordering.sh"
    script.write_text(
        "set -euo pipefail\n" + function("_version_lt")
        + "\nsort() { printf 'unexpected sort invocation\\n' >&2; return 97; }\n"
        + body, encoding="utf-8", newline="\n",
    )
    return subprocess.run(
        [bash, "--noprofile", "--norc", script.name, *args], cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize(("a", "b", "older"), [
    ("0.1.2-dev74", "0.1.2.dev102", True),
    ("0.1.2.dev102", "0.1.2-dev74", False),
    ("0.1.2.dev74", "0.1.2-dev74", False),
    ("0.1.9", "0.1.10", True),
    ("0.9.0", "0.10.0", True),
    ("1.0.0", "0.9.999", False),
    ("0.1.2.dev5", "0.1.2", True),
    ("0.1.2", "0.1.2.dev5", False),
    ("0.1.2", "0.1.2", False),
    ("1.02.3-dev0009", "1.2.3.dev10", True),
    ("0.1.2+source.aaaa", "0.1.2+source.bbbb", False),
    ("0.1.2.source.1234", "0.1.2", False),
])
def test_portable_package_ordering(tmp_path, a, b, older):
    result = run(
        tmp_path,
        'if _version_lt "$1" "$2"; then printf older; else printf not-older; fi\n',
        a, b,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == ("older" if older else "not-older")


@pytest.mark.parametrize(("source", "installed", "refused"), [
    ("0.1.2-dev150", "0.1.2.dev82", False),
    ("0.1.2-dev82", "0.1.2.dev150", True),
    ("0.1.2", "0.1.2.dev150", False),
    ("0.1.2-dev150", "0.1.2", True),
    ("0.1.2", "0.1.3", True),
])
def test_actual_downgrade_guard(tmp_path, source, installed, refused):
    body = function("_downgrade_guard") + """
CASE_SOURCE="$1"; CASE_INSTALLED="$2"; FORCE=0; ACTION=update
_source_version() { printf '%s\\n' "$CASE_SOURCE"; }
_installed_version() { printf '%s\\n' "$CASE_INSTALLED"; }
_warn() { :; }
_fail() { printf '%s\\n' "$*" >&2; }
_downgrade_guard
"""
    result = run(tmp_path, body, source, installed)
    assert result.returncode == (1 if refused else 0), result.stderr
    assert ("Refusing to downgrade" in result.stderr) == refused
    assert "unexpected sort invocation" not in result.stderr


def test_first_install_has_no_downgrade(tmp_path):
    body = function("_downgrade_guard") + """
_installed_version() { return 1; }
_source_version() { return 97; }
_downgrade_guard
"""
    result = run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("marker", ["current-version", "stamped-version"])
@pytest.mark.parametrize(("source", "published", "fresh"), [
    ("0.1.2-dev150", "0.1.2.dev82", True),
    ("0.1.2-dev82", "0.1.2.dev150", False),
    ("0.1.2", "0.1.2.dev150", True),
    ("0.1.2-dev150", "0.1.2", False),
    ("0.1.2+source.aaaa", "0.1.2+source.bbbb", True),
])
def test_publication_freshness_uses_portable_ordering(
    tmp_path, marker, source, published, fresh,
):
    (tmp_path / marker).write_text(published + "\n", encoding="utf-8")
    body = function("_index_publication_fresh") + """
SRC_VERSION="$1"; INSTALL_DIR="$PWD"; FORCE=0
if _index_publication_fresh; then printf fresh; else printf superseded; fi
"""
    result = run(tmp_path, body, source, published)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == ("fresh" if fresh else "superseded")
