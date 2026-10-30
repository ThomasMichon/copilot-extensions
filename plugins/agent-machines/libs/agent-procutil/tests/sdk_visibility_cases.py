"""Literal, bounded native packets for one continuous visibility epoch."""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from sdk_desktop_observer import ObservationBlocked


NATIVE_TEST = "libs/agent-procutil/tests/test_shell_caller_owned_native.py"
NATIVE_CLASSNAME = NATIVE_TEST.removesuffix(".py").replace("/", ".")
FUNCTION = "test_sdk_scope_retires_before_observer_cleanup"
CASE = re.compile(
    re.escape(FUNCTION)
    + r"\[([01])-(completed|abrupt|caller-exit|cancel)-(path|test-venv)-(.+)\]"
)
OFFER_FUNCTION = "test_native_offer_failure_preserves_parent_handle_custody"
OFFER_MODES = (
    "no-permission", "wrong-permission", "abort", "write-abort",
    "write-eof", "wrong-ack", "missing-ack", "adopted",
)
STARTUP_FUNCTION = "test_startup_receipt_failure_releases_input_before_waiting_for_worker"
STARTUP_CASE = re.compile(re.escape(STARTUP_FUNCTION) + r"\[(eof|malformed)-(.+)\]")


def packet_kind(nodes: list[str]) -> str:
    names = [node.partition("::")[2] for node in nodes]
    if len(nodes) == 16 and all(CASE.fullmatch(name) for name in names):
        return "sdk"
    if len(nodes) == 8 and set(names) == {f"{OFFER_FUNCTION}[{mode}]" for mode in OFFER_MODES}:
        return "offer"
    if len(nodes) == 4 and all(STARTUP_CASE.fullmatch(name) for name in names):
        keys = [STARTUP_CASE.fullmatch(name).groups() for name in names]
        hosts = {host for _, host in keys}
        kinds = {host.replace("\\", "/").rsplit("/", 1)[-1].casefold() for host in hosts}
        if kinds == {"powershell.exe", "pwsh.exe"} and set(keys) == set(
            itertools.product(("eof", "malformed"), hosts),
        ):
            return "startup"
    raise ObservationBlocked("native packet differs from its literal bounded control roster")


def validate_case_packet(sdk: Path, nodes: object) -> list[str]:
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 16:
        raise ObservationBlocked("native packet must contain at most 16 literal cases")
    for node in nodes:
        if not isinstance(node, str) or "\0" in node:
            raise ObservationBlocked("native packet has an invalid literal case")
        filename, separator, name = node.partition("::")
        if not separator or Path(filename) != sdk / NATIVE_TEST or not name:
            raise ObservationBlocked("native packet escaped its source-bound function cases")
    if len(set(nodes)) != len(nodes):
        raise ObservationBlocked("native packet contains duplicate cases")
    packet_kind(nodes)
    return nodes


def validate_case_packets(sdk: Path, packets: object) -> list[list[str]]:
    if not isinstance(packets, list) or len(packets) != 4:
        raise ObservationBlocked("qualification requires SDK, offer and startup failure packets")
    result = [validate_case_packet(sdk, packet) for packet in packets]
    if [packet_kind(packet) for packet in result] != ["sdk", "sdk", "offer", "startup"]:
        raise ObservationBlocked("qualification packet kinds or order differ")
    nodes = [node for packet in result[:2] for node in packet]
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
    startup_hosts = {
        STARTUP_CASE.fullmatch(node.partition("::")[2]).group(2) for node in result[3]
    }
    if startup_hosts != hosts:
        raise ObservationBlocked("startup failure controls differ from the qualified SDK hosts")
    return result


def native_arguments(sdk: Path, output: Path, nodes: list[str], packet: int) -> list[str]:
    validate_case_packet(sdk, nodes)
    if type(packet) is not int or packet not in (0, 1, 2, 3):
        raise ObservationBlocked("native packet ordinal must be zero through three")
    if packet_kind(nodes) != ("sdk", "sdk", "offer", "startup")[packet]:
        raise ObservationBlocked("native packet ordinal differs from its literal roster")
    return [
        "-q", "-x", "--noconftest", "-p", "no:cacheprovider",
        "--rootdir", str(sdk),
        "-c", str(output / "pytest.ini"),
        "-o", "tmp_path_retention_count=4", "-o", "tmp_path_retention_policy=all",
        "--junitxml=" + str(output / f"native-{packet + 1:02}.xml"), *nodes,
    ]


def verify_packet_junit(path: Path, nodes: list[str]) -> dict:
    tree = ET.parse(path)
    cases = tree.findall(".//testcase")
    names = [(case.attrib.get("classname"), case.attrib.get("name")) for case in cases]
    expected = {node.partition("::")[2] for node in nodes}
    if (
        not 1 <= len(nodes) <= 16 or len(expected) != len(nodes) or len(cases) != len(nodes)
        or len(set(names)) != len(nodes) or {name for _, name in names} != expected
        or any(classname != NATIVE_CLASSNAME for classname, _ in names)
        or tree.findall(".//failure") or tree.findall(".//error") or tree.findall(".//skipped")
    ):
        raise ObservationBlocked("actual native JUnit differs from its exact literal packet")
    return {"case_count": len(nodes), "cases": names, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_native_junit(output: Path, sdk: Path, packets: list[list[str]]) -> dict:
    validate_case_packets(sdk, packets)
    results = [
        verify_packet_junit(output / f"native-{index + 1:02}.xml", nodes)
        for index, nodes in enumerate(packets)
    ]
    return {"case_count": sum(len(nodes) for nodes in packets), "packets": results}
