"""Stdlib entry run under the selected service's retained runtime, not its payload."""

from __future__ import annotations

import contextlib
import io
import json
import re
import runpy
import stat
import sys
import sysconfig
from pathlib import Path

LIMIT = 64 * 1024


class BoundedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.size = 0

    def write(self, text):
        self.size += len(text.encode("utf-8"))
        if self.size > LIMIT:
            raise ValueError("hourly output limit")
        return super().write(text)


def collect(service: str, module: str, hour: str) -> dict:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,62}", service) is None or module != service.replace("-", "_"):
        raise ValueError("invalid native service")
    path = Path(sysconfig.get_path("purelib")) / module / "hourly-reporting.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"status": "unsupported"}
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > LIMIT:
        return {"status": "invalid"}
    # Import no service code until its selected wheel declares this read-only operation.
    from process_telemetry.hourly import DECLARATION, declaration, decode

    with path.open("rb") as stream:
        support = decode(stream.read(LIMIT + 1))
    if (
        type(support) is dict and support.get("schema") == DECLARATION
        and type(support.get("version")) is int and support["version"] != 1
    ):
        return {"status": "unsupported"}
    support = declaration(support, service)
    captured = BoundedOutput()
    previous = sys.argv
    try:
        sys.argv = [module, "telemetry", "hourly", "--hour", hour]
        with contextlib.redirect_stdout(captured):
            try:
                runpy.run_module(module, run_name="__main__", alter_sys=True)
            except SystemExit as error:
                if error.code not in (0, None):
                    return {"status": "unavailable"}
    finally:
        sys.argv = previous
    return {
        "status": "collected", "declaration": support,
        "contribution": decode(captured.getvalue().encode("utf-8")),
    }


def main() -> int:
    try:
        result = collect(*sys.argv[1:])
        raw = json.dumps(result, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > LIMIT:
            raise ValueError("hourly output limit")
    except (ValueError, OSError, ImportError, TypeError, RecursionError):
        raw = '{"status":"invalid"}'
    print(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
