import importlib.util
from pathlib import Path
import shutil
import unittest
import uuid


SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gateway_liveness", SERVICE / "gateway_liveness.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class LivenessTests(unittest.TestCase):
    def setUp(self):
        self.root = SERVICE / "tests" / ("_liveness-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.path = self.root / "sshd_config"
        self.original = b"# existing provider config\nPort 22\n"
        self.path.write_bytes(self.original)
        self.reloads = 0
        self.reject = None

        def runner(argv):
            if argv == ["reload"]:
                self.reloads += 1
                if self.reject == "reload" and self.reloads == 1:
                    raise policy.PolicyError("reload failed")
                return ""
            candidate = Path(argv[argv.index("-f") + 1])
            data = candidate.read_bytes()
            if "-t" in argv:
                if self.reject == "syntax" and policy.BEGIN in data:
                    raise policy.PolicyError("bad candidate")
                return ""
            user = argv[-1].split(",", 1)[0].split("=", 1)[1]
            managed = policy.BEGIN in data and user == "relayuser"
            interval = 15 if managed else 0
            port = 23 if managed and self.reject == "collateral" else 22
            if self.reject == "other-user" and policy.BEGIN in data and not managed:
                interval = 15
            return (f"port {port}\nclientaliveinterval {interval}\nclientalivecountmax 3\n"
                    "acceptenv LANG\nacceptenv LC_*\n")

        self.owner = policy.Policy(self.path, "sshd", ["reload"], runner)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_install_idempotence_and_byte_identical_removal(self):
        self.assertTrue(self.owner.apply("install", "relayuser")["configured"])
        self.assertIn(b"Match User relayuser", self.path.read_bytes())
        self.assertFalse(self.owner.apply("update", "relayuser")["changed"])
        self.assertFalse(self.owner.apply("uninstall", "relayuser")["configured"])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_no_final_newline_round_trips(self):
        self.path.write_bytes(b"Port 22")
        self.owner.apply("install", "relayuser")
        self.owner.apply("uninstall", "relayuser")
        self.assertEqual(self.path.read_bytes(), b"Port 22")

    def test_syntax_and_collateral_changes_refuse_before_write_or_reload(self):
        for reason in ("syntax", "collateral", "other-user"):
            with self.subTest(reason=reason):
                self.reject = reason
                with self.assertRaises(policy.PolicyError):
                    self.owner.apply("install", "relayuser")
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual(self.reloads, 0)

    def test_reload_failure_restores_and_reloads_predecessor(self):
        self.reject = "reload"
        with self.assertRaisesRegex(policy.PolicyError, "restored"):
            self.owner.apply("install", "relayuser")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.reloads, 2)

    def test_foreign_marker_and_different_user_refuse(self):
        self.path.write_bytes(self.original + policy.BEGIN + b"foreign")
        with self.assertRaises(policy.PolicyError):
            self.owner.apply("install", "relayuser")
        self.path.write_bytes(self.original)
        self.owner.apply("install", "relayuser")
        with self.assertRaises(policy.PolicyError):
            self.owner.apply("update", "different")

    def test_unsafe_username_and_linked_configuration_refuse(self):
        for user in ("root", "a\nMatch all", "*", "", "-bad"):
            with self.subTest(user=user), self.assertRaises(policy.PolicyError):
                self.owner.apply("install", user)
        linked = self.root / "hardlink"
        linked.hardlink_to(self.path)
        with self.assertRaises(policy.PolicyError):
            self.owner.apply("install", "relayuser")

    def test_status_is_read_only_and_does_not_claim_running_adoption(self):
        value = self.owner.apply("status", "relayuser")
        self.assertFalse(value["configured"])
        self.assertEqual(value["running_policy"], "not-probed")
        self.assertEqual(self.reloads, 0)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_effective_repeated_list_directives_are_preserved(self):
        value = self.owner.apply("install", "relayuser")
        self.assertEqual(value["effective"]["acceptenv"], "LANG\nLC_*")
