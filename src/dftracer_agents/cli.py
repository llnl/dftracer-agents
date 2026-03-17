from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer

from .agent import run_interactive, run_single
from .pipeline import build_pipeline

app = typer.Typer(add_completion=False, help="DFTracer agent workflow CLI")


@app.command()
def run(
    prompt: Optional[str] = typer.Argument(None, help="One-shot prompt (omit for interactive REPL)"),
) -> None:
    """Start an interactive DFTracer agent session (or run a single prompt)."""
    if prompt:
        output = asyncio.run(run_single(prompt))
        typer.echo(output)
    else:
        asyncio.run(run_interactive())


@app.command()
def pipeline(
    app_name: str = typer.Option(..., help="Application name"),
    language: str = typer.Option(..., help="Language: cpp/c++/python"),
    trace_path: str = typer.Option(..., help="Trace directory path"),
    data_dir: list[str] = typer.Option([], help="Repeat for each data dir"),
    output_prefix: str = typer.Option("./traces", help="Trace/output prefix"),
    uses_mpi: bool = typer.Option(False, help="Enable MPI profile"),
    uses_hip: bool = typer.Option(False, help="Enable HIP profile"),
    auto_detect: bool = typer.Option(True, help="Enable DFTracer dynamic detection"),
    function_tracing: bool = typer.Option(True, help="Enable finstrument tracing profile"),
    include_python_bindings: bool = typer.Option(True, help="Include DFTracer Python bindings"),
) -> None:
    """Generate an end-to-end pipeline plan in JSON."""
    result = build_pipeline(
        app_name=app_name,
        language=language,
        trace_path=trace_path,
        data_dirs=data_dir or ["$PWD"],
        output_prefix=output_prefix,
        uses_mpi=uses_mpi,
        uses_hip=uses_hip,
        auto_detect=auto_detect,
        enable_function_tracing=function_tracing,
        include_python_bindings=include_python_bindings,
    )
    typer.echo(json.dumps(result, indent=2))
