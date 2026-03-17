from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from mcp.server.fastmcp import FastMCP

from .shared import parse_module_tokens


def detect_available_modules(filter_text: str = "", limit: int = 200) -> dict[str, Any]:
    """Detect available environment modules and return compiler/MPI candidates."""
    avail = subprocess.run(
        "module avail 2>&1 || true",
        shell=True,
        executable="/bin/bash",
        text=True,
        capture_output=True,
    )
    modules = parse_module_tokens((avail.stdout or "") + "\n" + (avail.stderr or ""))

    needle = filter_text.strip().lower()
    if needle:
        modules = [module for module in modules if needle in module.lower()]

    modules = modules[: max(1, limit)]

    loaded = subprocess.run(
        "module list 2>&1 || true",
        shell=True,
        executable="/bin/bash",
        text=True,
        capture_output=True,
    )
    loaded_modules = parse_module_tokens((loaded.stdout or "") + "\n" + (loaded.stderr or ""))

    compiler_candidates = [
        module
        for module in modules
        if any(key in module.lower() for key in ("gcc", "llvm", "intel", "oneapi", "nvhpc", "aocc"))
    ]
    mpi_candidates = [module for module in modules if any(key in module.lower() for key in ("openmpi", "mpich", "mvapich", "mpi"))]

    return {
        "ok": True,
        "module_count": len(modules),
        "modules": modules,
        "loaded_modules": loaded_modules,
        "compiler_candidates": compiler_candidates,
        "mpi_candidates": mpi_candidates,
    }


def _discover_dftracer_cmake_config_dir() -> str:
    candidates: list[pathlib.Path] = []

    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv:
        venv_path = pathlib.Path(venv)
        candidates.extend(
            [
                venv_path / "lib64" / "cmake" / "dftracer",
                venv_path / "lib" / "cmake" / "dftracer",
            ]
        )
        for pybase in (venv_path / "lib", venv_path / "lib64"):
            if pybase.exists():
                for pydir in pybase.glob("python*/site-packages/dftracer/lib64/cmake/dftracer"):
                    candidates.append(pydir)
                for pydir in pybase.glob("python*/site-packages/dftracer/lib/cmake/dftracer"):
                    candidates.append(pydir)

    for site_package in [pathlib.Path(path) for path in sys.path if path]:
        if "site-packages" not in str(site_package):
            continue
        candidates.append(site_package / "dftracer" / "lib64" / "cmake" / "dftracer")
        candidates.append(site_package / "dftracer" / "lib" / "cmake" / "dftracer")

    for candidate in candidates:
        if (candidate / "dftracer-config.cmake").exists():
            return str(candidate.resolve())
    return ""


def _parse_cmake_probe_output(text: str) -> dict[str, Any]:
    vars_out: dict[str, Any] = {}
    targets: dict[str, dict[str, Any]] = {}

    for raw in text.splitlines():
        line = raw.strip()
        m_var = re.search(r"MCP_VAR:([^=]+)=(.*)$", line)
        if m_var:
            key = m_var.group(1).strip()
            value = m_var.group(2)
            if value == "__UNDEFINED__":
                vars_out[key] = None
            elif "<SEP>" in value:
                vars_out[key] = [item for item in value.split("<SEP>") if item]
            else:
                vars_out[key] = value
            continue

        m_tgt = re.search(r"MCP_TARGET:([^:]+):([^=]+)=(.*)$", line)
        if m_tgt:
            tgt = m_tgt.group(1).strip()
            prop = m_tgt.group(2).strip()
            value = m_tgt.group(3)
            if value == "__UNDEFINED__":
                parsed: Any = None
            elif "<SEP>" in value:
                parsed = [item for item in value.split("<SEP>") if item]
            else:
                parsed = value
            targets.setdefault(tgt, {})[prop] = parsed

    return {"variables": vars_out, "targets": targets}


def resolve_cmake_package_variables(
    package_name: str = "dftracer",
    cmake_config_dir: str = "",
    cmake_prefix_hint: str = "",
    query_vars: list[str] | None = None,
) -> dict[str, Any]:
    """Use CMake to load package config files and return requested variables."""
    cmake_exe = shutil.which("cmake")
    if not cmake_exe:
        return {"ok": False, "error": "cmake executable not found in PATH."}

    pkg = package_name.strip() or "dftracer"
    cfg_dir = cmake_config_dir.strip()
    if not cfg_dir and pkg.lower() == "dftracer":
        cfg_dir = _discover_dftracer_cmake_config_dir()

    vars_requested = query_vars or [
        "DFTRACER_INCLUDE_DIRS",
        "DFTRACER_LIBRARY_DIRS",
        "DFTRACER_LIBRARIES",
        "DFTRACER_VERSION",
    ]

    cmake_script = """
cmake_minimum_required(VERSION 3.16)
project(dftracer_cmake_probe LANGUAGES C CXX)

if(DEFINED CMAKE_PREFIX_HINT AND NOT CMAKE_PREFIX_HINT STREQUAL "")
    list(PREPEND CMAKE_PREFIX_PATH "${CMAKE_PREFIX_HINT}")
endif()

if(DEFINED CMAKE_CONFIG_DIR AND NOT CMAKE_CONFIG_DIR STREQUAL "")
    set(${PACKAGE_NAME}_DIR "${CMAKE_CONFIG_DIR}")
    get_filename_component(_pkg_prefix "${CMAKE_CONFIG_DIR}/../../.." ABSOLUTE)
    list(PREPEND CMAKE_PREFIX_PATH "${_pkg_prefix}")

    set(_dep_root "${CMAKE_CONFIG_DIR}/..")
    if(EXISTS "${_dep_root}/brahma")
        set(brahma_DIR "${_dep_root}/brahma")
    endif()
    if(EXISTS "${_dep_root}/cpp-logger")
        set(cpp-logger_DIR "${_dep_root}/cpp-logger")
    endif()
    if(EXISTS "${_dep_root}/gotcha")
        set(gotcha_DIR "${_dep_root}/gotcha")
    endif()
endif()

set(CMAKE_FIND_PACKAGE_PREFER_CONFIG ON)

if(NOT DEFINED QUERY_VARS OR QUERY_VARS STREQUAL "")
    set(QUERY_VARS DFTRACER_INCLUDE_DIRS;DFTRACER_LIBRARY_DIRS;DFTRACER_LIBRARIES;DFTRACER_VERSION)
endif()

if(PACKAGE_NAME STREQUAL "dftracer" AND NOT TARGET dftracer)
    add_library(dftracer INTERFACE)
endif()

find_package(${PACKAGE_NAME} CONFIG REQUIRED)

if(PACKAGE_NAME STREQUAL "dftracer" AND DEFINED CMAKE_CONFIG_DIR)
    set(_dftracer_targets_file "${CMAKE_CONFIG_DIR}/dftracer-targets.cmake")
    if(EXISTS "${_dftracer_targets_file}" AND NOT TARGET dftracer_core)
        include("${_dftracer_targets_file}")
    endif()
endif()

set(PROBE_OUT "${CMAKE_BINARY_DIR}/probe_vars.txt")
file(WRITE "${PROBE_OUT}" "")

foreach(_v IN LISTS QUERY_VARS)
    if(DEFINED ${_v})
        string(REPLACE ";" "<SEP>" _val "${${_v}}")
        file(APPEND "${PROBE_OUT}" "MCP_VAR:${_v}=${_val}\\n")
    else()
        file(APPEND "${PROBE_OUT}" "MCP_VAR:${_v}=__UNDEFINED__\\n")
    endif()
endforeach()

set(_targets ${PACKAGE_NAME} ${PACKAGE_NAME}_core ${PACKAGE_NAME}_core_dbg ${PACKAGE_NAME}_preload ${PACKAGE_NAME}_preload_dbg)
foreach(_t IN LISTS _targets)
    if(TARGET ${_t})
        get_target_property(_inc ${_t} INTERFACE_INCLUDE_DIRECTORIES)
        get_target_property(_lnk ${_t} INTERFACE_LINK_LIBRARIES)
        get_target_property(_loc ${_t} IMPORTED_LOCATION_RELEASE)
        if(_inc)
            string(REPLACE ";" "<SEP>" _inc_s "${_inc}")
            file(APPEND "${PROBE_OUT}" "MCP_TARGET:${_t}:INTERFACE_INCLUDE_DIRECTORIES=${_inc_s}\\n")
        else()
            file(APPEND "${PROBE_OUT}" "MCP_TARGET:${_t}:INTERFACE_INCLUDE_DIRECTORIES=__UNDEFINED__\\n")
        endif()
        if(_lnk)
            string(REPLACE ";" "<SEP>" _lnk_s "${_lnk}")
            file(APPEND "${PROBE_OUT}" "MCP_TARGET:${_t}:INTERFACE_LINK_LIBRARIES=${_lnk_s}\\n")
        else()
            file(APPEND "${PROBE_OUT}" "MCP_TARGET:${_t}:INTERFACE_LINK_LIBRARIES=__UNDEFINED__\\n")
        endif()
        if(_loc)
            file(APPEND "${PROBE_OUT}" "MCP_TARGET:${_t}:IMPORTED_LOCATION_RELEASE=${_loc}\\n")
        else()
            file(APPEND "${PROBE_OUT}" "MCP_TARGET:${_t}:IMPORTED_LOCATION_RELEASE=__UNDEFINED__\\n")
        endif()
    endif()
endforeach()
""".lstrip()

    with tempfile.TemporaryDirectory(prefix="dftracer_cmake_probe_") as td:
        src_dir = pathlib.Path(td) / "src"
        bld_dir = pathlib.Path(td) / "build"
        src_dir.mkdir(parents=True, exist_ok=True)
        bld_dir.mkdir(parents=True, exist_ok=True)
        script_path = src_dir / "CMakeLists.txt"
        script_path.write_text(cmake_script, encoding="utf-8")

        cmd = [
            cmake_exe,
            "-S",
            str(src_dir),
            "-B",
            str(bld_dir),
            f"-DPACKAGE_NAME={pkg}",
            f"-DQUERY_VARS={';'.join(vars_requested)}",
            f"-DCMAKE_CONFIG_DIR={cfg_dir}",
            f"-DCMAKE_PREFIX_HINT={cmake_prefix_hint.strip()}",
        ]

        proc = subprocess.run(cmd, text=True, capture_output=True)
        probe_file = bld_dir / "probe_vars.txt"
        merged = probe_file.read_text(encoding="utf-8", errors="ignore") if probe_file.exists() else (proc.stdout or "") + "\n" + (proc.stderr or "")
        parsed = _parse_cmake_probe_output(merged)
        return {
            "ok": proc.returncode == 0,
            "package_name": pkg,
            "cmake_config_dir": cfg_dir,
            "cmake_prefix_hint": cmake_prefix_hint.strip(),
            "query_vars": vars_requested,
            "cmake_command": cmd,
            "returncode": proc.returncode,
            "variables": parsed["variables"],
            "targets": parsed["targets"],
            "stderr": proc.stderr,
            "stdout": proc.stdout,
            "error": "" if proc.returncode == 0 else (proc.stderr.strip() or "cmake probe failed"),
        }


def register(mcp: FastMCP) -> None:
    mcp.tool()(detect_available_modules)
    mcp.tool()(resolve_cmake_package_variables)
