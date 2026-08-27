#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT:${HEARTH_DEPS:-$ROOT/.deps}:/tmp/hearthdeps${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m hearth "$@"
