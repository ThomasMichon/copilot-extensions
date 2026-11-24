"""Compile the pre-uv tier (tools/pre-uv-tier.txt) with the running Python.

CI runs this with Python 3.9 through uv:

    uv run --no-project --managed-python --python 3.9 python tools/check_pre_uv_tier.py

This file is itself in the pre-uv tier, so it must stay Python 3.9 compatible.
"""

import sys
from pathlib import Path
from typing import List, Optional, Sequence

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
    failures = []  # type: List[str]
    entries = listed_files()
    for entry in entries:
        path = ROOT / entry
        try:
            source = path.read_bytes()
        except OSError as error:
            failures.append("%s: cannot read (%s)" % (entry, error))
            continue
        try:
            compile(source, entry, "exec", dont_inherit=True)
        except SyntaxError as error:
            failures.append("%s:%s: %s" % (entry, error.lineno, error.msg))
    for failure in failures:
        print("pre-uv tier: " + failure, file=sys.stderr)
    if failures:
        return 1
    print("pre-uv tier: %d file(s) compile with Python %d.%d"
          % ((len(entries),) + tuple(sys.version_info[:2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())