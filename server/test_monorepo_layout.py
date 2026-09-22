import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class EmbeddedRouterLayout(unittest.TestCase):
    def test_router_fork_is_complete_and_contains_jev_contract_fixes(self):
        package = json.loads((ROOT / "router" / "package.json").read_text())
        self.assertEqual(package["name"], "codex-model-router")
        self.assertTrue((ROOT / "router" / "bin" / "codex-router").exists())

        router_source = (ROOT / "router" / "src" / "router.mjs").read_text()
        self.assertIn('route?.slug === "jev/auto"', router_source)
        self.assertIn("CODEX_ROUTER_STREAM_STALL_MS", router_source)
        self.assertIn("router_reasoning_effort", router_source)

    def test_root_installer_uses_only_the_embedded_router_source(self):
        installer = (ROOT / "install.sh").read_text()
        self.assertIn("router_dir=$repo_dir/router", installer)
        self.assertNotIn(".local/share/codex-router", installer)
        self.assertNotIn("git clone", installer)
        self.assertNotIn("CODEX_ROUTER_REPOSITORY_URL", installer)
        self.assertIn('CODEX_HOME=$prepare_home', installer)
        self.assertIn('"$router_dir/bin/install" --prepare-only', installer)
        self.assertNotIn('"$router_dir/install.sh" --prepare-only', installer)
        self.assertIn('"$router_dir/bin/install" --take-over-managed-router', installer)

    def test_windows_deploy_waits_for_ci_and_restarts_only_jev_services(self):
        deploy = (ROOT / "server" / "deploy-windows.ps1").read_text()
        self.assertIn("actions/workflows/ci.yml/runs", deploy)
        self.assertIn("@('merge', '--ff-only'", deploy)
        self.assertIn("service-windows.ps1", deploy)
        self.assertIn("JevHttpGateway-", deploy)
        self.assertNotIn("install.ps1\" -SkipSmoke", deploy)
        self.assertNotIn("router\\src\\service.mjs", deploy)

    def test_windows_service_matches_the_venv_redirector_relative_script(self):
        service = (ROOT / "server" / "service-windows.ps1").read_text()
        self.assertIn("|server[\\\\/]jev_server\\.py", service)
        self.assertIn("$Parent.ProcessId -ne $Owned.pid", service)

    def test_model_configuration_is_idempotent_and_preserves_other_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            state_dir = pathlib.Path(temp) / "state"
            state_dir.mkdir()
            user_models = state_dir / "user-models.json"
            user_models.write_text(json.dumps({
                "version": 1,
                "models": [{"slug": "example/model", "provider": "example"}],
            }))
            env = {
                **os.environ,
                "CODEX_ROUTER_STATE_DIR": str(state_dir),
            }
            command = ["node", str(ROOT / "server" / "configure-model.mjs")]
            subprocess.run(command, check=True, env=env, capture_output=True, text=True)
            subprocess.run(command, check=True, env=env, capture_output=True, text=True)
            payload = json.loads(user_models.read_text())
            slugs = [model["slug"] for model in payload["models"]]
            self.assertEqual(slugs.count("jev/auto"), 1)
            self.assertIn("example/model", slugs)
            # The overlay uses the embedded router's DACL contract, not Jev's
            # stricter credential-owner contract (elevated Windows differs).
            subprocess.run(["node", "--input-type=module", "-e",
                            "import {privateFileIsProtected} from './router/src/file-security.mjs'; process.exit(privateFileIsProtected(process.argv[1]) ? 0 : 1)",
                            str(user_models)], cwd=ROOT, capture_output=True, check=True)


if __name__ == "__main__":
    unittest.main()
