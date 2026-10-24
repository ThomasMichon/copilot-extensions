"""Opt-in capture instrumentation; never loaded by normal Bridge operation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from types import CodeType, FunctionType, ModuleType
from importlib.machinery import ModuleSpec, PathFinder

REQUEST = "BRIDGE_HTTP47_CAPTURE_REQUEST"
REQUEST_SHA = "BRIDGE_HTTP47_CAPTURE_REQUEST_SHA256"
INSTALLATION_HELPER_PREFIX = "plugins/agent-bridge/scripts/installation-context/"
_installed = False
ROLES = {
    "cold_recovery_worker.py": "worker",
    "cold_drain_peer.py": "peer",
    "agent_bridge/session_host/__main__.py": "host",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def load_request() -> tuple[Path, dict]:
    path = Path(os.environ[REQUEST])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != os.environ.get(REQUEST_SHA):
        raise ValueError("HTTP-47 runtime request digest mismatch")
    request = json.loads(raw)
    root = Path(request["root"])
    if (
        root.resolve() != root or path.is_symlink()
        or path.parent.parent != root / ".test-venvs"
        or not path.parent.name.startswith("http47-capture-")
    ):
        raise ValueError("HTTP-47 runtime request is not confined")
    return path, request


def relative_source(filename: str, proof: dict) -> str | None:
    normalized = filename.replace("\\", "/")
    candidates = []
    for path in proof:
        if not path.endswith(".py"):
            continue
        suffix = path.split("/src/", 1)[-1] if "/src/" in path else path
        if normalized.endswith("/" + suffix):
            candidates.append((len(suffix), path))
    return max(candidates)[1] if candidates else None


def entry_role(filename: str) -> str | None:
    name = filename.replace("\\", "/")
    return next((role for suffix, role in ROLES.items() if name.endswith(suffix)), None)


def installation_source(filename: str) -> str | None:
    normalized = filename.replace("\\", "/")
    _, marker, relative = normalized.rpartition("/scripts/installation-context/")
    if marker and relative.endswith(".py"):
        return INSTALLATION_HELPER_PREFIX + relative
    name = normalized.rsplit("/", 1)[-1]
    if name == "installation_context.py" or (
        name.startswith("_installation_context_") and name.endswith(".py")
    ):
        return INSTALLATION_HELPER_PREFIX + name
    return None


def launch_role(argv: list) -> str | None:
    values = [os.fsdecode(value) for value in argv]
    if "-m" in values and values[values.index("-m") + 1] == "agent_bridge.session_host":
        return "host"
    return next(
        (entry_role(value) for value in values[1:] if entry_role(value) is not None),
        None,
    )


def verify_preloaded(module: ModuleType, identity: dict) -> None:
    """Bind the containment plugin's already-imported process helper as well."""
    filename = module.__file__
    data = Path(filename).read_bytes()
    expected = compile(data, filename, "exec", dont_inherit=True, optimize=sys.flags.optimize)
    if sha(data) != identity["sha256"] or module.__loader__.get_code(module.__name__) != expected:
        raise ValueError("HTTP-47 preloaded containment dependency is unproven")
    codes = set()

    def collect(code: CodeType) -> None:
        codes.add(code)
        for value in code.co_consts:
            if isinstance(value, CodeType):
                collect(value)

    collect(expected)
    values = list(vars(module).values())
    values.extend(
        item for value in tuple(values)
        if isinstance(value, type) and value.__module__ == module.__name__
        for item in vars(value).values()
    )
    for value in values:
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if isinstance(value, FunctionType) and value.__code__.co_filename == filename:
            if value.__code__ not in codes:
                raise ValueError("HTTP-47 preloaded containment executable code is unproven")


def install(*, observer: bool = False) -> None:
    global _installed
    if REQUEST not in os.environ or _installed:
        return
    path, request = load_request()
    proof = request["source_proof"]
    root = Path(request["root"])
    bind_installation = request.get("capture_generation") in (48, 49)
    own = "tools/bridge_http47_runtime.py"
    if sha(Path(__file__).read_bytes()) != proof[own]["sha256"]:
        raise ValueError("HTTP-47 runtime guard source is unproven")
    guard = sys.modules[__name__]
    if guard.__loader__.get_code(__name__) != compile(
        Path(__file__).read_bytes(), __file__, "exec", dont_inherit=True,
        optimize=sys.flags.optimize,
    ):
        raise ValueError("HTTP-47 runtime guard executable code is unproven")
    if not observer:
        bootstrap = sys.modules["sitecustomize"]
        relative = "tools/http47_capture_bootstrap/sitecustomize.py"
        data = Path(bootstrap.__file__).read_bytes()
        if sha(data) != proof[relative]["sha256"] or (
            bootstrap.__loader__.get_code("sitecustomize") != compile(
                data, bootstrap.__file__, "exec", dont_inherit=True,
                optimize=sys.flags.optimize,
            )
        ):
            raise ValueError("HTTP-47 subprocess bootstrap source is unproven")
    modules = {}
    for source in proof:
        if "/src/" in source and source.endswith(".py"):
            name = source.split("/src/", 1)[1][:-3].replace("/", ".")
            modules[name.removesuffix(".__init__")] = source
    early = sorted(name for name in modules if name in sys.modules)
    # The runner's portfolio containment plugin imports this dependency before
    # conftest. No Bridge module or child runtime gets this narrow exception.
    if early and (not observer or early != ["agent_procutil"]):
        raise ValueError(f"HTTP-47 production modules loaded before interpreter admission: {early}")

    class Origins:
        @staticmethod
        def find_spec(
            fullname: str, path: list[str] | None = None, target: ModuleType | None = None
        ) -> ModuleSpec | None:
            if fullname not in modules:
                return None
            spec = PathFinder.find_spec(fullname, path, target)
            if spec is None or spec.origin is None or (
                sha(Path(spec.origin).read_bytes()) != proof[modules[fullname]]["sha256"]
            ):
                record({"kind": "refused", "source": modules[fullname]})
                raise ValueError(f"HTTP-47 unproven production module origin: {fullname}")
            return spec

    sys.meta_path.insert(0, Origins)
    directory = path.parent / "interpreters"
    directory.mkdir(exist_ok=True)
    log = directory / f"{os.getpid()}.jsonl"
    if log.exists():
        raise ValueError("HTTP-47 interpreter identity was reused")

    def record(event: dict) -> None:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    record({"kind": "start", "pid": os.getpid(), "parent": os.getppid(), "observer": observer})
    for name in early:
        verify_preloaded(sys.modules[name], proof[modules[name]])
        record({"kind": "exec", "source": modules[name], "role": None, "preloaded": True})
    checking = False

    def audit(event: str, args: tuple) -> None:
        nonlocal checking
        if checking:
            return
        if event == "subprocess.Popen":
            role = launch_role(args[1])
            if role is not None:
                env = args[3] if args[3] is not None else os.environ
                if env.get(REQUEST) != str(path) or env.get(REQUEST_SHA) != os.environ[REQUEST_SHA]:
                    raise ValueError("HTTP-47 contributing launch lost its binding")
                record({"kind": "launch", "role": role})
        if event != "exec" or not isinstance(args[0], CodeType):
            return
        code = args[0]
        source = relative_source(code.co_filename, proof)
        role = entry_role(code.co_filename)
        helper = installation_source(code.co_filename) if bind_installation else None
        if source is None and helper is not None:
            record({"kind": "refused", "source": helper})
            raise ValueError(f"HTTP-{request['capture_generation']} unproven installation helper origin")
        if helper is not None and "/src/" not in source and (
            Path(code.co_filename).resolve() != root / source
        ):
            record({"kind": "refused", "source": source})
            raise ValueError(f"HTTP-{request['capture_generation']} unproven installation helper origin")
        if source is None:
            # A worker/host/peer with the correct basename but foreign location
            # must not execute before we can prove its entrypoint.
            if role is not None:
                raise ValueError("HTTP-47 unproven subprocess entrypoint")
            return
        if "/src/" not in source and role is None and helper is None:
            return  # pytest assertion-rewrites its test bodies; not runtime modules.
        checking = True
        try:
            data = Path(code.co_filename).read_bytes()
            expected = compile(
                data, code.co_filename, "exec", dont_inherit=True, optimize=sys.flags.optimize
            )
            if sha(data) != proof[source]["sha256"] or code != expected:
                record({"kind": "refused", "source": source})
                raise ValueError(f"HTTP-47 unproven subprocess/module code: {source}")
            record({"kind": "exec", "source": source, "role": role})
        finally:
            checking = False

    sys.addaudithook(audit)
    _installed = True
    if observer:
        # The third-party pytest entrypoint is owned by the contained runner,
        # not misrepresented as a repository production module.
        main = sys.modules["__main__"]
        from importlib.metadata import distribution
        files = distribution("pytest").files or []
        actual = Path(main.__file__).resolve()
        item = next((f for f in files if Path(f.locate()).resolve() == actual), None)
        import base64
        if item is None or item.hash is None or item.hash.mode != "sha256" or (
            base64.urlsafe_b64encode(hashlib.sha256(actual.read_bytes()).digest())
            .rstrip(b"=").decode() != item.hash.value
        ):
            raise ValueError("HTTP-47 observer entrypoint is not installed pytest")
        record({"kind": "observer-entry", "distribution": "pytest", "source": str(item)})
        bootstrap = Path(request["root"]) / "tools/http47_capture_bootstrap"
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [str(bootstrap), str(Path(request["root"]) / "tools"), os.environ.get("PYTHONPATH", "")]
        )


def verify_interpreters(
    directory: Path, proof: dict, *,
    expected_roles: frozenset[str] = frozenset({"worker", "host", "peer"}),
    required_sources: frozenset[str] = frozenset(),
) -> dict:
    """Require a bound entrypoint for every actual contributing child launch."""
    logs = []
    for path in sorted(directory.glob("*.jsonl")):
        if path.is_symlink() or path.stat().st_nlink != 1:
            raise ValueError("HTTP-47 aliased interpreter receipt")
        events = [json.loads(line) for line in path.read_text().splitlines()]
        if not events or events[0].get("kind") != "start":
            raise ValueError("HTTP-47 missing interpreter start receipt")
        if any(event["kind"] == "refused" for event in events):
            raise ValueError("HTTP-47 interpreter rejected unproven source")
        logs.append(events)
    observers = [events for events in logs if events[0]["observer"]]
    if len(observers) != 1 or not any(e["kind"] == "observer-entry" for e in observers[0]):
        raise ValueError("HTTP-47 observer entrypoint is unproven")
    launched = Counter()
    started = Counter()
    sources = set()
    for events in logs:
        start = events[0]
        roles = {e["role"] for e in events if e["kind"] == "exec" and e["role"]}
        if not start["observer"]:
            if len(roles) != 1:
                raise ValueError("HTTP-47 child entrypoint is missing or ambiguous")
            started[(start["parent"], next(iter(roles)))] += 1
        for event in events:
            if event["kind"] == "launch":
                launched[(start["pid"], event["role"])] += 1
            if event["kind"] == "exec":
                if event["source"] not in proof:
                    raise ValueError("HTTP-47 executed source outside committed closure")
                sources.add(event["source"])
    if launched != started or {role for _, role in started} != expected_roles:
        raise ValueError("HTTP-47 contributing interpreter launch/entrypoint coverage mismatch")
    if not required_sources <= sources:
        raise ValueError("Capture required runtime source execution is incomplete")
    return {
        "policy": "every-owned-interpreter-source-and-entrypoint-v1",
        "roles": ["observer", *(role for role in ("worker", "host", "peer") if role in expected_roles)],
        "verified_sources": sorted(sources),
        "scope": "contained Python fixtures; not native Copilot or Windows qualification",
    }
