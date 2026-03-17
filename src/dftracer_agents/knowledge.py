from __future__ import annotations

import json
import pathlib
import shlex
import textwrap
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BuildProfile:
    name: str
    cmake_flags: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def infer_build_profile(
    language: str,
    uses_mpi: bool,
    uses_hip: bool,
    auto_detect: bool,
    include_python_bindings: bool,
    enable_function_tracing: bool,
) -> BuildProfile:
    flags = [
        "-DDFTRACER_ENABLE_MPI={}".format("ON" if uses_mpi else "OFF"),
        "-DDFTRACER_ENABLE_HIP_TRACING={}".format("ON" if uses_hip else "OFF"),
        "-DDFTRACER_ENABLE_DYNAMIC_DETECTION={}".format("ON" if auto_detect else "OFF"),
        "-DDFTRACER_BUILD_PYTHON_BINDINGS={}".format(
            "ON" if include_python_bindings else "OFF"
        ),
        "-DDFTRACER_ENABLE_FTRACING={}".format("ON" if enable_function_tracing else "OFF"),
    ]

    notes = [
        "Required deps in DFTracer CMake include cpp-logger, brahma, yaml-cpp, zlib.",
        "Optional deps auto-activate when detected: MPI, HWLOC, rocprofiler-sdk (HIP tracing).",
    ]

    if language.lower() == "python":
        notes.extend(
            [
                "For pure Python workloads, start with pydftracer interface and only enable C++ bindings if needed.",
                "Python install path can be tuned using DFTRACER_PYTHON_SITE and DFTRACER_INSTALL_DIR.",
            ]
        )

    if enable_function_tracing:
        notes.append(
            "Function tracing mode expects compilation with instrumentation flags such as -finstrument-functions."
        )

    return BuildProfile(
        name="application-centric",
        cmake_flags=flags,
        env={
            "DFTRACER_BUILD_TYPE": "Release",
            "DFTRACER_ENABLE_MPI": "ON" if uses_mpi else "OFF",
            "DFTRACER_ENABLE_HIP_TRACING": "ON" if uses_hip else "OFF",
            "DFTRACER_ENABLE_DYNAMIC_DETECTION": "ON" if auto_detect else "OFF",
            "DFTRACER_ENABLE_FTRACING": "ON" if enable_function_tracing else "OFF",
        },
        notes=notes,
    )


def python_annotation_patterns() -> dict[str, Any]:
    return {
        "init": "from dftracer.python import dftracer, dft_fn\nlog_inst = dftracer.initialize_log(logfile=None, data_dir=None, process_id=-1)",
        "function_decorator": "@dft_fn(\"TRAIN_STEP\").log\ndef train_step(...):\n    ...",
        "finalize": "log_inst.finalize()",
        "notes": [
            "Set DFTRACER_LOG_FILE and DFTRACER_DATA_DIR when initialize_log uses defaults.",
            "For multiprocess workloads, call finalize in spawned paths where appropriate.",
            "Use semantic labels for AI phases (data_loader, forward, backward, checkpoint, eval).",
        ],
    }


def cpp_annotation_patterns() -> dict[str, Any]:
    return {
        "strategy": [
            "Prefer function-level instrumentation via DFTRACER_ENABLE_FTRACING=ON and compiler flag -finstrument-functions.",
            "Use DFTracer C++ interfaces for explicit region boundaries where needed.",
            "Keep hot-path annotations coarse-grained first, then refine around bottlenecks.",
        ],
        "compile_flags": ["-g", "-finstrument-functions", "-Wl,-E", "-fvisibility=default"],
    }


def runtime_env_template(app_name: str, data_dirs: list[str], output_prefix: str) -> dict[str, str]:
    return {
        "DFTRACER_ENABLE": "1",
        "DFTRACER_LOG_FILE": f"{output_prefix}/{app_name}",
        "DFTRACER_DATA_DIR": ":".join(data_dirs),
    }


def postprocess_commands(trace_dir: str, output_dir: str) -> list[str]:
    output_path = pathlib.Path(output_dir)
    compacted_dir = output_path / "compacted"
    index_dir = output_path / "index"
    app_name = pathlib.Path(trace_dir).name or "dftracer-run"
    quoted_output = shlex.quote(str(output_path))
    quoted_compacted = shlex.quote(str(compacted_dir))
    quoted_index = shlex.quote(str(index_dir))
    quoted_trace = shlex.quote(str(trace_dir))
    quoted_app = shlex.quote(app_name)

    split_cmd = textwrap.dedent(
        f"""
        if command -v dftracer-split >/dev/null 2>&1; then
          dftracer-split -d {quoted_trace} -o {quoted_compacted} -n {quoted_app} --index-dir {quoted_index} --verify
        else
          dftracer_split -d {quoted_trace} -o {quoted_compacted} -n {quoted_app} --index-dir {quoted_index} --verify
        fi
        """
    ).strip()

    return [
        f"mkdir -p {quoted_output} {quoted_compacted} {quoted_index}",
        split_cmd,
    ]


def layered_analysis_commands(
    trace_path: str,
    view_types: list[str],
    output_dir: str | None = None,
) -> list[str]:
    analysis_dir = output_dir or str(pathlib.Path(trace_path).parent / "dfanalyzer")
    quoted_trace = shlex.quote(str(trace_path))
    quoted_output = shlex.quote(str(analysis_dir))
    quoted_views = shlex.quote(json.dumps(view_types, separators=(",", ":")))
    command = textwrap.dedent(
        f"""
        if [[ -n "${{VIRTUAL_ENV:-}}" && -x "${{VIRTUAL_ENV}}/bin/dfanalyzer" ]]; then
          DFANALYZER_BIN="${{VIRTUAL_ENV}}/bin/dfanalyzer"
        else
          DFANALYZER_BIN="$(command -v dfanalyzer)"
        fi
        mkdir -p {quoted_output}
        "$DFANALYZER_BIN" trace_path={quoted_trace} view_types={quoted_views} hydra.run.dir={quoted_output}
        """
    ).strip()
    return [command]
