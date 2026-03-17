#!/usr/bin/env bash
# install.sh — userspace-only setup for dftracer-agents (no sudo required).
# Creates a Python venv, installs this package and openai-agents in editable mode.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

echo "[dftracer-agents] root=${ROOT_DIR}"
echo "[dftracer-agents] userspace-only install (no sudo required)"

# ── Python venv ──────────────────────────────────────────────────────────────
if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e "${ROOT_DIR}"

echo "[dftracer-agents] installation complete"
echo ""
echo "Next steps:"
echo "  1. cp .env.example .env  and fill in your LIVAI_API_KEY / LIVAI_BASE_URL"
echo "  2. source ${VENV_DIR}/bin/activate"
echo "  3. dftracer-agents-run             # interactive REPL"
echo "  4. dftracer-agents-run \"<prompt>\"  # single-shot"
