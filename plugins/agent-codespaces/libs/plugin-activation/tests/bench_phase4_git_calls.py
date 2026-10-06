"""Manual, non-pytest-collected bench isolating picker-performance-and-
responsiveness Phase 4's remaining-item fix: caching the resolved ``git``
executable, and folding the bare-anchor ``--is-bare-repository``/
``--absolute-git-dir`` probe into one ``git rev-parse`` call.

Not part of the regular suite (no ``test_`` prefix) -- run directly:

    python libs/plugin-activation/tests/bench_phase4_git_calls.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin-resolve" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dropin-registry" / "src"))

from plugin_activation import resolver  # noqa: E402


def _bench_which_executable(iterations: int) -> tuple[float, float]:
    start = time.perf_counter()
    for _ in range(iterations):
        shutil.which("git")
    uncached = time.perf_counter() - start

    resolver._GIT_WHICH_CACHE.clear()
    start = time.perf_counter()
    for _ in range(iterations):
        if "git" not in resolver._GIT_WHICH_CACHE:
            resolver._GIT_WHICH_CACHE["git"] = shutil.which("git")
    cached = time.perf_counter() - start
    return uncached, cached


def _bench_bare_probe(root: Path, iterations: int) -> tuple[float, float]:
    git = shutil.which("git")
    assert git, "git must be on PATH to run this bench"

    def run(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            [git, "-c", "safe.bareRepository=all", "-C", str(root), *args],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()

    start = time.perf_counter()
    for _ in range(iterations):
        run("rev-parse", "--is-bare-repository")
        run("rev-parse", "--absolute-git-dir")
    two_calls = time.perf_counter() - start

    start = time.perf_counter()
    for _ in range(iterations):
        run("rev-parse", "--is-bare-repository", "--absolute-git-dir")
    one_call = time.perf_counter() - start
    return two_calls, one_call


def main() -> None:
    iterations = 200
    uncached, cached = _bench_which_executable(iterations)
    print(
        f"shutil.which('git') x{iterations}: {uncached:.3f}s  |  "
        f"_git-cached-lookup x{iterations}: {cached:.3f}s  |  "
        f"reduction: {(1 - cached / uncached) * 100:.1f}%"
    )

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bare"
        root.mkdir()
        subprocess.run(["git", "init", "--bare", "-q", str(root)], check=True)
        two_calls, one_call = _bench_bare_probe(root, iterations=30)
        print(
            f"bare-probe 2-calls x30: {two_calls:.3f}s  |  "
            f"bare-probe 1-call x30: {one_call:.3f}s  |  "
            f"reduction: {(1 - one_call / two_calls) * 100:.1f}%"
        )


if __name__ == "__main__":
    main()
