from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...knowledge import layered_analysis_commands


def generate_layered_analysis_plan(
    trace_path: str,
    view_types: list[str] | None = None,
    output_dir: str = "./analysis",
) -> dict[str, Any]:
    """Generate layered analysis commands with dfanalyzer."""
    views = view_types or ["time_range"]
    return {
        "commands": layered_analysis_commands(
            trace_path=trace_path,
            view_types=views,
            output_dir=output_dir,
        ),
        "notes": [
            "Uses the documented dfanalyzer CLI entrypoint.",
            "Point trace_path at the compacted postprocess output when dftracer-split has already been run.",
        ],
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(generate_layered_analysis_plan)
