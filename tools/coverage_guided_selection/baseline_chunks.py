"""Bounded scheduling of independent coverage-driver processes.

The two-worker path runs inside one existing plugin-test containment tree.
Coverage instrumentation stays in each driver, never in executor threads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class BaselineCollectionError(RuntimeError):
    """A failed collection cannot earn a partial baseline."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        details = "\n".join(
            f"{label}:\n{stream.rstrip()}"
            for label, stream in (("stdout", stdout), ("stderr", stderr))
            if stream
        )
        super().__init__(f"coverage baseline collection failed (exit {returncode}):\n{details}")


def timeout_error(
    exc: subprocess.TimeoutExpired, *, timeout_s: float,
) -> BaselineCollectionError:
    def decode(value: str | bytes | None) -> str:
        return value.decode() if isinstance(value, bytes) else (value or "")

    return BaselineCollectionError(
        -1,
        decode(exc.stdout),
        f"timed out after {timeout_s}s" + ((": " + decode(exc.stderr)) if exc.stderr else ""),
    )


@dataclass(frozen=True)
class Chunk:
    index: int
    total: int
    command: list[str]
    cwd: str
    sandbox: str
    coverage_file: str
    output_file: str
    timeout_s: float
    complete: bool = False


def run_chunk(chunk: Chunk) -> dict:
    from tools.coverage_guided_selection.baseline import _subprocess_env
    from tools.plugin_test_containment import no_window_kwargs

    paths = json.loads(chunk.command[-7])
    label = f"chunk {chunk.index + 1}/{chunk.total} {json.dumps(paths)}"
    print(
        f"Collecting coverage baseline chunk {chunk.index + 1}/{chunk.total} "
        f"({len(paths)} path(s), {chunk.timeout_s:g}s limit) {json.dumps(paths)}",
        flush=True,
    )
    try:
        proc = subprocess.run(
            chunk.command,
            cwd=Path(chunk.cwd),
            env=_subprocess_env(
                Path(chunk.coverage_file), Path(chunk.sandbox),
                **({"complete": True} if chunk.complete else {}),
            ),
            capture_output=True,
            text=True,
            timeout=chunk.timeout_s,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        error = timeout_error(exc, timeout_s=chunk.timeout_s)
        raise BaselineCollectionError(
            error.returncode, error.stdout, f"{label}\n{error.stderr}",
        ) from exc
    if proc.returncode != 0:
        raise BaselineCollectionError(proc.returncode, proc.stdout, f"{label}\n{proc.stderr}")
    try:
        result = json.loads(Path(chunk.output_file).read_text())
        if not isinstance(result, dict) or any(
            not isinstance(result.get(key), dict) for key in ("durations", "coverage")
        ):
            raise ValueError("driver output must contain duration and coverage mappings")
    except (OSError, ValueError) as exc:
        raise BaselineCollectionError(
            -1, proc.stdout, f"{label}\n{proc.stderr}\ninvalid coverage driver output: {exc}",
        ) from exc
    return result


def run_parallel(chunks: list[Chunk]) -> list[dict]:
    """Keep at most two chunks active and merge in discovery order.

    Stop scheduling on failure. Already-running chunks finish under their
    unchanged individual deadlines; the enclosing containment owns and reaps
    their complete descendant tree, including on aggregate resource failure.
    """
    results: dict[int, dict] = {}
    failed = Event()

    def collect(chunk: Chunk) -> dict:
        try:
            return run_chunk(chunk)
        except Exception:
            failed.set()
            raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending: dict[Future, int] = {}
        next_index = 0
        while pending or next_index < len(chunks):
            while len(pending) < 2 and next_index < len(chunks) and not failed.is_set():
                pending[pool.submit(collect, chunks[next_index])] = next_index
                next_index += 1
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=pending.__getitem__):
                index = pending.pop(future)
                results[index] = future.result()
    return [results[index] for index in range(len(chunks))]


def collect_contained(
    chunks: list[Chunk], tmp_path: Path, *, workers: int = 2, bounds: dict | None = None,
) -> list[dict]:
    from tools.coverage_guided_selection.baseline import _subprocess_env
    from tools.plugin_test_containment import Limits, run_contained

    manifest = tmp_path / "chunks.json"
    output = tmp_path / "chunks-result.json"
    error = tmp_path / "chunks-error.json"
    manifest.write_text(json.dumps([asdict(chunk) for chunk in chunks]), encoding="utf-8")
    limits = Limits() if bounds is None else Limits(
        wall_seconds=bounds["plugin_seconds"], max_processes=bounds["max_processes"],
        max_memory_mb=bounds["max_memory_mb"], max_temp_mb=bounds["max_temp_mb"],
    )
    returncode = run_contained(
        [sys.executable, str(Path(__file__).resolve()), str(manifest), str(output), str(error),
         str(workers)],
        cwd=Path(chunks[0].cwd),
        env=_subprocess_env(tmp_path / ".coverage-control", tmp_path / "control"),
        sandbox=tmp_path,
        limits=limits,
    )
    if returncode != 0:
        if error.is_file():
            details = json.loads(error.read_text(encoding="utf-8"))
            raise BaselineCollectionError(**details)
        raise BaselineCollectionError(
            returncode, "", "contained coverage scheduler failed; see containment diagnostics",
        )
    results = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(results, list) or len(results) != len(chunks):
        raise BaselineCollectionError(-1, "", "incomplete contained coverage results")
    return results


def main(argv: list[str]) -> int:
    manifest, output, error = map(Path, argv[:3])
    workers = int(argv[3]) if len(argv) > 3 else 2
    if workers not in (1, 2):
        raise ValueError("coverage workers must be 1 or 2")
    chunks = [Chunk(**entry) for entry in json.loads(manifest.read_text(encoding="utf-8"))]
    try:
        results = run_parallel(chunks) if workers == 2 else [run_chunk(chunk) for chunk in chunks]
    except BaselineCollectionError as exc:
        error.write_text(json.dumps({
            "returncode": exc.returncode, "stdout": exc.stdout, "stderr": exc.stderr,
        }), encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1
    output.write_text(json.dumps(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
