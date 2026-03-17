"""
DFTracer AI agent using the OpenAI Agents SDK.

Spawns the local modular MCP server package over stdio as a tool provider,
then runs an interactive or single-shot conversation via the configured
OpenAI-compatible endpoint (LIVAI or any other).

Usage:
  # Interactive REPL
  dftracer-agents-run

  # Single-shot (prompt via args or stdin)
  dftracer-agents-run "How do I annotate a Python training loop with DFTracer?"
  echo "..." | dftracer-agents-run

Environment (set in .env or export before running):
  OPENAI_API_KEY   — API key for the endpoint
  OPENAI_BASE_URL  — Base URL (e.g. https://livai-api.llnl.gov/v1)
  OPENAI_MODEL     — Model name (e.g. gpt-4o)
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncIterator

from agents import Agent, Runner
from agents.mcp import MCPServerStdio

SYSTEM_PROMPT = """\
You are DFTracer Agent, an expert in the DFTracer I/O tracing and analysis ecosystem.
You help users with:
- Selecting the right DFTracer build configuration (CMake flags, dependencies).
- Annotating C++ and Python workloads with DFTracer instrumentation.
- Generating DFTRACER_* runtime environment configurations.
- Post-processing trace data using dftracer-utils.
- Running layered analysis with dfanalyzer.

Always use the available tools to produce concrete, actionable outputs.
When a user describes their workload, gather needed details (language, MPI/HIP usage,
data directories, etc.) by asking, then call the appropriate tools to build a complete plan.
Prefer structured tool output over free-form text for build flags and commands.
"""


def _mcp_server_command() -> list[str]:
    """Return the command to spawn the MCP server in the current venv."""
    return [sys.executable, "-m", "dftracer_agents.mcp_servers.server"]


async def run_single(prompt: str) -> str:
    """Run a one-shot prompt and return the final text output."""
    cmd = _mcp_server_command()
    async with MCPServerStdio(
        params={"command": cmd[0], "args": cmd[1:]},
        name="dftracer-tools",
    ) as mcp_server:
        agent = Agent(
            name="DFTracer Agent",
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            instructions=SYSTEM_PROMPT,
            mcp_servers=[mcp_server],
        )
        result = await Runner.run(agent, prompt)
        return result.final_output


async def run_interactive() -> None:
    """Run a multi-turn interactive REPL session."""
    cmd = _mcp_server_command()
    async with MCPServerStdio(
        params={"command": cmd[0], "args": cmd[1:]},
        name="dftracer-tools",
    ) as mcp_server:
        agent = Agent(
            name="DFTracer Agent",
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            instructions=SYSTEM_PROMPT,
            mcp_servers=[mcp_server],
        )

        print("DFTracer Agent ready. Tools loaded from local MCP server.")
        print("Type your question and press Enter. Use Ctrl+D or 'exit' to quit.\n")

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", "bye"}:
                print("Bye.")
                break

            try:
                result = await Runner.run(agent, user_input)
                print(f"\nAgent: {result.final_output}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"\n[error] {exc}\n", file=sys.stderr)


def main() -> None:
    """Entry point: single-shot when args/stdin are provided, REPL otherwise."""
    # Load .env from project root if present (best-effort, no hard dep on dotenv)
    _try_load_dotenv()

    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        output = asyncio.run(run_single(prompt))
        print(output)
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
        if prompt:
            output = asyncio.run(run_single(prompt))
            print(output)
    else:
        asyncio.run(run_interactive())


def _try_load_dotenv() -> None:
    """Load .env from the project root (two levels up from this file) if found."""
    import pathlib

    env_path = pathlib.Path(__file__).parent.parent.parent.parent / ".env"
    if not env_path.is_file():
        # also try cwd
        env_path = pathlib.Path(".env")
    if not env_path.is_file():
        return

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Only set if not already in environment
            if key and key not in os.environ:
                os.environ[key] = value

    # Map LIVAI_* → standard OpenAI client vars if not already set
    _map_livai_vars()


def _map_livai_vars() -> None:
    if "LIVAI_API_KEY" in os.environ and "OPENAI_API_KEY" not in os.environ:
        os.environ["OPENAI_API_KEY"] = os.environ["LIVAI_API_KEY"]
    if "LIVAI_BASE_URL" in os.environ and "OPENAI_BASE_URL" not in os.environ:
        os.environ["OPENAI_BASE_URL"] = os.environ["LIVAI_BASE_URL"]
    if "LIVAI_MODEL" in os.environ and "OPENAI_MODEL" not in os.environ:
        os.environ["OPENAI_MODEL"] = os.environ["LIVAI_MODEL"]
