"""Experimental reference implementation; see the parent README."""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes as wt
from contextlib import contextmanager
import hashlib
import hmac
import http.client
import io
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import random
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if sys.version_info < (3, 9):
    raise SystemExit("Existing Python 3.9+ required")
sys.path.insert(0, str(HERE / "libs"))
from agent_procutil import (no_window_kwargs, spawn_daemon,
                            spawn_sync_in_kill_on_close_job)

PEERS = {"node-a": 23001, "node-b": 23002, "node-c": 23003,
         "node-d": 23004, "node-e": 23005}
PINS = {
    "amd64": {
        "ssh": ("OpenSSH-Win64.zip",
                "23f50f3458c4c5d0b12217c6a5ddfde0137210a30fa870e98b29827f7b43aba5")},
    "arm64": {
        "ssh": ("OpenSSH-ARM64.zip",
                "698c6aec31c1dd0fb996206e8741f4531a97355686b5431ef347d531b07fcd42")}}
URLS = {
    "ssh": "https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.0.0.0p2-Preview/"}
CONFIG_KEYS = {"schema", "machine", "user", "autostart", "target_id", "subscription",
               "resource_group", "bastion", "resource", "backend"}
BACKEND = "openssh-reverse"
PROVIDER = "gateway-relay"
GATEWAY_HTTP_LOCAL_PORT = 18787
BANNER_PROBE_INTERVAL = 30
AZURE_ACCOUNT_TIMEOUT = 60
STARTUP_TIMEOUT = 120
LOGGER = logging.getLogger("gateway-ssh-mesh")
ERROR_STAGE = "initialize"
GENERATED_KEY_FILES = {"host_ed25519", "host_ed25519.pub",
                       "client_ed25519", "client_ed25519.pub"}


class MeshError(RuntimeError):
    pass


def stage(code):
    global ERROR_STAGE
    if not re.fullmatch(r"[a-z0-9-]{1,64}", code):
        raise MeshError("Invalid internal diagnostic stage")
    ERROR_STAGE = code


def sanitized_error(error, action):
    result = {"ready": False, "action": action, "stage": ERROR_STAGE,
              "type": type(error).__name__,
              "error": str(error) if isinstance(error, MeshError) else "operation-refused-or-unavailable"}
    for attribute in ("errno", "winerror"):
        value = getattr(error, attribute, None)
        if type(value) is int:
            result[attribute] = value
    return result


def dll(name):
    return ctypes.WinDLL(name, use_last_error=True)


def api(lib, name, args, result=wt.BOOL):
    function = getattr(lib, name)
    function.argtypes, function.restype = args, result
    return function


def check(ok):
    if not ok:
        raise MeshError("Win32 operation failed (code %d)" % ctypes.get_last_error())
    return ok


def sid_string(pointer):
    out = wt.LPWSTR()
    adv, kernel = dll("advapi32"), dll("kernel32")
    check(api(adv, "ConvertSidToStringSidW",
              [wt.LPVOID, ctypes.POINTER(wt.LPWSTR)])(pointer, ctypes.byref(out)))
    try:
        return out.value
    finally:
        api(kernel, "LocalFree", [wt.LPVOID], wt.LPVOID)(out)


def process_birth(pid):
    kernel = dll("kernel32")
    handle = api(kernel, "OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD],
                 wt.HANDLE)(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wt.FILETIME() for _ in range(4)]
        if not api(kernel, "GetProcessTimes",
                   [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4)(
                       handle, *(ctypes.byref(t) for t in times)):
            return None
        if times[1].dwHighDateTime or times[1].dwLowDateTime:
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        api(kernel, "CloseHandle", [wt.HANDLE])(handle)


def identity():
    if os.name != "nt":
        raise MeshError("This lifecycle is Windows-only")
    kernel, adv = dll("kernel32"), dll("advapi32")
    handle = wt.HANDLE()
    current = api(kernel, "GetCurrentProcess", [], wt.HANDLE)()
    check(api(adv, "OpenProcessToken",
              [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)])(
                  current, 8, ctypes.byref(handle)))
    try:
        get = api(adv, "GetTokenInformation",
                  [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD,
                   ctypes.POINTER(wt.DWORD)])
        size = wt.DWORD()
        get(handle, 1, None, 0, ctypes.byref(size))
        data = ctypes.create_string_buffer(size.value)
        check(get(handle, 1, data, size, ctypes.byref(size)))
        pointer = ctypes.cast(data, ctypes.POINTER(wt.LPVOID))[0]
        sid = sid_string(pointer)
        username, domain = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(512)
        n, d, use = wt.DWORD(512), wt.DWORD(512), wt.DWORD()
        check(api(adv, "LookupAccountSidW",
                  [wt.LPCWSTR, wt.LPVOID, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
                   wt.LPWSTR, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)])(
                       None, pointer, username, ctypes.byref(n), domain,
                       ctypes.byref(d), ctypes.byref(use)))
        elevated = wt.DWORD()
        check(get(handle, 20, ctypes.byref(elevated), ctypes.sizeof(elevated),
                  ctypes.byref(size)))
        session = wt.DWORD()
        check(api(kernel, "ProcessIdToSessionId",
                  [wt.DWORD, ctypes.POINTER(wt.DWORD)])(
                      os.getpid(), ctypes.byref(session)))
        return {"sid": sid, "user": username.value, "domain": domain.value,
                "session": session.value, "admin": bool(elevated.value)}
    finally:
        api(kernel, "CloseHandle", [wt.HANDLE])(handle)


def require_user(who=None):
    who = identity() if who is None else who
    if who["admin"] or who["session"] == 0 or who["sid"] == "S-1-5-18":
        raise MeshError("Requires a non-elevated interactive user; SYSTEM/session 0 refused")
    return who


def no_reparse(path):
    if path.exists() and (path.is_symlink() or
                         getattr(path.stat(), "st_file_attributes", 0) & 0x400):
        raise MeshError("Reparse-point runtime path refused")


def private_directory(path, sid):
    pass
    path = Path(path)
    for ancestor in (path,) + tuple(path.parents):
        no_reparse(ancestor)
    adv, kernel = dll("advapi32"), dll("kernel32")
    descriptor = wt.LPVOID()
    sddl = "O:%sD:P(A;OICI;FA;;;%s)" % (sid, sid)
    check(api(adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
              [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(wt.LPVOID), wt.LPVOID])(
                  sddl, 1, ctypes.byref(descriptor), None))

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("descriptor", wt.LPVOID), ("inherit", wt.BOOL)]

    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        created = api(kernel, "CreateDirectoryW",
                      [wt.LPCWSTR, ctypes.POINTER(SecurityAttributes)])(
                          str(path), ctypes.byref(attributes))
        if not created and ctypes.get_last_error() != 183:
            check(False)
    finally:
        api(kernel, "LocalFree", [wt.LPVOID], wt.LPVOID)(descriptor)
    if not path.is_dir():
        raise MeshError("Runtime root is not a directory")
    verify_private(path, sid, protected=True)


def verify_private(path, sid, protected=False):
    no_reparse(path)
    adv, kernel = dll("advapi32"), dll("kernel32")
    owner, acl, descriptor = wt.LPVOID(), wt.LPVOID(), wt.LPVOID()
    result = api(adv, "GetNamedSecurityInfoW",
                 [wt.LPCWSTR, ctypes.c_int, wt.DWORD, ctypes.POINTER(wt.LPVOID),
                  wt.LPVOID, ctypes.POINTER(wt.LPVOID), wt.LPVOID,
                  ctypes.POINTER(wt.LPVOID)], wt.DWORD)(
                      str(path), 1, 5, ctypes.byref(owner), None,
                      ctypes.byref(acl), None, ctypes.byref(descriptor))
    if result:
        raise MeshError("Cannot inspect private runtime ACL")
    try:
        if not acl or sid_string(owner) != sid:
            raise MeshError("Runtime is not privately owned by this user")
        flags, revision = wt.WORD(), wt.DWORD()
        check(api(adv, "GetSecurityDescriptorControl",
                  [wt.LPVOID, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.DWORD)])(
                      descriptor, ctypes.byref(flags), ctypes.byref(revision)))
        if protected and not flags.value & 0x1000:
            raise MeshError("Runtime directory DACL must be protected from inherited grants")

        count = ctypes.c_ushort.from_address(acl.value + 4).value
        if count == 0:
            raise MeshError("Runtime ACL has no owner access")
        for index in range(count):
            ace = wt.LPVOID()
            check(api(adv, "GetAce",
                      [wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.LPVOID)])(
                          acl, index, ctypes.byref(ace)))
            kind = ctypes.c_ubyte.from_address(ace.value).value
            if kind != 0 or sid_string(ace.value + 8) != sid:
                raise MeshError("Runtime ACL grants access beyond the current user")
    finally:
        api(kernel, "LocalFree", [wt.LPVOID], wt.LPVOID)(descriptor)


def verify_runtime(root, sid):
    verify_private(root, sid, protected=True)
    for directory in ("config", "state", "logs", "bin", "versions"):
        path = root / directory
        if path.exists():
            verify_private(path, sid, protected=True)
            for child in path.rglob("*"):
                no_reparse(child)
                if child.is_file() or child.is_dir():
                    verify_private(child, sid)


def normalize_generated_key(path, sid):
    pass
    path = Path(path)
    if path.name not in GENERATED_KEY_FILES or path.parent.name != "config":
        raise MeshError("ACL normalization is restricted to the four generated key files")
    verify_private(path.parent, sid, protected=True)
    no_reparse(path)
    kernel, adv = dll("kernel32"), dll("advapi32")
    handle = api(kernel, "CreateFileW",
                 [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD,
                  wt.DWORD, wt.HANDLE], wt.HANDLE)(
                      str(path), 0x60080, 1, None, 3, 0x200080, None)
    if handle == ctypes.c_void_p(-1).value:
        check(False)

    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wt.DWORD), ("creation", wt.FILETIME),
                    ("access", wt.FILETIME), ("write", wt.FILETIME),
                    ("volume", wt.DWORD), ("size_high", wt.DWORD),
                    ("size_low", wt.DWORD), ("links", wt.DWORD),
                    ("index_high", wt.DWORD), ("index_low", wt.DWORD)]

    old_descriptor, new_descriptor = wt.LPVOID(), wt.LPVOID()
    try:
        information = FileInformation()
        check(api(kernel, "GetFileInformationByHandle",
                  [wt.HANDLE, ctypes.POINTER(FileInformation)])(
                      handle, ctypes.byref(information)))
        if information.attributes & (0x10 | 0x400) or information.links != 1:
            raise MeshError("Generated key must be a regular, non-linked file")
        owner, acl = wt.LPVOID(), wt.LPVOID()
        result = api(adv, "GetSecurityInfo",
                     [wt.HANDLE, ctypes.c_int, wt.DWORD, ctypes.POINTER(wt.LPVOID),
                      wt.LPVOID, ctypes.POINTER(wt.LPVOID), wt.LPVOID,
                      ctypes.POINTER(wt.LPVOID)], wt.DWORD)(
                          handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(acl),
                          None, ctypes.byref(old_descriptor))
        if result:
            raise MeshError("Generated-key owner inspection failed (code %d)" % result)
        if not acl or sid_string(owner) != sid:
            raise MeshError("Generated-key repair refuses foreign ownership")
        count = ctypes.c_ushort.from_address(acl.value + 4).value
        allowed = {sid, "S-1-5-18", "S-1-5-32-544"}
        if path.suffix == ".pub":
            allowed.add("S-1-1-0")
        seen = set()
        if not 1 <= count <= len(allowed):
            raise MeshError("Generated-key repair refuses an unexpected ACL")
        for index in range(count):
            ace = wt.LPVOID()
            check(api(adv, "GetAce", [wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.LPVOID)])(
                acl, index, ctypes.byref(ace)))
            if ctypes.c_ubyte.from_address(ace.value).value != 0:
                raise MeshError("Generated-key repair refuses nonstandard ACEs")
            trustee = sid_string(ace.value + 8)
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            if (trustee not in allowed or trustee in seen or
                    (trustee == "S-1-1-0" and mask & ~0x1200a9)):
                raise MeshError("Generated-key repair refuses foreign or overly broad grants (mask 0x%x)" % mask)
            seen.add(trustee)
        if sid not in seen or seen not in ({sid}, allowed, allowed - {"S-1-1-0"}):
            raise MeshError("Generated-key repair refuses an unknown native ACL shape")
        check(api(adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                  [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(wt.LPVOID), wt.LPVOID])(
                      "D:P(A;;FA;;;%s)" % sid, 1, ctypes.byref(new_descriptor), None))
        present, defaulted, new_acl = wt.BOOL(), wt.BOOL(), wt.LPVOID()
        check(api(adv, "GetSecurityDescriptorDacl",
                  [wt.LPVOID, ctypes.POINTER(wt.BOOL), ctypes.POINTER(wt.LPVOID),
                   ctypes.POINTER(wt.BOOL)])(
                       new_descriptor, ctypes.byref(present), ctypes.byref(new_acl),
                       ctypes.byref(defaulted)))
        result = api(adv, "SetSecurityInfo",
                     [wt.HANDLE, ctypes.c_int, wt.DWORD, wt.LPVOID, wt.LPVOID,
                      wt.LPVOID, wt.LPVOID], wt.DWORD)(
                          handle, 1, 0x80000004, None, None, new_acl, None)
        if result:
            raise MeshError("Generated-key ACL normalization failed (code %d)" % result)
        verify_private(path, sid, protected=True)
    finally:
        for descriptor in (old_descriptor, new_descriptor):
            if descriptor:
                api(kernel, "LocalFree", [wt.LPVOID], wt.LPVOID)(descriptor)
        api(kernel, "CloseHandle", [wt.HANDLE])(handle)


def repair_generated_key_acls(root, sid):
    stage("repair-generated-key-acls")
    verify_private(root, sid, protected=True)
    verify_private(root / "config", sid, protected=True)
    for name in sorted(GENERATED_KEY_FILES):
        path = root / "config" / name
        if path.exists():
            normalize_generated_key(path, sid)
    verify_runtime(root, sid)


def canonical_root(path):
    path = path.expanduser().absolute()
    for ancestor in (path,) + tuple(path.parents):
        no_reparse(ancestor)
    return path.resolve()


@contextmanager
def lifecycle_lock(root):
    kernel = dll("kernel32")
    name = "Local\\PrivateGatewaySshMesh-" + hashlib.sha256(
        str(root).casefold().encode("utf-8")).hexdigest()
    mutex = api(kernel, "CreateMutexW", [wt.LPVOID, wt.BOOL, wt.LPCWSTR],
                wt.HANDLE)(None, False, name)
    check(mutex)
    acquired = False
    try:
        result = api(kernel, "WaitForSingleObject", [wt.HANDLE, wt.DWORD],
                     wt.DWORD)(mutex, 0)
        if result not in (0, 0x80):
            raise MeshError("Another lifecycle operation owns this runtime")
        acquired = True
        yield
    finally:
        if acquired:
            api(kernel, "ReleaseMutex", [wt.HANDLE])(mutex)
        api(kernel, "CloseHandle", [wt.HANDLE])(mutex)


def atomic(path, value):
    path = Path(path)
    no_reparse(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".new")
    try:
        with temporary.open("xb") as output:
            output.write(value if isinstance(value, bytes) else value.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json(path, value):
    atomic(path, json.dumps(value, indent=2) + "\n")


def json_object(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MeshError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise MeshError("Malformed JSON") from None
    if not isinstance(value, dict):
        raise MeshError("Expected JSON object")
    return value


def read_json(path):
    no_reparse(Path(path))
    return json_object(Path(path).read_text(encoding="utf-8-sig"))


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        raise MeshError("Missing or unknown fields")


def machine_name(value):
    if not isinstance(value, str):
        raise MeshError("Invalid machine")
    value = value.removeprefix("operator-")
    if value not in PEERS:
        raise MeshError("Machine is outside the five-peer pilot")
    return value


def validate_config(value):
    fields(value, CONFIG_KEYS | (
        {"gateway_http"} if isinstance(value, dict) and "gateway_http" in value else set()))
    if "gateway_http" in value and type(value["gateway_http"]) is not bool:
        raise MeshError("gateway_http must be an explicit boolean")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise MeshError("Unsupported config schema")
    value = dict(value)
    if value["backend"] != BACKEND:
        raise MeshError("Only the explicitly selected openssh-reverse backend is supported")
    value["machine"] = machine_name(value["machine"])
    if value["user"] != "operator" or type(value["autostart"]) is not bool:
        raise MeshError("Pilot requires operator and an explicit boolean autostart")
    for key in ("target_id", "subscription"):
        if not isinstance(value[key], str) or not re.fullmatch(
                r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", value[key]):
            raise MeshError("Invalid Azure identifier")
    for key in ("resource_group", "bastion"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[\w.-]{1,100}", value[key]):
            raise MeshError("Invalid Azure resource name")
    expected = "/subscriptions/%s/resourceGroups/%s/providers/Microsoft.Compute/virtualMachines/" % (
        value["subscription"], value["resource_group"])
    if not isinstance(value["resource"], str) or not value["resource"].startswith(expected):
        raise MeshError("Azure resource does not match subscription/resource group")
    if not re.fullmatch(r"[\w.-]{1,100}", value["resource"][len(expected):]):
        raise MeshError("Invalid virtual machine name")
    return value


def validate_enrollment(value, config):
    fields(value, {"schema", "backend", "machine", "gateway_user", "host_key",
                   "relay_port", "server_port", "peers"})
    if type(value["schema"]) is not int or value["schema"] != 1 or value["backend"] != BACKEND:
        raise MeshError("Enrollment requires schema 1 and openssh-reverse backend")
    if value["gateway_user"] != "operator":
        raise MeshError("Unexpected Gateway SSH user")
    machine = machine_name(value["machine"])
    if machine != config["machine"]:
        raise MeshError("Enrollment is for a different machine")
    if type(value["relay_port"]) is not int or value["relay_port"] != PEERS[machine]:
        raise MeshError("Enrollment relay port disagrees with fixed pilot map")
    if type(value["server_port"]) is not int or value["server_port"] != 22:
        raise MeshError("Gateway SSH server port must be 22")
    host_key = public_key(value["host_key"])
    if not isinstance(value["peers"], list) or len(value["peers"]) != 5:
        raise MeshError("Enrollment must list exactly five registered peers")
    seen = set()
    for peer in value["peers"]:
        if not isinstance(peer, dict):
            raise MeshError("Invalid peer")
        fields(peer, {"machine", "relay_port"})
        name = machine_name(peer["machine"])
        if name in seen or type(peer["relay_port"]) is not int or peer["relay_port"] != PEERS[name]:
            raise MeshError("Duplicate peer or incorrect relay port")
        seen.add(name)
    result = dict(value)
    result["machine"] = machine
    result["host_key"] = host_key
    result["peers"] = [{"machine": name, "relay_port": port} for name, port in PEERS.items()]
    return result


def import_client(root, value):
    config = validate_config(read_json(root / "config" / "windows.yaml"))
    value = validate_enrollment(value, config)
    atomic(root / "config" / "gateway_known_hosts", "gateway-relay " + value["host_key"] + "\n")
    write_json(root / "config" / "enrollment.json", value)


def load_enrollment(root):
    path = root / "config" / "enrollment.json"
    if not path.exists():
        return None
    value = validate_enrollment(read_json(path),
                                validate_config(read_json(root / "config" / "windows.yaml")))
    pin = root / "config" / "gateway_known_hosts"
    if not pin.exists() or pin.read_text(encoding="utf-8").strip() != "gateway-relay " + value["host_key"]:
        raise MeshError("Gateway enrollment disagrees with the explicit host-key pin")
    return value


def local_forward_ports(config=None):
    return list(range(24001, 24006)) + (
        [GATEWAY_HTTP_LOCAL_PORT] if config and config.get("gateway_http") else [])


def relay_command(binary, root, enrollment, config=None):
    command = [str(binary), "-F", "NUL", "-N", "-T", "-p", "18444",
               "-i", str(root / "config" / "client_ed25519")]
    options = [
        "BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=" + str(root / "config" / "gateway_known_hosts"),
        "GlobalKnownHostsFile=NUL", "HostKeyAlias=gateway-relay",
        "ExitOnForwardFailure=yes", "ServerAliveInterval=15", "ServerAliveCountMax=3",
        "TCPKeepAlive=no", "ConnectTimeout=10", "PreferredAuthentications=publickey",
        "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "IdentityAgent=none"]
    for option in options:
        command.extend(["-o", option])
    command.extend(["-R", "127.0.0.1:%d:127.0.0.1:2223" % enrollment["relay_port"]])
    for index, port in enumerate(PEERS.values()):
        command.extend(["-L", "127.0.0.1:%d:127.0.0.1:%d" % (24001 + index, port)])
    if config and config.get("gateway_http"):
        command.extend(["-L", "127.0.0.1:%d:127.0.0.1:8787" % GATEWAY_HTTP_LOCAL_PORT])
    command.append(enrollment["gateway_user"] + "@127.0.0.1")
    return command


def child_environment(kind):
    env = dict(os.environ)
    for key in ("AUTH", "SSH_TEST_ENVIRONMENT", "SSH_ASKPASS", "SSH_AUTH_SOCK"):
        env.pop(key, None)
    env.update(AZURE_EXTENSION_USE_DYNAMIC_INSTALL="no",
               AZURE_CORE_COLLECT_TELEMETRY="no", AZURE_CORE_NO_COLOR="true")
    if kind == "ssh":
        env["SSH_TEST_ENVIRONMENT"] = "1"
    if kind == "azure":
        env.update(AZ_INSTALLER="MSI", AZURE_AUTO_UPGRADE_ENABLE="no",
                   AZURE_AUTO_UPGRADE_PROMPT="no")
    return env


def bounded(argv, cwd, env=None, timeout=30):
    process, job = spawn_sync_in_kill_on_close_job(
        list(map(str, argv)), require_job=True, allow_breakaway=False,
        cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs())
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        if process.returncode:
            raise MeshError("Bounded validation failed (exit %d)" % process.returncode)
        return stdout + stderr
    except subprocess.TimeoutExpired:
        raise MeshError("Bounded validation timed out") from None
    finally:
        if job is not None:
            job.terminate_and_wait(10)
        elif process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def extract_zip(payload, expected_digest, destination):
    if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_digest):
        raise MeshError("Release digest mismatch; archive not extracted")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if (len(members) > 4096 or
                sum(member.file_size for member in members) > 300 * 1024 * 1024):
            raise MeshError("Release archive exceeds size bound")
        for member in members:
            name = member.filename.replace("\\", "/")
            components = name.rstrip("/").split("/")
            reserved = {"con", "prn", "aux", "nul"} | {
                prefix + str(number) for prefix in ("com", "lpt") for number in range(1, 10)}
            if (name.startswith("/") or ":" in name or
                    any(part in ("..", ".", "") or part != part.rstrip(". ") or
                        part.split(".")[0].casefold() in reserved for part in components) or
                    (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise MeshError("Unsafe release archive path")
            target = destination.joinpath(*components)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(archive.read(member))


def architecture():
    arch = platform.machine().lower()
    if os.name == "nt":
        class SystemInfo(ctypes.Structure):
            _fields_ = [("architecture", wt.WORD), ("reserved", wt.WORD),
                        ("page_size", wt.DWORD), ("minimum", wt.LPVOID),
                        ("maximum", wt.LPVOID), ("mask", ctypes.c_size_t),
                        ("processors", wt.DWORD), ("type", wt.DWORD),
                        ("granularity", wt.DWORD), ("level", wt.WORD),
                        ("revision", wt.WORD)]
        info = SystemInfo()
        api(dll("kernel32"), "GetNativeSystemInfo", [ctypes.POINTER(SystemInfo)], None)(
            ctypes.byref(info))
        arch = {9: "amd64", 12: "arm64"}.get(info.architecture, "unsupported")
    if arch in ("amd64", "x86_64"):
        return "amd64"
    if arch in ("arm64", "aarch64"):
        return "arm64"
    raise MeshError("Only native x64/ARM64 hosts are supported")


def download(kind, arch, destination):
    stage("download-" + kind)
    filename, digest = PINS[arch][kind]
    request = urllib.request.Request(URLS[kind] + filename,
                                    headers={"User-Agent": "private-gateway-ssh-mesh/1"})
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read(100 * 1024 * 1024 + 1)
    if len(payload) > 100 * 1024 * 1024:
        raise MeshError("Release download exceeds size bound")
    extract_zip(payload, digest, destination)


def find_binary(slot, name):
    candidates = list((slot / "tools").rglob(name))
    if len(candidates) != 1:
        raise MeshError("Pinned binary missing or ambiguous")
    return candidates[0]


def public_key(text):
    if not isinstance(text, str):
        raise MeshError("Expected ed25519 public key")
    parts = text.strip().split()
    if len(parts) not in (2, 3) or parts[0] != "ssh-ed25519":
        raise MeshError("Only ed25519 public keys are accepted")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise MeshError("Invalid public key encoding") from None
    if (len(blob) != 51 or blob[:4] != struct.pack(">I", 11) or
            blob[4:15] != b"ssh-ed25519" or blob[15:19] != struct.pack(">I", 32)):
        raise MeshError("Invalid ed25519 public key wire format")
    return "ssh-ed25519 " + parts[1]


def ssh_path(path):
    return '"' + str(path).replace("\\", "/").replace('"', "") + '"'


def sshd_principal(who):
    domain, user = who["domain"], who["user"]
    if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value)
               for value in (domain, user)):
        raise MeshError("Cannot render the exact current Windows SSH principal")
    return domain.casefold() + "\\" + user.casefold()


def ensure_keys(root, slot):
    config = root / "config"
    who = require_user()
    sid = who["sid"]
    for name in ("host_ed25519", "client_ed25519"):
        stage("ensure-" + name.replace("_", "-"))
        key = config / name
        no_reparse(key)
        if not key.exists():
            if key.with_suffix(".pub").exists():
                raise MeshError("Orphaned generated public key; refusing key replacement")
            stage("generate-" + name.replace("_", "-"))
            bounded([find_binary(slot, "ssh-keygen.exe"), "-q", "-t", "ed25519",
                     "-N", "", "-f", key], root)
            stage("normalize-generated-key-acls")
            normalize_generated_key(key, sid)
            if key.with_suffix(".pub").exists():
                normalize_generated_key(key.with_suffix(".pub"), sid)
        if not key.with_suffix(".pub").exists():
            stage("derive-" + name.replace("_", "-") + "-public")
            output = bounded([find_binary(slot, "ssh-keygen.exe"), "-y", "-f", key], root)
            atomic(key.with_suffix(".pub"), public_key(output.decode("ascii")) + "\n")
            normalize_generated_key(key.with_suffix(".pub"), sid)
        stage("validate-" + name.replace("_", "-") + "-public")
        public_key(key.with_suffix(".pub").read_text(encoding="ascii"))
    for name in ("authorized_keys", "known_hosts"):
        if not (config / name).exists():
            atomic(config / name, "")
    sshd_config = "\n".join([
        "Port 2223", "ListenAddress 127.0.0.1", "AddressFamily inet",
        "HostKey " + ssh_path(config / "host_ed25519"),
        "AuthorizedKeysFile " + ssh_path(config / "authorized_keys"),
        "PidFile " + ssh_path(root / "state" / "sshd.pid"),
        "AllowUsers " + sshd_principal(who), "PubkeyAuthentication yes", "PasswordAuthentication no",
        "KbdInteractiveAuthentication no", "AuthenticationMethods publickey",
        "PermitEmptyPasswords no", "StrictModes yes", "AllowAgentForwarding no",
        "AllowTcpForwarding local", "PermitOpen localhost:2200 127.0.0.1:2200",
        "X11Forwarding no", "PermitTunnel no",
        "LogLevel ERROR", "Subsystem sftp internal-sftp", ""])
    path = config / "sshd_config"
    if not path.exists() or path.read_text(encoding="utf-8") != sshd_config:
        atomic(path, sshd_config)


def authorized_entry(key, name):
    return ('restrict,pty,port-forwarding,permitopen="localhost:2200",'
            'permitopen="127.0.0.1:2200" %s gateway-%s' % (key, name))


def authorize(root, value):
    fields(value, {"peers"})
    if not isinstance(value["peers"], list) or not 1 <= len(value["peers"]) <= 5:
        raise MeshError("Authorize between one and five pilot peers")
    config, seen, authorized, hosts, records = root / "config", set(), [], [], []
    for peer in value["peers"]:
        fields(peer, {"machine", "user", "public_key", "host_key"})
        name = machine_name(peer["machine"])
        if name in seen or peer["user"] != "operator":
            raise MeshError("Duplicate peer or unexpected SSH user")
        seen.add(name)
        key, host = public_key(peer["public_key"]), public_key(peer["host_key"])
        authorized.append(authorized_entry(key, name))
        alias = "operator-%s-gateway" % name
        hosts.append("%s %s" % (alias, host))
        records.append({"machine": name, "user": peer["user"],
                        "public_key": key, "host_key": host})

    atomic(config / "authorized_keys", "\n".join(authorized) + "\n")
    atomic(config / "known_hosts", "\n".join(hosts) + "\n")
    write_json(config / "authorized-peers.json", {"peers": records})


def ssh_registry(root):
    pass
    stage("ssh-registry-authorization")
    config = root / "config"
    metadata = config / "authorized-peers.json"
    targets = []
    canonical = set()
    marker = config / "canonical-routes.json"
    if marker.exists():
        value = read_json(marker)
        fields(value, {"schema", "machines"})
        if (type(value["schema"]) is not int or value["schema"] != 1 or
                not isinstance(value["machines"], list) or len(value["machines"]) > 5 or
                any(not isinstance(name, str) or name != "operator-" + machine_name(name)
                    for name in value["machines"]) or
                len(value["machines"]) != len(set(value["machines"]))):
            raise MeshError("Invalid verified canonical route selection")
        canonical = set(value["machines"])
    enrollment = load_enrollment(root)
    enrolled = {machine_name(p["machine"]) for p in enrollment["peers"]} if enrollment else set()
    if metadata.exists():
        value = read_json(metadata)
        if set(value) == {"machines"}:
            raise MeshError("Legacy authorization metadata requires explicit config-authorize")
        fields(value, {"peers"})
        if not isinstance(value["peers"], list) or len(value["peers"]) > 5:
            raise MeshError("Invalid explicit SSH authorization set")
        seen = set()
        authorized = (config / "authorized_keys").read_text(encoding="utf-8").splitlines()
        known_hosts = (config / "known_hosts").read_text(encoding="utf-8").splitlines()
        for peer in value["peers"]:
            fields(peer, {"machine", "user", "public_key", "host_key"})
            name = machine_name(peer["machine"])
            if name in seen or peer["user"] != "operator":
                raise MeshError("Invalid or duplicated SSH authorization target")
            seen.add(name)
            key, host = public_key(peer["public_key"]), public_key(peer["host_key"])
            alias = "operator-%s-gateway" % name
            if (authorized_entry(key, name) not in authorized or
                    "%s %s" % (alias, host) not in known_hosts):
                raise MeshError("SSH authorization metadata disagrees with owned key files")
            if name not in enrolled:
                continue
            identity_file = config / "client_ed25519"
            if not identity_file.is_file():
                raise MeshError("Own client identity is unavailable; registry refuses path guesses")
            verify_private(identity_file, require_user()["sid"], protected=True)
            target = {"name": alias, "hostname": "127.0.0.1", "user": peer["user"],
                            "port": 24001 + list(PEERS).index(name),
                            "identity_file": str(identity_file),
                            "options": {
                                "HostKeyAlias": alias,
                                "UserKnownHostsFile": str(config / "known_hosts"),
                                "StrictHostKeyChecking": "yes", "IdentitiesOnly": "yes",
                                "BatchMode": "yes", "IdentityAgent": "none",
                                "ProxyCommand": "none", "ProxyJump": "none",
                                "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
                                "ServerAliveInterval": 15, "ServerAliveCountMax": 3}}
            targets.append(target)
            if "operator-" + name in canonical:
                targets.append({**target, "name": "operator-" + name})
    if not canonical <= {target["name"] for target in targets}:
        raise MeshError("Verified canonical route no longer has explicit authorization")
    targets.sort(key=lambda target: target["port"])
    registry = {"transport": PROVIDER, "topology": "per-machine", "machines": targets}
    path = config / "agent-ssh-registry.yaml"
    write_json(path, registry)
    write_json(config / "agent-ssh-registry.provenance.json", {
        "schema": 1, "registry": str(path),
        "registry_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "authorization_source": str(metadata) if metadata.exists() else None,
        "authorization_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest()
        if metadata.exists() else None,
        "known_hosts_source": str(config / "known_hosts"),
        "module": str(config / "agent-ssh-module.yaml"),
        "configured": bool(targets), "live": "not-probed"})
    return registry


def describe_provider(root):
    stage("describe-provider")
    config = root / "config"
    registry, module = config / "agent-ssh-registry.yaml", config / "agent-ssh-module.yaml"
    snapshot = read_json(registry) if registry.exists() else None
    return {"provider": PROVIDER, "backend": BACKEND, "topology": "per-machine",
            "registry": str(registry), "module": str(module),
            "provenance": str(config / "agent-ssh-registry.provenance.json"),
            "registry_available": registry.is_file(), "module_available": module.is_file(),
            "configured": bool(snapshot and snapshot.get("transport") == PROVIDER and
                               snapshot.get("machines")),
            "live": "not-probed", "profile_owner": "agent-ssh",
            "lifecycle_owner": "fleet-manager"}


def az_command(arguments):
    path = shutil.which("az")
    if not path:
        raise MeshError("Azure CLI unavailable; install/authenticate separately")
    if Path(path).suffix.lower() in (".cmd", ".bat"):
        shim = Path(path).resolve()
        body = shim.read_text(encoding="utf-8").lower()
        python = shim.parent.parent / "python.exe"
        if (shim.name.lower() != "az.cmd" or
                '"%~dp0\\..\\python.exe" -ibm azure.cli %*' not in body or
                not python.is_file()):
            raise MeshError("Unsupported Azure CLI wrapper; select the standard installed Azure CLI package")

        return [str(python), "-IBm", "azure.cli"] + arguments
    return [path] + arguments


def tunnel_command(config):
    return az_command(["network", "bastion", "tunnel", "--subscription", config["subscription"],
                       "--resource-group", config["resource_group"], "--name", config["bastion"],
                       "--target-resource-id", config["resource"],
                       "--resource-port", "22", "--port", "18444"])


def banner(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.7) as connection:
            connection.settimeout(15)
            return connection.recv(256).startswith(b"SSH-2.0-")
    except (OSError, TimeoutError):
        return False


def gateway_http_health(port):
    pass
    try:
        deadline = time.monotonic() + 2
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            connection.settimeout(remaining)
            connection.sendall(
                b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            data = bytearray()
            while len(data) <= 4096:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                connection.settimeout(remaining)
                chunk = connection.recv(4097 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
        if len(data) > 4096:
            return False
        header, separator, body = bytes(data).partition(b"\r\n\r\n")
        status, _, lines = header.partition(b"\r\n")
        parts = status.split(b" ", 2)
        if (not separator or len(parts) < 2 or parts[0] not in (b"HTTP/1.0", b"HTTP/1.1")
                or parts[1] != b"200"):
            return False
        headers = http.client.parse_headers(io.BytesIO(lines + b"\r\n\r\n"))
        lengths = headers.get_all("Content-Length", [])
        if (headers.defects or len(headers.get_all("Content-Type", [])) != 1
                or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,3}", lengths[0])
                or int(lengths[0]) != len(body) or len(body) > 256
                or headers.get_content_type() != "application/json"
                or headers.get("Content-Encoding") is not None
                or headers.get("Transfer-Encoding") is not None):
            return False
        value = json_object(body.decode("utf-8"))
        return (isinstance(value, dict) and set(value) == {"status", "time"}
                and value["status"] == "ok" and type(value["time"]) in (int, float)
                and math.isfinite(value["time"]) and value["time"] > 0)
    except (OSError, ValueError, UnicodeError, http.client.HTTPException, MeshError):
        return False


class BannerProbes:
    pass

    def __init__(self, probe=None):
        self.results = {}
        self.probe = probe

    def ready(self, child, port):
        if not child.owns_port(port):
            self.results.pop(port, None)
            return False
        now = time.monotonic()
        previous = self.results.get(port)
        if (previous is None or previous[0] is not child.process or
                now - previous[1] >= BANNER_PROBE_INTERVAL):
            previous = (child.process, now, (self.probe or banner)(port))
            self.results[port] = previous
        return previous[2]


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def listener_pid(port):
    pass
    function = api(dll("iphlpapi"), "GetExtendedTcpTable",
                   [wt.LPVOID, ctypes.POINTER(wt.DWORD), wt.BOOL, wt.DWORD,
                    ctypes.c_int, wt.DWORD], wt.DWORD)
    size = wt.DWORD()
    if function(None, ctypes.byref(size), False, 2, 5, 0) != 122:
        return None
    if not 4 <= size.value <= 16 * 1024 * 1024:
        return None
    data = ctypes.create_string_buffer(size.value)
    if function(data, ctypes.byref(size), False, 2, 5, 0):
        return None
    raw = data.raw[:size.value]
    count = struct.unpack_from("<I", raw)[0]
    if len(raw) < 4 + count * 24:
        return None
    for index in range(count):
        state, address, local_port, _, _, pid = struct.unpack_from("<6I", raw, 4 + index * 24)
        if state == 2 and address == 0x0100007f and socket.ntohs(local_port & 0xffff) == port:
            return pid
    return None


class RelayDiagnostics:
    pass

    def __init__(self, port):
        self.port = port
        self.kind = None
        self.pending = b""
        self.oversized = False

    def _line(self, data):
        text = data.decode("utf-8", errors="replace").lower()
        kind = None
        match = re.search(r"remote port forwarding failed for listen port ([0-9]{1,5})(?![0-9])", text)
        if match and int(match[1]) == self.port:
            kind = "reverse-forward-refused"
        elif "host key verification failed" in text or "remote host identification has changed" in text:
            kind = "host-key-refused"
        elif "permission denied (" in text:
            kind = "authentication-refused"
        if kind:
            self.kind = kind

    def feed(self, chunk):
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not self.oversized:
                    self._line(self.pending)
                self.pending, self.oversized = b"", False
            if not self.oversized:
                self.pending += part
                if len(self.pending) > 2048:
                    self.pending, self.oversized = b"", True

    def finish(self):
        if self.pending and not self.oversized:
            self._line(self.pending)
        self.pending, self.oversized = b"", False

    def read(self, stream):
        try:
            reader = getattr(stream, "read1", stream.read)
            while True:
                chunk = reader(1024)
                if not chunk:
                    self.finish()
                    return
                self.feed(chunk)
        except OSError:
            self.kind = "diagnostic-read-failed"
            self.pending, self.oversized = b"", False

    def pump(self, stream):
        pass
        try:
            remaining = 65536
            while remaining:
                if os.name == "nt":
                    import msvcrt
                    available = wt.DWORD()
                    peek = api(dll("kernel32"), "PeekNamedPipe",
                               [wt.HANDLE, wt.LPVOID, wt.DWORD, wt.LPVOID,
                                ctypes.POINTER(wt.DWORD), wt.LPVOID], wt.BOOL)
                    if not peek(msvcrt.get_osfhandle(stream.fileno()), None, 0,
                                None, ctypes.byref(available), None):
                        if ctypes.get_last_error() == 109:
                            self.finish()
                            return
                        raise OSError("Owned relay pipe observation failed")
                    if not available.value:
                        return
                    count = min(remaining, available.value, 1024)
                else:
                    import select
                    if not select.select([stream], [], [], 0)[0]:
                        return
                    count = min(remaining, 1024)
                chunk = os.read(stream.fileno(), count)
                if not chunk:
                    self.finish()
                    return
                self.feed(chunk)
                remaining -= len(chunk)
        except (OSError, ValueError):
            self.kind = "diagnostic-read-failed"
            self.pending, self.oversized = b"", False

    def result(self):
        kind = self.kind
        if kind is None:
            return None
        result = {"kind": kind, "owner": "unknown"}
        if kind == "reverse-forward-refused":
            result.update(port=self.port, automatic_action="bounded-retry")
        return result


class Child:
    def __init__(self, name):
        self.name, self.process, self.job = name, None, None
        self.failures, self.retry_at, self.last_exit = 0, 0.0, None
        self.diagnostics, self.last_failure = None, None

    def start(self, argv, root, env):
        if self.process is not None and self.process.poll() is None:
            return
        self.cleanup()
        capture = self.name == "gateway-ssh"
        self.process, self.job = spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False, cwd=str(root), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            **no_window_kwargs())
        if capture:
            reverse = next((argv[index + 1] for index, value in enumerate(argv[:-1])
                            if value == "-R"), "")
            match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5}):127\.0\.0\.1:2223", reverse)
            port = int(match[1]) if match and int(match[1]) in PEERS.values() else None
            self.diagnostics = RelayDiagnostics(port)

    def observe(self):
        if self.process is not None and self.diagnostics is not None:
            self.diagnostics.pump(self.process.stderr)
        if self.process is not None and self.process.poll() is not None:
            self.last_exit = self.process.returncode
            self.cleanup()
            self.failures += 1
            self.retry_at = time.monotonic() + min(
                30, 2 ** min(self.failures, 5) * random.uniform(0.8, 1.2))
            LOGGER.warning("%s exited; code=%s", self.name, self.last_exit)
            if self.last_failure:
                LOGGER.warning("gateway relay failure: %s", self.last_failure["kind"])

    def cleanup(self):
        if self.job is not None:
            self.job.terminate_and_wait(10)
            self.job = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            if self.diagnostics is not None:
                self.diagnostics.pump(self.process.stderr)
                self.diagnostics.finish()
                self.last_failure = self.diagnostics.result()
                self.process.stderr.close()
                self.diagnostics = None
            self.process = None

    def status(self):
        running = self.process is not None and self.process.poll() is None
        result = {"running": running, "pid": self.process.pid if running else None,
                "birthtime": process_birth(self.process.pid) if running else None,
                "last_exit": self.last_exit, "failures": self.failures,
                "job_owned": self.job is not None,
                "retry_in_seconds": max(0, round(self.retry_at - time.monotonic(), 1))}
        if self.name == "gateway-ssh":
            result["last_failure"] = self.last_failure
        return result

    def owns_port(self, port):
        if self.process is None or self.process.poll() is not None:
            return False
        owner = listener_pid(port)
        if owner == self.process.pid:
            return True
        if owner is None or self.job is None:
            return False

        size = 8 + 4096 * ctypes.sizeof(ctypes.c_size_t)
        buffer = ctypes.create_string_buffer(size)
        returned = wt.DWORD()
        function = api(dll("kernel32"), "QueryInformationJobObject",
                       [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD,
                        ctypes.POINTER(wt.DWORD)])
        if not function(self.job.handle, 3, buffer, size, ctypes.byref(returned)):
            return False
        assigned, listed = struct.unpack_from("<II", buffer.raw)
        if assigned != listed or listed > 4096:
            return False
        pointer = ctypes.cast(ctypes.addressof(buffer) + 8,
                              ctypes.POINTER(ctypes.c_size_t))
        return owner in [pointer[index] for index in range(listed)]


def status_unavailable(reason="not-running"):
    return {"running": False, "ready": False, "transport": "down", "reason": reason}


def control(root, action="status"):
    path = root / "state" / "daemon.json"
    if not path.exists():
        return status_unavailable()
    record = read_json(path)
    fields(record, {"pid", "birthtime", "port", "version", "sid"})
    if (type(record["pid"]) is not int or record["pid"] <= 0 or
            type(record["birthtime"]) is not int or record["birthtime"] <= 0 or
            type(record["port"]) is not int or not 1024 <= record["port"] <= 65535 or
            not isinstance(record["sid"], str) or
            not isinstance(record["version"], str) or
            not re.fullmatch(r"[a-f0-9]{64}", record["version"])):
        raise MeshError("Invalid local control process record")
    if process_birth(record["pid"]) != record["birthtime"]:
        return status_unavailable("stale-process-record")
    if listener_pid(record["port"]) != record["pid"]:
        return status_unavailable("control-listener-not-owned")
    token = (root / "config" / "control-token").read_text(encoding="ascii")
    try:
        with socket.create_connection(("127.0.0.1", record["port"]), timeout=2) as connection:
            connection.settimeout(3)
            request = {"token": token, "action": action, "pid": record["pid"],
                       "birthtime": record["birthtime"]}
            connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            data = bytearray()
            while b"\n" not in data:
                chunk = connection.recv(4096)
                if not chunk or len(data) + len(chunk) > 65536:
                    raise MeshError("Invalid local control response")
                data.extend(chunk)
        response = json_object(bytes(data).decode("utf-8"))
    except (OSError, UnicodeError):
        return status_unavailable("control-unreachable")
    if (response.get("pid") != record["pid"] or
            response.get("birthtime") != record["birthtime"] or
            response.get("identity", {}).get("sid") != record["sid"] or
            process_birth(record["pid"]) != record["birthtime"]):
        raise MeshError("Local control process identity mismatch")
    return response


def active_slot(root):
    value = (root / "active").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise MeshError("Invalid active version marker")
    slot = root / "versions" / value
    verify_slot(slot)
    return slot


def verify_slot(slot):
    no_reparse(slot)
    receipt = read_json(slot / "files.json")
    if not set(source_files()).issubset(receipt):
        raise MeshError("Incomplete immutable slot receipt")
    for relative, expected in receipt.items():
        if not re.fullmatch(r"[a-zA-Z0-9_./-]+", relative) or ".." in relative.split("/"):
            raise MeshError("Invalid slot receipt path")
        path = slot.joinpath(*relative.split("/"))
        no_reparse(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise MeshError("Installed immutable slot drift")
    for binary in ("sshd.exe", "ssh.exe", "ssh-keygen.exe"):
        find_binary(slot, binary)


def ensure(root, timeout=STARTUP_TIMEOUT):
    result = control(root)
    if result["running"]:
        if result.get("version") != active_slot(root).name:
            raise MeshError("Running daemon differs from active marker; use update")
        return result
    if (root / "state" / "daemon.json").exists():
        record = read_json(root / "state" / "daemon.json")
        if process_birth(record["pid"]) == record["birthtime"]:
            raise MeshError("Existing daemon cannot be authenticated; start refused")
    slot = active_slot(root)
    config = validate_config(read_json(root / "config" / "windows.yaml"))
    if port_open(2223) or port_open(18444) or any(
            port_open(p) for p in local_forward_ports(config)):
        raise MeshError("Pilot port occupied; no process will be terminated")
    rotate_launch_log(root)
    python = read_json(root / "bin" / "interpreter.json")["python"]
    try:
        process = spawn_daemon(
            [python, str(slot / "windows.py"), "--root", str(root), "_serve"],
            console_children=True, breakaway=True, cwd=root,
            log_path=root / "logs" / "launch.log",
            env_overrides={"PYTHONDONTWRITEBYTECODE": "1"})
    except OSError:
        raise MeshError("Durable daemon launch rejected; caller Job must permit breakaway (no contained fallback)") from None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise MeshError("Daemon exited before local listener readiness")
        result = control(root)
        if result["running"] and result.get("bootstrap_ready"):
            return result
        time.sleep(0.2)
    stop(root)
    raise MeshError("Local SSH listener and owned Bastion SSH endpoint did not become ready")


def rotate_launch_log(root):
    path = root / "logs" / "launch.log"
    if path.exists() and path.stat().st_size > 1024 * 1024:
        backup = path.with_suffix(".log.1")
        if backup.exists():
            backup.unlink()
        os.replace(str(path), str(backup))


def stop(root):
    result = control(root, "stop")
    if not result["running"]:

        path = root / "state" / "daemon.json"
        if path.exists():
            record = read_json(path)
            if process_birth(record["pid"]) == record["birthtime"]:
                raise MeshError("Live daemon control unavailable; refusing unverified PID kill")
        return
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process_birth(result["pid"]) != result["birthtime"]:
            return
        time.sleep(0.1)
    raise MeshError("Owned daemon did not finish Job teardown; no unrelated PID killed")


def ensure_bastion(child, root, config, now, retry_at):
    if child.process is None and now >= max(child.retry_at, retry_at):
        if port_open(18444):
            raise MeshError("Owned Bastion port occupied by an unrelated listener")
        stage("bastion-account-check")
        bounded(az_command(["account", "show", "--subscription", config["subscription"],
                            "--output", "none"]), root, child_environment("azure"),
                timeout=AZURE_ACCOUNT_TIMEOUT)
        stage("bastion-tunnel-launch")
        child.start(tunnel_command(config), root, child_environment("azure"))


def reconcile_relay(child, tunnel, root, slot, enrollment, now, retry_at, config=None):
    if enrollment is None or not tunnel.owns_port(18444):
        if child.process is not None:
            child.cleanup()
        return
    if child.process is None and now >= max(child.retry_at, retry_at):
        if any(port_open(port) for port in local_forward_ports(config)):
            raise MeshError("Owned local forward port occupied; refusing unrelated listener")
        stage("gateway-ssh-launch")
        child.start(relay_command(find_binary(slot, "ssh.exe"), root, enrollment, config),
                    root, child_environment("relay"))


def serve(root):
    who = require_user()
    verify_runtime(root, who["sid"])
    config = validate_config(read_json(root / "config" / "windows.yaml"))
    if who["user"].lower() != config["user"]:
        raise MeshError("Daemon identity does not match configured SSH user")
    slot, children = HERE, {name: Child(name) for name in ("ssh", "tunnel", "gateway-ssh")}
    handler = RotatingFileHandler(root / "logs" / "supervisor.log",
                                  maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    token = (root / "config" / "control-token").read_text(encoding="ascii")
    birth = process_birth(os.getpid())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.2)
    lease = (root / "state" / "lease").open("a+b")
    import msvcrt
    try:
        lease.seek(0)
        msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        listener.close()
        lease.close()
        raise MeshError("Another pilot supervisor owns the lease") from None
    record = {"pid": os.getpid(), "birthtime": birth, "port": listener.getsockname()[1],
              "version": slot.name, "sid": who["sid"]}
    write_json(root / "state" / "daemon.json", record)
    shutdown = threading.Event()
    snapshot = {**record, "identity": who, "running": True, "ready": False,
                "listener_ready": False, "bastion_ready": False, "bootstrap_ready": False,
                "backend": BACKEND, "transport": "down", "reason": "starting"}
    snapshot_lock = threading.Lock()

    def dispatch():
        while not shutdown.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(0.5)
                try:
                    data = bytearray()
                    while b"\n" not in data and len(data) <= 4096:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        data.extend(chunk)
                    request = json_object(bytes(data).decode("utf-8"))
                    if (not isinstance(request.get("token"), str) or
                            not hmac.compare_digest(request["token"], token) or
                            request.get("pid") != os.getpid() or
                            request.get("birthtime") != birth or
                            request.get("action") not in ("status", "stop")):
                        continue
                    with snapshot_lock:
                        connection.sendall(json.dumps(snapshot).encode("utf-8") + b"\n")
                    if request["action"] == "stop":
                        shutdown.set()
                except (OSError, ValueError, MeshError):
                    continue

    dispatcher = threading.Thread(target=dispatch, name="mesh-control", daemon=True)
    dispatcher.start()
    auth_retry, problem, healthy_since, enrollment, error_detail = 0.0, None, None, None, None
    probes = BannerProbes()
    http_probes = BannerProbes(probe=gateway_http_health)
    try:
        while not shutdown.is_set():
            now = time.monotonic()
            for child in children.values():
                child.observe()
            try:
                ssh = children["ssh"]
                if ssh.process is None and now >= max(ssh.retry_at, auth_retry):
                    if port_open(2223):
                        raise MeshError("Owned SSH port occupied by an unrelated listener")
                    ssh.start([str(find_binary(slot, "sshd.exe")), "-D", "-e", "-f",
                               str(root / "config" / "sshd_config")], root, child_environment("ssh"))
                tunnel, relay = children["tunnel"], children["gateway-ssh"]
                ensure_bastion(tunnel, root, config, now, auth_retry)
                previous_enrollment = enrollment
                stage("gateway-enrollment")
                enrollment = load_enrollment(root)
                if enrollment != previous_enrollment and relay.process is not None:
                    relay.cleanup()
                    relay.retry_at = 0
                if enrollment is None:
                    problem = "auth-needed: public Gateway enrollment absent"
                else:
                    problem = None
                reconcile_relay(relay, tunnel, root, slot, enrollment, now, auth_retry, config)
                error_detail = None
            except (MeshError, OSError, subprocess.SubprocessError) as error:

                problem = "auth-needed-or-transport-unavailable"
                error_detail = sanitized_error(error, "supervise")
                if now >= auth_retry:
                    LOGGER.warning("transport reconcile unavailable; retry in 30s")
                    auth_retry = now + 30
            own_forward = 24001 + list(PEERS).index(config["machine"])
            listener_ready = probes.ready(children["ssh"], 2223)
            bastion_ready = probes.ready(children["tunnel"], 18444)
            relay_ready = probes.ready(children["gateway-ssh"], own_forward)
            if relay_ready:
                children["gateway-ssh"].last_failure = None
            http_enabled = config.get("gateway_http", False)
            http_ready = http_enabled and http_probes.ready(
                children["gateway-ssh"], GATEWAY_HTTP_LOCAL_PORT)
            ready = listener_ready and bastion_ready and relay_ready and (
                not http_enabled or http_ready)
            if enrollment is not None and not ready and problem is None:
                problem = "auth-needed-or-relay-forward-unavailable"
            if not relay_ready and children["gateway-ssh"].last_failure:
                problem = children["gateway-ssh"].last_failure["kind"]
            if listener_ready and bastion_ready and relay_ready and http_enabled and not http_ready:
                problem = "gateway-http-unavailable"
            if ready:
                healthy_since = now if healthy_since is None else healthy_since
                problem = None
                if now - healthy_since > 60:
                    for child in children.values():
                        child.failures = 0
            else:
                healthy_since = None
            health = {**record, "identity": who, "running": True, "ready": ready,
                      "listener_ready": listener_ready, "own_relay_banner": relay_ready,
                      "bastion_ready": bastion_ready, "bootstrap_ready": listener_ready and bastion_ready,
                      "backend": BACKEND, "last_error": error_detail,
                      "gateway_http": {
                          "configured": http_enabled,
                          "owned": http_enabled and children["gateway-ssh"].owns_port(
                              GATEWAY_HTTP_LOCAL_PORT),
                          "ready": bool(http_ready),
                          "endpoint": "http://127.0.0.1:%d" % GATEWAY_HTTP_LOCAL_PORT
                          if http_enabled else None,
                          "authenticated": "not-probed"},
                      "transport": "up" if relay_ready else "down", "reason": problem,
                      "children": {name: child.status() for name, child in children.items()},
                      "enrolled_machines": [p["machine"] for p in enrollment["peers"]] if enrollment else [],
                      "route_health": {name: probes.ready(children["gateway-ssh"], 24001 + index)
                                       if relay_ready else False
                                       for index, name in enumerate(PEERS)}}
            with snapshot_lock:
                snapshot.clear()
                snapshot.update(health)
            shutdown.wait(1)
    finally:
        shutdown.set()
        listener.close()
        dispatcher.join(timeout=1)
        failures = []
        for child in reversed(list(children.values())):
            try:
                child.cleanup()
            except (OSError, RuntimeError, TimeoutError):
                failures.append(child.name)
        path = root / "state" / "daemon.json"
        if not failures and path.exists() and read_json(path)["birthtime"] == birth:
            path.unlink()
        lease.close()
        handler.close()
        if failures:
            raise MeshError("Owned Job teardown could not be verified")


def source_files():
    return ["windows.py", "windows.yaml", "install.ps1", "libs/agent_procutil/__init__.py",
            "libs/LICENSE", "libs/provenance.json", "agent-ssh/module.yaml"]


def source_hash(config):
    digest = hashlib.sha256()
    for relative in source_files():
        stage("source-" + relative.lower().replace("/", "-").replace("_", "-").replace(".", "-"))
        digest.update(relative.encode("ascii") + b"\0" + (HERE / relative).read_bytes())
    digest.update(json.dumps(config, sort_keys=True).encode("utf-8"))
    digest.update(architecture().encode("ascii"))
    return digest.hexdigest()


def build_slot(root, config):
    stage("candidate-sourcehash")
    version = source_hash(config)
    slot = root / "versions" / version
    if slot.exists():
        verify_slot(slot)
        ensure_keys(root, slot)
        validate_slot(root, slot)
        return slot
    candidate = root / "versions" / (version + ".candidate-" + secrets.token_hex(8))
    candidate.mkdir()
    try:
        for relative in source_files():
            destination = candidate.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(HERE / relative, destination)
        for kind in ("ssh",):
            destination = candidate / "tools" / kind
            destination.mkdir(parents=True)
            download(kind, architecture(), destination)
        ensure_keys(root, candidate)
        validate_slot(root, candidate)
        receipt = {path.relative_to(candidate).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in candidate.rglob("*") if path.is_file()}
        write_json(candidate / "files.json", receipt)
        os.replace(str(candidate), str(slot))
        return slot
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


def validate_slot(root, slot):
    stage("validate-openssh-version")
    if b"OpenSSH_for_Windows_10.0" not in bounded([find_binary(slot, "ssh.exe"), "-V"], root):
        raise MeshError("Unexpected OpenSSH binary version")
    stage("validate-sshd-config")
    bounded([find_binary(slot, "sshd.exe"), "-t", "-f", root / "config" / "sshd_config"],
            root, child_environment("ssh"))


def register_autostart(root, enabled):
    import winreg
    name = "PrivateGatewaySshMesh"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        try:
            existing, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            existing = None
        powershell = shutil.which("pwsh.exe") or str(
            Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        command = subprocess.list2cmdline([
            str(Path(os.environ["SystemRoot"]) / "System32" / "conhost.exe"),
            "--headless", powershell, "-NoProfile", "-NonInteractive", "-File",
            str(root / "bin" / "start.ps1")])
        receipt = root / "config" / "autostart.json"
        registered = read_json(receipt).get("command") if receipt.exists() else command
        if existing is not None and existing != registered:
            raise MeshError("Autostart name owned by a different root/launcher")
        if enabled and existing is None:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
            write_json(receipt, {"command": command})
        elif not enabled and existing is not None:
            winreg.DeleteValue(key, name)


def write_launcher(root):
    python = Path(sys.executable).resolve()
    if sys.version_info < (3, 9) or python.name.lower() == "pythonw.exe":
        raise MeshError("Use an existing console-subsystem Python 3.9+")
    write_json(root / "bin" / "interpreter.json", {"python": str(python)})
    atomic(root / "bin" / "control.ps1", (HERE / "install.ps1").read_bytes())
    text = """param([string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'interpreter.json') -Raw | ConvertFrom-Json).python
& (Join-Path $PSScriptRoot 'control.ps1') -Action $Action -Root $root -Python $python -Config (Join-Path $root 'config\\windows.yaml')
exit $LASTEXITCODE
"""
    atomic(root / "bin" / "start.ps1", text)


def install_ssh_module(root, slot):
    stage("install-agent-ssh-module")
    source = slot / "agent-ssh" / "module.yaml"
    if read_json(source).get("module") != PROVIDER:
        raise MeshError("Declared agent-ssh module must identify gateway-relay")
    atomic(root / "config" / "agent-ssh-module.yaml", source.read_bytes())


def wait_mesh(root, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = control(root)
        if health.get("ready"):
            return health
        if not health["running"]:
            break
        time.sleep(0.5)
    raise MeshError("Mesh readiness unavailable (SSH banner is not authenticated user proof)")


def deployment_receipt(root, slot, old, who, health):
    http_enabled = validate_config(read_json(root / "config" / "windows.yaml")).get(
        "gateway_http", False)
    http_health = health.get("gateway_http")
    if http_enabled and (not isinstance(http_health, dict)
                         or http_health.get("ready") is not True
                         or http_health.get("owned") is not True):
        raise MeshError("Enabled Gateway HTTP route has no owned health observation")
    write_json(root / "state" / "deploy-manifest.json",
               {"schema": 1, "sourcehash": slot.name, "previous": old,
                "installed_at": time.time(), "identity": who,
                "local_listener": True, "own_relay_banner": True,
                "gateway_http": http_health,
                "authenticated_ssh_verified": False})


def verify_durable_launch(root):
    stage("durable-launch-preflight")
    try:
        process = spawn_daemon(
            [sys.executable, "-c", "pass"], console_children=True,
            breakaway=True, cwd=root, log_path=root / "logs" / "launch-preflight.log",
            env_overrides={"PYTHONDONTWRITEBYTECODE": "1"})
    except OSError:
        raise MeshError(
            "Durable launch preflight refused; predecessor not stopped. "
            "Run the normal installer from a caller that permits Job breakaway.") from None
    try:
        try:
            result = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise MeshError("Durable launch preflight timed out; predecessor not stopped") from None
        if result != 0:
            raise MeshError("Durable launch preflight failed; predecessor not stopped")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def deploy(root, config_path, accept_config=False, repair_key_acls=False):
    stage("installer-identity")
    who = require_user()
    stage("desired-config")
    config = validate_config(read_json(config_path))
    if config["user"] != who["user"].lower():
        raise MeshError("Installer identity disagrees with configured account")
    stage("runtime-directories")
    private_directory(root, who["sid"])
    for directory in ("config", "state", "logs", "bin", "versions"):
        private_directory(root / directory, who["sid"])
    if repair_key_acls:
        repair_generated_key_acls(root, who["sid"])
    stage("runtime-acl-audit")
    verify_runtime(root, who["sid"])
    runtime_config = root / "config" / "windows.yaml"
    previous_config = read_json(runtime_config) if runtime_config.exists() else None
    if previous_config is not None and previous_config != config and not accept_config:
        raise MeshError("Runtime config drift; review desired changes, then use --accept-config")
    if not runtime_config.exists():
        write_json(runtime_config, config)
    token = root / "config" / "control-token"
    if not token.exists():
        atomic(token, secrets.token_hex(32))
    old = (root / "active").read_text(encoding="ascii").strip() if (root / "active").exists() else None
    stage("predecessor-status")
    old_health = control(root)
    slot = build_slot(root, config)
    install_ssh_module(root, slot)
    stage("stable-launcher")
    write_launcher(root)
    if old == slot.name and old_health.get("ready"):
        register_autostart(root, config["autostart"])
        deployment_receipt(root, slot, old, who, old_health)
        return old_health
    if (old == slot.name and old_health.get("bootstrap_ready") and not old_health.get("ready")
            and load_enrollment(root) is None):
        return {**old_health, "reason": "auth-needed: import public descriptor and register relay public key",
                "deployment_complete": False}
    verify_durable_launch(root)


    if old_health["running"]:
        stage("predecessor-stop")
        stop(root)
    try:
        if previous_config != config:
            write_json(runtime_config, config)
        atomic(root / "active", slot.name + "\n")
        stage("candidate-ensure")
        health = ensure(root)
        if health.get("ready"):
            pass
        elif load_enrollment(root) is not None:
            stage("candidate-mesh-health")
            health = wait_mesh(root)
        else:

            return {**health, "reason": "auth-needed: import public descriptor and register relay public key",
                    "deployment_complete": False}
        stage("candidate-autostart")
        register_autostart(root, config["autostart"])
        stage("deployment-receipt")
        deployment_receipt(root, slot, old, who, health)
        return health
    except (MeshError, OSError):
        stop(root)
        if old:
            if previous_config is not None:
                write_json(runtime_config, previous_config)
            atomic(root / "active", old + "\n")
            ensure(root)
            if old_health.get("ready"):
                wait_mesh(root)
                raise MeshError("Candidate failed; predecessor restored and health-verified") from None
            raise MeshError("Candidate failed; predecessor restored; readiness not established") from None
        (root / "active").unlink()
        raise MeshError("First deployment failed; no active version published") from None


def uninstall(root, remove_config=False):
    stop(root)
    register_autostart(root, False)
    for name in ("versions", "bin", "logs"):
        path = root / name
        if path.exists():
            no_reparse(path)
            shutil.rmtree(path)
    if (root / "active").exists():
        (root / "active").unlink()
    for path in (root / "state").iterdir():
        if path.is_file():
            path.unlink()
    if remove_config:
        shutil.rmtree(root / "config")


def describe_keys(root):
    return {"machine": validate_config(read_json(root / "config" / "windows.yaml"))["machine"],
            "user": "operator",
            "public_key": public_key((root / "config" / "client_ed25519.pub").read_text(encoding="ascii")),
            "host_key": public_key((root / "config" / "host_ed25519.pub").read_text(encoding="ascii"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".gateway-ssh-mesh")
    parser.add_argument("action", choices=["install", "update", "start", "stop", "status",
                                          "uninstall", "import-client", "config-authorize",
                                          "identity", "describe-keys", "ssh-registry",
                                          "describe-provider", "_serve"])
    parser.add_argument("--config", type=Path, default=HERE / "windows.yaml")
    parser.add_argument("--remove-config", action="store_true")
    parser.add_argument("--accept-config", action="store_true")
    parser.add_argument("--repair-generated-key-acls", action="store_true")
    args = parser.parse_args()
    try:
        stage("caller-identity")
        who = require_user()
        stage("root-resolution")
        root = canonical_root(args.root)
        if args.repair_generated_key_acls and args.action not in ("install", "update"):
            raise MeshError("Generated-key ACL repair is only an explicit install/update option")
        if args.action == "identity":
            result = who
        elif args.action in ("install", "update"):
            with lifecycle_lock(root):
                result = deploy(root, args.config, args.accept_config, args.repair_generated_key_acls)
        elif args.action == "status" and not root.exists():
            result = status_unavailable("not-installed")
        else:
            stage("runtime-acl-audit")
            verify_runtime(root, who["sid"])
            if args.action == "_serve":
                serve(root)
                return 0
            if args.action == "status":
                result = control(root)
            elif args.action == "describe-keys":
                result = describe_keys(root)
            elif args.action == "describe-provider":
                result = describe_provider(root)
            else:
                with lifecycle_lock(root):
                    if args.action == "start":
                        result = ensure(root)
                    elif args.action == "stop":
                        stop(root)
                        result = status_unavailable("stopped")
                    elif args.action == "uninstall":
                        uninstall(root, args.remove_config)
                        result = status_unavailable("uninstalled; config preserved" if not args.remove_config else "uninstalled")
                    elif args.action == "import-client":
                        import_client(root, json_object(sys.stdin.read(32769)))
                        result = {"imported": True, "restart_required": False}
                    elif args.action == "config-authorize":
                        authorize(root, json_object(sys.stdin.read(32769)))
                        result = {"authorized": True, "profile_owner": "agent-ssh",
                                  "next_action": "ssh-registry"}
                    elif args.action == "ssh-registry":
                        result = ssh_registry(root)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ready", True) else 2
    except MeshError as error:
        print(json.dumps(sanitized_error(error, args.action)), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps(sanitized_error(error, args.action)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
