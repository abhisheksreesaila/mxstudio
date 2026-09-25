import json
import pathlib
import subprocess
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import MagicMock, patch

import yaml
from deploy.nucleus.preflight import (
    check_ports,
    caddy_is_distro_managed,
    installation_plan,
    inspect_installation,
    verify_installed_identity,
    read_environment,
    read_os_release,
    validate,
    probe,
)


ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "deploy/nucleus/install.sh"


class NucleusDeploymentTests(unittest.TestCase):
    def scratch_file(self):
        path = ROOT / "tests" / f".nucleus-test-{uuid.uuid4().hex}"
        with path.open("x", encoding="utf-8"):
            pass
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_default_is_read_only_dry_run_with_a_concrete_plan(self):
        result = subprocess.run(
            [str(SCRIPT), "--with-caddy", "--with-gateway"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("no files", result.stdout)
        plan = json.loads(result.stdout.split("plan=", 1)[1].splitlines()[0])
        self.assertTrue(plan["supported"])
        self.assertTrue(plan["caddy_available"] or "caddy" in plan["packages"])
        self.assertIn("mxstudio-nucleus-caddy.service", plan["services"])
        self.assertIn("/etc/mxstudio/Caddyfile", " ".join(plan["files"]))
        self.assertIn("approval boundary", result.stdout)

    def test_default_and_bad_options_never_install(self):
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, check=True)
        self.assertIn("DRY RUN", result.stdout)
        result = subprocess.run([str(SCRIPT), "--unknown"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        conflict = subprocess.run([str(SCRIPT), "--dry-run", "--apply"], capture_output=True, text=True)
        self.assertEqual(conflict.returncode, 2)
        help_result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, check=True)
        self.assertIn("--apply", help_result.stdout)
        self.assertIn("--install", help_result.stdout)
        self.assertIn("--env-file", help_result.stdout)
        self.assertIn("--service-user-current", help_result.stdout)

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_os_release_is_parsed_without_shell_execution(self):
        path = self.scratch_file()
        path.write_text('ID="omarchy"\nID_LIKE="arch"\n')
        self.assertEqual(read_os_release(path), {"ID": "omarchy", "ID_LIKE": "arch"})

    def test_distribution_install_plans_are_named_and_arch_never_upgrades(self):
        debian = installation_plan("ubuntu", "x86_64", with_caddy=True, with_gateway=True)
        arch = installation_plan("omarchy", "x86_64", with_caddy=True, with_gateway=True)
        unsupported = installation_plan("fedora", "x86_64", with_caddy=True)
        self.assertTrue(debian["supported"])
        self.assertIn("python3-venv", debian["packages"])
        self.assertEqual(debian["package_command"][:2], ["apt-get", "install"])
        self.assertTrue(arch["supported"])
        self.assertIn("pacman", arch["package_command"])
        self.assertIn("--needed", arch["package_command"])
        self.assertIn("--noconfirm", arch["package_command"])
        self.assertNotIn("--refresh", arch["package_command"])
        self.assertNotIn("--sysupgrade", arch["package_command"])
        self.assertIn("zero pending pacman upgrades", arch["arch_upgrade_policy"])
        self.assertFalse(unsupported["supported"])
        empty_plan = installation_plan("debian", "x86_64")
        self.assertEqual(empty_plan["packages"], [])
        self.assertEqual(empty_plan["package_command"], [])
        managed_caddy = installation_plan("debian", "x86_64", with_caddy=True, caddy_available=True)
        self.assertNotIn("caddy", managed_caddy["packages"])
        self.assertTrue(managed_caddy["caddy_available"])
        self.assertEqual(unsupported["packages"], [])
        custom_env = installation_plan("debian", "x86_64", env_file="/etc/mxstudio/run-identity.env")
        self.assertIn("/etc/mxstudio/run-identity.env", " ".join(custom_env["files"]))

    def test_caddy_is_only_reused_when_owned_by_the_supported_package_manager(self):
        result = SimpleNamespace(returncode=0, stdout="caddy: /usr/bin/caddy\n")
        with patch("deploy.nucleus.preflight.Path.is_file", return_value=True), patch(
            "deploy.nucleus.preflight.subprocess.run", return_value=result
        ) as run:
            self.assertTrue(caddy_is_distro_managed("debian"))
        self.assertEqual(run.call_args.args[0], ["dpkg-query", "--search", "/usr/bin/caddy"])
        result.stdout = "other-caddy: /usr/bin/caddy\n"
        with patch("deploy.nucleus.preflight.Path.is_file", return_value=True), patch(
            "deploy.nucleus.preflight.subprocess.run", return_value=result
        ):
            self.assertFalse(caddy_is_distro_managed("debian"))

    def test_arch_upgrade_guard_distinguishes_empty_query_from_errors(self):
        script = SCRIPT.read_text()
        start = script.index("check_arch_upgrade_state() {")
        end = script.index("\n}\n", start) + 3
        function = script[start:end]
        cases = (
            ("1", "", "", 0, ""),
            ("0", "", "", 0, ""),
            ("0", "oldpkg 1 -> 2\n", "", 1, "Pending Arch updates"),
            ("1", "", "error: database lock busy\n", 1, "Could not inspect"),
            ("2", "", "", 1, "Could not inspect"),
        )
        for status, stdout, stderr, expected_status, diagnostic in cases:
            with self.subTest(status=status, stdout=stdout, stderr=stderr):
                mocked_pacman = '''pacman() {
                    printf '%s' "$MOCK_PACMAN_STDOUT"
                    printf '%s' "$MOCK_PACMAN_STDERR" >&2
                    return "$MOCK_PACMAN_STATUS"
                }
                '''
                result = subprocess.run(
                    ["bash", "-c", mocked_pacman + function + "\ncheck_arch_upgrade_state"],
                    env={
                        **__import__("os").environ,
                        "MOCK_PACMAN_STATUS": status,
                        "MOCK_PACMAN_STDOUT": stdout,
                        "MOCK_PACMAN_STDERR": stderr,
                    },
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, expected_status)
                self.assertIn(diagnostic, result.stderr)

    def test_script_uses_no_arch_refresh_or_system_upgrade(self):
        script = SCRIPT.read_text()
        self.assertIn("pacman --sync --needed --noconfirm", script)
        self.assertIn("pacman --query --upgrades", script)
        self.assertNotIn("pacman -Sy", script)
        self.assertNotIn("pacman -Syu", script)
        self.assertNotIn("apt-get update", script)

    def test_environment_validation_is_literal_and_requires_credentials(self):
        path = self.scratch_file()
        try:
            path.write_text(
                'MXSTUDIO_NUCLEUS_API_KEY="test-only"\n'
                'NUCLEUS_MODEL_ID=example/a\n'
                f'NUCLEUS_MODEL_REVISION={"a" * 40}\n'
                'MXSTUDIO_HOME=/var/lib/mxstudio\n'
                'HF_HOME=/var/cache/mxstudio/huggingface\n'
            )
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
        finally:
            path.unlink(missing_ok=True)

    def test_environment_rejects_invalid_hostname_and_misaligned_cache(self):
        values = {
            "MXSTUDIO_NUCLEUS_API_KEY": "test",
            "NUCLEUS_MODEL_ID": "example/a",
            "NUCLEUS_MODEL_REVISION": "a" * 40,
            "MXSTUDIO_HOME": "/var/lib/mxstudio",
            "HF_HOME": "/var/cache/mxstudio/huggingface",
        }
        cases = (
            ("NUCLEUS_HOSTNAME", "bad;respond 200", "NUCLEUS_HOSTNAME"),
            ("NUCLEUS_HOSTNAME", "api.-invalid.local", "NUCLEUS_HOSTNAME"),
            ("HF_HOME", "relative/cache", "HF_HOME"),
            ("NUCLEUS_MODEL_REVISION", "main", "NUCLEUS_MODEL_REVISION"),
            ("NUCLEUS_MODEL_ID", "local-alias", "NUCLEUS_MODEL_ID"),
        )
        for key, override, message in cases:
            config = {**values, "NUCLEUS_HOSTNAME": "api.mxstudio.local", "HF_HOME": "/var/cache/mxstudio/huggingface", key: override}
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, message):
                validate(config)

    def test_installation_identity_reports_pinned_model_and_owned_paths_without_secrets(self):
        path = self.scratch_file()
        revision = "b" * 40
        path.write_text(
            "MXSTUDIO_NUCLEUS_API_KEY=not-reported\n"
            "NUCLEUS_MODEL_ID=example/model\n"
            f"NUCLEUS_MODEL_REVISION={revision}\n"
            f"MXSTUDIO_HOME={ROOT}\n"
            f"HF_HOME={ROOT}\n"
        )
        path.chmod(0o640)
        owner_uid = ROOT.stat().st_uid
        account = SimpleNamespace(pw_uid=owner_uid)
        with patch("deploy.nucleus.preflight.pwd.getpwnam", return_value=account), patch(
            "deploy.nucleus.preflight.os.geteuid", return_value=owner_uid
        ), patch("deploy.nucleus.preflight.os.access", return_value=True):
            inspected = inspect_installation(path, "mxstudio-test")
        self.assertEqual(inspected["service_user"], "mxstudio-test")
        self.assertEqual(inspected["mxstudio_home"], str(ROOT))
        self.assertEqual(inspected["hf_home"], str(ROOT))
        self.assertEqual(inspected["nucleus_model_revision"], revision)
        self.assertNotIn("api_key", inspected)

    def test_installed_identity_compares_systemd_user_group_and_environment_file(self):
        values = {
            "service_user": "operator",
            "mxstudio_home": "/home/operator/.mxstudio",
            "hf_home": "/home/operator/.cache/huggingface",
            "nucleus_model_id": "example/model",
            "nucleus_model_revision": "c" * 40,
        }
        outputs = iter((
            "/etc/systemd/system/mxstudio-nucleus.service\n",
            "operator\n",
            "operator\n",
            "{/etc/mxstudio/identity.env (ignore_errors=no)}\n",
            "active\n",
        ))
        completed = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=next(outputs))
        with patch("deploy.nucleus.preflight.validate_env_file"), patch(
            "deploy.nucleus.preflight.validate_private_group"
        ), patch("deploy.nucleus.preflight.inspect_installation", return_value=values), patch(
            "deploy.nucleus.preflight.subprocess.run", side_effect=completed
        ):
            installed = verify_installed_identity(pathlib.Path("/etc/mxstudio/identity.env"), "operator", "operator")
        self.assertEqual(installed["unit_user"], "operator")
        self.assertEqual(installed["unit_group"], "operator")
        self.assertEqual(installed["nucleus_model_revision"], "c" * 40)
        self.assertEqual(installed["unit_state"], "active")

    def test_current_user_group_must_be_private_for_environment_file_access(self):
        group = SimpleNamespace(gr_gid=1234, gr_mem=[])
        members = [SimpleNamespace(pw_name="operator", pw_gid=1234)]
        with patch("deploy.nucleus.preflight.grp.getgrnam", return_value=group), patch(
            "deploy.nucleus.preflight.pwd.getpwall", return_value=members
        ):
            from deploy.nucleus.preflight import validate_private_group
            validate_private_group("operator", "operator")
        with patch("deploy.nucleus.preflight.grp.getgrnam", return_value=group), patch(
            "deploy.nucleus.preflight.pwd.getpwall", return_value=members + [SimpleNamespace(pw_name="other", pw_gid=1234)]
        ):
            from deploy.nucleus.preflight import validate_private_group
            with self.assertRaisesRegex(ValueError, "shared"):
                validate_private_group("operator", "operator")

    def test_port_preflight_checks_both_address_families(self):
        fake_socket = MagicMock()
        with patch("deploy.nucleus.preflight.socket.socket", return_value=fake_socket) as create_socket:
            check_ports([8080])
        self.assertEqual(create_socket.call_count, 2)
        self.assertEqual(fake_socket.bind.call_count, 2)
        fake_socket.close.assert_called()

    def test_port_preflight_rejects_a_conflict(self):
        fake_socket = MagicMock()
        fake_socket.bind.side_effect = OSError("in use")
        with patch("deploy.nucleus.preflight.socket.socket", return_value=fake_socket):
            with self.assertRaisesRegex(ValueError, "port 443"):
                check_ports([443])

    def test_responsiveness_probe_authenticates_to_selected_loopback_api(self):
        values = {"MXSTUDIO_NUCLEUS_API_KEY": "controller-test", "LITELLM_MASTER_KEY": "gateway-test"}
        for gateway, port, key in ((False, 8080, "controller-test"), (True, 4000, "gateway-test")):
            opener = MagicMock()
            opener.open.return_value.__enter__.return_value.status = 200
            with patch("deploy.nucleus.preflight.request.build_opener", return_value=opener):
                probe(values, gateway)
            req = opener.open.call_args.args[0]
            self.assertEqual(req.full_url, f"http://127.0.0.1:{port}/v1/models")
            self.assertEqual(req.get_header("Authorization"), "Bearer " + key)

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
            with self.subTest(name=name):
                config = (ROOT / "deploy/nucleus" / name).read_text()
                self.assertNotRegex(config, r"flush_interval\s+-")

    def test_script_has_no_curl_installer_or_temporary_directory(self):
        script = SCRIPT.read_text()
        self.assertNotIn("curl", script)
        self.assertNotIn("mktemp", script)

    def test_caddy_is_validated_before_activation_and_existing_resources_are_preserved(self):
        script = SCRIPT.read_text()
        self.assertLess(script.index("caddy validate"), script.index("activate mxstudio-nucleus-caddy.service"))
        self.assertIn("Refusing to overwrite existing", script)
        self.assertIn("manual", script)

    def test_unit_templates_are_non_root_and_loopback(self):
        service = (ROOT / "deploy/nucleus/mxstudio-nucleus.service.in").read_text()
        self.assertIn("User=@NUCLEUS_USER@", service)
        self.assertIn("EnvironmentFile=@NUCLEUS_ENV_FILE@", service)
        self.assertIn("MAX_SERVE_HOST=127.0.0.1", service)
        self.assertIn("nucleus run --host 127.0.0.1 --port 8080 --workers 1", service)
        self.assertNotIn("MXSTUDIO_HOME=/var/lib/mxstudio", service)
        self.assertNotIn("HF_HOME=/var/cache/mxstudio/huggingface", service)
        self.assertNotIn("StateDirectory=mxstudio", service)
        gateway = (ROOT / "deploy/nucleus/litellm-nucleus.service.in").read_text()
        self.assertIn("EnvironmentFile=@NUCLEUS_ENV_FILE@", gateway)
        self.assertIn("@MXSTUDIO_HOME@/.litellm-venv", gateway)
        caddy = (ROOT / "deploy/nucleus/Caddyfile").read_text()
        self.assertIn("path /v1/models", caddy)
        self.assertNotIn("path /v1/*", caddy)


if __name__ == "__main__":
    unittest.main()
