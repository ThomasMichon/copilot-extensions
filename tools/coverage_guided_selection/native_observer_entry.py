"""Pre-user-code line-entry proof; uses the existing sparse tracer, not a new parser."""
import os
import time

_phases = []


def phase(name):
    if os.environ.get("CGS_PRELUDE_PHASE_PROFILE") == "1":
        if len(_phases) >= 64:
            raise RuntimeError("native prelude phase bound exceeded")
        _phases.append({"phase": name, "wall_ns": time.perf_counter_ns(),
                        "cpu_ns": time.process_time_ns()})


phase("native-entry-first")
import hashlib
phase("native-entry-hashlib-ready")
import sys
from tools.coverage_guided_selection.measurement_launch import ChildJSON

json = ChildJSON()
phase("native-entry-json-ready")
import marshal
from types import ModuleType

phase("native-entry-imports-ready")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


with open("/proc/self/status") as status_file:
    posture = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in status_file
               if line.startswith(("CapEff:", "CapBnd:", "NoNewPrivs:"))}
require(os.getuid() == 1000 and int(posture["CapEff"], 16) == int(posture["CapBnd"], 16) == 0,
        "native entry worker identity or capabilities differ")
require(posture["NoNewPrivs"] == "1", "native entry requires no-new-privileges")
for path, expected, message in (
    ("/sys/fs/cgroup/cpu.max", "200000 100000", "native entry CPU limit differs"),
    ("/sys/fs/cgroup/memory.max", "6442450944", "native entry memory limit differs"),
    ("/sys/fs/cgroup/pids.max", "256", "native entry process limit differs"),
):
    with open(path) as file:
        require(file.read().strip() == expected, message)
control = os.environ["CGS_NATIVE_CONTROL"]
with open(os.path.join(control, "catalog.json")) as file:
    catalog = json.loads(file.read())
for name in ("_cgs_child_audit", "_cgs_child_stream"):
    path = catalog["helpers"][name]["path"]
    with open(path, "rb") as file:
        content = file.read()
    require(hashlib.sha256(content).hexdigest() == catalog["helpers"][name]["sha256"],
            "native entry helper source identity differs")
    module = ModuleType(name)
    module.__dict__.update(__file__=path, __package__="", __spec__=None,
                           __loader__=None, __cached__=None)
    sys.modules[name] = module
    exec(compile(content, path, "exec", dont_inherit=True), module.__dict__, module.__dict__)
    phase("native-entry-helper-" + name)

stream = sys.modules["_cgs_child_stream"]
code_tree = stream._code_tree
phase("native-entry-helpers-ready")
auditor = sys.modules["_cgs_child_audit"]
os.environ["_CGS_CHILD_MANIFEST"] = catalog["manifest"]
os.environ["_CGS_CHILD_AUDITOR"] = str(os.getppid())
compile_receipts = []
generated_code_refs = {}
generated_ids = {}
source_events = os.path.join(control, "compile", "source-events-" + str(os.getpid()) + ".jsonl")
source_event_fd = os.open(source_events, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def code_digest(code):
    return hashlib.sha256(marshal.dumps(code)).hexdigest()


def origin_identity(code, offset):
    result = {"code_sha256": code_digest(code), "code_name": code.co_name,
              "instruction_offset": offset, "file": os.path.basename(code.co_filename),
              "source_bound": False}
    try:
        with open(code.co_filename, "rb") as source:
            content = source.read(1_048_577)
        if len(content) > 1_048_576:
            return result
        reference = compile(content, code.co_filename, "exec", dont_inherit=True,
                            optimize=sys.flags.optimize)
        result.update(source_sha256=hashlib.sha256(content).hexdigest(),
                      source_bound=any(candidate == code for candidate in code_tree(reference)))
    except (OSError, ValueError, SyntaxError):
        pass
    return result


def native_source_event(event, args, origin, offset):
    """Diagnostic-only native emission context; this does not admit generated code."""
    if event == "compile":
        source, filename = args
        if not isinstance(source, (bytes, str)):
            return
        content = source.encode("utf-8", "surrogatepass") if isinstance(source, str) else source
        if len(content) > 65_536:
            return
        with stream._generated_lock:
            if len(compile_receipts) < 512:
                compile_receipts.append((content, filename, origin, offset))
        return
    code = args[0]
    with stream._generated_lock:
        receipts = tuple(compile_receipts)
    for content, filename, creator, creator_offset in reversed(receipts):
        if filename != code.co_filename or creator is not origin:
            continue
        try:
            # Observed compiler flags are explicit; no caller-derived ambient inheritance.
            reference = compile(content, filename, "exec", flags=code.co_flags,
                                dont_inherit=True, optimize=sys.flags.optimize)
        except (ValueError, SyntaxError):
            continue
        if reference != code:
            continue
        record = {
            "schema": "ACTUAL-COMPILE-EXEC/1", "pid": os.getpid(),
            "compile_source_sha256": hashlib.sha256(content).hexdigest(),
            "compile_source_utf8": content.decode("utf-8", "surrogatepass"),
            "filename": filename, "executing_code_sha256": code_digest(code),
            "actual_compile_exec_code_equal": True,
            "same_native_emitting_code_object": True,
            "creator": origin_identity(origin, creator_offset),
            "exec_instruction_offset": offset,
            "generated_source_admitted": False,
        }
        if record["creator"]["source_bound"] and record["creator"].get("source_sha256") in catalog["origin_source_sha256"]:
            digest = record["compile_source_sha256"]
            identifier = 1 + int(digest[:4], 16) % 65534
            reserved = {row["id"] for row in catalog["sources"].values()}
            target = os.path.join(control, "generated", digest + ".py")
            temporary = os.path.join(
                control, "generated", f".publish-{os.getpid()}-{os.urandom(16).hex()}",
            )
            created = False
            try:
                with open(temporary, "xb") as output:
                    created = True
                    output.write(content)
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    with open(target, "rb") as existing:
                        same = existing.read() == content
                    if not same:
                        auditor.error("actual generated source changed")
                        record["generated_refusal"] = "source changed"
            finally:
                if created:
                    os.unlink(temporary)
            if "generated_refusal" not in record:
                actual_codes = tuple(code_tree(code))
                with stream._generated_lock:
                    collision = identifier in reserved or (
                        identifier in generated_ids and generated_ids[identifier] != digest
                    )
                    new_codes = [value for value in actual_codes if id(value) not in generated_code_refs]
                    full = len(generated_code_refs) + len(new_codes) > 4096
                    if not collision and not full:
                        generated_ids[identifier] = digest
                        for actual_code in actual_codes:
                            generated_code_refs[id(actual_code)] = actual_code
                            stream._code_sources[id(actual_code)] = (
                                actual_code, (identifier, len(content.splitlines())),
                            )
                        record.update(generated_source_admitted=True, source_id=identifier)
                if collision or full:
                    auditor.error("actual generated-source identifier collision" if collision else
                                  "actual generated code-object bound exceeded")
                    record["generated_refusal"] = "identifier collision" if collision else "code-object bound"
        os.write(source_event_fd, (json.dumps(record) + "\n").encode("utf-8"))
        break


def source_check(event, args):
    if event != "exec":
        return
    code = args[0]
    filename = code.co_filename
    normalized = filename.replace("\\", "/")
    if "/fleet_manager/" not in normalized and not normalized.startswith("fleet_manager/"):
        return
    relative = normalized if normalized.startswith("fleet_manager/") else (
        "fleet_manager/" + normalized.split("/fleet_manager/", 1)[1])
    expected = catalog["sources"].get(relative)
    try:
        key = os.path.normcase(filename)
        if expected is None or stream._sources.get(key) != (expected["id"], expected["lines"]):
            raise ValueError("SDK executable path is outside the verified source inventory")
        with open(filename, "rb") as file:
            content = file.read()
        if hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise ValueError("SDK source bytes do not match canonical catalog")
        actual = compile(content, filename, "exec", dont_inherit=True, optimize=sys.flags.optimize)
        if actual != code:
            raise ValueError("SDK executable code differs from canonical source bytes")
        actual_codes = tuple(code_tree(code))
        with stream._generated_lock:
            stream._expected_codes[key] = frozenset(actual_codes)
            for actual_code in actual_codes:
                stream._code_sources[id(actual_code)] = (
                    actual_code, (expected["id"], expected["lines"]),
                )
    except Exception as error:
        with stream._generated_lock:
            stream._expected_codes.pop(os.path.normcase(filename), None)
            for actual_code in code_tree(code):
                stream._code_sources[id(actual_code)] = (actual_code, None)
        auditor.error("native SDK code/source identity refused: " + type(error).__name__)


def report_prelude(control, *, start_ns, end_ns, end_cpu_ns, duration_us):
    report = {
        "schema": "CGS-NATIVE-PRELUDE-PHASES/1", "diagnostic_only": True,
        "start_ns": start_ns, "end_ns": end_ns, "end_cpu_ns": end_cpu_ns,
        "start_cpu_ns": int(os.environ["_CGS_PRELUDE_CPU_NS"]),
        "duration_us": duration_us, "phases": _phases,
    }
    with open(os.path.join(control, "compile", f"prelude-phases-{os.getpid()}.json"), "x") as output:
        json.dump(report, output, sort_keys=True)


sys.addaudithook(source_check)
original_call = stream._call


def scoped_call(frame, event, arg):
    name = frame.f_globals.get("__name__", "")
    if isinstance(name, str) and (name == "fleet_manager" or name.startswith("fleet_manager.")):
        if id(frame.f_code) not in stream._code_sources and frame.f_code.co_filename not in stream._sources:
            auditor.error("SDK frame lacks actual verified source identity: " +
                          os.path.basename(frame.f_code.co_filename) + "::" + frame.f_code.co_name)
            return None
    return original_call(frame, event, arg)


stream._call = scoped_call
if os.environ.get("CGS_PRELUDE_PHASE_PROFILE") == "1":
    stream._phase_marker, stream._phase_reporter = phase, report_prelude
phase("native-entry-guards-ready")
stream.start(str(control), compile_sources=False)
