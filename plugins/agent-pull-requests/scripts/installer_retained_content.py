"""Bounded retained-source reads with caller-owned progress and settlement."""
from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

_Input = TypeVar("_Input")
_Result = TypeVar("_Result")


def bounded_content_map(
    check: Callable[[_Input], _Result], inputs: Iterable[_Input], *,
    on_result: Callable[[_Result], None] | None = None,
) -> list[_Result]:
    """Keep up to four reads pending, reporting failures in input order."""
    results: list[_Result] = []
    iterator = iter(inputs)
    readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retained-content")
    pending: deque[Future[_Result]] = deque()
    failed = exhausted = False
    input_failure: BaseException | None = None

    def refill() -> None:
        nonlocal exhausted, input_failure
        if exhausted:
            return
        try:
            pending.append(readers.submit(check, next(iterator)))
        except StopIteration:
            exhausted = True
        except BaseException as error:  # noqa: BLE001 - settle earlier reads before input failure
            input_failure = error
            exhausted = True

    try:
        for _ in range(4):
            refill()
        while pending:
            result = pending[0].result()
            results.append(result)
            if on_result is not None:
                on_result(result)
            pending.popleft()
            refill()
        if input_failure is not None:
            raise input_failure
        return results
    except BaseException:
        failed = True
        raise
    finally:
        # A cancelled thread join is not proof that its reader has finished.
        interruption: BaseException | None = None
        while True:
            try:
                for future in pending:
                    if future.done():
                        continue
                    try:
                        future.result()
                    except BaseException as error:
                        if isinstance(error, (KeyboardInterrupt, SystemExit)):
                            interruption = interruption or error
                        if not future.done():
                            raise
                readers.shutdown(wait=True)
                break
            except (KeyboardInterrupt, SystemExit) as error:
                interruption = interruption or error
        if interruption is not None and not failed:
            raise interruption


def retained_source_hashes(
    projects: Iterable[Path], mirror: Callable[[Path], Path], *,
    native_path: Callable[[Path], str], check_time: Callable[[], object],
    error_type: type[Exception],
    content_map: Callable[..., Any] = bounded_content_map,
    on_progress: Callable[[], None] | None = None,
) -> dict[Path, str]:
    verified_pairs: set[tuple[str, str]] = set()

    def content_pairs() -> Iterator[tuple[Path, Path]]:
        for project in projects:
            destination = Path(native_path(mirror(project)))
            for path in destination.rglob("*"):
                check_time()
                if path.is_file():
                    original_path = project / path.relative_to(destination)
                    pair = (str(original_path), str(path))
                    if pair not in verified_pairs:
                        verified_pairs.add(pair)
                        yield original_path, path

    def verify_content(pair: tuple[Path, Path]) -> tuple[Path, str]:
        original_path, path = pair
        check_time()
        retained_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        check_time()
        original_hash = hashlib.sha256(Path(native_path(original_path)).read_bytes()).hexdigest()
        check_time()
        if original_hash != retained_hash:
            raise error_type("source content changed while retaining UV inputs")
        return original_path, retained_hash

    progress: dict[str, Callable[[tuple[Path, str]], None]] = (
        {"on_result": lambda _result: on_progress()} if on_progress is not None else {}
    )
    return dict(content_map(verify_content, content_pairs(), **progress))
