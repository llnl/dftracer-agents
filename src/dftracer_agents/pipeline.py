from __future__ import annotations

from typing import Any

from .knowledge import (
    cpp_annotation_patterns,
    infer_build_profile,
    layered_analysis_commands,
    postprocess_commands,
    python_annotation_patterns,
    runtime_env_template,
)


def build_pipeline(
    app_name: str,
    language: str,
    trace_path: str,
    data_dirs: list[str],
    output_prefix: str,
    uses_mpi: bool = False,
    uses_hip: bool = False,
    auto_detect: bool = True,
    enable_function_tracing: bool = True,
    include_python_bindings: bool = True,
    analysis_views: list[str] | None = None,
) -> dict[str, Any]:
    views = analysis_views or ["time_range"]

    profile = infer_build_profile(
        language=language,
        uses_mpi=uses_mpi,
        uses_hip=uses_hip,
        auto_detect=auto_detect,
        include_python_bindings=include_python_bindings,
        enable_function_tracing=enable_function_tracing,
    )

    annotations = (
        cpp_annotation_patterns()
        if language.lower() in {"cpp", "c++", "c"}
        else python_annotation_patterns()
    )

    runtime_env = runtime_env_template(
        app_name=app_name,
        data_dirs=data_dirs,
        output_prefix=output_prefix,
    )

    postprocess = postprocess_commands(trace_dir=trace_path, output_dir=f"{output_prefix}/post")
    analysis = layered_analysis_commands(
        trace_path=f"{output_prefix}/post/compacted",
        view_types=views,
        output_dir=f"{output_prefix}/analysis",
    )

    return {
        "application": app_name,
        "language": language,
        "dependency_profile": {
            "name": profile.name,
            "cmake_flags": profile.cmake_flags,
            "env": profile.env,
            "notes": profile.notes,
        },
        "annotation_plan": annotations,
        "compile_plan": {
            "cmake_configure": "cmake -S . -B build " + " ".join(profile.cmake_flags),
            "cmake_build": "cmake --build build -j",
            "manual_compile_hint": "Use -finstrument-functions when function tracing is enabled.",
        },
        "runtime_env": runtime_env,
        "postprocess": postprocess,
        "layered_analysis": analysis,
    }
