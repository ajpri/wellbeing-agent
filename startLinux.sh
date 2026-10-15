#!/usr/bin/env bash
# Local development launcher for Debian and Fedora families.
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT"
YES=false
CHECK=false
for arg in "$@"; do
    case "$arg" in
        --yes) YES=true ;;
        --check) CHECK=true ;;
        --help|-h)
            printf 'Usage: ./startLinux.sh [--yes] [--check]\n  --yes    Accept dependency installation (may download runtimes).\n  --check  Check tools, configuration and ports without changing anything.\n'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || die 'This launcher requires Linux.'
[[ -r /etc/os-release ]] || die 'Cannot identify this Linux distribution.'
# os-release is trusted OS configuration, not the application's .env.
source /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
    *debian*|*ubuntu*) FAMILY=debian ;;
    *fedora*) FAMILY=fedora ;;
    *) die 'Supported distributions are Debian and Fedora and their derivatives.' ;;
esac
(( EUID != 0 )) || die 'Run as your normal user; only package installation uses sudo.'

export PATH="$HOME/.local/bin:$HOME/.local/share/wellbeing-agent/node/bin:$PATH"
missing=()
packages=()
for tool in curl tar xz setsid sha256sum; do
    if ! command -v "$tool" >/dev/null; then
        missing+=("$tool")
        case "$tool" in
            xz) [[ $FAMILY == debian ]] && packages+=(xz-utils) || packages+=(xz) ;;
            setsid) packages+=(util-linux) ;;
            sha256sum) packages+=(coreutils) ;;
            *) packages+=("$tool") ;;
        esac
    fi
done
need_uv=false
need_python=false
need_node=false
command -v uv >/dev/null || { missing+=('uv'); need_uv=true; }
PYTHON=''
if ! $need_uv; then
    PYTHON=$(uv python find --no-python-downloads 3.12 2>/dev/null || true)
fi
[[ -n $PYTHON ]] || { missing+=('Python 3.12 (managed by uv)'); need_python=true; }
node_ok() {
    command -v node >/dev/null && command -v npm >/dev/null &&
        node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22 || (a===22 && b>=12) ? 0 : 1)' >/dev/null 2>&1
}
node_ok || { missing+=('Node.js >=22.12 and npm'); need_node=true; }

if ((${#missing[@]})); then
    printf 'Missing or incompatible dependencies:\n'
    printf '  - %s\n' "${missing[@]}"
    printf '\nProposed installation:\n'
    if ((${#packages[@]})); then
        if [[ $FAMILY == debian ]]; then
            printf '  sudo apt-get update\n  sudo apt-get install -y'
        else
            printf '  sudo dnf install -y'
        fi
        printf ' %q' "${packages[@]}"; printf '\n'
    fi
    $need_uv && printf '  Download https://astral.sh/uv/install.sh and run it as your user (no shell profile changes).\n'
    $need_python && printf '  uv python install 3.12 (managed Python; downloads from Astral python-build-standalone releases).\n'
    $need_node && printf '  Download latest Node.js 22 binary and SHA256 manifest from https://nodejs.org/dist/.\n  Verify checksum and install under ~/.local/share/wellbeing-agent/node (no shell profile changes).\n'
    $CHECK && die 'Dependency checks failed. Run without --check to offer installation.'
    if ! $YES; then
        [[ -t 0 ]] || die 'Noninteractive session: install dependencies manually or pass --yes.'
        read -r -p 'Install these dependencies now? [y/N] ' answer
        [[ $answer == y || $answer == Y || $answer == yes || $answer == YES ]] || die 'Installation declined.'
    fi
    if ((${#packages[@]})); then
        command -v sudo >/dev/null || die 'sudo is required for system packages; ask your administrator to install the listed packages.'
        if [[ $FAMILY == debian ]]; then
            sudo apt-get update
            sudo apt-get install -y "${packages[@]}"
        else
            sudo dnf install -y "${packages[@]}"
        fi
    fi
    if $need_uv; then
        installer=$(mktemp)
        if ! curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$installer"; then
            rm -f -- "$installer"; die 'Could not download uv installer.'
        fi
        if ! UV_NO_MODIFY_PATH=1 sh "$installer"; then
            rm -f -- "$installer"; die 'uv installation failed.'
        fi
        rm -f -- "$installer"
    fi
    command -v uv >/dev/null || die 'uv is still unavailable after installation.'
    $need_python && uv python install 3.12
    PYTHON=$(uv python find --no-python-downloads 3.12) || die 'Python 3.12 is unavailable.'
    if $need_node; then
        "$PYTHON" scripts/linux_bootstrap.py install-node
    fi
    hash -r
    node_ok || die 'Node.js >=22.12 and npm are still unavailable. Check PATH or install them manually.'
    for tool in curl tar xz setsid sha256sum; do
        command -v "$tool" >/dev/null || die "$tool is still unavailable."
    done
fi

if [[ ! -f .env ]]; then
    $CHECK && die '.env is missing. Copy .env.example to .env and set GEMINI_API_KEY.'
    (umask 077; cp -n .env.example .env)
    printf 'Created .env from .env.example.\n'
fi

if $CHECK; then
    # No dependency sync or downloads in check mode.
    [[ -x .venv/bin/python ]] || die 'Project environment is missing; run normally to install project dependencies.'
    .venv/bin/python scripts/linux_bootstrap.py check
    printf 'Checks passed. Ready to run ./startLinux.sh\n'
    exit 0
fi

uv sync --locked --python "$PYTHON"
# Match main.py's dotenv parser, without evaluating shell code or printing secrets.
.venv/bin/python scripts/linux_bootstrap.py setup-key
.venv/bin/python scripts/linux_bootstrap.py check
stamp=journal-app/node_modules/.linux-bootstrap-lock
if [[ ! -f $stamp ]] || ! cmp -s journal-app/package-lock.json "$stamp" || [[ ! -x journal-app/node_modules/.bin/vite ]]; then
    (cd journal-app && npm ci --legacy-peer-deps)
    cp journal-app/package-lock.json "$stamp"
fi

pids=()
probe_pid=''
cleanup() {
    trap - EXIT INT TERM
    if [[ -n $probe_pid ]]; then
        kill -TERM "$probe_pid" 2>/dev/null || true
        wait "$probe_pid" 2>/dev/null || true
    fi
    for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
    # Give children time to shut down, then reap any remaining descendants.
    for _ in {1..30}; do
        alive=false
        for pid in "${pids[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=true; done
        $alive || break
        sleep 0.1
    done
    for pid in "${pids[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
    for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Disable job control so setsid's PID also identifies its process group.
set +m
wait_ready() {
    .venv/bin/python scripts/linux_bootstrap.py ready "$1" "$2" &
    probe_pid=$!
    wait "$probe_pid"
    probe_pid=''
}
printf 'Starting backend at http://localhost:8000\n'
setsid uv run --no-sync python main.py &
pids+=("$!")
wait_ready http://127.0.0.1:8000/openapi.json "${pids[0]}"
printf 'Starting frontend at http://localhost:5173\n'
setsid bash -c 'cd journal-app && exec npm run dev -- --host 127.0.0.1 --port 5173 --strictPort' &
pids+=("$!")
wait_ready http://127.0.0.1:5173/ "${pids[1]}"
printf '\nApp ready: http://localhost:5173\nPress Ctrl+C to stop both servers.\n'
while true; do
    for pid in "${pids[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            status=0
            wait "$pid" || status=$?
            ((status != 0)) || status=1
            printf 'A server stopped; shutting down both servers.\n' >&2
            exit "$status"
        fi
    done
    sleep 0.5
done
