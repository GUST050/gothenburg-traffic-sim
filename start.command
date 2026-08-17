#!/bin/sh
# Double-click this file in Finder (macOS) to start the traffic map.
#
# It exists because the single most common way to end up staring at
# ERR_CONNECTION_REFUSED is not a bug in the server — it is never having
# started one, or having started it in the wrong directory. This script
# cannot be started in the wrong directory: it cd's to its own location,
# which is the repo root by construction.
#
# From a terminal `python3 serve.py` does exactly the same thing.
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

echo "Startar kartan — stäng det här fönstret för att stoppa servern."
python3 serve.py "$@" || hold_open
