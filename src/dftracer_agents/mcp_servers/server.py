from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .modules.dfanalyzer import generate_layered_analysis_plan
from .modules.dftracer import (
    annotate_and_create_patch,
    auto_annotate_application,
    detect_dftracer_profile,
    generate_annotation_plan,
    generate_cpp_compile_instructions,
    generate_runtime_env,
)
from .modules.dftracer_utils import generate_postprocess_plan
from .modules.environment import detect_available_modules, resolve_cmake_package_variables
from .modules.pipeline import build_end_to_end_pipeline, execute_pipeline_stage
from .registry import register_all

mcp = FastMCP("dftracer-pipeline-mcp")
register_all(mcp)


def main() -> None:
    mcp.run()

__all__ = [
    "mcp",
    "main",
    "detect_available_modules",
    "resolve_cmake_package_variables",
    "detect_dftracer_profile",
    "generate_annotation_plan",
    "generate_cpp_compile_instructions",
    "generate_runtime_env",
    "generate_postprocess_plan",
    "generate_layered_analysis_plan",
    "build_end_to_end_pipeline",
    "execute_pipeline_stage",
    "auto_annotate_application",
    "annotate_and_create_patch",
]


if __name__ == "__main__":
    main()
