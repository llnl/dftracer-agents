#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
GOOSE_BIN="${DFTRACER_GOOSE_BIN:-${VENV_DIR}/bin/goose}"

_restore_if_set() {
  local name="$1"
  local value="$2"
  if [[ -n "${value}" ]]; then
    export "${name}=${value}"
  fi
}

if [[ ! -x "${GOOSE_BIN}" ]]; then
  echo "goose not found at ${GOOSE_BIN}. Run ./scripts/install.sh first."
  exit 1
fi

PRESET_LIVAI_BASE_URL="${LIVAI_BASE_URL:-}"
PRESET_LIVAI_API_KEY="${LIVAI_API_KEY:-}"
PRESET_LIVAI_MODEL="${LIVAI_MODEL:-}"
PRESET_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
PRESET_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
PRESET_OPENAI_MODEL="${OPENAI_MODEL:-}"
PRESET_GOOSE_PROVIDER="${GOOSE_PROVIDER:-}"
PRESET_GOOSE_MODEL="${GOOSE_MODEL:-}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
fi

_restore_if_set LIVAI_BASE_URL "${PRESET_LIVAI_BASE_URL}"
_restore_if_set LIVAI_API_KEY "${PRESET_LIVAI_API_KEY}"
_restore_if_set LIVAI_MODEL "${PRESET_LIVAI_MODEL}"
_restore_if_set OPENAI_BASE_URL "${PRESET_OPENAI_BASE_URL}"
_restore_if_set OPENAI_API_KEY "${PRESET_OPENAI_API_KEY}"
_restore_if_set OPENAI_MODEL "${PRESET_OPENAI_MODEL}"
_restore_if_set GOOSE_PROVIDER "${PRESET_GOOSE_PROVIDER}"
_restore_if_set GOOSE_MODEL "${PRESET_GOOSE_MODEL}"

if [[ -z "${PRESET_OPENAI_API_KEY}" && -n "${PRESET_LIVAI_API_KEY}" ]]; then
  export OPENAI_API_KEY="${PRESET_LIVAI_API_KEY}"
elif [[ -n "${LIVAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="${LIVAI_API_KEY}"
fi
if [[ -z "${PRESET_OPENAI_BASE_URL}" && -n "${PRESET_LIVAI_BASE_URL}" ]]; then
  export OPENAI_BASE_URL="${PRESET_LIVAI_BASE_URL}"
elif [[ -n "${LIVAI_BASE_URL:-}" && -z "${OPENAI_BASE_URL:-}" ]]; then
  export OPENAI_BASE_URL="${LIVAI_BASE_URL}"
fi
if [[ -z "${PRESET_OPENAI_MODEL}" && -n "${PRESET_LIVAI_MODEL}" ]]; then
  export OPENAI_MODEL="${PRESET_LIVAI_MODEL}"
elif [[ -n "${LIVAI_MODEL:-}" && -z "${OPENAI_MODEL:-}" ]]; then
  export OPENAI_MODEL="${LIVAI_MODEL}"
fi

if [[ -n "${OPENAI_BASE_URL:-}" ]]; then
  if [[ "${OPENAI_BASE_URL}" =~ ^(https?://[^/]+)(/(.*))?$ ]]; then
    if [[ -z "${OPENAI_HOST:-}" ]]; then
      export OPENAI_HOST="${BASH_REMATCH[1]}"
    fi
    if [[ -z "${OPENAI_BASE_PATH:-}" ]]; then
      base_path="${BASH_REMATCH[3]:-}"
      base_path="${base_path#/}"
      if [[ -z "${base_path}" || "${base_path}" == "v1" ]]; then
        export OPENAI_BASE_PATH="v1/chat/completions"
      elif [[ "${base_path}" == */chat/completions || "${base_path}" == */responses ]]; then
        export OPENAI_BASE_PATH="${base_path}"
      else
        export OPENAI_BASE_PATH="${base_path}/chat/completions"
      fi
    fi
  fi
fi

if [[ -z "${GOOSE_PROVIDER:-}" ]]; then
  export GOOSE_PROVIDER="openai"
fi
if [[ -n "${OPENAI_MODEL:-}" && -z "${GOOSE_MODEL:-}" ]]; then
  export GOOSE_MODEL="${OPENAI_MODEL}"
fi
if [[ -n "${OPENAI_API_KEY:-}" && -z "${GOOSE_EDITOR_API_KEY:-}" ]]; then
  export GOOSE_EDITOR_API_KEY="${OPENAI_API_KEY}"
fi
if [[ -n "${OPENAI_BASE_URL:-}" && -z "${GOOSE_EDITOR_HOST:-}" ]]; then
  export GOOSE_EDITOR_HOST="${OPENAI_BASE_URL}"
fi
if [[ -n "${OPENAI_MODEL:-}" && -z "${GOOSE_EDITOR_MODEL:-}" ]]; then
  export GOOSE_EDITOR_MODEL="${OPENAI_MODEL}"
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Missing OPENAI_API_KEY (or LIVAI_API_KEY). Set it in .env."
  exit 1
fi

exec "${GOOSE_BIN}" "$@"