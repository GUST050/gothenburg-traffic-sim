#!/usr/bin/env bash
# Optional Windows/WSL launcher. Run this from an Ubuntu 24.04 WSL checkout.
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Usage: ./start-wsl.sh [serve.py options]

First run installs the project's Python dependencies and builds the local SUMO
network and direction split. Later runs reuse those files and start the app.
Open the printed localhost URL in your Windows browser. Stop with Ctrl+C.
EOF
    exit 0
fi

if ! uname -r | grep -Eiq '(microsoft|wsl)'; then
    echo 'This launcher is for Ubuntu inside Windows Subsystem for Linux (WSL).' >&2
    echo 'On macOS or regular Linux, use the README setup and python3 serve.py.' >&2
    exit 2
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$repo_root"

case "$repo_root" in
    /mnt/*)
        echo 'Tip: keep this checkout under your WSL home directory for faster file access.' >&2
        ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo 'Python is missing. In Ubuntu run: sudo apt update && sudo apt install git python3 python3-venv' >&2
    exit 2
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)' 2>/dev/null; then
    echo 'Use Python 3.11 or 3.12 in WSL. Ubuntu 24.04 provides Python 3.12.' >&2
    exit 2
fi

venv_python="$repo_root/.venv/bin/python"
if [[ ! -x "$venv_python" ]]; then
    echo 'Creating the project virtual environment...'
    if ! python3 -m venv "$repo_root/.venv"; then
        echo 'Install venv in Ubuntu: sudo apt update && sudo apt install python3-venv' >&2
        exit 2
    fi
fi

requirements_hash=$(sha256sum requirements.txt)
requirements_hash=${requirements_hash%% *}
stamp="$repo_root/.venv/.gs-wsl-requirements.sha256"
if [[ ! -f "$stamp" ]] || [[ "$(cat -- "$stamp")" != "$requirements_hash" ]]; then
    echo 'Installing Python and SUMO packages (first run may take a while)...'
    "$venv_python" -m pip install -r requirements.txt
    printf '%s\n' "$requirements_hash" > "$stamp"
fi

"$venv_python" - <<'PY'
from traffic_sim.simulation.runtime import sumo_home
print(f'Using SUMO: {sumo_home()}')
PY

if [[ -e sumo/net.net.xml ]] || [[ -e sumo/network_metadata.json ]]; then
    if [[ ! -s sumo/net.net.xml ]] || [[ ! -s sumo/network_metadata.json ]]; then
        echo 'The SUMO network is incomplete. Inspect sumo/net.net.xml and sumo/network_metadata.json before rebuilding.' >&2
        exit 2
    fi
else
    echo 'Building the local SUMO network...'
    "$venv_python" build_sumo_net.py
fi
if [[ ! -s sumo/direction_split.json ]]; then
    echo 'Building the direction-split estimate...'
    "$venv_python" -m dirsplit.predict
fi
if [[ ! -s sumo/net.net.xml ]] || [[ ! -s sumo/network_metadata.json ]] ||
   [[ ! -s sumo/direction_split.json ]]; then
    echo 'SUMO setup did not produce all required files; the server was not started.' >&2
    exit 2
fi

echo 'Open the localhost address printed below in your Windows browser.'
exec "$venv_python" serve.py --no-open "$@"
