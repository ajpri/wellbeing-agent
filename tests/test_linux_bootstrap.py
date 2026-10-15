"""Run with: uv run python -m unittest discover -s tests -p 'test_linux_bootstrap.py'.

Fixtures stub uv/npm/node; no packages are installed and no Gemini calls are made.
"""

import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/linux_bootstrap.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linux bootstrap ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy(ROOT / "startLinux.sh", self.root)
        shutil.copytree(ROOT / "scripts", self.root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "journal-app/node_modules/.bin").mkdir(parents=True)
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").symlink_to(sys.executable)
        (self.root / ".env.example").write_text("GEMINI_API_KEY=your-api-key-here\n")
        (self.root / ".env").write_text("GEMINI_API_KEY='test-key-not-real'\nPORT=8000\nJOURNAL_DIR=./journals\n")
        (self.root / "journal-app/package-lock.json").write_text("{}\n")
        (self.root / "journal-app/node_modules/.linux-bootstrap-lock").write_text("{}\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.executable(self.root / "journal-app/node_modules/.bin/vite", "exit 0")
        self.executable(self.bin / "node", "exit 0")
        python = shlex.quote(sys.executable)
        self.executable(self.bin / "uv", f'''case "$1" in
find) exit 2 ;;
python) printf '%s\\n' {python} ;;
sync) exit 0 ;;
run) shift 2; exec {python} main.py ;;
*) exit 2 ;;
esac''')
        self.executable(self.bin / "npm", f"exec {python} ../frontend.py")
        self.env = {k: v for k, v in os.environ.items() if k not in {"PORT", "JOURNAL_DIR", "GEMINI_API_KEY", "GOOGLE_API_KEY"}}
        self.env["HOME"] = str(self.root / "home")
        self.env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        # The fixture's Python symlink is outside the real virtual environment.
        # Keep dotenv available when these tests run through `uv run` as documented.
        import dotenv
        self.env["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(Path(dotenv.__file__).resolve().parent.parent),
            self.env.get("PYTHONPATH", ""),
        )))

    @staticmethod
    def executable(path, body):
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, *args):
        return subprocess.run(["bash", str(self.root / "startLinux.sh"), *args], cwd="/tmp", env=self.env, capture_output=True, text=True, timeout=10)

    def free_ports(self):
        for port in (8000, 5173):
            try:
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", port))
            except OSError:
                self.skipTest(f"Port {port} unavailable for integration test")

    def test_missing_tools_check_is_read_only(self):
        self.executable(self.bin / "uv", "exit 1")
        self.executable(self.bin / "node", "exit 1")
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        result = self.run_script("--check", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.12", result.stdout)
        self.assertIn("Node.js", result.stdout)
        self.assertEqual(before, sorted(p.relative_to(self.root) for p in self.root.rglob("*")))

    def test_noninteractive_does_not_install(self):
        self.executable(self.bin / "uv", "exit 1")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Noninteractive session", result.stderr)

    def test_missing_env_created_only_in_normal_mode(self):
        (self.root / ".env").unlink()
        result = self.run_script("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".env").exists())
        result = self.run_script()
        self.assertIn("Created .env", result.stdout)
        self.assertIn("Noninteractive session", result.stderr)
        self.assertEqual((self.root / ".env").stat().st_mode & 0o777, 0o600)

    def test_config_and_shell_syntax_are_not_executed(self):
        self.free_ports()
        (self.root / ".env").write_text('GEMINI_API_KEY="$(touch leaked)"\nPORT=9000\nJOURNAL_DIR=elsewhere\n')
        result = self.run_script("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PORT must be 8000", result.stderr)
        self.assertIn("JOURNAL_DIR", result.stderr)
        self.assertNotIn("$(touch", result.stdout + result.stderr)
        self.assertFalse((self.root / "leaked").exists())

    def test_blank_and_placeholder_keys_rejected(self):
        self.free_ports()
        for key in ("", "your-api-key-here", "   "):
            with self.subTest(key=key):
                (self.root / ".env").write_text(f'GEMINI_API_KEY="{key}"\n')
                self.assertNotEqual(self.run_script("--check").returncode, 0)

    def test_key_setup_preserves_other_settings_and_hides_key(self):
        from dotenv import dotenv_values

        env_path = self.root / ".env"
        env_path.write_text("# Keep this comment\nGEMINI_API_KEY=your-api-key-here\nPORT=8000\n")
        with patch.object(helper, "ROOT", self.root), patch.dict(os.environ, self.env, clear=True), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="yes"), \
                patch.object(helper.getpass, "getpass", return_value="test-secret-value"), \
                patch("builtins.print") as output:
            helper.setup_key()
        self.assertEqual(dotenv_values(env_path)["GEMINI_API_KEY"], "test-secret-value")
        self.assertIn("# Keep this comment", env_path.read_text())
        self.assertEqual(dotenv_values(env_path)["PORT"], "8000")
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("test-secret-value", str(output.call_args_list))

    def test_key_setup_declined_or_existing_key_leaves_file_unchanged(self):
        env_path = self.root / ".env"
        for existing in (True, False):
            env_path.write_text("GEMINI_API_KEY=" + ("test-existing-key" if existing else "your-api-key-here") + "\n")
            before = env_path.read_bytes()
            with patch.object(helper, "ROOT", self.root), patch.dict(os.environ, self.env, clear=True), \
                    patch.object(sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", return_value="no") as prompt, \
                    patch.object(helper.getpass, "getpass") as secret, patch("builtins.print"):
                if existing:
                    helper.setup_key()
                    prompt.assert_not_called()
                else:
                    with self.assertRaisesRegex(RuntimeError, "skipped"):
                        helper.setup_key()
                secret.assert_not_called()
            self.assertEqual(before, env_path.read_bytes())

    def test_invalid_exported_key_is_not_written_over(self):
        with patch.object(helper, "ROOT", self.root), patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "overriding"):
                helper.setup_key()

    def test_existing_environment_takes_precedence(self):
        self.free_ports()
        self.env["PORT"] = "9000"
        self.assertIn("PORT must be 8000", self.run_script("--check").stderr)

    def test_successful_check_and_occupied_port(self):
        self.free_ports()
        self.assertEqual(self.run_script("--check").returncode, 0)
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 8000))
            sock.listen()
            result = self.run_script("--check")
            self.assertIn("Port 8000 is unavailable", result.stderr)

    def start_servers(self, backend_failure=False):
        server = '''import http.server, os, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
Path(__file__ + '.pids').write_text(f'{os.getpid()} {child.pid}')
http.server.HTTPServer.allow_reuse_address = True
http.server.HTTPServer(('127.0.0.1', PORT), http.server.SimpleHTTPRequestHandler).serve_forever()
'''
        (self.root / "main.py").write_text("raise SystemExit(7)" if backend_failure else server.replace("PORT", "8000"))
        (self.root / "frontend.py").write_text(server.replace("PORT", "5173"))
        # Backend readiness uses this exact route; frontend serves its directory.
        (self.root / "openapi.json").write_text("{}")
        log = (self.root / "output.log").open("w+")
        self.addCleanup(log.close)
        proc = subprocess.Popen(["bash", str(self.root / "startLinux.sh")], env=self.env, stdout=log, stderr=log)
        def stop():
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        self.addCleanup(stop)
        return proc, log

    def await_ready(self, proc, log):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            log.seek(0)
            output = log.read()
            if "App ready:" in output:
                return
            if proc.poll() is not None:
                self.fail(output)
            time.sleep(0.1)
        self.fail("Servers did not become ready")

    def test_backend_startup_failure_stops_launcher(self):
        self.free_ports()
        proc, log = self.start_servers(backend_failure=True)
        self.assertNotEqual(proc.wait(timeout=10), 0)
        self.assertFalse((self.root / "frontend.py.pids").exists())

    def test_interrupt_and_server_failure_cleanup(self):
        for action in (signal.SIGINT, signal.SIGTERM, "frontend_failure"):
            with self.subTest(action=action):
                self.free_ports()
                proc, log = self.start_servers()
                self.await_ready(proc, log)
                frontend = int((self.root / "frontend.py.pids").read_text().split()[0])
                if action == "frontend_failure":
                    os.kill(frontend, signal.SIGTERM)
                else:
                    proc.send_signal(action)
                self.assertNotEqual(proc.wait(timeout=10), 0)
                # Verify all descendants have exited (zombies are no longer running).
                for path in self.root.glob("*.pids"):
                    for pid in path.read_text().split():
                        stat = Path(f"/proc/{pid}/stat")
                        self.assertTrue(not stat.exists() or stat.read_text().split()[2] == "Z")


if __name__ == "__main__":
    unittest.main()
