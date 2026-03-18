#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "venv python not found at ${PYTHON_BIN}. Run ./scripts/install.sh first."
  exit 1
fi

exec "${PYTHON_BIN}" -m dftracer_agents.cli goose-extension-link "$@"