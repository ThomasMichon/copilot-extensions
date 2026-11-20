"""Actor-free evidence controls; no test reads the host's native identities."""

import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "libs/update-driver/src/update_driver/boot.py"
spec = importlib.util.spec_from_file_location("unit_boot_domain", SOURCE)
assert spec and spec.loader
boot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = boot
spec.loader.exec_module(boot)

BOOT = "windows:11111111-1111-4111-8111-111111111111"
NEXT_BOOT = "windows:22222222-2222-4222-8222-222222222222"
DOMAIN = bytes(range(16))
NEXT_DOMAIN = bytes(range(1, 17))


class NativeFunction:
    def __init__(self, function: Callable[..., Any]) -> None:
        self.function = function

    def __call__(self, *args: Any) -> Any:
        return self.function(*args)


class NativeValue:
    def __init__(self, value: int = 0) -> None:
        self.value = value if value >= 0 else (1 << 64) + value


class NativeBuffer:
    def __init__(self) -> None:
        self.data = b"\0" * 16

    def __bytes__(self) -> bytes:
        return self.data


class NativeByte:
    def __mul__(self, length: int) -> type[NativeBuffer]:
        assert length == 16
        return NativeBuffer


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls: list[str] = []
    counters = [DOMAIN, DOMAIN]
    boots = [BOOT, BOOT]
    behavior = {"open": 123, "read": True, "size": 16, "close": True}

    def create(
        path: Any, access: Any, sharing: Any, security: Any,
        disposition: Any, flags: Any, template: Any,
    ) -> Any:
        assert path == r"\\.\VmGenerationCounter"
        assert access == 0xC0000000 and sharing == 3
        assert security is None and disposition == 3 and flags == 0 and template is None
        calls.append("open")
        return behavior["open"]

    def read(
        handle: Any, control: Any, data: Any, size: Any, output: Any, capacity: Any,
        returned: Any, overlapped: Any,
    ) -> Any:
        assert handle == 123 and control == 0x32C004
        assert data is None and size == 0 and overlapped is None
        assert capacity == 16
        calls.append("read")
        output.data = counters.pop(0)
        returned._obj.value = behavior["size"]
        return behavior["read"]

    def close(handle: Any) -> Any:
        assert handle == 123
        calls.append("close")
        return behavior["close"]

    library = SimpleNamespace(
        CreateFileW=NativeFunction(create), DeviceIoControl=NativeFunction(read),
        CloseHandle=NativeFunction(close),
    )

    def load(name: str, **kwargs: Any) -> SimpleNamespace:
        assert name == "kernel32" and kwargs == {"use_last_error": True}
        return library

    def kernel_boot() -> str:
        calls.append("boot")
        return boots.pop(0)

    monkeypatch.setattr(boot, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(boot, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(boot, "kernel_boot_identity", kernel_boot)
    native_api = ModuleType("ctypes")
    for name in ("c_uint32", "c_int", "c_void_p"):
        setattr(native_api, name, NativeValue)
    setattr(native_api, "c_ubyte", NativeByte())
    setattr(native_api, "c_wchar_p", object())
    setattr(native_api, "POINTER", lambda kind: ("pointer", kind))
    setattr(native_api, "byref", lambda value: SimpleNamespace(_obj=value))
    setattr(native_api, "WinDLL", load)
    setattr(native_api, "get_last_error", lambda: 5)
    monkeypatch.setitem(sys.modules, "ctypes", native_api)
    return SimpleNamespace(
        calls=calls, counters=counters, boots=boots, behavior=behavior, library=library,
    )


def test_native_sample_and_serialized_record_roundtrip(native: SimpleNamespace) -> None:
    evidence = boot.native_execution_domain_boot_evidence()
    assert asdict(evidence) == {
        "version": 1, "platform": "windows", "source": "windows-vm-generation-counter",
        "domain": DOMAIN.hex(), "boot": BOOT,
    }
    record = json.loads(json.dumps(asdict(evidence)))
    assert boot.parse_execution_domain_boot_evidence(record) == evidence
    assert native.calls == ["boot", "open", "read", "close", "open", "read", "close", "boot"]


@pytest.mark.parametrize("changed", ["boot", "domain", "both"])
def test_incoherent_native_sample_refuses(native: SimpleNamespace, changed: str) -> None:
    if changed in ("boot", "both"):
        native.boots[1] = NEXT_BOOT
    if changed in ("domain", "both"):
        native.counters[1] = NEXT_DOMAIN
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="incoherent"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls.count("close") == 2


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("version", 1.0),
    ("platform", "linux"), ("source", "machine-id"), ("source", "firmware-uuid"),
    ("domain", "00" * 16), ("domain", "ff" * 16), ("domain", DOMAIN.hex().upper()),
    ("domain", DOMAIN.hex() + "00"), ("domain", "not-a-native-id"),
    ("domain", b"0123456789abcdef"), ("boot", NEXT_BOOT.replace("windows:", "linux:")),
    ("boot", "windows:00000000-0000-0000-0000-000000000000"), ("boot", None),
])
def test_record_rejects_unsupported_or_malformed_evidence(
    native: SimpleNamespace, field: str, value: Any,
) -> None:
    record = asdict(boot.native_execution_domain_boot_evidence())
    record[field] = value
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="invalid"):
        boot.parse_execution_domain_boot_evidence(record)


@pytest.mark.parametrize("change", ["missing", "extra", "array", "none"])
def test_record_shape_is_strict(native: SimpleNamespace, change: str) -> None:
    record: Any = asdict(boot.native_execution_domain_boot_evidence())
    if change == "missing":
        del record["domain"]
    elif change == "extra":
        record["qualified"] = True
    elif change == "array":
        record = list(record.values())
    else:
        record = None
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="invalid"):
        boot.parse_execution_domain_boot_evidence(record)


@pytest.mark.parametrize("name,platform", [
    ("posix", "linux"), ("posix", "darwin"), ("posix", "freebsd"),
    ("posix", "win32"), ("nt", "linux"),
])
def test_unsupported_venue_does_not_probe_or_fall_back(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, name: str, platform: str,
) -> None:
    monkeypatch.setattr(boot, "os", SimpleNamespace(name=name))
    monkeypatch.setattr(boot, "sys", SimpleNamespace(platform=platform))
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="unsupported"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == []


@pytest.mark.parametrize("handle", [None, 0, (1 << 64) - 1])
def test_unavailable_device_has_no_fallback_or_invalid_handle_close(
    native: SimpleNamespace, handle: Any,
) -> None:
    native.behavior["open"] = handle
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="unavailable"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == ["boot", "open"]


@pytest.mark.parametrize("failure", ["read", "short", "long", "zero", "ones", "close"])
def test_bad_native_result_refuses_and_closes_its_handle(
    native: SimpleNamespace, failure: str,
) -> None:
    if failure == "read":
        native.behavior["read"] = False
    elif failure in ("short", "long"):
        native.behavior["size"] = 15 if failure == "short" else 17
    elif failure in ("zero", "ones"):
        native.counters[0] = b"\0" * 16 if failure == "zero" else b"\xff" * 16
    else:
        native.behavior["close"] = False
    with pytest.raises(boot.ExecutionDomainEvidenceError):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == ["boot", "open", "read", "close"]


def test_native_reader_exception_still_closes_handle(native: SimpleNamespace) -> None:
    def fail(*args: Any) -> Any:
        raise OSError("synthetic native reader failure")

    native.library.DeviceIoControl.function = fail
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="unavailable"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == ["boot", "open", "close"]


def test_second_native_read_failure_releases_both_handles(native: SimpleNamespace) -> None:
    read = native.library.DeviceIoControl.function

    def fail_second(*args: Any) -> Any:
        if native.calls.count("read") == 1:
            raise OSError("synthetic second-read failure")
        return read(*args)

    native.library.DeviceIoControl.function = fail_second
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="unavailable"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == ["boot", "open", "read", "close", "open", "close"]


@pytest.mark.parametrize("value", ["not-a-boot", "linux:" + BOOT.split(":")[1], None])
def test_bad_boot_evidence_refuses_without_returning_a_domain(
    native: SimpleNamespace, value: Any,
) -> None:
    native.boots[0] = value
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="invalid"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == ["boot"]


def test_native_boot_failure_has_no_domain_fallback(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> str:
        raise OSError("synthetic boot failure")

    monkeypatch.setattr(boot, "kernel_boot_identity", unavailable)
    with pytest.raises(boot.ExecutionDomainEvidenceError, match="unavailable"):
        boot.native_execution_domain_boot_evidence()
    assert native.calls == []


@pytest.mark.parametrize("platform", ["linux", "windows", "darwin"])
def test_existing_boot_identity_contract_does_not_require_domain_evidence(platform: str) -> None:
    assert boot.valid_boot_identity(BOOT.replace("windows:", platform + ":"))
    assert not boot.valid_boot_identity(platform + ":00000000-0000-0000-0000-000000000000")
