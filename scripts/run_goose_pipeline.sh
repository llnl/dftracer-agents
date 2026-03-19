#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
GOOSE_BIN="${DFTRACER_GOOSE_BIN:-goose}"
if [[ ! -x "${GOOSE_BIN}" ]]; then
  GOOSE_BIN="${VENV_DIR}/bin/goose"
fi
MCP_CMD="${ROOT_DIR}/.venv/bin/python -m dftracer_agents.mcp_servers.server"
RECIPE_PATH="${ROOT_DIR}/goose/recipes/00_dftracer_pipeline.yaml"
STAGE_TIMEOUT_SECONDS="${DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS:-120}"
RUN_TEXT="Run the loaded DFTracer pipeline recipe once. Execute the full pipeline and return only the final JSON response."

NAME="${DFTRACER_PIPELINE_NAME:-ior}"
REPO_URL="${DFTRACER_PIPELINE_REPO_URL:-https://github.com/hpc/ior}"
REPO_REF="${DFTRACER_PIPELINE_REPO_REF:-4.0.0}"
LANGUAGE="${DFTRACER_PIPELINE_LANGUAGE:-cpp}"
WORKSPACE_ROOT="${DFTRACER_PIPELINE_WORKSPACE_ROOT:-${ROOT_DIR}/workspaces/${NAME}}"
REPO_DIR="${DFTRACER_PIPELINE_REPO_DIR:-${WORKSPACE_ROOT}/source/${NAME}}"
VENV_PREFIX="${DFTRACER_PIPELINE_VENV_DIR:-${WORKSPACE_ROOT}/venv}"
TRACE_DIR="${DFTRACER_PIPELINE_TRACE_DIR:-${WORKSPACE_ROOT}/traces/terminal_default}"
POST_DIR="${DFTRACER_PIPELINE_POST_DIR:-${WORKSPACE_ROOT}/artifacts/terminal_default/postprocess}"
COMPACTED_TRACE_DIR="${DFTRACER_PIPELINE_COMPACTED_TRACE_DIR:-${POST_DIR}/compacted}"
ANALYSIS_DIR="${DFTRACER_PIPELINE_ANALYSIS_DIR:-${WORKSPACE_ROOT}/artifacts/terminal_default/analysis}"

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

if [[ ! -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  echo "venv python not found at ${ROOT_DIR}/.venv/bin/python. Run ./scripts/install.sh first."
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

if [[ -n "${OPENAI_MODEL:-}" && -z "${GOOSE_MODEL:-}" ]]; then
  export GOOSE_MODEL="${OPENAI_MODEL}"
fi
if [[ -z "${GOOSE_PROVIDER:-}" ]]; then
  export GOOSE_PROVIDER="openai"
fi

export PYTHONUNBUFFERED=1

mkdir -p "${ROOT_DIR}/.cache/goose/pipeline_contexts" "${TRACE_DIR}" "${POST_DIR}" "${COMPACTED_TRACE_DIR}" "${ANALYSIS_DIR}"

CONTEXT_FILE="$(mktemp "${ROOT_DIR}/.cache/goose/pipeline_contexts/terminal_pipeline_XXXXXX.txt")"
cat >"${CONTEXT_FILE}" <<EOF
Application Name: ${NAME}
Repository URL: ${REPO_URL}
Repository Ref: ${REPO_REF}
Repository Directory: ${REPO_DIR}
Workspace Root: ${WORKSPACE_ROOT}
Language: ${LANGUAGE}
Workspace Venv: ${VENV_PREFIX}
Trace Directory: ${TRACE_DIR}
Postprocess Directory: ${POST_DIR}
Compacted Trace Directory: ${COMPACTED_TRACE_DIR}
Analysis Directory: ${ANALYSIS_DIR}
Goal: Plan the full DFTracer pipeline for the default IOR workflow.
EOF

echo "[goose-pipeline] recipe: ${RECIPE_PATH}" >&2
echo "[goose-pipeline] run_text: ${RUN_TEXT}" >&2
echo "[goose-pipeline] context: ${CONTEXT_FILE}" >&2
echo "[goose-pipeline] environment: OPENAI_BASE_URL=$([[ -n "${OPENAI_BASE_URL:-}" ]] && printf set || printf missing), OPENAI_MODEL=${OPENAI_MODEL:-missing}, OPENAI_API_KEY=$([[ -n "${OPENAI_API_KEY:-}" ]] && printf set || printf missing), LIVAI_BASE_URL=$([[ -n "${LIVAI_BASE_URL:-}" ]] && printf set || printf missing), LIVAI_MODEL=${LIVAI_MODEL:-missing}, LIVAI_API_KEY=$([[ -n "${LIVAI_API_KEY:-}" ]] && printf set || printf missing)" >&2
echo "[goose-pipeline] workspace_root: ${WORKSPACE_ROOT}" >&2
echo "[goose-pipeline] repo_dir: ${REPO_DIR}" >&2
echo "[goose-pipeline] venv_dir: ${VENV_PREFIX}" >&2
echo "[goose-pipeline] trace_dir: ${TRACE_DIR}" >&2
echo "[goose-pipeline] stage_timeout_seconds: ${STAGE_TIMEOUT_SECONDS}" >&2

cmd=(
  "${GOOSE_BIN}" run
  --recipe "${RECIPE_PATH}"
  --no-session
  --params "name=${NAME}"
  --params "repo_url=${REPO_URL}"
  --params "repo_ref=${REPO_REF}"
  --params "venv_dir=${VENV_PREFIX}"
  --params "trace_dir=${TRACE_DIR}"
  --params "post_dir=${POST_DIR}"
  --params "compacted_trace_dir=${COMPACTED_TRACE_DIR}"
  --params "analysis_dir=${ANALYSIS_DIR}"
  --params "language=${LANGUAGE}"
  --params "repo_dir=${REPO_DIR}"
)

echo "[goose-pipeline] command: ${cmd[*]}" >&2
if command -v timeout >/dev/null 2>&1; then
  exec timeout "${STAGE_TIMEOUT_SECONDS}" "${cmd[@]}" <<<"${RUN_TEXT}"
else
  exec "${cmd[@]}" <<<"${RUN_TEXT}"
fi

