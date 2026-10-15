"""Helpers for startLinux.sh; environment values are never executed or logged."""

import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import warnings

ROOT = Path(__file__).resolve().parent.parent


def environment():
    from dotenv import dotenv_values

    values = dotenv_values(ROOT / ".env")
    # load_dotenv() in main.py preserves existing process environment variables.
    values.update(os.environ)
    return values


def valid_key(key):
    key = (key or "").strip()
    return bool(key) and key.lower() not in {"your-api-key-here", "changeme", "replace-me"}


def setup_key():
    from dotenv import set_key

    values = environment()
    # Google's client prefers GOOGLE_API_KEY when both names are set.
    if valid_key(values.get("GOOGLE_API_KEY") or values.get("GEMINI_API_KEY")):
        return
    print("A Gemini API key is required.\n"
          "1. Sign in at https://aistudio.google.com/apikey\n"
          "2. Create an API key for your Google Cloud project (or use the default project).\n"
          "3. Copy the key and paste it at the hidden terminal prompt below.\n"
          "You can also edit GEMINI_API_KEY in .env yourself. Do not commit your key.")
    if values.get("GOOGLE_API_KEY") or "GEMINI_API_KEY" in os.environ:
        raise RuntimeError("An invalid key is overriding .env. Correct or unset the exported key; also check GOOGLE_API_KEY in .env.")
    if not sys.stdin.isatty():
        raise RuntimeError("Noninteractive session: set GEMINI_API_KEY in .env or the environment, then rerun.")
    if input("Save your Gemini API key to .env now? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise RuntimeError("Key setup skipped. Set GEMINI_API_KEY in .env and rerun.")
    # Refuse getpass's echoing fallback when a hidden prompt is unavailable.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        key = getpass.getpass("Gemini API key (hidden): ").strip()
    if not valid_key(key) or any(char.isspace() for char in key):
        raise RuntimeError("Key is blank, a placeholder, or contains whitespace; .env was not changed.")
    env_path = ROOT / ".env"
    env_path.chmod(0o600)
    set_key(env_path, "GEMINI_API_KEY", key, quote_mode="always")
    print("Saved GEMINI_API_KEY to .env (read/write for your user only).")


def check():
    values = environment()
    errors = []
    key = values.get("GOOGLE_API_KEY") or values.get("GEMINI_API_KEY")
    if not valid_key(key):
        errors.append("Set GEMINI_API_KEY to a non-placeholder value in .env.")
    if values.get("PORT", "8000") != "8000":
        errors.append("PORT must be 8000: the frontend proxy currently uses that port.")
    journal = values.get("JOURNAL_DIR", "./journals")
    if not journal or (ROOT / journal).resolve() != (ROOT / "journals").resolve():
        errors.append("JOURNAL_DIR must resolve to ./journals: the frontend reads that directory.")
    elif not os.access(ROOT / "journals" if (ROOT / "journals").exists() else ROOT, os.W_OK):
        errors.append("The journal directory is not writable.")
    for port in (8000, 5173):
        for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            try:
                with socket.socket(family) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind((address, port))
            except OSError as exc:
                # IPv6 may be disabled on an otherwise supported system.
                if exc.errno in (97, 99):
                    continue
                errors.append(f"Port {port} is unavailable. Stop the process using it and retry.")
                break
    if errors:
        raise RuntimeError("\n".join(errors))
    print("Configuration and ports checked. Gemini key access/quota has not been tested.")


def ready(url, pid, timeout=60):
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            raise RuntimeError("Server exited during startup; see its logs above.") from None
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Server was not ready within 60 seconds; see its logs above.")


def install_node():
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(platform.machine())
    if not arch:
        raise RuntimeError("Automatic Node installation supports x86_64 and aarch64; install Node >=22.12 manually.")
    base = "https://nodejs.org/dist/"
    with urllib.request.urlopen(base + "index.json", timeout=30) as response:
        releases = json.load(response)
    version = next(r["version"] for r in releases if r["version"].startswith("v22."))
    filename = f"node-{version}-linux-{arch}.tar.xz"
    destination = Path.home() / ".local/share/wellbeing-agent/node"
    # Never overwrite an existing user-managed installation.
    if destination.exists():
        raise RuntimeError(f"{destination} already exists. Update or move it manually and rerun.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        staging = Path(directory)
        archive = staging / filename
        with urllib.request.urlopen(f"{base}{version}/SHASUMS256.txt", timeout=30) as response:
            checksums = dict(line.split()[::-1] for line in response.read().decode().splitlines())
        expected = checksums[filename]
        digest = hashlib.sha256()
        with urllib.request.urlopen(f"{base}{version}/{filename}", timeout=60) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError("Node.js checksum verification failed; nothing was installed.")
        subprocess.run(["tar", "-xJf", str(archive), "-C", str(staging)], check=True)
        shutil.move(str(staging / filename.removesuffix(".tar.xz")), destination)
    print(f"Installed Node.js {version} to {destination}")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "check":
            check()
        elif sys.argv[1] == "setup-key":
            setup_key()
        elif sys.argv[1] == "ready":
            ready(*sys.argv[2:])
        elif sys.argv[1] == "install-node":
            install_node()
        else:
            raise RuntimeError("Unknown bootstrap helper command.")
    except (KeyboardInterrupt, EOFError):
        print("\nKey setup cancelled; edit .env or rerun when ready.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Do not include URLs, response bodies or environment data in failures.
        if isinstance(exc, RuntimeError):
            print(f"Error: {exc}", file=sys.stderr)
        else:
            print(f"Bootstrap helper failed ({type(exc).__name__}); check network access and dependencies.", file=sys.stderr)
        sys.exit(1)
