"""Exercise the distributable Windows skill and account-home resolution."""
import json
import io
import zipfile
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest import mock
import dashboard

ROOT = Path(__file__).resolve().parents[1]


class StatePaths(unittest.TestCase):
    def test_account_home_is_shared_by_service_smoke_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {key: value for key, value in os.environ.items()
                   if key not in ("MODEL_ROUTER_STATE_DIR", "CODEX_ROUTER_STATE_DIR", "KIMI_CODEX_STATE_DIR")}
            env["CODEX_HOME"] = tmp
            code = "import json,local_runtime,jev_server,smoke,report_routing; print(json.dumps([local_runtime.STATE,jev_server.STATE,smoke.STATE,report_routing.LIVE_LOG]))"
            result = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "server",
                                    env=env, capture_output=True, text=True, check=True)
            paths = json.loads(result.stdout)
            self.assertEqual(paths[:3], [str(Path(tmp) / "codex-router")] * 3)
            self.assertEqual(paths[3], str(Path(tmp) / "codex-router/jev-router-live.jsonl"))

    def test_windows_client_bootstraps_node_without_docker(self):
        installer = (ROOT / 'server/install-client.ps1').read_text()
        self.assertIn('winget', installer.lower())
        self.assertIn('OpenJS.NodeJS.LTS', installer)
        self.assertIn('Docker is not required', installer)


@unittest.skipUnless(os.name == "nt", "Windows installer")
class WindowsInstall(unittest.TestCase):
    def test_http_installer_configures_fresh_home_without_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'merged-models.json').write_text(json.dumps({'models': [{'slug': 'jev/auto'}]}))
            with mock.patch.object(dashboard, 'STATE', tmp):
                bundle = dashboard.client_bundle()
            # Keep the real installer/configuration; never replace this user's running task.
            prepared = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(bundle)) as source, zipfile.ZipFile(prepared, 'w') as target:
                for name in source.namelist():
                    target.writestr(name, b'exit 0' if name == 'server/install-local-service.ps1' else source.read(name))
            bundle = prepared.getvalue()
            class Download(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(bundle)
                def log_message(self, *args):
                    pass
            service = ThreadingHTTPServer(('127.0.0.1', 0), Download)
            threading.Thread(target=service.serve_forever, daemon=True).start()
            try:
                home = root / 'fresh home'
                home.mkdir()
                original = '# keep\n[features]\nexample = true\n'
                (home / 'config.toml').write_text(original)
                origin = f'http://127.0.0.1:{service.server_port}'
                command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                           str(ROOT / 'server/install-client.ps1'), '-ServiceUrl', origin, '-CodexHome', str(home)]
                for _ in range(2):
                    subprocess.run(command, check=True, capture_output=True)
                config = (home / 'config.toml').read_text()
                self.assertIn('model_provider = "jev-local"', config)
                self.assertIn('base_url = "http://127.0.0.1:4321/v1"', config)
                self.assertIn('requires_openai_auth = true', config)
                self.assertIn(original, config)
                self.assertEqual((home / 'config.toml.before-jev-local').read_text(), original)
                self.assertFalse((home / 'auth.json').exists())
                hooks = json.loads((home / 'hooks.json').read_text())['hooks']
                self.assertEqual(len(hooks['Stop']), 1)
                self.assertIn(origin, (home / 'skills/jev-assist/SKILL.md').read_text(encoding='utf-8'))
                self.assertFalse((home / 'skills/jev-assist/SKILL.md.before-jev-http').exists())
                script = str(ROOT / 'server/uninstall-client.ps1').replace("'", "''")
                target = str(home).replace("'", "''")
                uninstall = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
                             f"function Get-ScheduledTask {{ $null }}; & '{script}' -CodexHome '{target}'"]
                for _ in range(2):
                    result = subprocess.run(uninstall, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((home / 'config.toml').read_text(), original)
                self.assertEqual(json.loads((home / 'hooks.json').read_text()), {})
                self.assertFalse((home / 'jev-assist-client').exists())
                self.assertFalse((home / 'skills/jev-assist/SKILL.md').exists())
            finally:
                service.shutdown()
                service.server_close()

    def test_optional_skill_installs_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(ROOT / "install-skill.ps1"), "-SkillsDirectory", tmp]
            subprocess.run(command, capture_output=True, check=True)
            installed = Path(tmp) / "jev-assist/SKILL.md"
            self.assertEqual(installed.read_bytes(), (ROOT / "skills/jev-assist/SKILL.md").read_bytes())
            installed.write_text("existing user customization")
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(installed.read_text(), "existing user customization")


if __name__ == "__main__":
    unittest.main()
