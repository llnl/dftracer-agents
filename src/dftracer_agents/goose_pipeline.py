from __future__ import annotations

import json
import os
import pathlib
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from .workspace import detect_repo_attributes, tree_summary

DEFAULT_PIPELINE_NAME = "ior"
DEFAULT_PIPELINE_LANGUAGE = "cpp"
DEFAULT_PIPELINE_REPO_URL = "https://github.com/hpc/ior"
DEFAULT_PIPELINE_REPO_REF = "4.0.0"
DEFAULT_TERMINAL_RUN_ID = "terminal_default"
DEFAULT_GOOSE_STAGE_TIMEOUT_SECONDS = 120
GOOSE_PIPELINE_STAGE_ORDER = [
    "detect",
    "test_default_build_setup",
    "test_default_run",
    "annotate",
    "build_with_dftracer",
    "postprocess",
    "dfanalyzer",
]

GOOSE_STAGE_RECIPE_FILES = {
    "detect": "subrecipes/10_detect_stage.yaml",
    "test_default_build_setup": "subrecipes/20_build_setup_stage.yaml",
    "test_default_run": "subrecipes/30_default_run_stage.yaml",
    "annotate": None,
    "build_with_dftracer": "subrecipes/50_build_with_dftracer_stage.yaml",
    "postprocess": "subrecipes/60_postprocess_stage.yaml",
    "dfanalyzer": "subrecipes/70_dfanalyzer_stage.yaml",
}

GOOSE_STAGE_INSTRUCTION_TEXT = {
    "detect": "Execute the detect stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "test_default_build_setup": "Execute the default build setup stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "test_default_run": "Execute the default run stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "annotate": "Execute the annotate stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "build_with_dftracer": "Execute the DFTracer rebuild stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "postprocess": "Execute the postprocess stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "dfanalyzer": "Execute the DFAnalyzer stage using the recipe parameters and return only the JSON response required by the recipe schema.",
}

GOOSE_STAGE_DEPENDENCIES = {
    "detect": [],
    "test_default_build_setup": ["detect"],
    "test_default_run": ["detect", "test_default_build_setup"],
    "annotate": ["detect", "test_default_run"],
    "build_with_dftracer": ["detect", "annotate", "test_default_build_setup"],
    "postprocess": ["build_with_dftracer"],
    "dfanalyzer": ["postprocess"],
}

GOOSE_STAGE_REQUIRED_FIELDS = {
    "detect": {
        "top_level": [
            "summary",
            "language",
            "build_system",
            "uses_mpi",
            "mpi_detection",
            "uses_hip",
            "dftracer_flags",
            "notes",
            "handoff",
        ],
        "handoff": ["language", "build_system", "uses_mpi", "mpi_detection", "uses_hip", "dftracer_flags"],
    },
    "test_default_build_setup": {
        "top_level": ["summary", "needs_docs", "needs_docs_reason", "commands", "notes", "handoff"],
        "handoff": ["commands", "install_prefix", "needs_docs", "needs_docs_reason"],
    },
    "test_default_run": {
        "top_level": ["summary", "run_cmd", "notes", "handoff"],
        "handoff": ["run_cmd"],
    },
    "annotate": {
        "top_level": ["summary", "ok", "annotation", "patch", "notes", "handoff"],
        "handoff": ["ok", "language", "patch_applied"],
    },
    "build_with_dftracer": {
        "top_level": ["summary", "commands", "notes", "handoff"],
        "handoff": ["commands", "install_prefix"],
    },
    "postprocess": {
        "top_level": ["summary", "commands", "notes", "handoff"],
        "handoff": ["post_dir", "compacted_trace_dir", "index_dir"],
    },
    "dfanalyzer": {
        "top_level": ["summary", "commands", "notes", "handoff"],
        "handoff": ["analysis_dir", "commands"],
    },
}


def project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def goose_pipeline_recipe_path(root: pathlib.Path | None = None) -> pathlib.Path:
    base = root or project_root()
    return base / "goose" / "recipes" / "00_dftracer_pipeline.yaml"


def goose_stage_recipe_path(stage_name: str, root: pathlib.Path | None = None, language: str = DEFAULT_PIPELINE_LANGUAGE) -> pathlib.Path:
    base = root or project_root()
    if stage_name == "annotate":
        normalized = (language or "").strip().lower()
        recipe_name = "subrecipes/42_annotate_python_stage.yaml" if normalized == "python" else "subrecipes/41_annotate_c_cpp_stage.yaml"
        return base / "goose" / "recipes" / recipe_name
    recipe_name = GOOSE_STAGE_RECIPE_FILES[stage_name]
    if recipe_name is None:
        raise KeyError(f"No direct Goose recipe configured for stage {stage_name}")
    return base / "goose" / "recipes" / recipe_name


def goose_extension_command(root: pathlib.Path | None = None) -> str:
    base = root or project_root()
    python_bin = base / ".venv" / "bin" / "python"
    if not python_bin.exists():
        python_bin = pathlib.Path(os.environ.get("PYTHON", "python3"))
    return shlex.join([str(python_bin), "-m", "dftracer_agents.mcp_servers.server"])


def goose_stage_instruction_text(stage_name: str) -> str:
    return GOOSE_STAGE_INSTRUCTION_TEXT[stage_name]


def _pipeline_context_payload(defaults: dict[str, str]) -> dict[str, Any]:
    return {
        "name": defaults["name"],
        "repo_url": defaults["repo_url"],
        "repo_ref": defaults["repo_ref"],
        "language": defaults["language"],
        "workspace_root": defaults["workspace_root"],
        "repo_dir": defaults["repo_dir"],
        "venv_dir": defaults["venv_dir"],
        "trace_dir": defaults["trace_dir"],
        "post_dir": defaults["post_dir"],
        "compacted_trace_dir": defaults["compacted_trace_dir"],
        "analysis_dir": defaults["analysis_dir"],
        "repo_summary": json.loads(defaults["repo_summary_json"]),
        "repo_attrs": json.loads(defaults["repo_attrs_json"]),
    }


def build_stage_input_payload(
    stage_name: str,
    *,
    defaults: dict[str, str],
    stage_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    upstream: dict[str, Any] = {}
    for dependency in GOOSE_STAGE_DEPENDENCIES[stage_name]:
        payload = stage_results[dependency]
        upstream[dependency] = {
            "stage": payload.get("stage", dependency),
            "summary": payload.get("summary", ""),
            "handoff": payload.get("handoff", {}),
        }
    return {
        "stage": stage_name,
        "context": _pipeline_context_payload(defaults),
        "upstream": upstream,
    }


def _effective_language(defaults: dict[str, str], stage_results: dict[str, dict[str, Any]]) -> str:
    detect_handoff = (stage_results.get("detect") or {}).get("handoff", {})
    return str(detect_handoff.get("language") or defaults["language"])


def build_goose_stage_params(
    stage_name: str,
    *,
    defaults: dict[str, str],
    pipeline_context_file: pathlib.Path,
    stage_results: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, Any]]:
    stage_input = build_stage_input_payload(stage_name, defaults=defaults, stage_results=stage_results)
    params = {
        "stage_name": stage_name,
        "name": defaults["name"],
        "repo_url": defaults["repo_url"],
        "repo_ref": defaults["repo_ref"],
        "venv_dir": defaults["venv_dir"],
        "trace_dir": defaults["trace_dir"],
        "post_dir": defaults["post_dir"],
        "compacted_trace_dir": defaults["compacted_trace_dir"],
        "analysis_dir": defaults["analysis_dir"],
        "language": _effective_language(defaults, stage_results),
        "repo_dir": defaults["repo_dir"],
        "repo_summary_json": defaults["repo_summary_json"],
        "repo_attrs_json": defaults["repo_attrs_json"],
        "pipeline_context_file": str(pipeline_context_file),
        "stage_input_json": json.dumps(stage_input),
    }
    postprocess_handoff = (stage_results.get("postprocess") or {}).get("handoff", {})
    if postprocess_handoff.get("compacted_trace_dir"):
        params["compacted_trace_dir"] = str(postprocess_handoff["compacted_trace_dir"])
    if postprocess_handoff.get("post_dir"):
        params["post_dir"] = str(postprocess_handoff["post_dir"])
    dftracer_build_handoff = (stage_results.get("build_with_dftracer") or {}).get("handoff", {})
    if dftracer_build_handoff.get("install_prefix"):
        params["venv_dir"] = str(dftracer_build_handoff["install_prefix"])
    build_setup_handoff = (stage_results.get("test_default_build_setup") or {}).get("handoff", {})
    if build_setup_handoff.get("install_prefix"):
        params["venv_dir"] = str(build_setup_handoff["install_prefix"])
    return params, stage_input


def _validate_stage_payload(stage_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("stage", stage_name)
    contract = GOOSE_STAGE_REQUIRED_FIELDS[stage_name]
    missing = [key for key in contract["top_level"] if key not in normalized]
    handoff = normalized.get("handoff")
    if not isinstance(handoff, dict):
        missing.append("handoff")
        handoff = {}
    missing.extend(f"handoff.{key}" for key in contract["handoff"] if key not in handoff)
    if missing:
        missing_text = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Goose terminal pipeline returned mismatched output at {stage_name}: missing {missing_text}")
    return normalized


def _parse_goose_json_text(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None

    if isinstance(payload, dict):
        for key in ("response", "result", "output", "final_output", "final_response", "content"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
            if isinstance(nested, str):
                parsed = _parse_goose_json_text(nested)
                if parsed is not None:
                    return parsed
        return payload
    if isinstance(payload, list):
        for item in reversed(payload):
            parsed = _parse_goose_json_text(json.dumps(item)) if not isinstance(item, str) else _parse_goose_json_text(item)
            if parsed is not None:
                return parsed
    return None


def _default_paths(
    root: pathlib.Path,
    *,
    name: str,
    workspace_root: str = "",
    repo_dir: str = "",
    venv_dir: str = "",
    trace_dir: str = "",
    post_dir: str = "",
    compacted_trace_dir: str = "",
    analysis_dir: str = "",
) -> dict[str, str]:
    workspace = pathlib.Path(workspace_root).expanduser() if workspace_root else root / "workspaces" / name
    repo_path = pathlib.Path(repo_dir).expanduser() if repo_dir else workspace / "source" / name
    venv_path = pathlib.Path(venv_dir).expanduser() if venv_dir else workspace / "venv"
    trace_path = pathlib.Path(trace_dir).expanduser() if trace_dir else workspace / "traces" / DEFAULT_TERMINAL_RUN_ID
    artifacts_root = workspace / "artifacts" / DEFAULT_TERMINAL_RUN_ID
    post_path = pathlib.Path(post_dir).expanduser() if post_dir else artifacts_root / "postprocess"
    compacted_path = pathlib.Path(compacted_trace_dir).expanduser() if compacted_trace_dir else post_path / "compacted"
    analysis_path = pathlib.Path(analysis_dir).expanduser() if analysis_dir else artifacts_root / "analysis"
    return {
        "workspace_root": str(workspace),
        "repo_dir": str(repo_path),
        "venv_dir": str(venv_path),
        "trace_dir": str(trace_path),
        "post_dir": str(post_path),
        "compacted_trace_dir": str(compacted_path),
        "analysis_dir": str(analysis_path),
    }


def build_terminal_pipeline_defaults(
    root: pathlib.Path | None = None,
    *,
    name: str = DEFAULT_PIPELINE_NAME,
    repo_url: str = DEFAULT_PIPELINE_REPO_URL,
    repo_ref: str = DEFAULT_PIPELINE_REPO_REF,
    language: str = DEFAULT_PIPELINE_LANGUAGE,
    workspace_root: str = "",
    repo_dir: str = "",
    venv_dir: str = "",
    trace_dir: str = "",
    post_dir: str = "",
    compacted_trace_dir: str = "",
    analysis_dir: str = "",
) -> dict[str, str]:
    base = root or project_root()
    defaults = {
        "name": name,
        "repo_url": repo_url,
        "repo_ref": repo_ref,
        "language": language,
    }
    defaults.update(
        _default_paths(
            base,
            name=name,
            workspace_root=workspace_root,
            repo_dir=repo_dir,
            venv_dir=venv_dir,
            trace_dir=trace_dir,
            post_dir=post_dir,
            compacted_trace_dir=compacted_trace_dir,
            analysis_dir=analysis_dir,
        )
    )
    repo_path = pathlib.Path(defaults["repo_dir"])
    repo_summary: list[str] = []
    repo_attrs: dict[str, Any] = {}
    if repo_path.exists():
        try:
            repo_summary = tree_summary(repo_path, max_entries=60)
        except Exception:
            repo_summary = []
        try:
            repo_attrs = detect_repo_attributes(repo_path)
        except Exception:
            repo_attrs = {}
    defaults["repo_summary_json"] = json.dumps(repo_summary)
    defaults["repo_attrs_json"] = json.dumps(repo_attrs)
    return defaults


def write_terminal_pipeline_context(
    defaults: dict[str, str],
    *,
    root: pathlib.Path | None = None,
) -> pathlib.Path:
    base = root or project_root()
    context_dir = base / ".cache" / "goose" / "pipeline_contexts"
    context_dir.mkdir(parents=True, exist_ok=True)
    repo_name = defaults["name"]
    run_hint = pathlib.Path(defaults["repo_dir"]) / "src" / repo_name
    context = "\n".join(
        [
            f"Application Name: {defaults['name']}",
            f"Repository URL: {defaults['repo_url']}",
            f"Repository Ref: {defaults['repo_ref']}",
            f"Repository Directory: {defaults['repo_dir']}",
            f"Workspace Root: {defaults['workspace_root']}",
            f"Language: {defaults['language']}",
            f"Workspace Venv: {defaults['venv_dir']}",
            f"Trace Directory: {defaults['trace_dir']}",
            f"Postprocess Directory: {defaults['post_dir']}",
            f"Compacted Trace Directory: {defaults['compacted_trace_dir']}",
            f"Analysis Directory: {defaults['analysis_dir']}",
            f"Default Baseline Run Hint: {run_hint} -a POSIX -w -r -k -t 64k -b 4m -F",
            "Goal: Plan the full DFTracer pipeline for the default IOR workflow.",
        ]
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="terminal_pipeline_",
        suffix=".txt",
        dir=context_dir,
        delete=False,
    ) as handle:
        handle.write(context)
        return pathlib.Path(handle.name)


def build_goose_stage_command(
    stage_name: str,
    *,
    defaults: dict[str, str],
    pipeline_context_file: pathlib.Path,
    stage_results: dict[str, dict[str, Any]],
    root: pathlib.Path | None = None,
    extra_args: list[str] | None = None,
) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    base = root or project_root()
    recipe_path = goose_stage_recipe_path(stage_name, base, defaults["language"])
    launcher = base / "scripts" / "start_goose.sh"
    params, stage_input = build_goose_stage_params(
        stage_name,
        defaults=defaults,
        pipeline_context_file=pipeline_context_file,
        stage_results=stage_results,
    )

    cmd = [
        "bash",
        str(launcher),
        "run",
        "--with-builtin",
        "summon",
        "--recipe",
        str(recipe_path),
        "--no-session",
        "--no-profile",
        "--output-format",
        "json",
        "--with-extension",
        goose_extension_command(base),
    ]
    for key in (
        "stage_name",
        "name",
        "repo_url",
        "repo_ref",
        "venv_dir",
        "trace_dir",
        "post_dir",
        "compacted_trace_dir",
        "analysis_dir",
        "language",
        "repo_dir",
        "repo_summary_json",
        "repo_attrs_json",
        "pipeline_context_file",
        "stage_input_json",
    ):
        cmd.extend(["--params", f"{key}={params[key]}"])
    if extra_args:
        cmd.extend(extra_args)
    return cmd, params, stage_input


def _emit_progress(message: str, *, enabled: bool) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _redacted_env_summary() -> str:
    fields = {
        "OPENAI_BASE_URL": "set" if os.environ.get("OPENAI_BASE_URL") else "missing",
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "missing"),
        "OPENAI_API_KEY": "set" if os.environ.get("OPENAI_API_KEY") else "missing",
        "LIVAI_BASE_URL": "set" if os.environ.get("LIVAI_BASE_URL") else "missing",
        "LIVAI_MODEL": os.environ.get("LIVAI_MODEL", "missing"),
        "LIVAI_API_KEY": "set" if os.environ.get("LIVAI_API_KEY") else "missing",
    }
    return ", ".join(f"{key}={value}" for key, value in fields.items())


def _run_goose_stage(
    stage_name: str,
    *,
    cmd: list[str],
    instruction_text: str,
    progress: bool,
    timeout_seconds: int,
) -> tuple[dict[str, Any], float]:
    start = time.monotonic()
    process = subprocess.Popen(
        cmd,
        cwd=str(project_root()),
        env=os.environ.copy(),
        stdin=subprocess.PIPE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if process.stdin is not None:
        process.stdin.write(instruction_text)
        if not instruction_text.endswith("\n"):
            process.stdin.write("\n")
        process.stdin.close()
        process.stdin = None

    heartbeat_at = start + 5.0
    stderr_chunks: list[str] = []
    stderr_queue: queue.Queue[str | None] = queue.Queue()

    def _stderr_reader() -> None:
        if process.stderr is None:
            stderr_queue.put(None)
            return
        for line in process.stderr:
            stderr_queue.put(line)
        stderr_queue.put(None)

    stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    stderr_thread.start()
    stderr_closed = False

    while True:
        rc = process.poll()

        while True:
            try:
                line = stderr_queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                stderr_closed = True
                break
            clean = line.rstrip("\n")
            stderr_chunks.append(clean)
            _emit_progress(f"[goose-pipeline][{stage_name}][stderr] {clean}", enabled=progress)

        now = time.monotonic()
        if rc is not None:
            break
        if timeout_seconds > 0 and (now - start) >= timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise RuntimeError(
                f"Goose terminal pipeline timed out at {stage_name} after {timeout_seconds}s"
            )
        if now >= heartbeat_at:
            elapsed = now - start
            _emit_progress(f"[goose-pipeline][{stage_name}] still running after {elapsed:.1f}s", enabled=progress)
            heartbeat_at = now + 5.0
        time.sleep(0.2)

    stdout = process.stdout.read() if process.stdout is not None else ""
    remaining_stderr = ""
    if process.stderr is not None and not stderr_closed:
        remaining_stderr = process.stderr.read()
    process.wait()
    if not stderr_closed and remaining_stderr:
        for raw_line in remaining_stderr.splitlines():
            stderr_chunks.append(raw_line)
            _emit_progress(f"[goose-pipeline][{stage_name}][stderr] {raw_line}", enabled=progress)
    stderr_thread.join(timeout=1.0)

    stdout = (stdout or "").strip()
    stderr = "\n".join(part for part in stderr_chunks if part).strip()
    elapsed = time.monotonic() - start

    if process.returncode != 0:
        detail = stderr or stdout or f"Goose exited with rc={process.returncode}"
        raise RuntimeError(f"Goose terminal pipeline failed at {stage_name}: {detail}")

    payload = _parse_goose_json_text(stdout)
    if payload is None:
        raise RuntimeError(
            f"Goose terminal pipeline returned non-JSON output at {stage_name}: {stdout or stderr or '<empty>'}"
        )
    return payload, elapsed


def run_terminal_goose_pipeline(
    root: pathlib.Path | None = None,
    *,
    name: str = DEFAULT_PIPELINE_NAME,
    repo_url: str = DEFAULT_PIPELINE_REPO_URL,
    repo_ref: str = DEFAULT_PIPELINE_REPO_REF,
    language: str = DEFAULT_PIPELINE_LANGUAGE,
    workspace_root: str = "",
    repo_dir: str = "",
    venv_dir: str = "",
    trace_dir: str = "",
    post_dir: str = "",
    compacted_trace_dir: str = "",
    analysis_dir: str = "",
    progress: bool = True,
) -> dict[str, Any]:
    base = root or project_root()
    timeout_seconds = int(os.environ.get("DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS", str(DEFAULT_GOOSE_STAGE_TIMEOUT_SECONDS)))
    defaults = build_terminal_pipeline_defaults(
        base,
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
    )
    context_path = write_terminal_pipeline_context(defaults, root=base)
    results: dict[str, Any] = {
        "recipe": "direct-stage-recipes",
        "defaults": defaults,
        "stage_order": list(GOOSE_PIPELINE_STAGE_ORDER),
        "stages": {},
        "stage_inputs": {},
        "context_file": str(context_path),
    }

    _emit_progress(f"[goose-pipeline] recipe: {results['recipe']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] context: {results['context_file']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] environment: {_redacted_env_summary()}", enabled=progress)
    _emit_progress(f"[goose-pipeline] workspace_root: {defaults['workspace_root']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] repo_dir: {defaults['repo_dir']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] venv_dir: {defaults['venv_dir']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] trace_dir: {defaults['trace_dir']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] repo_attrs_hint: {defaults['repo_attrs_json']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] stage_timeout_seconds: {timeout_seconds}", enabled=progress)

    for index, stage_name in enumerate(GOOSE_PIPELINE_STAGE_ORDER, start=1):
        _emit_progress(
            f"[goose-pipeline] stage {index}/{len(GOOSE_PIPELINE_STAGE_ORDER)} starting: {stage_name}",
            enabled=progress,
        )
        cmd, params, stage_input = build_goose_stage_command(
            stage_name,
            defaults=defaults,
            pipeline_context_file=context_path,
            stage_results=results["stages"],
            root=base,
        )
        results["stage_inputs"][stage_name] = stage_input
        _emit_progress(f"[goose-pipeline][{stage_name}] command: {shlex.join(cmd)}", enabled=progress)
        _emit_progress(
            (
                f"[goose-pipeline][{stage_name}] params: "
                f"repo_url={params['repo_url']}, repo_ref={params['repo_ref']}, language={params['language']}, "
                f"repo_dir={params['repo_dir']}, trace_dir={params['trace_dir']}"
            ),
            enabled=progress,
        )
        payload, elapsed = _run_goose_stage(
            stage_name,
            cmd=cmd,
            instruction_text=goose_stage_instruction_text(stage_name),
            progress=progress,
            timeout_seconds=timeout_seconds,
        )
        results["stages"][stage_name] = _validate_stage_payload(stage_name, payload)
        summary = str(payload.get("summary") or "").strip() or "<no summary>"
        _emit_progress(
            f"[goose-pipeline] stage {index}/{len(GOOSE_PIPELINE_STAGE_ORDER)} complete: {stage_name} ({elapsed:.1f}s)",
            enabled=progress,
        )
        _emit_progress(f"[goose-pipeline][{stage_name}] summary: {summary}", enabled=progress)

    _emit_progress("[goose-pipeline] all stages complete", enabled=progress)
    return results