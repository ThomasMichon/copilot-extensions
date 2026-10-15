"""Bounded, synthetic two-consumer routing-read benchmark; never a service installer."""

from __future__ import annotations

import argparse
import json
import queue
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[4]
for library in ("zdd", "agent-procutil", "work-coalescing-singleton"):
    sys.path.insert(0, str(REPO / "libs" / library / "src"))

from agent_procutil import no_window_kwargs  # noqa: E402
from work_coalescing_singleton import CoalescingServer, client  # noqa: E402
from zdd.routing import read_table  # noqa: E402

CONSUMERS = ("alpha", "beta")


def read(root: Path, consumer: str) -> dict:
    if consumer not in CONSUMERS:
        raise ValueError("unknown synthetic consumer")
    table = read_table(root / consumer, strict=True)
    if table != {"fixture": consumer}:
        raise RuntimeError("routing fixture missing or crossed consumer boundary")
    return table


def serve(root: Path) -> None:
    def compute(kind: str, payload: dict) -> dict:
        if kind != "read":
            raise ValueError("unsupported probe operation")
        return read(root, payload["consumer"])

    server = CoalescingServer(compute)
    try:
        server.start()
        print(json.dumps(server.rendezvous()), flush=True)
        sys.stdin.readline()
    finally:
        server.close()


@contextmanager
def helper(root: Path):
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve", str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **no_window_kwargs(),
    )
    ready: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=lambda: ready.put(process.stdout.readline()), daemon=True
    )
    reader.start()
    try:
        rendezvous = json.loads(ready.get(timeout=15))
        host, port = rendezvous["endpoint"].rsplit(":", 1)
        endpoint = (host, int(port), rendezvous["token"])
        # A successful request, not just the startup line, establishes readiness.
        if request(endpoint, "alpha") != {"fixture": "alpha"}:
            raise RuntimeError("helper readiness returned the wrong consumer")
        yield endpoint
    finally:
        if process.poll() is None:
            process.stdin.write("\n")
            process.stdin.flush()
        try:
            _, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        reader.join(timeout=1)
        if process.returncode:
            raise RuntimeError(f"probe helper exited {process.returncode}: {stderr}")


def request(endpoint: tuple[str, int, str], consumer: str) -> dict:
    return client.request(
        *endpoint,
        kind="read",
        key=consumer,
        payload={"consumer": consumer},
        request_deadline_s=2,
        client_id=consumer,
    )


def measure(operation, rounds: int) -> dict:
    def run(consumer: str) -> list[float]:
        samples = []
        for _ in range(rounds):
            start = time.perf_counter()
            if operation(consumer) != {"fixture": consumer}:
                raise RuntimeError("measured operation returned the wrong consumer")
            samples.append((time.perf_counter() - start) * 1000)
        return samples

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, CONSUMERS))
    samples = sorted(sample for result in results for sample in result)
    return {
        "requests": len(samples),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(samples[max(0, (95 * len(samples) + 99) // 100 - 1)], 3),
        "wall_ms": round((time.perf_counter() - start) * 1000, 3),
    }


def probe(rounds: int) -> dict:
    with TemporaryDirectory(prefix=".probe-", dir=Path(__file__).parent) as directory:
        root = Path(directory)
        for consumer in CONSUMERS:
            (root / consumer).mkdir()
            (root / consumer / "active.json").write_text(
                json.dumps({"fixture": consumer}), encoding="utf-8"
            )
        direct = measure(lambda consumer: read(root, consumer), rounds)

        def cold(consumer: str) -> dict:
            result = subprocess.run(
                [
                    sys.executable, str(Path(__file__).resolve()),
                    "--read", str(root), consumer,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
                **no_window_kwargs(),
            )
            return json.loads(result.stdout)

        cold_result = measure(cold, min(rounds, 10))
        with helper(root) as first, helper(root) as second:
            endpoints = dict(zip(CONSUMERS, (first, second)))
            separate = measure(
                lambda consumer: request(endpoints[consumer], consumer), rounds
            )
        with helper(root) as shared:
            combined = measure(lambda consumer: request(shared, consumer), rounds)
        try:
            request(shared, "alpha")
        except client.DaemonUnavailable:
            unavailable_detected = True
        else:
            raise RuntimeError("closed helper still answered")
        if read(root, "alpha") != {"fixture": "alpha"}:
            raise RuntimeError("direct path failed after helper shutdown")
        return {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "rounds_per_consumer": rounds,
            "warm_direct_read": direct,
            "fresh_process_per_read": cold_result,
            "two_resident_helpers": separate,
            "one_shared_helper": combined,
            "closed_helper_refused_and_direct_path_preserved": unavailable_detected,
            "limits": (
                "Synthetic trusted consumers; no auth isolation, install, "
                "RSS or cutover proof."
            ),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--read", nargs=2)
    parser.add_argument("--rounds", type=int, default=100)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
    elif args.read:
        print(json.dumps(read(Path(args.read[0]), args.read[1])))
    else:
        if not 1 <= args.rounds <= 1000:
            parser.error("--rounds must be between 1 and 1000")
        print(json.dumps(probe(args.rounds), indent=2))
