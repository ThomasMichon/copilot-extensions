"""Qualification of the same contract through Python and a real Node import."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid


LIB = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "configuration_layers", LIB / "src" / "configuration_layers" / "__init__.py",
)
sdk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdk)
FIXTURES = json.loads((LIB / "tests" / "fixtures.json").read_text(encoding="utf-8"))


def node(requests):
    completed = subprocess.run(
        ["node", str(LIB / "tests" / "node_driver.mjs")],
        input=json.dumps(requests), text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return json.loads(completed.stdout)


def layer(identity, tier, origin, values, **fields):
    return {"id": identity, "tier": tier, "origin": origin, "values": values, **fields}


class CompositionTests(unittest.TestCase):
    def test_cross_language_order_and_replacement(self):
        requests = [{"context": FIXTURES["context"], "layers": case["layers"]}
                    for case in FIXTURES["cases"]]
        results = node(requests)
        for case, request, result in zip(FIXTURES["cases"], requests, results):
            with self.subTest(case=case["name"]):
                before = copy.deepcopy(request)
                composed = sdk.compose(**request)
                self.assertEqual(composed, result["value"])
                self.assertEqual(composed["values"], case["expected"])
                self.assertEqual(composed["layers"], case["ordered"])
                self.assertEqual(request, before)
        provenance = results[1]["value"]["provenance"]
        self.assertEqual(provenance["/map/retained"]["id"], "defaults")
        self.assertEqual(provenance["/map/changed"]["id"], "machine")
        self.assertEqual(provenance["/empty"]["id"], "defaults")
        self.assertEqual(provenance["/to_map"]["id"], "base")
        self.assertEqual(provenance["/null"]["id"], "base")
        self.assertNotIn("/null/old", provenance)
        self.assertNotIn("/a~1b~0c", provenance)
        self.assertEqual(provenance["/a~1b~0c/nested"]["id"], "base")

    def test_exact_identity_and_cell_are_not_names(self):
        case = FIXTURES["cases"][0]
        requests = []
        for field, value in [("repository_id", "repo-beta"), ("installation_cell", "cell-two"),
                             ("execution_machine", "worker-two")]:
            context = {**FIXTURES["context"], field: value}
            requests.append({"context": context, "layers": case["layers"]})
        results = node(requests)
        for request, result in zip(requests, results):
            self.assertEqual(sdk.compose(**request), result["value"])
        self.assertTrue(results[0]["value"]["values"]["wrong_repository"])
        self.assertNotIn("repository", results[0]["value"]["values"])
        self.assertTrue(results[1]["value"]["values"]["wrong_cell"])
        self.assertNotIn("exact", results[1]["value"]["layers"])
        self.assertTrue(results[2]["value"]["values"]["wrong_machine"])

    def test_unbound_and_explicit_binding_errors(self):
        context = copy.deepcopy(FIXTURES["context"])
        knowledge = layer("knowledge", "repository", "knowledge", {"private": True},
                          repository_id=context["repository_id"])
        requests = [{"context": context, "layers": [knowledge]},
                    {"context": {**context, "binding": "unresolved"}, "layers": []},
                    {"context": {**context, "binding": "bound"}, "layers": []},
                    {"context": {**context, "binding": "guess"}, "layers": []}]
        results = node(requests)
        self.assertEqual(results[0]["value"], sdk.compose(context, [knowledge]))
        self.assertEqual(results[0]["value"]["values"], {})
        for request, result in zip(requests[1:], results[1:]):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.compose(**request)
            self.assertTrue(result["error"])

    def test_malformed_and_conflicting_layers_never_fallback(self):
        context = FIXTURES["context"]
        base = layer("base", "repository", "base", {}, repository_id=context["repository_id"])
        invalid = [
            [base, {**base, "repository_id": "repo-beta"}],
            [{**base, "execution_machine": context["execution_machine"]}],
            [{**base, "repository_id": ""}],
            [{**base, "values": None}],
            [{**base, "origin": "local_machine"}],
            [{**base, "tier": "__proto__"}],
            [{**base, "source": {"root": "relative", "path": "a.json"}}],
            [{**base, "source": {"root": str(LIB), "path": "../a.json"}}],
            [{**base, "extra": True}],
        ]
        requests = [{"context": context, "layers": layers} for layers in invalid]
        for request, result in zip(requests, node(requests)):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.compose(**request)
            self.assertTrue(result["error"])

    def test_non_json_native_values_and_detached_results(self):
        invalid = [float("nan"), float("inf"), 10**1000, {1: "value"},
                   {"nested": (1, 2)}, object()]
        cyclic = {}; cyclic["self"] = cyclic
        invalid.append(cyclic)
        for value in invalid:
            with self.assertRaises(sdk.ConfigurationError):
                sdk.compose(FIXTURES["context"], [layer("d", "defaults", "defaults", {"v": value})])
        self.assertTrue(all(node([{"action": "native-invalid",
                                   "context": FIXTURES["context"]}])[0]["value"]))
        layers = [layer("d", "defaults", "defaults", {"__proto__": {"safe": [1]}})]
        result = sdk.compose(FIXTURES["context"], layers)
        self.assertEqual(result, node([{"context": FIXTURES["context"], "layers": layers}])[0]["value"])
        result["values"]["__proto__"]["safe"].append(2)
        self.assertEqual(layers[0]["values"]["__proto__"]["safe"], [1])


class SourceTests(unittest.TestCase):
    def setUp(self):
        # A task-owned workspace under this library, never a system temp directory.
        self.workspace = LIB / (".test-work-" + uuid.uuid4().hex)
        self.base = self.workspace / "base"
        self.knowledge = self.workspace / "knowledge"
        self.outside = self.workspace / "outside"
        for root in (self.base, self.knowledge, self.outside):
            (root / "config").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.workspace)

    def test_knowledge_same_tier_and_source_relative_ownership(self):
        (self.base / "config" / "prefs.json").write_text(
            '{"asset":"base.txt","retained":"base.txt","value":"base"}', encoding="utf-8")
        (self.knowledge / "config" / "prefs.json").write_text(
            '{"asset":"knowledge.txt","value":"knowledge"}', encoding="utf-8")
        context = {**FIXTURES["context"], "binding": "bound",
                   "base_root": str(self.base), "knowledge_root": str(self.knowledge)}
        base = sdk.read_source(str(self.base), "config/prefs.json")
        knowledge = sdk.read_source(str(self.knowledge), "config/prefs.json")
        layers = [
            layer("knowledge", "repository", "knowledge", **knowledge,
                  repository_id=context["repository_id"]),
            layer("machine", "machine", "local_machine", {"value": "machine"},
                  execution_machine=context["execution_machine"]),
            layer("base", "repository", "base", **base, repository_id=context["repository_id"]),
        ]
        result = sdk.compose(context, layers)
        self.assertEqual(result["layers"], ["base", "knowledge", "machine"])
        self.assertEqual(result["values"]["value"], "machine")
        self.assertEqual(sdk.resolve_path(result, "/asset"),
                         str(self.knowledge / "config" / "knowledge.txt"))
        self.assertEqual(sdk.resolve_path(result, "/retained"),
                         str(self.base / "config" / "base.txt"))
        requests = [{"context": context, "layers": layers},
                    {"action": "read", "root": str(self.knowledge), "path": "config/prefs.json"},
                    {"action": "path", "result": result, "pointer": "/asset"}]
        outputs = node(requests)
        self.assertEqual(outputs[0]["value"], result)
        self.assertEqual(outputs[1]["value"], knowledge)
        self.assertEqual(outputs[2]["value"], sdk.resolve_path(result, "/asset"))
        wrong = copy.deepcopy(layers)
        wrong[0]["source"]["root"] = str(self.base)
        with self.assertRaises(sdk.ConfigurationError):
            sdk.compose(context, wrong)
        self.assertTrue(node([{"context": context, "layers": wrong}])[0]["error"])
        with self.assertRaises(sdk.ConfigurationError):
            sdk.resolve_path(result, "/value")  # Machine values have no file owner.

    def test_optional_missing_and_present_invalid_sources(self):
        bad = {
            "list.json": b"[]", "broken.json": b"{",
            "duplicate.json": b'{"key":1,"\\u006bey":2}',
            "nested-duplicate.json": b'{"key":{"x":1,"x":2}}',
            "number.json": b'{"x":1e999}', "utf8.json": b'{"x":"\xff"}',
            "surrogate.json": b'{"x":"\\ud800"}', "bom.json": b'\xef\xbb\xbf{}',
        }
        for name, content in bad.items():
            (self.base / name).write_bytes(content)
        paths = ["missing.json", *bad, "config", "missing-required.json"]
        requests = [{"action": "read", "root": str(self.base), "path": name,
                     "optional": name != "missing-required.json"} for name in paths]
        outputs = node(requests)
        self.assertIsNone(sdk.read_source(str(self.base), "missing.json"))
        self.assertIsNone(outputs[0]["value"])
        for request, output in zip(requests[1:], outputs[1:]):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.read_source(request["root"], request["path"], optional=request["optional"])
            self.assertTrue(output["error"], request["path"])
        document = sdk.read_source(str(self.base), "list.json",
                                   parser=lambda text: {"native": json.loads(text)})
        self.assertEqual(document["values"], {"native": []})
        self.assertEqual(node([{"action": "read", "root": str(self.base),
                               "path": "list.json", "native_parser": True}])[0]["value"], document)
        with self.assertRaises(sdk.ConfigurationError):
            sdk.read_source(str(self.base), "list.json", parser=lambda _: {1: "not-json"})

    def test_winning_path_must_remain_source_owned(self):
        (self.base / "assets").mkdir()
        (self.base / "assets" / "file").write_text("asset", encoding="utf-8")
        (self.base / "config" / "prefs.json").write_text(
            '{"path":"../assets/file"}', encoding="utf-8")
        document = sdk.read_source(str(self.base), "config/prefs.json")
        result = sdk.compose(FIXTURES["context"], [
            layer("source", "repository", "base", **document,
                  repository_id=FIXTURES["context"]["repository_id"]),
        ])
        expected = str(self.base / "assets" / "file")
        self.assertEqual(sdk.resolve_path(result, "/path"), expected)
        self.assertEqual(node([{"action": "path", "result": result,
                               "pointer": "/path"}])[0]["value"], expected)
        requests = []
        for value in ["../../outside/file", "C:\\absolute", "../NUL",
                      "../assets/file:stream", "../assets/file\n"]:
            source_layer = layer("source", "repository", "base", {"path": value},
                                 repository_id=FIXTURES["context"]["repository_id"],
                                 source={"root": str(self.base), "path": "config/prefs.json"})
            result = sdk.compose(FIXTURES["context"], [source_layer])
            requests.append({"action": "path", "result": result, "pointer": "/path"})
        for request, output in zip(requests, node(requests)):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.resolve_path(request["result"], request["pointer"])
            self.assertTrue(output["error"])
        for value, expected in [(".", self.base / "config"), ("..", self.base),
                                ("../assets/./file", self.base / "assets" / "file")]:
            result["values"]["path"] = value
            self.assertEqual(sdk.resolve_path(result, "/path"), str(expected))
            self.assertEqual(node([{"action": "path", "result": result,
                                   "pointer": "/path"}])[0]["value"], str(expected))

    def test_symlink_loop_is_a_configuration_error(self):
        try:
            (self.base / "loop").symlink_to("loop", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"native symlink creation unavailable: {exc}")
        requests = [{"action": "read", "root": str(self.base), "path": p}
                    for p in ("loop", "loop/prefs.json")]
        for request, output in zip(requests, node(requests)):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.read_source(request["root"], request["path"])
            self.assertTrue(output["error"])
        result = sdk.compose(FIXTURES["context"], [
            layer("source", "repository", "base", {"path": "../loop/file"},
                  repository_id=FIXTURES["context"]["repository_id"],
                  source={"root": str(self.base), "path": "config/prefs.json"}),
        ])
        with self.assertRaises(sdk.ConfigurationError):
            sdk.resolve_path(result, "/path")
        self.assertTrue(node([{"action": "path", "result": result,
                               "pointer": "/path"}])[0]["error"])

    def test_unsafe_paths_and_symlink_escapes(self):
        unsafe = ["../outside/a.json", "config/../../outside/a.json",
                  "/absolute.json", "C:\\absolute.json", "config\\..\\a.json",
                  "file.json:stream", "config//a.json", "NUL", "a.json.", "a.json "]
        requests = [{"action": "read", "root": str(self.base), "path": p} for p in unsafe]
        for request, output in zip(requests, node(requests)):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.read_source(request["root"], request["path"])
            self.assertTrue(output["error"])
        try:
            (self.base / "escape").symlink_to(self.outside, target_is_directory=True)
            (self.base / "broken").symlink_to(self.outside / "absent", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"native symlink creation unavailable: {exc}")
        (self.outside / "secret.json").write_text('{"outside":true}', encoding="utf-8")
        paths = ["escape/secret.json", "escape/absent.json", "broken/nested.json", "broken"]
        outputs = node([{"action": "read", "root": str(self.base), "path": p} for p in paths])
        for p, output in zip(paths, outputs):
            with self.assertRaises(sdk.ConfigurationError):
                sdk.read_source(str(self.base), p)
            self.assertTrue(output["error"], p)
        result = sdk.compose(FIXTURES["context"], [
            layer("linked-asset", "repository", "base", {"asset": "../escape/secret.json"},
                  repository_id=FIXTURES["context"]["repository_id"],
                  source={"root": str(self.base), "path": "config/prefs.json"}),
        ])
        with self.assertRaises(sdk.ConfigurationError):
            sdk.resolve_path(result, "/asset")
        self.assertTrue(node([{"action": "path", "result": result,
                               "pointer": "/asset"}])[0]["error"])


if __name__ == "__main__":
    unittest.main()
