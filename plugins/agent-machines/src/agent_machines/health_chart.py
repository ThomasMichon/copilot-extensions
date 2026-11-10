"""A static chart over an already bounded health report; no active content."""

from __future__ import annotations

from html import escape


def _health_block(health: dict, top: int) -> tuple[list[str], int]:
    parts = []

    def text(line):
        nonlocal top
        parts.append(f'<text x="16" y="{top}">{escape(line)}</text>')
        top += 24

    text("Local health: separate owner observations, not telemetry totals or a live probe.")
    if health["state"] != "available":
        text("Local health store unavailable; no healthy or empty-history inference.")
        return parts, top
    view = health["view"]
    text(
        f'{view["observed_hours"]}/168 observed slots; source qualification: '
        f'{view["observation_qualification"]}; carrier receipt: {view["carrier_receipt"]}.',
    )
    text("Domain cells: ok green, failed red, stale amber, unknown gray, unsupported purple.")
    text("Slot cells: observed blue; missing gray; invalid/expired/future/owner-mismatch amber.")
    domains = next((
        hour["sample"]["domains"] for hour in view["hours"] if hour["state"] == "observed"
    ), {})
    colors = {
        "ok": "#237a38", "failed": "#bc3030", "stale": "#af6b00",
        "unknown": "#888", "unsupported": "#76558c", "observed": "#2463a5",
        "missing": "#ddd",
    }
    for domain in ("slots", *domains):
        text(domain.replace("_", " "))
        for index, hour in enumerate(view["hours"]):
            state = hour["state"]
            detail = f'hour {hour["hour"]}; slot {state}'
            if domain != "slots" and state == "observed":
                observation = hour["sample"]["domains"][domain]
                state = observation["state"]
                detail += (
                    f'; {domain}: {state}; observed {observation["observed_state"]}; '
                    f'source age ms {observation["age_ms"]}; evidence {observation["evidence"]}; '
                    f'reason {observation["reason"]}'
                )
            parts.append(
                f'<rect x="{16 + index * 6}" y="{top - 14}" width="5" height="10" '
                f'fill="{colors.get(state, "#af6b00")}"><title>{escape(detail)}</title></rect>',
            )
        top += 20
    text("Stale is source age at cutoff, not current uptime; missing owner evidence remains unknown.")
    return parts, top


def render(report: dict) -> str:
    hours = report["hours"]
    counts = [sum(group["observations"] for group in hour["groups"]) for hour in hours]
    errors = [
        sum(group["outcomes"].get("error", 0) for group in hour["groups"])
        for hour in hours
    ]
    timing_groups = sorted({
        (group["component"], group["stage"])
        for hour in hours for group in hour["groups"] if group["kind"] == "performance"
    })
    maximum = max(counts, default=0) or 1
    service_lines = []
    if report.get("services"):
        service_lines.append("Service contributions: separate namespaces, not added to file totals.")
        for service in report["services"]:
            service_lines.append(
                f'{service["instance"]}/{service["service"]}: {service["hour"]}; {service["state"]}',
            )
            for group in service.get("contribution", {}).get("groups", []):
                line = f'{group["metric"]}/{group["stage"]}: {group["observations"]} observed events'
                if group["metric"] == "operation-duration":
                    line += f'; successful {group["successful_durations"]}; p95 {group["p95"]} ns'
                service_lines.append(line)
    health_parts = []
    chart_height = 480 + 110 * len(timing_groups) + 24 * len(service_lines)
    if "health" in report:
        health_parts, chart_height = _health_block(report["health"], chart_height)
        chart_height += 20
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="{chart_height}" '
        f'viewBox="0 0 1040 {chart_height}" role="img">',
        "<title>Seven-day local telemetry observations</title>",
        f'<rect width="1040" height="{chart_height}" fill="white"/>',
        '<g font-family="sans-serif" font-size="14" fill="#222">',
        '<text x="16" y="24">168 UTC hours: observed events (blue), errors (red)</text>',
        '<text x="16" y="46">Gaps mean no observations, not uptime; population and loss unknown.</text>',
        f'<text x="16" y="66">Observation cutoff (UTC): {escape(report["generated_at"])}</text>',
        f'<text x="16" y="86">Hour starts: {escape(hours[0]["hour"])} '
        f'through {escape(hours[-1]["hour"])}</text>',
    ]
    for index, (count, error) in enumerate(zip(counts, errors)):
        x = 16 + index * 6
        if count:
            height = count / maximum * 180
            parts.append(
                f'<rect x="{x}" y="{310 - height:.2f}" width="5" '
                f'height="{height:.2f}" fill="#2463a5"/>',
            )
        else:
            parts.append(f'<path d="M{x} 306v4" stroke="#aaa"/>')
        if error:
            height = error / maximum * 180
            parts.append(
                f'<rect x="{x}" y="{310 - height:.2f}" width="5" '
                f'height="{height:.2f}" fill="#bc3030"/>',
            )
    for index in range(0, len(hours), 24):
        label = escape(hours[index]["hour"][5:16].replace("T", " ") + "Z")
        parts.append(f'<text x="{16 + index * 6}" y="330">{label}</text>')
    parts.append(
        f'<text x="1024" y="330" text-anchor="end">'
        f'{escape(hours[-1]["hour"][11:16])}Z</text>',
    )
    coverage = report["coverage"]
    parts.append(
        f'<text x="16" y="365">{sum(counts)} observed records; '
        f'{len(timing_groups)} separate timing groups below.</text>',
    )
    parts.append(
        f'<text x="16" y="389">Scan limited: {str(coverage["scan_limited"]).lower()}; '
        f'unavailable inputs: {coverage["unavailable_inputs"]}. No p99 or uptime claim.</text>',
    )
    parts.append(
        f'<text x="16" y="413">Invalid input: malformed {coverage["malformed_records"]}, '
        f'partial {coverage["partial_records"]}, oversized {coverage["oversized_records"]}, '
        f'unsupported {coverage["unsupported_records"]}; '
        f'changed inputs {coverage["changed_inputs"]}.</text>',
    )
    parts.append(
        f'<text x="16" y="437">Excluded records: future {coverage["future_records"]}, '
        f'expired {coverage["expired_records"]}, group limit {coverage["unrepresented_records"]}; '
        f'duplicate inputs {coverage["duplicate_inputs"]}.</text>',
    )
    for row, identity in enumerate(timing_groups):
        values = [
            next((
                group["p95_ns"] for group in hour["groups"]
                if group["kind"] == "performance"
                and (group["component"], group["stage"]) == identity
            ), None)
            for hour in hours
        ]
        maximum_ns = max((value for value in values if value is not None), default=0) or 1
        top = 480 + row * 110
        label = escape("/".join(identity))
        parts.append(
            f'<text x="16" y="{top}">{label}: observed-success p95; '
            f'chart ceiling {maximum_ns / 1_000_000:.3f} ms</text>',
        )
        segment = []
        for index, value in enumerate(values):
            if value is None:
                if segment:
                    parts.append(
                        f'<polyline points="{" ".join(segment)}" fill="none" stroke="#2463a5"/>',
                    )
                    segment = []
                continue
            x = 18 + index * 6
            y = top + 80 - value / maximum_ns * 60
            segment.append(f"{x},{y:.2f}")
            parts.append(f'<circle cx="{x}" cy="{y:.2f}" r="2" fill="#2463a5"/>')
        if segment:
            parts.append(
                f'<polyline points="{" ".join(segment)}" fill="none" stroke="#2463a5"/>',
            )
    for row, line in enumerate(service_lines):
        y = 480 + 110 * len(timing_groups) + row * 24
        parts.append(f'<text x="16" y="{y}">{escape(line)}</text>')
    parts.extend(health_parts)
    parts.append("</g></svg>\n")
    return "\n".join(parts)
