#!/bin/sh
# Double-click this file in Finder (macOS) to start the traffic map.
#
# It exists because the single most common way to end up staring at
# ERR_CONNECTION_REFUSED is not a bug in the server — it is never having
# started one, or having started it in the wrong directory. This script
# cannot be started in the wrong directory: it cd's to its own location,
# which is the repo root by construction.
#
# From a terminal `make serve` does the same thing (and `python3 serve.py`
# hands itself over to .venv when it exists).
# Everything after the script name is passed through, so
# `./start.command --port 8001` works too.

cd "$(dirname "$0")" || exit 1

# Double-clicked, Terminal closes the window the moment this script ends,
# so an error message would flash past unread. Hold the window open on
# failure only — a successful run ends when the user stops the server.
hold_open() {
    printf '\nTryck Enter för att stänga fönstret.'
    read -r _
}

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 hittades inte."
    echo "Installera Apples kommandoradsverktyg med:  xcode-select --install"
    hold_open
    exit 1
fi

if [ ! -f web/data/network.geojson ]; then
    echo "Kartdatan saknas i den här kopian (web/data/network.geojson)."
    echo "Kör:  make data"
    hold_open
    exit 1
fi

# The map runs on any python3, but new simulations need the project's own
# environment (.venv: Python 3.12 + requirements.lock). Without it the
# server used to offer jobs that could only fail (2026-10-10), so offer to
# build it here, where a person is sitting at the keyboard.
# Same order as serve.py and the Makefile: an adopted interpreter
# (.simulation-python, `make setup USE=...`), then .venv, then python3.
configured=$(sed -n '/[^[:space:]]/{s/^[[:space:]]*//;s/[[:space:]]*$//;p;q;}' .simulation-python 2>/dev/null)
# A configured interpreter that no longer exists (a removed Python, a stale
# file) must not stop a launch that .venv could serve (review 2026-10-10).
if [ -n "$configured" ] && [ ! -x "$configured" ]; then
    echo "Obs: $configured (från .simulation-python) finns inte eller går inte att köra."
    echo "Använder .venv i stället. Kör  make setup  för att rätta filen."
    configured=
fi

if [ -z "$configured" ] && [ ! -x .venv/bin/python ]; then
    echo "Simuleringsmiljön (.venv) saknas. Kartan fungerar ändå, men nya"
    echo "simuleringar kräver den (cirka 1 GB, några minuter första gången)."
    printf 'Installera den nu? [j/N] '
    read -r answer
    case "$answer" in
        [jJyY]*) python3 tools/setup_venv.py || echo "Installationen misslyckades — kartan startar ändå." ;;
        *) echo "Hoppar över. Installera senare med:  make setup" ;;
    esac
fi

python=python3
if [ -n "$configured" ]; then
    python=$configured
elif [ -x .venv/bin/python ]; then
    python=.venv/bin/python
fi

echo "Startar kartan — stäng det här fönstret för att stoppa servern."
"$python" serve.py "$@" || hold_open
