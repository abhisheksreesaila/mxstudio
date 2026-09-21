import pathlib
import subprocess
import unittest
import tempfile
from unittest.mock import MagicMock, patch

import yaml
from deploy.nucleus.preflight import read_environment, validate, probe


ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/nucleus/install.sh"


class NucleusDeploymentTests(unittest.TestCase):
    def test_default_is_read_only_dry_run(self):
        result = subprocess.run(
            [str(SCRIPT), "--with-caddy", "--with-gateway"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("no files", result.stdout)

    def test_default_and_bad_options_never_install(self):
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, check=True)
        self.assertIn("DRY RUN", result.stdout)
        result = subprocess.run([str(SCRIPT), "--unknown"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_environment_validation_is_literal_and_requires_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "nucleus.env"
            path.write_text('MXSTUDIO_NUCLEUS_API_KEY="test-only"\nNUCLEUS_MODEL_ID=example/a\n')
            path.chmod(0o640)
            values = read_environment(path)
            validate(values)
            with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
                validate(values, gateway=True)
            path.write_text("MXSTUDIO_NUCLEUS_API_KEY=\nNUCLEUS_MODEL_ID=example/a\n")
            with self.assertRaisesRegex(ValueError, "MXSTUDIO_NUCLEUS_API_KEY"):
                validate(read_environment(path))
            path.write_text("MXSTUDIO_NUCLEUS_API_KEY=one\nMXSTUDIO_NUCLEUS_API_KEY=two\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_environment(path)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "other users"):
                read_environment(path)

    def test_responsiveness_probe_authenticates_to_selected_loopback_api(self):
        values = {"MXSTUDIO_NUCLEUS_API_KEY": "controller-test", "LITELLM_MASTER_KEY": "gateway-test"}
        for gateway, port, key in ((False, 8080, "controller-test"), (True, 4000, "gateway-test")):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.status = 200
            with patch("deploy.nucleus.preflight.request.build_opener", return_value=opener):
                probe(values, gateway)
            req = opener.open.call_args.args[0]
            self.assertEqual(req.full_url, f"http://127.0.0.1:{port}/v1/models")
            self.assertEqual(req.get_header("Authorization"), f"Bearer {key}")

    def test_gateway_fallback_is_explicit_and_never_on_local_alias(self):
        local = yaml.safe_load((ROOT / "deploy/nucleus/litellm_config.yaml").read_text())
        self.assertEqual([row["model_name"] for row in local["model_list"]], ["nucleus-local"])
        self.assertNotIn("router_settings", local)
        self.assertTrue(local["general_settings"]["disable_spend_logs"])
        cloud = yaml.safe_load((ROOT / "deploy/nucleus/litellm_config.cloud.example.yaml").read_text())
        self.assertEqual(cloud["router_settings"]["fallbacks"], [{"hybrid-local": ["cloud-gpt-4o"]}])
        self.assertIn("[proxy]", (ROOT / "deploy/nucleus/litellm-requirements.txt").read_text())

    def test_caddy_preserves_upstream_cancellation(self):
        for name in ("Caddyfile", "Caddyfile.gateway"):
            with self.subTest(template=name):
                config = (ROOT / "deploy/nucleus" / name).read_text()
                self.assertNotRegex(config, r"flush_interval\s+-")

    def test_script_has_no_curl_installer(self):
        self.assertNotIn("curl", SCRIPT.read_text())

    def test_unit_templates_are_non_root_and_loopback(self):
        service = (ROOT / "deploy/nucleus/mxstudio-nucleus.service.in").read_text()
        self.assertIn("User=@NUCLEUS_USER@", service)
        self.assertIn("MAX_SERVE_HOST=127.0.0.1", service)
        self.assertIn("nucleus run --host 127.0.0.1 --port 8080 --workers 1", service)
        caddy = (ROOT / "deploy/nucleus/Caddyfile").read_text()
        self.assertIn("path /v1/models", caddy)
        self.assertNotIn("path /v1/*", caddy)
