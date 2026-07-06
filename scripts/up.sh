#!/bin/sh
# One-command launch: Intiface Central + viam-server (which now serves the
# dashboard itself) + browser. Ctrl-C stops viam-server.
#
# Usage: scripts/up.sh [config.json]
# Default config: viam.practice.local.json, else viam.local.json, else viam.fake.local.json
cd "$(dirname "$0")/.."
set -u

CONFIG="${1:-}"
if [ -z "$CONFIG" ]; then
    for candidate in viam.practice.local.json viam.local.json viam.fake.local.json; do
        [ -f "$candidate" ] && CONFIG="$candidate" && break
    done
fi
if [ -z "$CONFIG" ] || [ ! -f "$CONFIG" ]; then
    echo "no config found. Copy a template first, e.g.:" >&2
    echo "  cp viam.practice.json viam.practice.local.json  # then set executable_path" >&2
    exit 1
fi
echo "config: $CONFIG"

# viam-server: PATH, or a locally built RDK checkout
VIAM_SERVER="$(command -v viam-server || true)"
if [ -z "$VIAM_SERVER" ]; then
    for candidate in "$HOME"/viam/rdk/bin/*/viam-server; do
        [ -x "$candidate" ] && VIAM_SERVER="$candidate" && break
    done
fi
if [ -z "$VIAM_SERVER" ]; then
    echo "viam-server not found. Install it:" >&2
    echo "  brew tap viamrobotics/brews && brew install viam-server" >&2
    exit 1
fi

# environment
[ -d .venv ] || ./setup.sh

# Intiface Central (macOS): launch if installed and not running
if [ "$(uname)" = "Darwin" ] && [ -d "/Applications/Intiface Central.app" ]; then
    if ! pgrep -qf "Intiface Central" 2>/dev/null; then
        echo "launching Intiface Central (press its play button if the engine isn't set to auto-start)"
        open -g -a "Intiface Central"
    fi
fi

# open the dashboard once it's up (the module serves it on :8765)
PORT=$(python3 -c "import json;cfg=json.load(open('$CONFIG'));print(next((s.get('attributes',{}).get('port',8765) for s in cfg.get('services',[]) if s.get('name')=='dashboard'),8765))" 2>/dev/null || echo 8765)
(
    for _ in $(seq 1 60); do
        sleep 1
        if curl -sf "http://localhost:$PORT/state.json" > /dev/null 2>&1; then
            echo "dashboard: http://localhost:$PORT"
            command -v open > /dev/null && open "http://localhost:$PORT"
            exit 0
        fi
    done
    echo "dashboard did not come up on :$PORT — check the viam-server logs above" >&2
) &

echo "starting viam-server (ctrl-C to stop everything) ..."
exec "$VIAM_SERVER" -config "$CONFIG"
