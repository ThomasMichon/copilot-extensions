"""Experimental reference implementation; see the parent README."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

MAX_SAMPLE_GAP_SECONDS = 300
COMPONENTS = ("producer", "relay-a", "worker-a", "worker-b")


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamps must be strings")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must include a timezone offset or Z")
    return timestamp.astimezone(timezone.utc)


def read_samples(log_dir: Path) -> list[dict]:
    if not log_dir.is_dir():
        raise FileNotFoundError(f"health log directory does not exist: {log_dir}")
    samples = []
    for path in sorted(log_dir.glob("health.jsonl*")):
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    sample = json.loads(line)
                    parse_timestamp(sample["timestamp"])
                    if sample["schema_version"] != 1:
                        raise ValueError("unsupported schema")
                    for component in COMPONENTS:
                        state = sample["producer"] if component == "producer" else sample["tunnels"][component]
                        if state["status"] not in ("healthy", "unhealthy"):
                            raise ValueError("invalid health status")
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError(f"{path}:{line_number}: invalid health sample: {exc}") from exc
                samples.append(sample)
    return sorted(samples, key=lambda sample: parse_timestamp(sample["timestamp"]))


def summarize(
    samples: list[dict], since: datetime | None, until: datetime
) -> dict:
    if since is not None and since > until:
        raise ValueError("--since must not be after --until")
    selected = [
        sample for sample in samples
        if (since is None or parse_timestamp(sample["timestamp"]) >= since)
        and parse_timestamp(sample["timestamp"]) <= until
    ]
    start = since or (parse_timestamp(selected[0]["timestamp"]) if selected else None)
    gaps = []
    previous = start
    for current in [parse_timestamp(sample["timestamp"]) for sample in selected] + [until]:
        if previous is not None and (current - previous).total_seconds() > MAX_SAMPLE_GAP_SECONDS:
            gaps.append({
                "from": previous.isoformat(),
                "to": current.isoformat(),
                "seconds": (current - previous).total_seconds(),
            })
        previous = current
    last_sample = parse_timestamp(selected[-1]["timestamp"]) if selected else None
    components = {}
    unhealthy_events = []
    for sample in selected:
        unhealthy = {}
        for component in COMPONENTS:
            state = sample["producer"] if component == "producer" else sample["tunnels"][component]
            if state["status"] == "unhealthy":
                unhealthy[component] = state
        if unhealthy:
            unhealthy_events.append({"timestamp": sample["timestamp"], "components": unhealthy})
    for component in COMPONENTS:
        states = [
            sample["producer"] if component == "producer" else sample["tunnels"][component]
            for sample in selected
        ]
        components[component] = {
            "healthy_samples": sum(state["status"] == "healthy" for state in states),
            "unhealthy_samples": sum(state["status"] == "unhealthy" for state in states),
            "last_status": (
                states[-1]["status"]
                if last_sample and (until - last_sample).total_seconds() <= MAX_SAMPLE_GAP_SECONDS
                else "unknown"
            ),
        }
    return {
        "sample_count": len(selected),
        "from": start.isoformat() if start else None,
        "to": until.isoformat(),
        "last_sample": last_sample.isoformat() if last_sample else None,
        "components": components,
        "unhealthy_events": unhealthy_events,
        "unobserved_intervals": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=Path.home() / "harness-gateway-fleet")
    parser.add_argument("--since", type=parse_timestamp, help="ISO timestamp including timezone")
    parser.add_argument("--until", type=parse_timestamp, default=datetime.now(timezone.utc))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = summarize(read_samples(args.log_dir), args.since, args.until)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"health history: {exc}\n")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Samples: {report['sample_count']} | Last sample: {report['last_sample'] or 'none'}")
        for name, state in report["components"].items():
            print(
                f"{name}: last={state['last_status']} "
                f"healthy={state['healthy_samples']} unhealthy={state['unhealthy_samples']}"
            )
        for gap in report["unobserved_intervals"]:
            print(f"UNKNOWN: {gap['from']} .. {gap['to']} ({gap['seconds']:.0f}s without a sample)")
        for event in report["unhealthy_events"]:
            print(f"UNHEALTHY: {event['timestamp']} {json.dumps(event['components'], sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
