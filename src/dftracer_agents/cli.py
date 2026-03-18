from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import typer

from .agent import run_interactive, run_single
from .goose_pipeline import (
    DEFAULT_PIPELINE_LANGUAGE,
    DEFAULT_PIPELINE_NAME,
    DEFAULT_PIPELINE_REPO_REF,
    DEFAULT_PIPELINE_REPO_URL,
    goose_extension_command,
    run_terminal_goose_pipeline,
)
from .pipeline import build_pipeline

app = typer.Typer(add_completion=False, help="DFTracer agent workflow CLI")


def build_goose_extension_link(root: Path | None = None) -> str:
    base = root or Path(__file__).resolve().parents[2]
    extension_argv = shlex.split(goose_extension_command(base))
    query: list[tuple[str, str]] = [
        ("cmd", extension_argv[0]),
        ("id", "dftracer-pipeline-mcp"),
        ("name", "DFTracer Pipeline MCP"),
        (
            "description",
            "DFTracer instrumentation, build, postprocess, and analysis MCP tools",
        ),
    ]
    query.extend(("arg", arg) for arg in extension_argv[1:])
    return f"goose://extension?{urlencode(query, doseq=True)}"


def build_goose_session_command(root: Path | None = None) -> list[str]:
    base = root or Path(__file__).resolve().parents[2]
    return [
        str(base / "scripts" / "start_goose.sh"),
        "session",
        "--with-extension",
        goose_extension_command(base),
    ]


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


@app.command("goose-pipeline")
def goose_pipeline(
    name: str = typer.Option(DEFAULT_PIPELINE_NAME, help="Application name"),
    repo_url: str = typer.Option(DEFAULT_PIPELINE_REPO_URL, help="Repository URL"),
    repo_ref: str = typer.Option(DEFAULT_PIPELINE_REPO_REF, help="Repository ref/tag/branch"),
    language: str = typer.Option(DEFAULT_PIPELINE_LANGUAGE, help="Primary language"),
    workspace_root: str = typer.Option("", help="Workspace root override"),
    repo_dir: str = typer.Option("", help="Repository directory override"),
    venv_dir: str = typer.Option("", help="Virtual environment directory override"),
    trace_dir: str = typer.Option("", help="Trace directory override"),
    post_dir: str = typer.Option("", help="Postprocess directory override"),
    compacted_trace_dir: str = typer.Option("", help="Compacted trace directory override"),
    analysis_dir: str = typer.Option("", help="Analysis directory override"),
    quiet: bool = typer.Option(False, "--quiet-progress", help="Suppress stage progress on stderr"),
) -> None:
    """Run the Goose DFTracer pipeline recipe directly from the terminal."""
    result = run_terminal_goose_pipeline(
        root=Path(__file__).resolve().parents[2],
        name=name,
        repo_url=repo_url,
        repo_ref=repo_ref,
        language=language,
        workspace_root=workspace_root,
        repo_dir=repo_dir,
        venv_dir=venv_dir,
        trace_dir=trace_dir,
        post_dir=post_dir,
        compacted_trace_dir=compacted_trace_dir,
        analysis_dir=analysis_dir,
        progress=not quiet,
    )
    typer.echo(json.dumps(result, indent=2))


@app.command("goose-extension-link")
def goose_extension_link() -> None:
    """Print a goose:// deeplink that installs the DFTracer MCP server as a Goose extension."""
    typer.echo(build_goose_extension_link(Path(__file__).resolve().parents[2]))


@app.command("goose-session")
def goose_session() -> None:
    """Start a Goose session with the DFTracer MCP server attached as an extension."""
    cmd = build_goose_session_command(Path(__file__).resolve().parents[2])
    completed = subprocess.run(cmd, check=False)
    raise typer.Exit(completed.returncode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
