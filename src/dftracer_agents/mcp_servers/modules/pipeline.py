from __future__ import annotations

import os
import pathlib
from typing import Any

from mcp.server.fastmcp import FastMCP

from ...knowledge import infer_build_profile, layered_analysis_commands, postprocess_commands, runtime_env_template
from ...pipeline import build_pipeline
from .dftracer import auto_annotate_application
from .shared import (
    docs_context,
    enforce_install_prefix_commands,
    guess_fallback_run_command,
    install_dftracer_for_profile,
    is_placeholder_run_command,
    run_command_list,
    run_shell_command,
    select_compilers,
)


def build_end_to_end_pipeline(
    app_name: str,
    language: str,
    trace_path: str,
    data_dirs: list[str],
    output_prefix: str = "./traces",
    uses_mpi: bool = False,
    uses_hip: bool = False,
    auto_detect: bool = True,
    enable_function_tracing: bool = True,
    include_python_bindings: bool = True,
    analysis_views: list[str] | None = None,
) -> dict[str, Any]:
    """Build a full DFTracer agent pipeline from instrumentation to post-processing and layered analysis."""
    result = build_pipeline(
        app_name=app_name,
        language=language,
        trace_path=trace_path,
        data_dirs=data_dirs,
        output_prefix=output_prefix,
        uses_mpi=uses_mpi,
        uses_hip=uses_hip,
        auto_detect=auto_detect,
        enable_function_tracing=enable_function_tracing,
        include_python_bindings=include_python_bindings,
        analysis_views=analysis_views,
    )
    result["docs_context"] = docs_context()
    return result


def execute_pipeline_stage(
    stage: str,
    workspace_root: str,
    repo_dir: str = "",
    trace_dir: str = "",
    install_prefix: str = "",
    language: str = "cpp",
    uses_mpi: bool = False,
    uses_hip: bool = False,
    build_commands: list[str] | None = None,
    app_command: str = "",
    app_name: str = "app",
    data_dirs: list[str] | None = None,
    output_dir: str = "",
    install_dftracer_on_detect: bool = True,
    auto_apply_annotations: bool = False,
    annotation_max_files: int = 20,
    patch_build_files: bool = True,
    dry_run: bool = False,
    continue_on_failure: bool = False,
) -> dict[str, Any]:
    """Execute one guided DFTracer stage and return structured command output."""
    stage_key = stage.strip().lower()
    ctx = docs_context()
    ws = pathlib.Path(workspace_root).expanduser().resolve()
    repo = pathlib.Path(repo_dir).expanduser().resolve() if repo_dir else ws
    trace = pathlib.Path(trace_dir).expanduser().resolve() if trace_dir else (ws / "traces")
    install = pathlib.Path(install_prefix).expanduser().resolve() if install_prefix else (ws / ".venv")

    if stage_key in {"test_default_build_setup", "build_with_dftracer"}:
        stage_key = "build_app"
    elif stage_key in {"test_default_run", "run_with_dftracer"}:
        stage_key = "run_app"

    env = os.environ.copy()
    compiler_info = select_compilers(uses_mpi=uses_mpi)
    if not compiler_info["ok"]:
        return {
            "stage": stage_key,
            "ok": False,
            "error": compiler_info["error"],
            "compiler_versions": compiler_info.get("versions", {}),
            "docs_context": ctx,
        }
    env.update(compiler_info["compiler_env"])

    if stage_key == "detect":
        profile = infer_build_profile(
            language=language,
            uses_mpi=uses_mpi,
            uses_hip=uses_hip,
            auto_detect=True,
            include_python_bindings=True,
            enable_function_tracing=True,
        )
        result: dict[str, Any] = {
            "stage": stage_key,
            "ok": True,
            "compiler_env": compiler_info["compiler_env"],
            "compiler_versions": compiler_info["versions"],
            "docs_context": ctx,
            "profile": {
                "name": profile.name,
                "cmake_flags": profile.cmake_flags,
                "env": profile.env,
                "notes": profile.notes,
            },
        }

        if install_dftracer_on_detect:
            install_run = install_dftracer_for_profile(ws=ws, profile_env=profile.env, base_env=env)
            result["install_dftracer"] = install_run
            result["ok"] = bool(install_run.get("ok", False))
        return result

    if stage_key == "build_app":
        commands = build_commands or []
        if not commands:
            return {
                "stage": stage_key,
                "ok": False,
                "error": "No build commands provided. Pass build_commands as a non-empty list.",
                "docs_context": ctx,
            }
        patched_commands = enforce_install_prefix_commands(commands, install)
        run = run_command_list(patched_commands, cwd=str(repo), env=env, continue_on_failure=continue_on_failure)
        return {
            "stage": stage_key,
            "workspace_root": str(ws),
            "repo_dir": str(repo),
            "install_prefix": str(install),
            "compiler_env": compiler_info["compiler_env"],
            "compiler_versions": compiler_info["versions"],
            "docs_context": ctx,
            "commands_rewritten": patched_commands != commands,
            "original_commands": commands,
            "effective_commands": patched_commands,
            **run,
        }

    if stage_key == "install_dftracer":
        profile = infer_build_profile(
            language=language,
            uses_mpi=uses_mpi,
            uses_hip=uses_hip,
            auto_detect=True,
            include_python_bindings=True,
            enable_function_tracing=True,
        )
        run = install_dftracer_for_profile(ws=ws, profile_env=profile.env, base_env=env)
        return {
            "stage": stage_key,
            "compiler_env": compiler_info["compiler_env"],
            "compiler_versions": compiler_info["versions"],
            "docs_context": ctx,
            **run,
        }

    if stage_key == "annotate":
        result: dict[str, Any] = {
            "stage": stage_key,
            "ok": True,
            "language": language,
            "docs_context": ctx,
        }
        if auto_apply_annotations:
            apply_out = auto_annotate_application(
                repo_dir=str(repo),
                language=language,
                max_files=annotation_max_files,
                patch_build_files=patch_build_files,
                dry_run=dry_run,
            )
            result["auto_annotation"] = apply_out
            result["ok"] = bool(apply_out.get("ok", False))
        return result

    if stage_key == "run_app":
        requested_app_command = app_command.strip()
        if not requested_app_command:
            return {
                "stage": stage_key,
                "ok": False,
                "error": "No app_command provided.",
                "docs_context": ctx,
            }

        effective_app_command = requested_app_command
        if is_placeholder_run_command(effective_app_command):
            guessed = guess_fallback_run_command(repo=repo, install_prefix=install, app_name=app_name)
            if guessed:
                effective_app_command = guessed
            else:
                return {
                    "stage": stage_key,
                    "ok": False,
                    "error": (
                        "app_command looks like placeholder text and no runnable fallback binary was found. "
                        "Provide a concrete shell command (e.g. /path/to/ior -a POSIX -w -r -i 1 -b 1m -t 256k)."
                    ),
                    "requested_app_command": requested_app_command,
                    "docs_context": ctx,
                }

        dirs = data_dirs or [str(repo)]
        runtime_env = runtime_env_template(app_name=app_name, data_dirs=dirs, output_prefix=str(trace))
        runtime_env.update(
            {
                "DFTRACER_ENABLE": "1",
                "DFTRACER_INC_METADATA": "1",
                "DFTRACER_METADATA_USE_POSIX": "1",
            }
        )
        env.update(runtime_env)
        step = run_shell_command(effective_app_command, cwd=str(repo), env=env)
        return {
            "stage": stage_key,
            "runtime_env": runtime_env,
            "compiler_env": compiler_info["compiler_env"],
            "compiler_versions": compiler_info["versions"],
            "docs_context": ctx,
            "requested_app_command": requested_app_command,
            "effective_app_command": effective_app_command,
            "ok": step["ok"],
            "steps": [step],
            "failed_step": None if step["ok"] else step,
        }

    if stage_key == "postprocess":
        post_out = pathlib.Path(output_dir).expanduser().resolve() if output_dir else (ws / "artifacts" / "postprocess")
        commands = postprocess_commands(trace_dir=str(trace), output_dir=str(post_out))
        run = run_command_list(commands, cwd=str(ws), env=env, continue_on_failure=continue_on_failure)
        return {
            "stage": stage_key,
            "trace_dir": str(trace),
            "output_dir": str(post_out),
            "compiler_env": compiler_info["compiler_env"],
            "compiler_versions": compiler_info["versions"],
            "docs_context": ctx,
            **run,
        }

    if stage_key == "dfanalyzer":
        analysis_out = pathlib.Path(output_dir).expanduser().resolve() if output_dir else (ws / "artifacts" / "analysis")
        analysis_trace = pathlib.Path(trace_dir).expanduser().resolve() if trace_dir else (ws / "artifacts" / "postprocess" / "compacted")
        commands = layered_analysis_commands(
            trace_path=str(analysis_trace),
            view_types=["time_range"],
            output_dir=str(analysis_out),
        )
        run = run_command_list(commands, cwd=str(ws), env=env, continue_on_failure=continue_on_failure)
        return {
            "stage": stage_key,
            "trace_dir": str(analysis_trace),
            "output_dir": str(analysis_out),
            "compiler_env": compiler_info["compiler_env"],
            "compiler_versions": compiler_info["versions"],
            "docs_context": ctx,
            **run,
        }

    return {
        "stage": stage_key,
        "ok": False,
        "error": "Unsupported stage. Use one of: detect, test_default_build_setup, test_default_run, install_dftracer, annotate, build_with_dftracer, run_with_dftracer, postprocess, dfanalyzer.",
        "docs_context": ctx,
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(build_end_to_end_pipeline)
    mcp.tool()(execute_pipeline_stage)
