"""Run the independent full-source guards within one CI step."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUARDS = (
    "check-no-sibling-tracking-writes.py",
    "check-headless-launch.py",
    "check-daemon-coverage.py",
    "check-asyncio-timeout-handlers.py",
)


def _run_guard(guard: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / guard)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
            check=False,
            **(
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if sys.platform == "win32"
                else {}
            ),
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, output + "\nGuard exceeded its 300-second deadline.\n"
    except OSError as exc:
        return 125, f"Could not launch guard: {exc}\n"
    return result.returncode, result.stdout


def main() -> int:
    failed = False
    with ThreadPoolExecutor(max_workers=len(GUARDS)) as pool:
        for guard, (status, output) in zip(GUARDS, pool.map(_run_guard, GUARDS)):
            print(f"::group::{guard}", flush=True)
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
            print("::endgroup::", flush=True)
            if status:
                print(f"::error::{guard} exited with status {status}", flush=True)
                failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
