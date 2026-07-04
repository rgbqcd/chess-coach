#!/bin/sh
# viam-server module entrypoint. The socket path arrives as "$1".
cd "$(dirname "$0")"

[ -d .venv ] || ./setup.sh
exec .venv/bin/python -m src.main "$@"
