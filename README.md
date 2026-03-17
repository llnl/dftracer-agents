# dftracer-agents

AI agent pipeline for the DFTracer I/O tracing ecosystem, built with the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python) and a local MCP server.
Pure Python — no binary install, no GLIBC requirements.

Covers the full DFTracer stack:

- `dftracer` (collection and instrumentation)
- `pydftracer` (Python and AI/PyTorch annotations)
- `dftracer-utils` (post-processing and compaction)
- `dfanalyzer` (layered analysis)

The agent automatically selects and calls the right tools to:

1. Choose the right DFTracer build configuration (CMake flags, dependencies).
2. Generate C++ / Python annotation guidance for a workload.
3. Produce `DFTRACER_*` runtime environment variable sets.
4. Build post-processing pipelines with `dftracer-utils`.
5. Run layered analysis with `dfanalyzer`.

## Project layout

```text
src/dftracer_agents/
  agent.py        — OpenAI Agents SDK entry point (REPL + single-shot)
  mcp_server.py   — FastMCP server exposing 7 DFTracer tools
  pipeline.py     — procedural pipeline orchestration helpers
  knowledge.py    — curated build flags, command templates, annotation patterns
  cli.py          — Typer CLI (dftracer-agents run / pipeline)
scripts/
  install.sh      — create .venv and pip-install everything
  start_agent.sh  — load .env, map LIVAI vars, launch the REPL
  run_mcp_server.sh — spawn MCP server over stdio (for external clients)
```

## Quick start

### 1. Install

```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

Creates `.venv`, installs `openai-agents`, `mcp`, and this package.
Userspace-only — no `sudo`, no system writes.

### 2. Configure endpoint

```bash
cp .env.example .env
```

LLNL LIVAI example:

```dotenv
LIVAI_BASE_URL=https://livai-api.llnl.gov/v1
LIVAI_MODEL=gpt-4o
LIVAI_API_KEY=your_key_here
```

`scripts/start_agent.sh` automatically maps `LIVAI_*` → `OPENAI_BASE_URL` /
`OPENAI_API_KEY` / `OPENAI_MODEL` for the OpenAI Agents SDK.

### 3. Start the agent

```bash
# interactive REPL
./scripts/start_agent.sh

# or after activating the venv:
source .venv/bin/activate
dftracer-agents-run

# single-shot
dftracer-agents-run "How do I annotate a Python training loop with DFTracer?"
echo "My app is C++ with MPI. Give me build flags and runtime env." | dftracer-agents-run
```

### 4. CLI pipeline helper (no LLM required)

```bash
dftracer-agents pipeline \
  --app-name my_app \
  --language cpp \
  --trace-path ./traces \
  --uses-mpi
```

## MCP tools

| Tool | Purpose |
| --- | --- |
| `detect_dftracer_profile` | Select build/dependency profile from app metadata |
| `generate_annotation_plan` | C++ / Python instrumentation guidance |
| `generate_cpp_compile_instructions` | CMake configure / build / install commands |
| `generate_runtime_env` | `DFTRACER_*` env variable set for a workload run |
| `generate_postprocess_plan` | Post-processing chain via `dftracer-utils` |
| `generate_layered_analysis_plan` | `dfanalyzer` layered analysis commands |
| `build_end_to_end_pipeline` | Full build → annotate → run → post-process → analyze plan |

## Example agent prompt

```text
My app is C++ with MPI and HIP kernels on LLNL hardware.
Recommend DFTracer build flags, patch instrumentation points, give compile
instructions, runtime DFTRACER_* env vars, post-processing commands, and
dfanalyzer layered analysis commands.
```

## Sources

- [DFTracer](https://github.com/llnl/dftracer)
- [pydftracer](https://github.com/rayandrew/pydftracer)
- [dftracer-utils](https://github.com/llnl/dftracer-utils)
- [dfanalyzer](https://github.com/llnl/dfanalyzer)
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python)

