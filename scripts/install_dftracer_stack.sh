#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <workspace-root> [dftracer-ref]"
  exit 1
fi

WORKSPACE_ROOT="$(cd "$1" && pwd)"
DFTRACER_REF="${2:-main}"
DFTRACER_GCC_MODULE="${DFTRACER_GCC_MODULE:-gcc/12.2}"
EXTERNAL_DIR="${WORKSPACE_ROOT}/external"
BUILD_DIR="${WORKSPACE_ROOT}/build/dftracer"
INSTALL_DIR="${WORKSPACE_ROOT}/install"
VENV_DIR="${WORKSPACE_ROOT}/venv"
DFTRACER_SRC="${EXTERNAL_DIR}/dftracer"
DFTRACER_UTILS_SRC="${EXTERNAL_DIR}/dftracer-utils"
DFANALYZER_SRC="${EXTERNAL_DIR}/dfanalyzer"
PYTHON_BIN="${VENV_DIR}/bin/python"

mkdir -p "${EXTERNAL_DIR}" "${BUILD_DIR}" "${INSTALL_DIR}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install 'dftracer[dfanalyzer]' ipywidgets pandas matplotlib

load_modules_if_available() {
  if command -v module >/dev/null 2>&1; then
    return 0
  fi

  for init_script in \
    /etc/profile.d/modules.sh \
    /usr/share/lmod/lmod/init/bash \
    /usr/share/Modules/init/bash; do
    if [[ -f "${init_script}" ]]; then
      # shellcheck source=/dev/null
      source "${init_script}"
      break
    fi
  done

  command -v module >/dev/null 2>&1
}

clone_or_update() {
  local repo_url="$1"
  local repo_dir="$2"
  local ref="${3:-main}"

  if [[ -d "${repo_dir}/.git" ]]; then
    git -C "${repo_dir}" fetch --all --tags
    git -C "${repo_dir}" checkout "${ref}"
    git -C "${repo_dir}" pull --ff-only || true
  else
    git clone --branch "${ref}" "${repo_url}" "${repo_dir}"
  fi
}

clone_or_update https://github.com/llnl/dftracer "${DFTRACER_SRC}" "${DFTRACER_REF}"
clone_or_update https://github.com/llnl/dftracer-utils "${DFTRACER_UTILS_SRC}" main
clone_or_update https://github.com/llnl/dfanalyzer "${DFANALYZER_SRC}" main

for repo_dir in "${DFTRACER_UTILS_SRC}"; do
  if [[ -f "${repo_dir}/pyproject.toml" || -f "${repo_dir}/setup.py" ]]; then
    python -m pip install -e "${repo_dir}" || echo "warning: failed to install ${repo_dir} into workspace venv"
  fi
done

if ! command -v cmake >/dev/null 2>&1; then
  echo "cmake not found; installed Python packages only"
  exit 0
fi

if [[ -n "${DFTRACER_GCC_MODULE}" ]]; then
  if load_modules_if_available; then
    echo "loading compiler module ${DFTRACER_GCC_MODULE}"
    if module -t list 2>&1 | grep -q '^PrgEnv-cray$'; then
      echo "detected PrgEnv-cray; switching to PrgEnv-gnu"
      module unload PrgEnv-cray || true
      module load PrgEnv-gnu || true
    fi
    module load "${DFTRACER_GCC_MODULE}"
  else
    echo "module command not available; continuing with current compiler"
  fi
fi

if command -v gcc >/dev/null 2>&1 && command -v g++ >/dev/null 2>&1; then
  CC="$(which gcc)"
  CXX="$(which g++)"
  export CC
  export CXX
  echo "using C compiler: ${CC}"
  echo "using C++ compiler: ${CXX}"
else
  echo "gcc/g++ not found in PATH after module load"
  exit 1
fi

# CMake caches compiler choices. If this directory was previously configured
# with Cray wrappers, clear cache to force re-detection using CC/CXX.
rm -f "${BUILD_DIR}/CMakeCache.txt"
rm -rf "${BUILD_DIR}/CMakeFiles"

cmake -S "${DFTRACER_SRC}" -B "${BUILD_DIR}" \
  -DCMAKE_C_COMPILER="${CC}" \
  -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${INSTALL_DIR}" \
  -DDFTRACER_BUILD_PYTHON_BINDINGS=ON \
  -DDFTRACER_ENABLE_DYNAMIC_DETECTION=ON

if [[ -f "${BUILD_DIR}/CMakeCache.txt" ]]; then
  C_COMPILER_IN_CACHE="$(grep '^CMAKE_C_COMPILER:FILEPATH=' "${BUILD_DIR}/CMakeCache.txt" | cut -d= -f2-)"
  CXX_COMPILER_IN_CACHE="$(grep '^CMAKE_CXX_COMPILER:FILEPATH=' "${BUILD_DIR}/CMakeCache.txt" | cut -d= -f2-)"
  echo "cmake cache C compiler: ${C_COMPILER_IN_CACHE}"
  echo "cmake cache C++ compiler: ${CXX_COMPILER_IN_CACHE}"
fi

cmake --build "${BUILD_DIR}" -j
cmake --install "${BUILD_DIR}"

echo "dftracer stack prepared"
echo "workspace: ${WORKSPACE_ROOT}"
echo "venv: ${VENV_DIR}"
echo "install prefix: ${INSTALL_DIR}"
echo "add to env: PATH=${INSTALL_DIR}/bin:$PATH"
echo "            LD_LIBRARY_PATH=${INSTALL_DIR}/lib:${INSTALL_DIR}/lib64:$LD_LIBRARY_PATH"
