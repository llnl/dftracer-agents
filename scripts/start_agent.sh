#!/usr/bin/env bash
# start_agent.sh — activate the venv and launch the DFTracer AI agent REPL.
# Accepts an optional single-shot prompt as arguments:
#   ./scripts/start_agent.sh                          # interactive REPL
#   ./scripts/start_agent.sh "annotate my training loop"  # single-shot
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "venv not found at ${VENV_DIR}. Run ./scripts/install.sh first."
  exit 1
fi

# Load .env if present
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
fi

# Map LLNL LIVAI vars → standard OpenAI client env vars
if [[ -n "${LIVAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="${LIVAI_API_KEY}"
fi
if [[ -n "${LIVAI_BASE_URL:-}" && -z "${OPENAI_BASE_URL:-}" ]]; then
  export OPENAI_BASE_URL="${LIVAI_BASE_URL}"
fi
if [[ -n "${LIVAI_MODEL:-}" && -z "${OPENAI_MODEL:-}" ]]; then
  export OPENAI_MODEL="${LIVAI_MODEL}"
fi

# Validate required vars
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Missing OPENAI_API_KEY (or LIVAI_API_KEY). Set it in .env."
  exit 1
fi
if [[ -z "${OPENAI_BASE_URL:-}" ]]; then
  echo "Missing OPENAI_BASE_URL (or LIVAI_BASE_URL). Set it in .env."
  exit 1
fi

# Run agent (REPL or single-shot)
if [[ $# -gt 0 ]]; then
  exec "${PYTHON}" -m dftracer_agents.agent "$@"
else
  exec "${PYTHON}" -m dftracer_agents.agent
fi
