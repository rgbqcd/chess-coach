#!/bin/sh
# Build the project environment with uv (falls back to stdlib venv + pip).
cd "$(dirname "$0")"
set -e

if command -v uv >/dev/null 2>&1; then
    uv sync --frozen 2>/dev/null || uv sync
else
    echo "uv not found; falling back to python venv + pip" >&2
    python3 -m venv .venv
    .venv/bin/pip install viam-sdk 'buttplug==1.0.0' 'bleak>=0.22' 'chess>=1.11' 'httpx>=0.27'
fi
