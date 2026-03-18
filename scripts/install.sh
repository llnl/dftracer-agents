#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
ENV_FILE="${ROOT_DIR}/.env"
CARGO_HOME_DEFAULT="/usr/workspace/iopp/cargo_cache"
LOCAL_CARGO_HOME_FALLBACK="${ROOT_DIR}/.cargo-goose"
RUSTUP_INIT_URL="https://sh.rustup.rs"
GNU_COMPILER_MODULE="PrgEnv-gnu/8.6.0"
GOOSE_SOURCE_DIR="${GOOSE_SOURCE_DIR:-${CARGO_HOME_DEFAULT}/goose}"
GOOSE_BUILD_DIR="${ROOT_DIR}/.cache/goose-build"
GOOSE_TARGET="x86_64-unknown-linux-gnu"
GOOSE_BINARY_PATH="${GOOSE_BUILD_DIR}/target/${GOOSE_TARGET}/release/goose"
GOOSE_VENV_LINK="${VENV_DIR}/bin/goose"
EFFECTIVE_CARGO_HOME=""
RUSTUP_BIN_DIR=""
SACP_CACHE_SOURCE="${ROOT_DIR}/.cargo-goose/registry/src/index.crates.io-1949cf8c6b5b557f/sacp-10.1.0"
GOOSE_EMBEDDED_CARGO_SRC="${GOOSE_SOURCE_DIR}/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f"
RMCP_CACHE_SOURCE="${GOOSE_EMBEDDED_CARGO_SRC}/rmcp-0.16.0"
RMCP_MACROS_CACHE_SOURCE="${GOOSE_EMBEDDED_CARGO_SRC}/rmcp-macros-0.16.0"
PCTX_CONFIG_CACHE_SOURCE="${ROOT_DIR}/.cargo-goose/registry/src/index.crates.io-1949cf8c6b5b557f/pctx_config-0.1.3"
PCTX_CODE_EXEC_RUNTIME_CACHE_SOURCE="${ROOT_DIR}/.cargo-goose/registry/src/index.crates.io-1949cf8c6b5b557f/pctx_code_execution_runtime-0.1.3"

log() {
  printf '[dftracer-agents] %s\n' "$*"
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    log "missing required command: ${command_name}"
    exit 1
  fi
}

load_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    log "loading ${ENV_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

load_modules_if_available() {
  if type module >/dev/null 2>&1; then
    return 0
  fi

  if [[ -f /etc/profile.d/modules.sh ]]; then
    # shellcheck disable=SC1091
    source /etc/profile.d/modules.sh
  elif [[ -f /usr/share/lmod/lmod/init/bash ]]; then
    # shellcheck disable=SC1091
    source /usr/share/lmod/lmod/init/bash
  fi

  type module >/dev/null 2>&1
}

configure_compiler_toolchain() {
  if load_modules_if_available; then
    if module -t list 2>&1 | grep -q '^PrgEnv-cray$'; then
      log "detected PrgEnv-cray; switching to ${GNU_COMPILER_MODULE}"
      module unload PrgEnv-cray || true
    fi
    log "loading compiler module ${GNU_COMPILER_MODULE}"
    module load "${GNU_COMPILER_MODULE}"
  else
    log "module command not available; using current compiler environment"
  fi

  require_command gcc
  require_command g++

  CC="$(command -v gcc)"
  CXX="$(command -v g++)"
  AR="$(command -v ar)"
  CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="${CC}"
  export CC
  export CXX
  export AR
  export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER
  export CXXSTDLIB=stdc++

  log "using C compiler: ${CC}"
  log "using C++ compiler: ${CXX}"
  log "using Rust linker: ${CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER}"
}

cargo_home_has_goose_git_deps() {
  local cargo_home="$1"

  [[ -d "${cargo_home}/git/checkouts" ]] || return 1
  compgen -G "${cargo_home}/git/checkouts/llama-cpp-rs-*" >/dev/null 2>&1
}

cargo_home_has_crate() {
  local cargo_home="$1"
  local crate_name="$2"

  compgen -G "${cargo_home}/registry/cache/*/${crate_name}" >/dev/null 2>&1
}

lockfile_has_package_version() {
  local lockfile_path="$1"
  local package_name="$2"
  local package_version="$3"

  python - <<'PY' "$lockfile_path" "$package_name" "$package_version"
from pathlib import Path
import sys

lockfile = Path(sys.argv[1])
package_name = sys.argv[2]
package_version = sys.argv[3]

text = lockfile.read_text() if lockfile.exists() else ""
needle = f'name = "{package_name}"\nversion = "{package_version}"'
raise SystemExit(0 if needle in text else 1)
PY
}

resolve_cargo_home() {
  local requested_cargo_home
  requested_cargo_home="${CARGO_HOME:-${CARGO_HOME_DEFAULT}}"

  if cargo_home_has_goose_git_deps "${requested_cargo_home}"; then
    EFFECTIVE_CARGO_HOME="${requested_cargo_home}"
    return 0
  fi

  if cargo_home_has_goose_git_deps "${LOCAL_CARGO_HOME_FALLBACK}"; then
    EFFECTIVE_CARGO_HOME="${LOCAL_CARGO_HOME_FALLBACK}"
    log "offline Goose git cache missing in ${requested_cargo_home}; using ${EFFECTIVE_CARGO_HOME}"
    return 0
  fi

  log "no offline cargo cache contains the Goose llama-cpp-rs checkout"
  log "checked: ${requested_cargo_home} and ${LOCAL_CARGO_HOME_FALLBACK}"
  exit 1
}

resolve_rustup_bin_dir() {
  local requested_cargo_home
  requested_cargo_home="${CARGO_HOME:-${CARGO_HOME_DEFAULT}}"

  if command -v rustup >/dev/null 2>&1; then
    RUSTUP_BIN_DIR="$(dirname "$(command -v rustup)")"
    log "using existing rustup at ${RUSTUP_BIN_DIR}/rustup"
    return 0
  fi

  if [[ -x "${requested_cargo_home}/bin/rustup" ]]; then
    RUSTUP_BIN_DIR="${requested_cargo_home}/bin"
    log "using rustup from ${RUSTUP_BIN_DIR}/rustup"
    return 0
  fi

  if [[ -x "${CARGO_HOME_DEFAULT}/bin/rustup" ]]; then
    RUSTUP_BIN_DIR="${CARGO_HOME_DEFAULT}/bin"
    log "using rustup from ${RUSTUP_BIN_DIR}/rustup"
    return 0
  fi

  require_command curl
  log "installing rustup into ${requested_cargo_home}"
  export CARGO_HOME="${requested_cargo_home}"
  export RUSTUP_HOME="${RUSTUP_HOME:-${HOME}/.rustup}"
  export RUSTUP_INIT_SKIP_PATH_CHECK=yes
  curl --proto '=https' --tlsv1.2 -sSf "${RUSTUP_INIT_URL}" | sh -s -- -y
  RUSTUP_BIN_DIR="${requested_cargo_home}/bin"
}

prepare_goose_build_dir() {
  mkdir -p "$(dirname "${GOOSE_BUILD_DIR}")"
  rm -rf "${GOOSE_BUILD_DIR}"
  mkdir -p "${GOOSE_BUILD_DIR}"
  (
    cd "${GOOSE_SOURCE_DIR}"
    tar \
      --exclude='./.git' \
      --exclude='./.cargo' \
      --exclude='./target' \
      -cf - .
  ) | (
    cd "${GOOSE_BUILD_DIR}"
    tar -xf -
  )
}

patch_goose_offline_dependencies() {
  local goose_mcp_manifest
  local local_sacp_dir
  local local_rmcp_dir
  local local_rmcp_macros_dir
  local local_pctx_config_dir
  local local_pctx_code_exec_runtime_dir
  local goose_workspace_manifest
  goose_mcp_manifest="${GOOSE_BUILD_DIR}/crates/goose-mcp/Cargo.toml"
  local_sacp_dir="${GOOSE_BUILD_DIR}/vendor/sacp"
  local_rmcp_dir="${GOOSE_BUILD_DIR}/vendor/rmcp"
  local_rmcp_macros_dir="${GOOSE_BUILD_DIR}/vendor/rmcp-macros"
  local_pctx_config_dir="${GOOSE_BUILD_DIR}/vendor/pctx_config"
  local_pctx_code_exec_runtime_dir="${GOOSE_BUILD_DIR}/vendor/pctx_code_execution_runtime"
  goose_workspace_manifest="${GOOSE_BUILD_DIR}/Cargo.toml"

  if ! cargo_home_has_crate "${CARGO_HOME}" "image-0.24.9.crate"; then
    if ! cargo_home_has_crate "${CARGO_HOME}" "image-0.25.9.crate"; then
      log "offline cargo cache is missing both image-0.24.9 and image-0.25.9"
      exit 1
    fi

    log "patching Goose image dependency from 0.24.9 to cached 0.25.9 with offline-safe features"
    sed -i 's/image = { version = "0.24.9", features = \["jpeg"\] }/image = { version = "0.25.9", default-features = false, features = ["jpeg", "png"] }/' "${goose_mcp_manifest}"
  fi

  if [[ -d "${SACP_CACHE_SOURCE}" ]]; then
    mkdir -p "${GOOSE_BUILD_DIR}/vendor"
    rm -rf "${local_sacp_dir}"
    cp -a "${SACP_CACHE_SOURCE}" "${local_sacp_dir}"
    sed -i 's/version = "0.12.0"/version = "0.16.0"/' "${local_sacp_dir}/Cargo.toml"
    python - <<'PY' "${local_sacp_dir}/src/mcp_server/builder.rs"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = """        output_schema: schema_for_output::<M::Output>().ok(),\n        annotations: None,\n        icons: None,\n        meta: None,\n"""
new = """        output_schema: schema_for_output::<M::Output>().ok(),\n        annotations: None,\n        execution: None,\n        icons: None,\n        meta: None,\n"""
if old in text:
    text = text.replace(old, new)
path.write_text(text)
PY
    if ! grep -q '^sacp = { path = "vendor/sacp" }$' "${goose_workspace_manifest}"; then
      sed -i '/^\[patch\.crates-io\]$/a sacp = { path = "vendor/sacp" }' "${goose_workspace_manifest}"
    fi
  fi

  if [[ -d "${RMCP_CACHE_SOURCE}" ]]; then
    mkdir -p "${GOOSE_BUILD_DIR}/vendor"
    rm -rf "${local_rmcp_dir}"
    cp -a "${RMCP_CACHE_SOURCE}" "${local_rmcp_dir}"
    if ! grep -q '^rmcp = { path = "vendor/rmcp" }$' "${goose_workspace_manifest}"; then
      sed -i '/^\[patch\.crates-io\]$/a rmcp = { path = "vendor/rmcp" }' "${goose_workspace_manifest}"
    fi
  fi

  if [[ -d "${RMCP_MACROS_CACHE_SOURCE}" ]]; then
    mkdir -p "${GOOSE_BUILD_DIR}/vendor"
    rm -rf "${local_rmcp_macros_dir}"
    cp -a "${RMCP_MACROS_CACHE_SOURCE}" "${local_rmcp_macros_dir}"
    if ! grep -q '^rmcp-macros = { path = "vendor/rmcp-macros" }$' "${goose_workspace_manifest}"; then
      sed -i '/^\[patch\.crates-io\]$/a rmcp-macros = { path = "vendor/rmcp-macros" }' "${goose_workspace_manifest}"
    fi
  fi

  if [[ -d "${PCTX_CONFIG_CACHE_SOURCE}" ]]; then
    mkdir -p "${GOOSE_BUILD_DIR}/vendor"
    rm -rf "${local_pctx_config_dir}"
    cp -a "${PCTX_CONFIG_CACHE_SOURCE}" "${local_pctx_config_dir}"
    python - <<'PY' "${local_pctx_config_dir}/Cargo.toml"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
text = text.replace('[dependencies.rmcp]\nversion = "0.14"', '[dependencies.rmcp]\nversion = "0.16.0"')
text = text.replace('[dependencies.reqwest]\nversion = "0.12.28"', '[dependencies.reqwest]\nversion = "0.13.2"')
text = text.replace('rustls-tls-native-roots', 'rustls')
path.write_text(text)
PY
    if ! grep -q '^pctx_config = { path = "vendor/pctx_config" }$' "${goose_workspace_manifest}"; then
      sed -i '/^\[patch\.crates-io\]$/a pctx_config = { path = "vendor/pctx_config" }' "${goose_workspace_manifest}"
    fi
  fi

  if [[ -d "${PCTX_CODE_EXEC_RUNTIME_CACHE_SOURCE}" ]]; then
    mkdir -p "${GOOSE_BUILD_DIR}/vendor"
    rm -rf "${local_pctx_code_exec_runtime_dir}"
    cp -a "${PCTX_CODE_EXEC_RUNTIME_CACHE_SOURCE}" "${local_pctx_code_exec_runtime_dir}"
    python - <<'PY' "${local_pctx_code_exec_runtime_dir}/Cargo.toml"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
text = text.replace('[dependencies.rmcp]\nversion = "0.14"', '[dependencies.rmcp]\nversion = "0.16.0"')
text = text.replace('[build-dependencies.rmcp]\nversion = "0.14"', '[build-dependencies.rmcp]\nversion = "0.16.0"')
path.write_text(text)
PY
    if ! grep -q '^pctx_code_execution_runtime = { path = "vendor/pctx_code_execution_runtime" }$' "${goose_workspace_manifest}"; then
      sed -i '/^\[patch\.crates-io\]$/a pctx_code_execution_runtime = { path = "vendor/pctx_code_execution_runtime" }' "${goose_workspace_manifest}"
    fi
  fi

  (
    cd "${GOOSE_BUILD_DIR}"
    if cargo_home_has_crate "${CARGO_HOME}" "image-0.25.9.crate"; then
      cargo update --offline -p image@0.24.9 --precise 0.25.9 || true
    fi
    if [[ -d "${SACP_CACHE_SOURCE}" ]]; then
      cargo update --offline -p sacp
    fi
    if [[ -d "${RMCP_CACHE_SOURCE}" ]]; then
      if lockfile_has_package_version Cargo.lock rmcp 0.12.0; then
        cargo update --offline -p rmcp@0.12.0 --precise 0.16.0
      fi
      if lockfile_has_package_version Cargo.lock rmcp 0.14.0; then
        cargo update --offline -p rmcp@0.14.0 --precise 0.16.0
      fi
      if lockfile_has_package_version Cargo.lock rmcp-macros 0.12.0; then
        cargo update --offline -p rmcp-macros@0.12.0 --precise 0.16.0
      fi
      if lockfile_has_package_version Cargo.lock rmcp-macros 0.14.0; then
        cargo update --offline -p rmcp-macros@0.14.0 --precise 0.16.0
      fi
    fi
    if [[ -d "${PCTX_CONFIG_CACHE_SOURCE}" ]]; then
      cargo update --offline -p pctx_config || true
    fi
    if [[ -d "${PCTX_CODE_EXEC_RUNTIME_CACHE_SOURCE}" ]]; then
      cargo update --offline -p pctx_code_execution_runtime || true
    fi
  )
}

ensure_latest_rust() {
  export RUSTUP_HOME="${RUSTUP_HOME:-${HOME}/.rustup}"
  resolve_rustup_bin_dir
  export PATH="${RUSTUP_BIN_DIR}:${PATH}"

  log "updating Rust stable toolchain to latest"
  rustup self update
  rustup update stable
  rustup default stable
  rustup target add "${GOOSE_TARGET}"
}

ensure_python_env() {
  require_command python3

  if [[ ! -d "${VENV_DIR}" ]]; then
    log "creating virtualenv at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
  fi

  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"

  log "upgrading Python packaging tools"
  python -m pip install --upgrade pip setuptools wheel

  log "installing Python dependencies and project"
  python -m pip install -e "${ROOT_DIR}"
}

build_goose_cli() {
  resolve_cargo_home
  export CARGO_HOME="${EFFECTIVE_CARGO_HOME}"
  export PATH="${RUSTUP_BIN_DIR}:${PATH}"

  require_command cargo

  if [[ ! -d "${GOOSE_SOURCE_DIR}" ]]; then
    log "goose source cache not found at ${GOOSE_SOURCE_DIR}"
    exit 1
  fi

  prepare_goose_build_dir
  patch_goose_offline_dependencies

  log "building Goose CLI from ${GOOSE_BUILD_DIR}"
  (
    cd "${GOOSE_BUILD_DIR}"
    cargo build \
      --offline \
      --locked \
      --release \
      --target "${GOOSE_TARGET}" \
      --package goose-cli \
      --bin goose
  )

  if [[ ! -x "${GOOSE_BINARY_PATH}" ]]; then
    log "expected Goose binary missing at ${GOOSE_BINARY_PATH}"
    exit 1
  fi

  ln -sfn "${GOOSE_BINARY_PATH}" "${GOOSE_VENV_LINK}"
  log "linked Goose CLI into ${GOOSE_VENV_LINK}"
}

main() {
  log "root=${ROOT_DIR}"
  log "userspace-only install"

  load_env_file
  configure_compiler_toolchain
  ensure_latest_rust
  ensure_python_env
  build_goose_cli

  log "installation complete"
  printf '\n'
  log "next steps:"
  log "1. cp .env.example .env and fill in your API settings if needed"
  log "2. source ${VENV_DIR}/bin/activate"
  log "3. dftracer-agents-run"
  log "4. goose --help"
}

main "$@"
