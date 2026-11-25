"""POSIX installers construct runtimes at the shared requires-python floor."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REQUEST = re.compile(r"""new_signed_venv\s+\S+\s+\S+\s+["']?(\d+)\.(\d+)["']?""")


def test_posix_installers_request_at_least_the_python311_floor() -> None:
    sources = sorted(ROOT.glob("plugins/*/scripts/install.sh")) + sorted(
        ROOT.glob("plugins/*/scripts/cell_install.py")
    )
    requests: list[tuple[str, tuple[int, int]]] = []
    for path in sources:
        for match in REQUEST.finditer(path.read_text(encoding="utf-8")):
            requests.append((path.relative_to(ROOT).as_posix(), (int(match[1]), int(match[2]))))
    assert requests, "expected POSIX installers to construct signed venvs"
    assert [entry for entry in requests if entry[1] < (3, 11)] == []
