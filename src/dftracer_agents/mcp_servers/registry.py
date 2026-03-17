from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .modules import dfanalyzer, dftracer, dftracer_utils, environment, pipeline


def register_all(mcp: FastMCP) -> None:
    environment.register(mcp)
    dftracer.register(mcp)
    dftracer_utils.register(mcp)
    dfanalyzer.register(mcp)
    pipeline.register(mcp)
