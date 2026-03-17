from __future__ import annotations

import os
import pathlib
import re
import shlex
import shutil
import subprocess
from typing import Any


def docs_context() -> dict[str, Any]:
    return {
        "primary_examples": "https://dftracer.readthedocs.io/en/latest/examples.html",
        "python_examples": "https://dftracer.readthedocs.io/projects/python/en/latest/examples.html",
        "api_reference": "https://dftracer.readthedocs.io/en/latest/api.html",
        "utils_cli": "https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html#dftracer-split",
        "analyzer_docs": "https://dftracer.readthedocs.io/projects/analyzer/en/latest/",
        "note": "Use these references as context for DFTracer inference, dftracer-utils post-processing, and DFAnalyzer execution decisions.",
    }


def parse_module_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", text)
    modules: set[str] = set()
    for line in cleaned.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("-") or raw.startswith("Lmod"):
            continue
        if raw.endswith(":") or raw.startswith("/"):
            continue
        for tok in raw.split():
            if "/" not in tok:
                continue
            if tok.startswith("(") and tok.endswith(")"):
                continue
            tok = tok.strip("()")
            if re.fullmatch(r"[A-Za-z0-9._+\-/]+", tok):
                modules.add(tok)
    return sorted(modules)


def run_shell_command(command: str, cwd: str | None, env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )
    return {
        "command": command,
        "cwd": cwd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "ok": result.returncode == 0,
    }


def run_command_list(
    commands: list[str],
    cwd: str | None,
    env: dict[str, str],
    continue_on_failure: bool = False,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for cmd in commands:
        step = run_shell_command(cmd, cwd=cwd, env=env)
        steps.append(step)
        if not step["ok"] and not continue_on_failure:
            break

    ok = all(step["ok"] for step in steps)
    return {
        "ok": ok,
        "steps": steps,
        "failed_step": next((step for step in steps if not step["ok"]), None),
    }


def enforce_install_prefix_commands(commands: list[str], install_prefix: pathlib.Path) -> list[str]:
    prefix = str(install_prefix)
    patched: list[str] = []

    for cmd in commands:
        new_cmd = cmd

        if "configure" in new_cmd:
            if re.search(r"--prefix(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)", new_cmd):
                new_cmd = re.sub(
                    r"--prefix(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)",
                    f"--prefix={prefix}",
                    new_cmd,
                )
            else:
                new_cmd = f"{new_cmd} --prefix={prefix}"

        if "cmake" in new_cmd and "--install" not in new_cmd:
            if re.search(r"-DCMAKE_INSTALL_PREFIX=(\"[^\"]*\"|'[^']*'|\S+)", new_cmd):
                new_cmd = re.sub(
                    r"-DCMAKE_INSTALL_PREFIX=(\"[^\"]*\"|'[^']*'|\S+)",
                    f"-DCMAKE_INSTALL_PREFIX={prefix}",
                    new_cmd,
                )
            elif " -S " in f" {new_cmd} " or " cmake .." in f" {new_cmd} ":
                new_cmd = f"{new_cmd} -DCMAKE_INSTALL_PREFIX={prefix}"

        if re.search(r"\bcmake\s+--install\b", new_cmd):
            if re.search(r"--prefix(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)", new_cmd):
                new_cmd = re.sub(
                    r"--prefix(=|\s+)(\"[^\"]*\"|'[^']*'|\S+)",
                    f"--prefix {prefix}",
                    new_cmd,
                )
            else:
                new_cmd = f"{new_cmd} --prefix {prefix}"

        if re.search(r"\bmake\b", new_cmd) and "install" in new_cmd:
            if re.search(r"\bprefix\s*=", new_cmd):
                new_cmd = re.sub(
                    r"\bprefix\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)",
                    f"prefix={prefix}",
                    new_cmd,
                )
            elif re.search(r"\bPREFIX\s*=", new_cmd):
                new_cmd = re.sub(
                    r"\bPREFIX\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)",
                    f"PREFIX={prefix}",
                    new_cmd,
                )
            elif not re.search(r"\bDESTDIR\s*=", new_cmd):
                new_cmd = f"{new_cmd} prefix={prefix}"

        patched.append(new_cmd)

    return patched


def is_placeholder_run_command(command: str) -> bool:
    raw = (command or "").strip().lower()
    if not raw:
        return True
    markers = (
        "full shell command",
        "run the baseline app test",
        "run the application including all required arguments",
        "<command>",
        "your command here",
    )
    return any(tok in raw for tok in markers)


def guess_fallback_run_command(repo: pathlib.Path, install_prefix: pathlib.Path, app_name: str) -> str:
    requested = (app_name or "").strip()
    names: list[str] = []
    if requested:
        names.append(requested)
    names.extend(["ior", "mdtest"])

    for name in names:
        candidates = [
            install_prefix / "bin" / name,
            repo / "src" / name,
            repo / name,
        ]
        exe = next((path for path in candidates if path.exists() and os.access(path, os.X_OK)), None)
        if exe is None:
            resolved = shutil.which(name)
            if resolved:
                exe = pathlib.Path(resolved)
        if exe is None:
            continue

        qexe = shlex.quote(str(exe))
        if name == "ior":
            return f"{qexe} -a POSIX -w -r -i 1 -b 1m -t 256k"
        return qexe

    return ""


def install_dftracer_for_profile(
    ws: pathlib.Path,
    profile_env: dict[str, str],
    base_env: dict[str, str],
) -> dict[str, Any]:
    env = dict(base_env)
    env.update(profile_env)
    env["DFTRACER_ENABLE_DYNAMIC_DETECTION"] = "ON"
    env["DFTRACER_BUILD_PYTHON_BINDINGS"] = "ON"
    env["DFTRACER_BUILD_TYPE"] = "Release"

    py = ws / "venv" / "bin" / "python"
    python_cmd = str(py) if py.exists() else "python3"
    bootstrap = run_command_list(
        [f"{python_cmd} -m pip install --upgrade pip setuptools wheel"],
        cwd=str(ws),
        env=env,
        continue_on_failure=False,
    )
    if not bootstrap["ok"]:
        return {
            "venv_python": str(py),
            "profile_env": profile_env,
            **bootstrap,
        }

    install_cmd = (
        "export CC=\"$(which gcc)\" "
        "&& export CXX=\"$(which g++)\" "
        f"&& {python_cmd} -m pip install -v --no-binary=dftracer --force-reinstall 'dftracer[dfanalyzer]'"
    )
    env["CC"] = shutil.which("gcc") or env.get("CC", "")
    env["CXX"] = shutil.which("g++") or env.get("CXX", "")
    install_step = run_shell_command(install_cmd, cwd=str(ws), env=env)
    install_log = (install_step.get("stdout") or "") + "\n" + (install_step.get("stderr") or "")

    required_flags = {
        "DFTRACER_ENABLE_MPI": env.get("DFTRACER_ENABLE_MPI", "OFF"),
        "DFTRACER_ENABLE_HIP_TRACING": env.get("DFTRACER_ENABLE_HIP_TRACING", "OFF"),
        "DFTRACER_ENABLE_DYNAMIC_DETECTION": env.get("DFTRACER_ENABLE_DYNAMIC_DETECTION", "ON"),
        "DFTRACER_BUILD_PYTHON_BINDINGS": env.get("DFTRACER_BUILD_PYTHON_BINDINGS", "ON"),
        "DFTRACER_BUILD_TYPE": env.get("DFTRACER_BUILD_TYPE", "Release"),
    }
    missing_flags: list[str] = []
    if install_step["ok"]:
        for key, value in required_flags.items():
            env_pat = re.search(rf"{key}\s*=\s*{re.escape(value)}", install_log)
            cmake_pat = re.search(rf"-D{key}(:[A-Z_]+)?={re.escape(value)}", install_log)
            if not (env_pat or cmake_pat):
                missing_flags.append(f"{key}={value}")

    if install_step["ok"] and missing_flags:
        install_step["ok"] = False
        install_step["returncode"] = 1
        install_step["stderr"] = (
            (install_step.get("stderr") or "")
            + "\nRequired DFTracer flags were not confirmed in verbose install logs: "
            + ", ".join(missing_flags)
        )

    show_step = run_shell_command(f"{python_cmd} -m pip show dftracer dftracer-analyzer", cwd=str(ws), env=env)
    if install_step["ok"] and not show_step["ok"]:
        install_step["ok"] = False
        install_step["returncode"] = 1
        install_step["stderr"] = (
            (install_step.get("stderr") or "")
            + "\nInstalled packages could not be verified via pip show dftracer dftracer-analyzer."
        )

    steps = [*bootstrap["steps"], install_step, show_step]
    run = {
        "ok": all(step["ok"] for step in steps),
        "steps": steps,
        "failed_step": next((step for step in steps if not step["ok"]), None),
    }
    return {
        "venv_python": str(py),
        "profile_env": profile_env,
        "required_flags": required_flags,
        "missing_flag_verification": missing_flags,
        **run,
    }


def compiler_first_line(binary_path: str) -> str:
    try:
        result = subprocess.run([binary_path, "--version"], text=True, capture_output=True)
        line = (result.stdout or result.stderr or "").splitlines()
        return line[0].strip() if line else ""
    except Exception:
        return ""


def select_compilers(uses_mpi: bool) -> dict[str, Any]:
    gcc = shutil.which("gcc")
    gxx = shutil.which("g++")
    if not gcc or not gxx:
        return {
            "ok": False,
            "error": "gcc/g++ not found in PATH.",
            "compiler_env": {},
            "versions": {},
        }

    versions = {
        "gcc": compiler_first_line(gcc),
        "g++": compiler_first_line(gxx),
    }
    compiler_env = {
        "CC": gcc,
        "CXX": gxx,
    }

    if uses_mpi:
        mpicc = shutil.which("mpicc")
        mpicxx = shutil.which("mpic++") or shutil.which("mpicxx")
        if not mpicc or not mpicxx:
            return {
                "ok": False,
                "error": "MPI requested but mpicc/mpic++ not found in PATH.",
                "compiler_env": {},
                "versions": versions,
            }
        compiler_env["CC"] = mpicc
        compiler_env["CXX"] = mpicxx
        compiler_env["MPICC"] = mpicc
        compiler_env["MPICXX"] = mpicxx

    compiler_env["CMAKE_C_COMPILER"] = compiler_env["CC"]
    compiler_env["CMAKE_CXX_COMPILER"] = compiler_env["CXX"]
    compiler_env["CMAKE_ARGS"] = (
        f"-DCMAKE_C_COMPILER={compiler_env['CC']} "
        f"-DCMAKE_CXX_COMPILER={compiler_env['CXX']}"
    )

    return {
        "ok": True,
        "error": "",
        "compiler_env": compiler_env,
        "versions": versions,
    }