#!/usr/bin/env bash

set -euo pipefail

readonly LOCAL_PYTHON=".venv/bin/python"
PYTHON_BIN="python3"

if [[ -x "$LOCAL_PYTHON" ]]; then
  PYTHON_BIN="$LOCAL_PYTHON"
fi

exec "$PYTHON_BIN" -m src.evaluation "$@"
