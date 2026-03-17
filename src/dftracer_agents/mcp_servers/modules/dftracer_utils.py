from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...knowledge import postprocess_commands


def generate_postprocess_plan(trace_dir: str, output_dir: str = "./post") -> dict[str, Any]:
    """Generate post-processing command chain using dftracer-utils."""
    return {
        "commands": postprocess_commands(trace_dir=trace_dir, output_dir=output_dir),
        "notes": [
            "Use dftracer-split to compact the raw trace directory and build an index in one step.",
            "The compacted output directory is the preferred input for downstream DFAnalyzer runs.",
        ],
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(generate_postprocess_plan)
