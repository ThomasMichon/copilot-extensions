"""Bounded retained-source reads with caller-owned progress and settlement."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path


def bounded_content_map(check, inputs, *, on_result=None):
    """Settle four reads at a time, reporting failures in input order."""
    results = []
    iterator = iter(inputs)
    readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="retained-content")
    pending = []
    failed = False
    try:
        while True:
            pending = []
            input_failure = None
            try:
                for _ in range(4):
                    pending.append(readers.submit(check, next(iterator)))
            except StopIteration:
                pass
            except BaseException as error:
                input_failure = error
            for future in pending:
                result = future.result()
                results.append(result)
                if on_result is not None:
                    on_result(result)
            if input_failure is not None:
                raise input_failure
            if len(pending) < 4:
                return results
    except BaseException:
        failed = True
        raise
    finally:
        # A cancelled thread join is not proof that its reader has finished.
        interruption = None
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
    projects, mirror, *, native_path, check_time, error_type,
    content_map=bounded_content_map, on_progress=None,
):
    verified_pairs = set()

    def content_pairs():
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

    def verify_content(pair):
        original_path, path = pair
        check_time()
        retained_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        check_time()
        original_hash = hashlib.sha256(Path(native_path(original_path)).read_bytes()).hexdigest()
        check_time()
        if original_hash != retained_hash:
            raise error_type("source content changed while retaining UV inputs")
        return original_path, retained_hash

    progress = {"on_result": lambda _result: on_progress()} if on_progress is not None else {}
    return dict(content_map(verify_content, content_pairs(), **progress))
