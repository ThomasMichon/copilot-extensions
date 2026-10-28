"""Literal, bounded native packets for one continuous visibility epoch."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from sdk_desktop_observer import ObservationBlocked


NATIVE_TEST = "libs/agent-procutil/tests/test_shell_caller_owned_native.py"
FUNCTION = "test_sdk_scope_retires_before_observer_cleanup"
CASE = re.compile(
    re.escape(FUNCTION)
    + r"\[([01])-(completed|abrupt|caller-exit|cancel)-(path|test-venv)-(.+)\]"
)


def validate_case_packet(sdk: Path, nodes: object) -> list[str]:
    if not isinstance(nodes, list) or len(nodes) != 16:
        raise ObservationBlocked("native packet must contain exactly 16 literal cases")
    for node in nodes:
        if not isinstance(node, str) or "\0" in node:
            raise ObservationBlocked("native packet has an invalid literal case")
        filename, separator, name = node.partition("::")
        if not separator or Path(filename) != sdk / NATIVE_TEST or CASE.fullmatch(name) is None:
            raise ObservationBlocked("native packet escaped its source-bound function cases")
    if len(set(nodes)) != 16:
        raise ObservationBlocked("native packet contains duplicate cases")
    return nodes


def validate_case_packets(sdk: Path, packets: object) -> list[list[str]]:
    if not isinstance(packets, list) or len(packets) != 2:
        raise ObservationBlocked("qualification requires two disjoint native packets")
    result = [validate_case_packet(sdk, packet) for packet in packets]
    nodes = [node for packet in result for node in packet]
    keys = [CASE.fullmatch(node.partition("::")[2]).groups() for node in nodes]
    hosts = {key[3] for key in keys}
    kinds = {host.replace("\\", "/").rsplit("/", 1)[-1].casefold() for host in hosts}
    expected = set(itertools.product(
        ("0", "1"), ("completed", "abrupt", "caller-exit", "cancel"),
        ("path", "test-venv"), hosts,
    ))
    if (len(hosts) != 2 or kinds != {"powershell.exe", "pwsh.exe"}
            or len(set(nodes)) != 32 or set(keys) != expected):
        raise ObservationBlocked("native packets differ from the complete 32-case Windows matrix")
    return result


def native_arguments(sdk: Path, output: Path, nodes: list[str], packet: int) -> list[str]:
    validate_case_packet(sdk, nodes)
    if type(packet) is not int or packet not in (0, 1):
        raise ObservationBlocked("native packet ordinal must be zero or one")
    return [
        "-q", "-x", "--noconftest", "-p", "no:cacheprovider",
        "--rootdir", str(sdk),
        "-c", str(output / "pytest.ini"),
        "--junitxml=" + str(output / f"native-{packet + 1:02}.xml"), *nodes,
    ]


def verify_packet_junit(path: Path, nodes: list[str]) -> dict:
    tree = ET.parse(path)
    cases = tree.findall(".//testcase")
    names = [(case.attrib["classname"], case.attrib["name"]) for case in cases]
    expected = {node.partition("::")[2] for node in nodes}
    if (
        len(nodes) != 16 or len(expected) != 16 or len(cases) != 16
        or len(set(names)) != 16 or {name for _, name in names} != expected
        or any(not classname.endswith("test_shell_caller_owned_native") for classname, _ in names)
        or tree.findall(".//failure") or tree.findall(".//error") or tree.findall(".//skipped")
    ):
        raise ObservationBlocked("actual native JUnit differs from its exact 16-case packet")
    return {"case_count": 16, "cases": names, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_native_junit(output: Path, sdk: Path, packets: list[list[str]]) -> dict:
    validate_case_packets(sdk, packets)
    results = [
        verify_packet_junit(output / f"native-{index + 1:02}.xml", nodes)
        for index, nodes in enumerate(packets)
    ]
    return {"case_count": 32, "packets": results}
