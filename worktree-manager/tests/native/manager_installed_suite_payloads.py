"""Complete payload acquisition for the existing installed lifecycle witness.

The qualification owner supplies the frozen materialized artifact. This module
does not select a release, admit an upgrade baseline, install runtimes or own
native cleanup. Its command executor must be the witness's bounded native owner.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.server
import json
from pathlib import Path
import re
import runpy
import shutil
import sys
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlsplit
import urllib.request

REPO = Path(__file__).resolve().parents[3]
_PROVENANCE = runpy.run_path(
    str(REPO / "tools" / "clean-room" / "lib" / "materialized_provenance.py"),
)
_PROFILE = runpy.run_path(
    str(Path(__file__).with_name("manager_installed_readiness_profile.py")),
)["required_readiness_profile"]


def selected_profile(
    artifact: Path, candidate_commit: str, repository_config: dict, *, transport: dict | None = None,
) -> dict:
    """Verify the complete frozen artifact before deriving its enabled roster."""
    if not artifact.is_absolute():
        raise ValueError("Select the absolute frozen artifact path")
    _PROVENANCE["verify"](artifact, candidate_commit)
    catalog = json.loads(
        (artifact / ".github" / "plugin" / "marketplace.json").read_text(encoding="utf-8"),
    )
    manifests = {}
    # Validate catalog paths before using them to read any manifest.
    for row in catalog["plugins"]:
        name, source = row["name"], row["source"]
        if (
            not isinstance(name, str) or not name
            or "/" in name or "\\" in name or ":" in name or name in {".", ".."}
            or source != "plugins/" + name
        ):
            raise ValueError("The frozen catalog contains an invalid plugin source")
        manifests[name] = json.loads(
            (artifact / "plugins" / name / "plugin.json").read_text(encoding="utf-8"),
        )
    manager = tomllib.loads(
        (artifact / "worktree-manager" / "pyproject.toml").read_text(encoding="utf-8"),
    )
    profile = _PROFILE(
        catalog, manifests, marketplace_root=str(artifact),
        manager_version=manager["project"]["version"], repository_config=repository_config,
    )
    if transport is not None:
        repository = Path(transport["repository"])
        url = urlsplit(transport["url"])
        if (
            not repository.is_absolute()
            or url.scheme != "http" or url.hostname != "127.0.0.1"
            or not url.port or url.path != "/suite-transport.git"
            or url.username is not None or url.password is not None or url.query or url.fragment
            or any(
                not isinstance(transport.get(key), str)
                or not re.fullmatch(r"[0-9a-f]{40}", transport[key])
                for key in ("commit", "tree")
            )
        ):
            raise ValueError("The frozen Git transport must name its exact private repository")
        profile["settings"]["extraKnownMarketplaces"][catalog["name"]]["source"] = {
            "source": "git", "url": transport["url"],
        }
    return profile


def prepare_git_transport(
    artifact: Path, candidate_commit: str, *, environment: dict, project: Path,
    diagnostics: Path, deadline: float, run,
) -> dict:
    """Serialize frozen artifact bytes for a real copied Git install, not a release."""
    _PROVENANCE["verify"](artifact, candidate_commit)
    repository = project.parent / "suite-transport.git"
    if repository.exists() or repository.is_symlink():
        raise ValueError("Frozen Git transport requires a fresh fixture-owned repository")
    date = run(
        ["git", "show", "-s", "--format=%ct", candidate_commit],
        environment, REPO, diagnostics, "suite-source-date", timeout=30,
    )
    if not date.isdecimal():
        raise ValueError("The frozen source commit has no valid Git timestamp")
    run(
        ["git", "init", "--bare", "--initial-branch=main", repository],
        environment, project, diagnostics, "suite-transport-init", timeout=30,
    )
    stream = project.parent / "suite-transport.fi"
    paths = _PROVENANCE["content_manifest"](artifact)
    paths[_PROVENANCE["RECEIPT"]] = {"kind": "file", "executable": False}
    message = b"Frozen materialized fixture transport\n"
    with stream.open("xb") as output:
        output.write(
            b"commit refs/heads/main\n"
            + f"committer Suite Fixture <fixture@example.invalid> {date} +0000\n".encode("ascii")
            + f"data {len(message)}\n".encode("ascii") + message + b"\n",
        )
        for relative, row in sorted(paths.items()):
            if time.monotonic() >= deadline:
                raise TimeoutError("Frozen Git transport exceeded the existing case budget")
            if row["kind"] == "directory":
                continue
            if row["kind"] != "file":
                raise ValueError("Frozen Git transport requires regular artifact files")
            content = (artifact / relative).read_bytes()
            mode = "100755" if row["executable"] else "100644"
            output.write(
                f"M {mode} inline ".encode("ascii")
                + json.dumps(relative, ensure_ascii=False).encode("utf-8") + b"\n"
                + f"data {len(content)}\n".encode("ascii") + content + b"\n",
            )
        output.write(b"\ndone\n")
    program = (
        "import pathlib,subprocess,sys\n"
        "with pathlib.Path(sys.argv[2]).open('rb') as source:\n"
        " result=subprocess.run(['git','-c','gc.auto=0','-c','maintenance.auto=false',"
        "'--git-dir',sys.argv[1],'fast-import','--quiet'],"
        "stdin=source,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        "raise SystemExit(result.returncode)\n"
    )
    run(
        [sys.executable, "-I", "-B", "-c", program, repository, stream],
        environment, project, diagnostics, "suite-transport-import", timeout=120,
    )
    stream.unlink()
    commit, tree = run(
        ["git", "--git-dir", repository, "rev-parse", "HEAD", "HEAD^{tree}"],
        environment, project, diagnostics, "suite-transport-head", timeout=30,
    ).splitlines()
    _PROVENANCE["verify"](artifact, candidate_commit)
    return {
        "repository": str(repository),
        "commit": commit, "tree": tree,
    }


@contextmanager
def serve_git_transport(
    transport: dict, *, environment: dict, project: Path, diagnostics: Path,
    deadline: float, run,
):
    """Serve actual Git object data for the caller's bounded acquisition scope."""
    repository = Path(transport["repository"])
    prefix = "/suite-transport.git"
    failures = []
    requests = 0

    @contextmanager
    def request_directory():
        path = Path(tempfile.mkdtemp(prefix="git-http-", dir=repository.parent))
        # Preserve response data when backend custody or delivery fails.
        yield path
        shutil.rmtree(path)

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.git_request()

        def do_POST(self):
            self.git_request()

        def git_request(self):
            nonlocal requests
            location = urlsplit(self.path)
            suffix = location.path.removeprefix(prefix)
            if not (
                self.command == "GET" and (
                    suffix == "/HEAD" and not location.query
                    or suffix == "/info/refs" and location.query == "service=git-upload-pack"
                )
                or self.command == "POST" and suffix == "/git-upload-pack" and not location.query
            ) or not location.path.startswith(prefix + "/"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 1024 * 1024:
                    raise ValueError("Git transport request exceeds its input bound")
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Git transport request body is incomplete")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Git transport exceeded the existing case budget")
                requests += 1
                with request_directory() as raw:
                    source, target = Path(raw) / "request", Path(raw) / "response"
                    source.write_bytes(body)
                    env = {
                        **environment, "GIT_PROJECT_ROOT": str(repository.parent),
                        "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": "/" + repository.name + suffix,
                        "REQUEST_METHOD": self.command, "QUERY_STRING": location.query,
                        "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                        "CONTENT_LENGTH": str(length),
                        "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
                    }
                    program = (
                        "import pathlib,subprocess,sys\n"
                        "with pathlib.Path(sys.argv[1]).open('rb') as source, "
                        "pathlib.Path(sys.argv[2]).open('wb') as target:\n"
                        " result=subprocess.run(['git','http-backend'],stdin=source,stdout=target,"
                        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
                        "raise SystemExit(result.returncode)\n"
                    )
                    run(
                        [sys.executable, "-I", "-B", "-c", program, source, target],
                        env, project, diagnostics, f"suite-git-http-{requests}",
                        timeout=min(20, remaining),
                    )
                    with target.open("rb") as response:
                        headers, status = [], 200
                        for _ in range(32):
                            line = response.readline(8193)
                            if line in (b"\r\n", b"\n"):
                                break
                            if not line or len(line) > 8192:
                                raise ValueError("Git backend returned invalid response headers")
                            name, value = line.decode("latin1").rstrip("\r\n").split(":", 1)
                            if name.lower() == "status":
                                status = int(value.strip().split()[0])
                            else:
                                headers.append((name, value.strip()))
                        else:
                            raise ValueError("Git backend exceeded its response header bound")
                        self.send_response(status)
                        for name, value in headers:
                            self.send_header(name, value)
                        self.end_headers()
                        shutil.copyfileobj(response, self.wfile, 64 * 1024)
            except Exception as error:
                failures.append(error)
                self.close_connection = True

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    started = False
    try:
        thread.start()
        started = True
        url = f"http://127.0.0.1:{server.server_port}{prefix}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/HEAD", timeout=5) as response:
            if response.read(1024) != (repository / "HEAD").read_bytes():
                raise RuntimeError("The private Git transport did not serve its owned repository")
        yield {**transport, "url": url}
    finally:
        if started:
            server.shutdown()
        server.server_close()
        if started:
            thread.join(5)
        if thread.is_alive() or server.socket.fileno() != -1:
            raise RuntimeError("The private Git transport did not stop")
        if failures:
            raise failures[0]


def install_payload_closure(
    artifact: Path, candidate_commit: str, *, repository_config: dict,
    copilot: Path, engine: Path, environment: dict, project: Path,
    diagnostics: Path, transport: dict, run,
) -> dict:
    """Acquire every declared payload with the actual CLI, then verify its bytes."""
    from manager_suite_fixture import require_venue

    require_venue()
    if not Path(transport["repository"]).resolve().is_relative_to(project.parent.resolve()):
        raise ValueError("The frozen Git transport must belong to this private fixture")
    profile = selected_profile(
        artifact, candidate_commit, repository_config, transport=transport,
    )
    if repository_config.get("require_application_readiness") is not True:
        raise ValueError("Apply the required-readiness profile before suite acquisition")
    if not copilot.is_absolute() or not copilot.is_file():
        raise ValueError("Select the actual native Copilot executable")
    home = Path(environment["COPILOT_HOME"])
    if not home.is_absolute() or not home.is_relative_to(Path(environment["USERPROFILE"])):
        raise ValueError("Suite acquisition requires the fixture's private Copilot home")
    settings = json.loads(
        (project / ".github" / "copilot" / "settings.json").read_text(encoding="utf-8"),
    )
    if settings != profile["settings"]:
        raise ValueError("The representative harness does not enable the complete frozen suite")
    executable_hash = hashlib.sha256(copilot.read_bytes()).hexdigest()
    environment = {**environment, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    marketplace = next(iter(profile["settings"]["extraKnownMarketplaces"]))
    names = sorted(profile["inventory"])

    def verify_transport(phase):
        observed = run(
            ["git", "--git-dir", transport["repository"], "rev-parse", "HEAD", "HEAD^{tree}"],
            environment, project, diagnostics, "suite-transport-" + phase, timeout=30,
        )
        if observed.splitlines() != [transport["commit"], transport["tree"]]:
            raise ValueError("The frozen Git transport changed during acquisition")

    verify_transport("before")
    run(
        [copilot, "plugin", "marketplace", "add", transport["url"]],
        environment, project, diagnostics, "suite-marketplace-register", timeout=120,
    )
    for name in names:
        run(
            [copilot, "plugin", "install", name + "@" + marketplace],
            environment, project, diagnostics, "suite-payload-" + name, timeout=120,
        )
    run(
        [copilot, "plugin", "list", "--json"],
        environment, project, diagnostics, "suite-cli-inventory", timeout=30,
    )
    observed = json.loads(run(
        [
            engine, "-I", "-B", "-c",
            "import json;from agent_worktrees.reconcile import core_installed_payload_dir;"
            "print(json.dumps({name:str(core_installed_payload_dir(name) or '') "
            "for name in json.loads(__import__('sys').argv[1])}))",
            json.dumps(names),
        ],
        environment, project, diagnostics, "suite-selected-payloads", timeout=30,
    ))
    if not isinstance(observed, dict) or set(observed) != set(names):
        raise ValueError("Installed discovery returned an incomplete suite")
    closure = {}
    expected_tree = _PROVENANCE["content_manifest"](artifact / "plugins")
    installed_trees = {}
    for name in names:
        payload = Path(observed[name])
        if (
            payload.name != name or not payload.is_absolute()
            or not payload.resolve().is_relative_to(home.resolve())
        ):
            raise ValueError(
                f"{name}: selected payload is not installed in the private home "
                f"(selected={observed[name]!r}, home={str(home)!r})",
            )
        if payload.parent not in installed_trees:
            installed_trees[payload.parent] = _PROVENANCE["content_manifest"](payload.parent)
        prefix = name + "/"
        expected = {
            key[len(prefix):]: value for key, value in expected_tree.items()
            if key.startswith(prefix)
        }
        actual = {
            key[len(prefix):]: value for key, value in installed_trees[payload.parent].items()
            if key.startswith(prefix)
        }
        if actual != expected:
            raise ValueError(f"{name}: installed payload differs from the frozen candidate")
        closure[name] = {
            "path": str(payload), "version": profile["inventory"][name]["version"],
            "manifest_sha256": hashlib.sha256(
                json.dumps(actual, sort_keys=True).encode("utf-8"),
            ).hexdigest(),
        }
    _PROVENANCE["verify"](artifact, candidate_commit)
    verify_transport("after")
    if hashlib.sha256(copilot.read_bytes()).hexdigest() != executable_hash:
        raise ValueError("The native Copilot executable changed during acquisition")
    return {
        "candidate_commit": candidate_commit, "profile": profile,
        "copilot_sha256": executable_hash, "payloads": closure,
        "runtime_installation": "not-observed", "transport": transport,
    }
