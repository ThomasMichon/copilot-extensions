import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import credential_push
import producer_push


class ProducerContractTests(unittest.TestCase):
    def test_cli_token_provider_keeps_its_bounded_headless_contract(self):
        result = SimpleNamespace(stdout=json.dumps({
            "accessToken": "synthetic-token", "expires_on": 2000000000,
        }))
        for provider, arguments in (
            (credential_push._az_get_access_token, ("https://resource.example.invalid",)),
            (producer_push._mint_token, ()),
        ):
            with self.subTest(provider=provider.__name__), patch(
                "subprocess.run", return_value=result,
            ) as run:
                token, expiry = provider(*arguments)
                self.assertEqual(token, "synthetic-token")
                self.assertEqual(expiry, 2000000000)
                self.assertEqual(run.call_args.kwargs["timeout"], 30)
                self.assertEqual(
                    run.call_args.kwargs["creationflags"],
                    getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

    def test_real_push_and_verify_construct_unmodified_authorization(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "token": "synthetic-resource"}
        scheme = "Bear" + "er "
        with patch.object(credential_push.requests, "post", return_value=response) as post:
            self.assertTrue(credential_push._push(
                "https://resource.example.invalid", "", "synthetic-resource",
                2000000000, "synthetic-gateway",
            )["ok"])
            self.assertEqual(
                post.call_args.kwargs["headers"]["Authorization"],
                scheme + "synthetic-gateway",
            )
            self.assertTrue(credential_push._verify(
                "https://resource.example.invalid", "", "synthetic-resource",
                "synthetic-gateway",
            ))
            self.assertEqual(
                post.call_args.kwargs["headers"]["Authorization"],
                scheme + "synthetic-gateway",
            )


if __name__ == "__main__":
    unittest.main()
