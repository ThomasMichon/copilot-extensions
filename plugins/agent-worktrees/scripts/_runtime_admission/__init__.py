"""Canonical admission tooling; activation is not source or feed approval."""
from pathlib import Path
import json
import sys


def activate() -> None:
    """Make only this self-contained toolkit's first-party owners importable.

    Call in the installation authority's admitted tooling interpreter, never in
    the runtime under construction. Packaging is supplied by that locked
    tooling environment; this payload contains no third-party dependencies.
    """
    root = Path(__file__).resolve().parent
    record = json.loads((root / "toolkit.json").read_text(encoding="utf-8"))
    paths = [str(root / relative) for relative in record["importRoots"]]
    sys.path[:0] = [path for path in paths if path not in sys.path]
