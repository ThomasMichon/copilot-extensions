"""Compile the pre-uv tier (tools/pre-uv-tier.txt) with the running Python.

CI runs this with Python 3.9 through uv:

    uv run --no-project --managed-python --python 3.9 python tools/check_pre_uv_tier.py

This file is itself in the pre-uv tier, so it must stay Python 3.9 compatible.
"""

import ast
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tools" / "pre-uv-tier.txt"
REQUIRED = (3, 9)


def listed_files(manifest: Path = MANIFEST) -> List[str]:
    entries = []  # type: List[str]
    for line in manifest.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.append(entry)
    return entries


def fragment_files(source: bytes, entry: str) -> List[str]:
    """Return the sibling fragments a listed entrypoint loads and executes.

    An entrypoint that declares a top-level `_FRAGMENT_FILES` tuple of file
    names compiles and runs those files in its own namespace, so they belong
    to the pre-uv tier too.
    """
    try:
        tree = ast.parse(source, entry)
    except SyntaxError:
        return []
    parent = entry.rsplit("/", 1)[0] + "/" if "/" in entry else ""
    names = []  # type: List[str]
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_FRAGMENT_FILES" for target in node.targets):
            continue
        for item in node.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                names.append(parent + item.value)
    return names


def check(root: Path = ROOT, manifest: Path = MANIFEST) -> Tuple[List[str], int]:
    """Compile every listed file and the fragments it loads; return failures and count."""
    failures: List[str] = []
    pending = listed_files(manifest)
    seen: Set[str] = set()
    while pending:
        entry = pending.pop(0)
        if entry in seen:
            continue
        seen.add(entry)
        try:
            source = (root / entry).read_bytes()
        except OSError as error:
            failures.append("%s: cannot read (%s)" % (entry, error))
            continue
        try:
            compile(source, entry, "exec", dont_inherit=True)
        except SyntaxError as error:
            failures.append("%s:%s: %s" % (entry, error.lineno, error.msg))
            continue
        pending.extend(fragment_files(source, entry))
    return failures, len(seen)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--any-version" not in args and sys.version_info[:2] != REQUIRED:
        print(
            "check_pre_uv_tier: run with Python %d.%d (found %d.%d); use "
            "uv run --no-project --managed-python --python 3.9 python tools/check_pre_uv_tier.py"
            % (REQUIRED + tuple(sys.version_info[:2])),
            file=sys.stderr,
        )
        return 2
    failures, count = check()
    for failure in failures:
        print("pre-uv tier: " + failure, file=sys.stderr)
    if failures:
        return 1
    print("pre-uv tier: %d file(s) compile with Python %d.%d"
          % ((count,) + tuple(sys.version_info[:2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())