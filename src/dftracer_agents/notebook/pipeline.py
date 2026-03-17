from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import textwrap
from collections.abc import Callable, MutableMapping
from datetime import datetime
from typing import Any

from ..knowledge import layered_analysis_commands, postprocess_commands
from ..workspace import WorkspaceLayout


class NotebookPipelineRuntime:
    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        self.ns = namespace
        self.pipeline_results: dict[str, str] = self.app_state["results"]
        self.pipeline_exec: dict[str, dict[str, Any]] = {}
        self.pipeline_state: dict[str, Any] = {}
        self.pipeline_stages = [
            "detect",
            "test_default_build_setup",
            "test_default_run",
            "install_dftracer",
            "annotate",
            "build_with_dftracer",
            "run_with_dftracer",
            "postprocess",
            "dfanalyzer",
        ]
        self.executable_stages = {
            "test_default_build_setup",
            "test_default_run",
            "install_dftracer",
            "build_with_dftracer",
            "run_with_dftracer",
            "postprocess",
            "dfanalyzer",
        }

    @property
    def app_state(self) -> dict[str, Any]:
        return self.ns["APP_STATE"]

    def install(self) -> None:
        self.ns["PIPELINE_RESULTS"] = self.pipeline_results
        self.ns["PIPELINE_EXEC"] = self.pipeline_exec
        self.ns["PIPELINE_STAGES"] = self.pipeline_stages
        self.ns["EXECUTABLE_STAGES"] = self.executable_stages
        self.ns["prompt_context"] = self.prompt_context
        self.ns["config_value"] = self.config_value
        self.ns["run_stage"] = self.run_stage
        self.ns["execute_stage"] = self.execute_stage
        self.ns["run_pipeline"] = self.run_pipeline
        self.ns["run_last_failed_stage"] = self.run_last_failed_stage
        self.ns["find_latest_pipeline_state"] = self.find_latest_pipeline_state

    def _optional_callable(self, name: str) -> Callable[..., Any] | None:
        value = self.ns.get(name)
        return value if callable(value) else None

    def _effective_config(self) -> dict[str, Any]:
        return self.ns["effective_config"]()

    def _workspace_env(self, layout: Any) -> dict[str, str]:
        return self.ns["workspace_env"](layout)

    def _apply_module_setup(self, command: str) -> str:
        fn = self._optional_callable("apply_module_setup_to_shell_command")
        return fn(command) if fn else command

    def _module_setup_lines(self) -> list[str]:
        fn = self._optional_callable("module_setup_lines")
        return fn() if fn else []

    def _default_docs_context(self) -> dict[str, str]:
        return {
            "primary_examples": "https://dftracer.readthedocs.io/en/latest/examples.html",
            "python_examples": "https://dftracer.readthedocs.io/projects/python/en/latest/examples.html",
            "api_reference": "https://dftracer.readthedocs.io/en/latest/api.html",
            "utils_cli": "https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html#dftracer-split",
            "analyzer_docs": "https://dftracer.readthedocs.io/projects/analyzer/en/latest/",
        }

    def _extract_docs_urls(self, text: str) -> dict[str, str]:
        urls: dict[str, str] = {}
        if not text:
            return urls
        found = re.findall(r"https://dftracer\.readthedocs\.io[^\s\"'<>\)]+", text)
        for url in found:
            if "/projects/python/" in url:
                urls.setdefault("python_examples", url)
            elif "/projects/utils/" in url:
                urls.setdefault("utils_cli", url)
            elif "/projects/analyzer/" in url:
                urls.setdefault("analyzer_docs", url)
            elif url.endswith("/api.html"):
                urls.setdefault("api_reference", url)
            else:
                urls.setdefault("primary_examples", url)
        return urls

    def _collect_docs_context(self) -> dict[str, str]:
        ctx = dict(self._default_docs_context())
        existing = self.app_state.get("mcp_docs_context")
        if isinstance(existing, dict):
            for key, value in existing.items():
                if isinstance(value, str) and value.startswith("https://"):
                    ctx[key] = value

        for stage_text in self.app_state.get("results", {}).values():
            if isinstance(stage_text, str):
                ctx.update(self._extract_docs_urls(stage_text))

        self.app_state["mcp_docs_context"] = ctx
        return ctx

    def prompt_context(self) -> str:
        config = self._effective_config()
        tree_text = "\n".join(self.app_state.get("tree_summary", [])[:80])
        docs_url = self.app_state.get("docs_url", "").strip()
        docs_line = f"App documentation URL: {docs_url}" if docs_url else "App documentation URL: (not provided)"
        modules = self.app_state.get("selected_modules", [])
        module_cmds = self.app_state.get("module_commands", "").strip()
        modules_line = ", ".join(modules) if modules else "(none selected)"
        module_cmds_line = module_cmds if module_cmds else "(none)"
        docs = self._collect_docs_context()
        return textwrap.dedent(
            f"""
            Repository URL: {config['repo_url']}
            Branch: {config['branch']}
            Repository path on disk: {config['repo_dir']}
            Language: {config['language']}
            Build system: {config['build_system']}
            Uses MPI: {config['uses_mpi']}
            Uses HIP/ROCm: {config['uses_hip']}
            Workload type: {config['workload_type']}
            Detail level: {config['detail_level']}
            User notes: {config['notes'] or 'none'}
            Trace dir: {config['trace_dir']}
            Artifact dir: {config['artifact_dir']}
            Preferred app install prefix: {config['venv_dir']} (workspace venv prefix)
            Workspace venv: {config['venv_dir']}
            Selected modules: {modules_line}
            Extra module commands: {module_cmds_line}
            {docs_line}

            DFTracer documentation context (always consider during inference):
            - Examples: {docs.get('primary_examples', self._default_docs_context()['primary_examples'])}
            - Python examples: {docs.get('python_examples', self._default_docs_context()['python_examples'])}
            - API reference: {docs.get('api_reference', self._default_docs_context()['api_reference'])}
            - dftracer-utils CLI: {docs.get('utils_cli', self._default_docs_context()['utils_cli'])}
            - DFAnalyzer docs: {docs.get('analyzer_docs', self._default_docs_context()['analyzer_docs'])}

            Source tree sample:
            {tree_text}
            """
        ).strip()

    def config_value(self, key: str) -> str:
        try:
            return str(self._effective_config().get(key) or "(not set)")
        except Exception:
            return "(workspace not ready)"

    def _ensure_run_id(self) -> str:
        run_id = self.app_state.get("current_run_id")
        if not run_id:
            run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
            self.app_state["current_run_id"] = run_id
        return run_id

    def _stage_output_dir(self) -> pathlib.Path:
        layout = self.app_state.get("workspace")
        if not layout:
            raise RuntimeError("Workspace not prepared")
        out_dir = layout.artifacts / self._ensure_run_id()
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _state_file_path(self) -> pathlib.Path:
        return self._stage_output_dir() / "pipeline_state.json"

    def _serialize_workspace(self, layout: Any) -> dict[str, str]:
        if isinstance(layout, WorkspaceLayout):
            return layout.as_dict()
        if hasattr(layout, "as_dict"):
            return dict(layout.as_dict())
        return {}

    def _deserialize_workspace(self, data: dict[str, Any]) -> WorkspaceLayout | None:
        required = {
            "root",
            "source",
            "repo",
            "external",
            "build",
            "install",
            "venv",
            "traces",
            "artifacts",
            "logs",
            "cache",
        }
        if not data or not required.issubset(data):
            return None
        return WorkspaceLayout(**{key: pathlib.Path(str(value)) for key, value in data.items() if key in required})

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _init_pipeline_state(self, stage_names: list[str]) -> dict[str, Any]:
        layout = self.app_state.get("workspace")
        state = {
            "schema_version": 1,
            "run_id": self._ensure_run_id(),
            "status": "running",
            "started_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "workspace": self._serialize_workspace(layout),
            "repo_url": self.app_state.get("repo_url", ""),
            "branch": self.app_state.get("branch", ""),
            "docs_url": self.app_state.get("docs_url", ""),
            "selected_modules": list(self.app_state.get("selected_modules", [])),
            "module_commands": self.app_state.get("module_commands", ""),
            "repo_attrs": self.app_state.get("repo_attrs", {}),
            "feedback": self.app_state.get("feedback", {}),
            "mcp_docs_context": self._collect_docs_context(),
            "trace_dir": str(self._active_trace_dir()) if layout else "",
            "artifacts_dir": str(self._stage_output_dir()) if layout else "",
            "stage_order": list(stage_names),
            "pipeline_results": dict(self.pipeline_results),
            "pipeline_exec": dict(self.pipeline_exec),
            "last_failed_stage": None,
            "last_completed_stage": None,
            "stages": {
                stage: {
                    "index": idx,
                    "status": "pending",
                    "attempt_count": 0,
                    "latest_log": "",
                    "attempts": [],
                }
                for idx, stage in enumerate(stage_names, 1)
            },
        }
        return state

    def _write_pipeline_state(self) -> pathlib.Path:
        if not self.pipeline_state:
            self.pipeline_state = self._init_pipeline_state(self.pipeline_stages)
        self.pipeline_state["updated_at"] = self._now_iso()
        self.pipeline_state["pipeline_results"] = dict(self.pipeline_results)
        self.pipeline_state["pipeline_exec"] = dict(self.pipeline_exec)
        self.pipeline_state["last_failed_stage"] = self.app_state.get("last_failed_stage")
        self.pipeline_state["artifacts_dir"] = str(self._stage_output_dir()) if self.app_state.get("workspace") else ""
        self.pipeline_state["trace_dir"] = self.app_state.get("current_trace_dir", self.pipeline_state.get("trace_dir", ""))
        state_path = self._state_file_path()
        state_path.write_text(json.dumps(self.pipeline_state, indent=2, sort_keys=True), encoding="utf-8")
        self.app_state["last_pipeline_state_file"] = str(state_path)
        return state_path

    def _load_pipeline_state(self, state_path: pathlib.Path) -> dict[str, Any]:
        return json.loads(state_path.read_text(encoding="utf-8"))

    def _restore_state_into_runtime(self, state: dict[str, Any]) -> None:
        workspace = self._deserialize_workspace(state.get("workspace", {}))
        if workspace is not None:
            self.app_state["workspace"] = workspace
        self.app_state["repo_url"] = state.get("repo_url", self.app_state.get("repo_url", ""))
        self.app_state["branch"] = state.get("branch", self.app_state.get("branch", ""))
        self.app_state["docs_url"] = state.get("docs_url", self.app_state.get("docs_url", ""))
        self.app_state["selected_modules"] = list(state.get("selected_modules", self.app_state.get("selected_modules", [])))
        self.app_state["module_commands"] = state.get("module_commands", self.app_state.get("module_commands", ""))
        self.app_state["repo_attrs"] = state.get("repo_attrs", self.app_state.get("repo_attrs", {}))
        self.app_state["feedback"] = state.get("feedback", self.app_state.get("feedback", {}))
        self.app_state["mcp_docs_context"] = state.get("mcp_docs_context", self.app_state.get("mcp_docs_context", {}))
        self.app_state["current_run_id"] = state.get("run_id", self.app_state.get("current_run_id", ""))
        self.app_state["current_trace_dir"] = state.get("trace_dir", self.app_state.get("current_trace_dir", ""))
        self.pipeline_results.clear()
        self.pipeline_results.update(state.get("pipeline_results", {}))
        self.pipeline_exec.clear()
        self.pipeline_exec.update(state.get("pipeline_exec", {}))
        self.pipeline_state = state
        self._refresh_stage_commands_for_resume("postprocess")
        self._refresh_stage_commands_for_resume("dfanalyzer")

    def _next_stage_attempt(self, stage_name: str) -> int:
        stage_state = self.pipeline_state.setdefault("stages", {}).setdefault(
            stage_name,
            {"index": 0, "status": "pending", "attempt_count": 0, "latest_log": "", "attempts": []},
        )
        return int(stage_state.get("attempt_count", 0)) + 1

    def _stage_file_paths(
        self,
        stage_name: str,
        stage_index: int,
        attempt: int,
    ) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        out_dir = self._stage_output_dir()
        safe_name = "".join(c if c.isalnum() or c in {"_", "-"} else "_" for c in stage_name)
        stage_dir = out_dir / f"stage_{stage_index:02d}_{safe_name}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        return (
            stage_dir / f"output_attempt_{attempt:02d}_running.log",
            stage_dir / f"output_attempt_{attempt:02d}.log",
            stage_dir / f"output_attempt_{attempt:02d}_failed.log",
        )

    def _sync_stage_alias_logs(self, stage_dir: pathlib.Path, final_path: pathlib.Path, ok: bool) -> None:
        latest_ok = stage_dir / "output.log"
        latest_fail = stage_dir / "output_failed.log"
        latest_running = stage_dir / "output_running.log"
        if latest_running.exists():
            latest_running.unlink()
        if ok:
            if latest_fail.exists():
                latest_fail.unlink()
            shutil.copyfile(final_path, latest_ok)
        else:
            if latest_ok.exists():
                latest_ok.unlink()
            shutil.copyfile(final_path, latest_fail)

    def find_latest_pipeline_state(self, workspace_root: str | None = None) -> str:
        candidates: list[pathlib.Path] = []
        if workspace_root:
            root = pathlib.Path(workspace_root).expanduser().resolve()
            if root.is_file() and root.name == "pipeline_state.json":
                return str(root)
            if (root / "artifacts").exists():
                candidates.extend((root / "artifacts").glob("run_*/pipeline_state.json"))
            candidates.extend(root.glob("run_*/pipeline_state.json"))

        last_state = self.app_state.get("last_pipeline_state_file")
        if last_state:
            path = pathlib.Path(str(last_state))
            if path.exists():
                candidates.append(path)

        layout = self.app_state.get("workspace")
        if layout:
            candidates.extend(pathlib.Path(layout.artifacts).glob("run_*/pipeline_state.json"))

        existing = [path for path in candidates if path.exists()]
        if not existing:
            raise FileNotFoundError("No pipeline_state.json found for the requested workspace.")
        latest = max(existing, key=lambda path: path.stat().st_mtime)
        return str(latest)

    def _active_trace_dir(self) -> pathlib.Path:
        layout = self.app_state.get("workspace")
        if not layout:
            raise RuntimeError("Workspace not prepared")
        trace_dir = layout.traces / self._ensure_run_id()
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.app_state["current_trace_dir"] = str(trace_dir)
        return trace_dir

    def _trace_dir_for_postprocess(self) -> pathlib.Path:
        current = self.app_state.get("current_trace_dir")
        if current:
            return pathlib.Path(str(current))

        layout = self.app_state.get("workspace")
        if not layout:
            raise RuntimeError("Workspace not prepared")

        run_id = self.app_state.get("current_run_id")
        if run_id:
            candidate = layout.traces / str(run_id)
            if candidate.exists():
                self.app_state["current_trace_dir"] = str(candidate)
                return candidate

        return layout.traces

    def _active_trace_files(self) -> list[pathlib.Path]:
        trace_dir = self._trace_dir_for_postprocess()
        return sorted(list(trace_dir.rglob("*.pfw")) + list(trace_dir.rglob("*.pfw.gz")))

    def _traced_run_completed_with_teardown_signal(
        self,
        returncode: int,
        stdout: str,
        trace_files: list[pathlib.Path],
    ) -> bool:
        if returncode != -11:
            return False
        if not trace_files:
            return False
        markers = [
            "IOR-",
            "Results:",
            "Summary of all tests:",
        ]
        return all(marker in stdout for marker in markers)

    def _refresh_stage_commands_for_resume(self, stage_name: str) -> None:
        if stage_name not in {"postprocess", "dfanalyzer"}:
            return

        layout = self.app_state.get("workspace")
        if not layout:
            return

        out_dir = self._stage_output_dir()
        if stage_name == "postprocess":
            commands = postprocess_commands(
                trace_dir=str(self._trace_dir_for_postprocess()),
                output_dir=str(out_dir / "postprocess"),
            )
        else:
            commands = layered_analysis_commands(
                trace_path=str(out_dir / "postprocess" / "compacted"),
                view_types=["time_range"],
                output_dir=str(out_dir / "analysis"),
            )

        exec_data = dict(self.pipeline_exec.get(stage_name) or {})
        exec_data["commands"] = commands
        self.pipeline_exec[stage_name] = exec_data

    def _parse_exec_tag(self, tag: str, text: str) -> Any:
        match = re.search(rf"DFTRACER_{tag}:\s*", text)
        if not match:
            return None
        rest = text[match.end() :].strip()
        try:
            value, _ = json.JSONDecoder().raw_decode(rest)
            return value
        except Exception:
            pass
        if tag == "RUN":
            line = rest.splitlines()[0].strip() if rest else ""
            return line or None
        return None

    def _extract_command_list_fallback(self, text: str) -> list[str]:
        candidates: list[str] = []
        fenced = re.findall(r"```(?:json|sh|bash)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        candidates.extend(fenced)
        candidates.append(text)

        for block in candidates:
            arr_match = re.search(r"\[\s*(?:\"[^\"]+\"\s*,?\s*)+\]", block, flags=re.DOTALL)
            if not arr_match:
                continue
            try:
                value = json.loads(arr_match.group(0))
            except Exception:
                continue
            if isinstance(value, list) and all(isinstance(x, str) for x in value):
                return value
        return []

    def _synthesize_default_build_commands(self, layout: Any, uses_mpi: bool) -> list[str]:
        if not layout:
            return []

        repo = layout.repo
        prefix = str(layout.venv)

        if (repo / "configure.ac").exists() or (repo / "bootstrap").exists():
            commands: list[str] = []
            if (repo / "bootstrap").exists():
                commands.append("./bootstrap")
            configure_cmd = f"./configure --prefix={prefix}"
            if uses_mpi:
                configure_cmd = f"export CC=$(which mpicc) && export CXX=$(which mpic++) && {configure_cmd}"
            commands.append(configure_cmd)
            commands.append("make -j$(nproc)")
            commands.append(f"make install prefix={prefix}")
            return commands

        if (repo / "CMakeLists.txt").exists():
            build_dir = str(layout.build / "default")
            configure_cmd = f"cmake -S . -B {build_dir} -DCMAKE_INSTALL_PREFIX={prefix}"
            if uses_mpi:
                configure_cmd = f"export CC=$(which mpicc) && export CXX=$(which mpic++) && {configure_cmd}"
            return [
                configure_cmd,
                f"cmake --build {build_dir} -j$(nproc)",
                f"cmake --install {build_dir} --prefix {prefix}",
            ]

        return []

    def _compiler_version_line(self, binary_path: str) -> str:
        try:
            result = subprocess.run([binary_path, "--version"], text=True, capture_output=True, check=False)
        except Exception:
            return ""
        text = (result.stdout or result.stderr or "").splitlines()
        return text[0].strip() if text else ""

    def _resolve_compiler_with_modules(self, binary_name: str) -> str | None:
        lines = self._module_setup_lines()
        if not lines:
            return shutil.which(binary_name)
        shell_cmd = " && ".join(lines + [f"which {binary_name}"])
        result = subprocess.run(
            shell_cmd,
            shell=True,
            executable="/bin/bash",
            text=True,
            capture_output=True,
            check=False,
        )
        path = (result.stdout or "").strip().splitlines()
        return path[-1].strip() if result.returncode == 0 and path else None

    def _apply_compiler_env(self, env: dict[str, str], uses_mpi: bool, out_fn: Callable[[str], None] = print) -> bool:
        gcc = self._resolve_compiler_with_modules("gcc")
        gxx = self._resolve_compiler_with_modules("g++")
        if not gcc or not gxx:
            out_fn("  ✗ gcc/g++ not found in PATH\n")
            return False

        out_fn(f"  Compiler check: gcc -> {gcc} ({self._compiler_version_line(gcc)})\n")
        out_fn(f"  Compiler check: g++ -> {gxx} ({self._compiler_version_line(gxx)})\n")

        cc = gcc
        cxx = gxx
        if uses_mpi:
            mpicc = self._resolve_compiler_with_modules("mpicc")
            mpicxx = self._resolve_compiler_with_modules("mpic++") or self._resolve_compiler_with_modules("mpicxx")
            if not mpicc or not mpicxx:
                out_fn("  ✗ MPI requested but mpicc/mpic++ not found in PATH\n")
                return False
            cc = mpicc
            cxx = mpicxx
            env["MPICC"] = mpicc
            env["MPICXX"] = mpicxx
            out_fn(f"  MPI compiler wrappers: CC={mpicc} CXX={mpicxx}\n")

        env["CC"] = cc
        env["CXX"] = cxx
        env["CMAKE_C_COMPILER"] = cc
        env["CMAKE_CXX_COMPILER"] = cxx
        cmake_args = f"-DCMAKE_C_COMPILER={cc} -DCMAKE_CXX_COMPILER={cxx}"
        existing = env.get("CMAKE_ARGS", "").strip()
        env["CMAKE_ARGS"] = f"{existing} {cmake_args}".strip() if existing else cmake_args
        out_fn(f"  Using compilers: CC={env['CC']} CXX={env['CXX']}\n")
        return True

    def _response_indicates_mpi_enabled(self, text: str) -> bool:
        if not text:
            return False
        patterns = [
            r"DFTRACER_ENABLE_MPI\s*[:=]\s*[\"']?ON[\"']?",
            r"-DDFTRACER_ENABLE_MPI(:[A-Z_]+)?=ON",
            r"\"uses_mpi\"\s*:\s*true",
        ]
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    def _infer_uses_mpi(self, config: dict[str, Any] | None = None, out_fn: Callable[[str], None] = print) -> bool:
        cfg = config or self._effective_config()
        if bool(cfg.get("uses_mpi")):
            return True

        attrs = self.app_state.get("repo_attrs") or {}
        if bool(attrs.get("uses_mpi")):
            out_fn("  MPI inferred from detected repo attributes; enabling DFTRACER_ENABLE_MPI=ON\n")
            return True

        detect_text = str(self.pipeline_results.get("detect", ""))
        if self._response_indicates_mpi_enabled(detect_text):
            out_fn("  MPI inferred from detect stage output; enabling DFTRACER_ENABLE_MPI=ON\n")
            return True

        mpicc = self._resolve_compiler_with_modules("mpicc")
        mpicxx = self._resolve_compiler_with_modules("mpic++") or self._resolve_compiler_with_modules("mpicxx")
        if mpicc and mpicxx:
            out_fn(f"  MPI wrappers detected ({mpicc}, {mpicxx}); enabling DFTRACER_ENABLE_MPI=ON\n")
            return True

        return False

    def _run_shell_streaming(self, shell_cmd: str, env: dict[str, str], out_fn: Callable[[str], None] = print) -> tuple[int, str]:
        proc = subprocess.Popen(
            shell_cmd,
            shell=True,
            executable="/bin/bash",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            lines.append(line)
            out_fn(f"    {line}\n")
        rc = proc.wait()
        return rc, "\n".join(lines) + "\n"

    def _run_dftracer_pip_install(self, out_fn: Callable[[str], None] = print) -> bool:
        layout = self.app_state.get("workspace")
        if not layout:
            out_fn("  [skip] workspace not prepared\n")
            return False

        config = self._effective_config()
        attrs = self.app_state.get("repo_attrs") or {}
        uses_mpi = self._infer_uses_mpi(config, out_fn=out_fn)
        language = str(config.get("language") or "").lower()
        has_python_sources = bool(attrs.get("has_python")) or language == "python"

        env = self._workspace_env(layout)
        env["DFTRACER_ENABLE_MPI"] = "ON" if uses_mpi else "OFF"
        env["DFTRACER_ENABLE_HIP_TRACING"] = "ON" if config.get("uses_hip") else "OFF"
        env["DFTRACER_ENABLE_DYNAMIC_DETECTION"] = "ON"
        env["DFTRACER_BUILD_PYTHON_BINDINGS"] = "ON" if has_python_sources else "OFF"
        env["DFTRACER_BUILD_TYPE"] = "Release"

        if not self._apply_compiler_env(env, uses_mpi=uses_mpi, out_fn=out_fn):
            return False

        py = layout.venv / "bin" / "python"
        if not py.exists():
            out_fn(f"  [skip] venv python not found at {py}\n")
            return False

        out_fn("  Feature flags:\n")
        for key in [
            "DFTRACER_ENABLE_MPI",
            "DFTRACER_ENABLE_HIP_TRACING",
            "DFTRACER_ENABLE_DYNAMIC_DETECTION",
            "DFTRACER_BUILD_PYTHON_BINDINGS",
            "DFTRACER_BUILD_TYPE",
        ]:
            out_fn(f"    {key}={env[key]}\n")

        pip_cmd = shlex.join([
            str(py),
            "-m",
            "pip",
            "install",
            "-v",
            "--no-binary=dftracer",
            "--force-reinstall",
            "dftracer[dfanalyzer]",
        ])

        if uses_mpi:
            cc_for_pip = self._resolve_compiler_with_modules("mpicc")
            cxx_for_pip = self._resolve_compiler_with_modules("mpic++") or self._resolve_compiler_with_modules("mpicxx")
            compiler_pair = "mpicc/mpic++"
        else:
            cc_for_pip = self._resolve_compiler_with_modules("gcc")
            cxx_for_pip = self._resolve_compiler_with_modules("g++")
            compiler_pair = "gcc/g++"

        if not cc_for_pip or not cxx_for_pip:
            out_fn(f"  ✗ Could not resolve {compiler_pair} for pip install\n")
            return False

        env["CC"] = cc_for_pip
        env["CXX"] = cxx_for_pip
        out_fn(f"  export CC={env['CC']}\n")
        out_fn(f"  export CXX={env['CXX']}\n")

        shell_cmd = f"export CC={shlex.quote(env['CC'])} && export CXX={shlex.quote(env['CXX'])} && {pip_cmd}"
        shell_cmd = self._apply_module_setup(shell_cmd)
        out_fn(f"  $ {shell_cmd}\n")

        rc, install_log = self._run_shell_streaming(shell_cmd, env=env, out_fn=out_fn)

        stage_dir = self._stage_output_dir() / "stage_04_install_dftracer"
        stage_dir.mkdir(parents=True, exist_ok=True)
        pip_log_path = stage_dir / "pip_install_verbose.log"
        pip_log_path.write_text(install_log, encoding="utf-8")
        out_fn(f"  Full pip verbose log saved: {pip_log_path}\n")

        if rc != 0:
            out_fn(f"  ✗ pip install failed (rc={rc})\n")
            return False

        required_flags = {
            "DFTRACER_ENABLE_MPI": env["DFTRACER_ENABLE_MPI"],
            "DFTRACER_ENABLE_HIP_TRACING": env["DFTRACER_ENABLE_HIP_TRACING"],
            "DFTRACER_ENABLE_DYNAMIC_DETECTION": env["DFTRACER_ENABLE_DYNAMIC_DETECTION"],
            "DFTRACER_BUILD_PYTHON_BINDINGS": env["DFTRACER_BUILD_PYTHON_BINDINGS"],
            "DFTRACER_BUILD_TYPE": env["DFTRACER_BUILD_TYPE"],
        }
        missing_flags: list[str] = []
        for key, value in required_flags.items():
            env_pat = re.search(rf"{key}\s*=\s*{re.escape(value)}", install_log)
            cmake_pat = re.search(rf"-D{key}(:[A-Z_]+)?={re.escape(value)}", install_log)
            if not (env_pat or cmake_pat):
                missing_flags.append(f"{key}={value}")

        if missing_flags:
            out_fn("  ⚠ DFTracer install completed, but some flags were not explicitly echoed in verbose output:\n")
            for item in missing_flags:
                out_fn(f"    - {item}\n")
            out_fn("  Continuing because pip install succeeded. See full log file above for details.\n")

        show_cmd = shlex.join([str(py), "-m", "pip", "show", "dftracer", "dftracer-analyzer"])
        show_cmd = self._apply_module_setup(show_cmd)
        show_res = subprocess.run(show_cmd, shell=True, executable="/bin/bash", env=env, text=True, capture_output=True, check=False)
        if show_res.returncode != 0:
            out_fn("  ✗ dftracer / dftracer-analyzer package metadata lookup failed after install\n")
            return False

        out_fn("  ✓ dftracer[dfanalyzer] installed with inferred feature flags\n")
        return True

    def _is_placeholder_dftracer_run(self, cmd: str) -> bool:
        raw = (cmd or "").strip().lower()
        if not raw:
            return True
        markers = [
            "full shell command",
            "run the baseline app test",
            "run the application including all required arguments",
            "<command>",
            "your command here",
        ]
        return any(token in raw for token in markers)

    def _guess_baseline_run_command(self) -> str:
        layout = self.app_state.get("workspace")
        candidates: list[pathlib.Path] = []
        if layout:
            candidates.extend([
                layout.venv / "bin" / "ior",
                layout.repo / "src" / "ior",
                layout.repo / "ior",
            ])

        ior_bin = next((path for path in candidates if path.exists() and os.access(path, os.X_OK)), None)
        if ior_bin is not None:
            return f"{shlex.quote(str(ior_bin))} -a POSIX -w -r -k -t 64k -b 4m -F"

        for name in ["ior", "mdtest"]:
            resolved = shutil.which(name)
            if resolved:
                return shlex.quote(resolved)

        return ""

    def _run_shell_commands(
        self,
        commands: list[str],
        cwd: pathlib.Path | None,
        env: dict[str, str] | None,
        out_fn: Callable[[str], None] = print,
        continue_on_failure: bool = False,
    ) -> bool:
        if not commands:
            out_fn("  (no commands extracted from agent response — check stage output above)\n")
            return False

        all_ok = True
        for command in commands:
            exec_cmd = self._apply_module_setup(command)
            out_fn(f"\n  [cwd] {cwd}\n")
            out_fn(f"  $ {exec_cmd}\n")
            result = subprocess.run(
                exec_cmd,
                shell=True,
                executable="/bin/bash",
                cwd=str(cwd) if cwd else None,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            for line in result.stdout.strip().splitlines():
                out_fn(f"    {line}\n")
            for line in result.stderr.strip().splitlines():
                out_fn(f"    [stderr] {line}\n")
            if result.returncode != 0:
                out_fn(f"  ✗ failed (rc={result.returncode})\n")
                all_ok = False
                if not continue_on_failure:
                    return False
            else:
                out_fn("  ✓\n")

        return all_ok

    def _enforce_install_prefix_commands(self, commands: list[str], prefix: pathlib.Path) -> list[str]:
        prefix_str = str(prefix)
        token = r"(\"[^\"]*\"|'[^']*'|[^\s'\"]+)"
        patched: list[str] = []

        for command in commands:
            new_cmd = command

            if "configure" in new_cmd:
                if re.search(rf"--prefix(=|\s+){token}", new_cmd):
                    new_cmd = re.sub(rf"--prefix(=|\s+){token}", f"--prefix={prefix_str}", new_cmd)
                else:
                    new_cmd = f"{new_cmd} --prefix={prefix_str}"

            if "cmake" in new_cmd and "--install" not in new_cmd:
                if re.search(rf"-DCMAKE_INSTALL_PREFIX={token}", new_cmd):
                    new_cmd = re.sub(rf"-DCMAKE_INSTALL_PREFIX={token}", f"-DCMAKE_INSTALL_PREFIX={prefix_str}", new_cmd)
                elif " -S " in f" {new_cmd} " or " cmake .." in f" {new_cmd} ":
                    new_cmd = f"{new_cmd} -DCMAKE_INSTALL_PREFIX={prefix_str}"

            if re.search(r"\bcmake\s+--install\b", new_cmd):
                if re.search(rf"--prefix(=|\s+){token}", new_cmd):
                    new_cmd = re.sub(rf"--prefix(=|\s+){token}", f"--prefix {prefix_str}", new_cmd)
                else:
                    new_cmd = f"{new_cmd} --prefix {prefix_str}"

            if re.search(r"\bmake\b", new_cmd) and "install" in new_cmd:
                if re.search(rf"\bprefix\s*=\s*{token}", new_cmd):
                    new_cmd = re.sub(rf"\bprefix\s*=\s*{token}", f"prefix={prefix_str}", new_cmd)
                elif re.search(rf"\bPREFIX\s*=\s*{token}", new_cmd):
                    new_cmd = re.sub(rf"\bPREFIX\s*=\s*{token}", f"PREFIX={prefix_str}", new_cmd)
                elif re.search(r"\bDESTDIR\s*=", new_cmd):
                    pass
                else:
                    new_cmd = f"{new_cmd} prefix={prefix_str}"

            patched.append(new_cmd)

        return patched

    async def run_stage(self, stage_name: str, extra_context: str = "") -> str:
        if stage_name == "install_dftracer":
            message = textwrap.dedent(
                """
                [deterministic] install_dftracer skips agent/MCP analysis.
                Using notebook-side inferred DFTracer flags (MPI/HIP/Python bindings/build type) and proceeding directly to pip install execution.
                """
            ).strip()
            self.pipeline_results[stage_name] = message
            self.pipeline_exec[stage_name] = {}
            return message

        if stage_name == "run_with_dftracer":
            baseline_cmd = (self.pipeline_exec.get("test_default_run") or {}).get("run_cmd", "").strip()
            if not baseline_cmd:
                baseline_cmd = self._guess_baseline_run_command()
            if not baseline_cmd:
                message = (
                    "[deterministic] run_with_dftracer: stage 03 run command not in cache. "
                    "Run test_default_run first or provide a runnable baseline binary."
                )
                self.pipeline_results[stage_name] = message
                self.pipeline_exec[stage_name] = {"run_cmd": ""}
                return message

            exec_data = dict(self.pipeline_exec.get(stage_name) or {})
            exec_data["run_cmd"] = baseline_cmd
            self.pipeline_exec[stage_name] = exec_data
            message = (
                f"[deterministic] run_with_dftracer: reusing exact run command from stage 03.\n"
                f"  command: {baseline_cmd}\n"
                "  DFTracer env will be injected by execute_stage."
            )
            self.pipeline_results[stage_name] = message
            return message

        context = self.prompt_context()
        if extra_context:
            context += f"\n\n{extra_context}"

        layout = self.app_state.get("workspace")
        venv_dir = str(layout.venv) if layout else "(workspace not prepared)"
        repo_dir = str(layout.repo) if layout else "(workspace not prepared)"
        trace_dir = str(self._trace_dir_for_postprocess()) if layout else "(workspace not prepared)"
        post_dir = str(self._stage_output_dir() / "postprocess") if layout else "./postprocess"
        analysis_dir = str(self._stage_output_dir() / "analysis") if layout else "./analysis"
        compacted_trace_dir = str(pathlib.Path(post_dir) / "compacted")

        prompts = {
            "detect": textwrap.dedent(
                f"""
                {context}

                Call detect_dftracer_profile with the detected language, MPI usage, and HIP usage.
                Examine the source tree: identify primary language, build system, MPI/HIP usage, I/O patterns.
                List which DFTracer feature flags should be ON or OFF for this application and explain why.
                """
            ).strip(),
            "test_default_build_setup": textwrap.dedent(
                f"""
                {context}

                Look for build instructions in: README.md, README, configure.ac, CMakeLists.txt,
                Makefile.am, setup.py, pyproject.toml, docs/, INSTALL, BUILD.
                Use the App documentation URL if provided above.

                If you CANNOT find clear build instructions and no URL was given, respond:
                  NEED_DOCS: <reason>

                Otherwise provide step-by-step explanation, then at the very end append — on its own line —
                a JSON array of complete shell commands (with absolute paths) to configure, build, and install
                the default (non-DFTracer) app into the workspace venv prefix:
                DFTRACER_EXEC: ["cmd1", "cmd2", ...]

                Command requirements:
                - NEVER install under app/install, workspace/install, or /usr/local.
                - For autotools configure, use: --prefix={venv_dir}
                - For cmake configure, include: -DCMAKE_INSTALL_PREFIX={venv_dir}
                - For cmake --install, include: --prefix {venv_dir}
                - For make install, include: prefix={venv_dir} (unless DESTDIR is explicitly required)

                Source dir: {repo_dir}
                Install prefix (must use this): {venv_dir}
                """
            ).strip(),
            "test_default_run": textwrap.dedent(
                f"""
                {context}

                Determine a minimal smoke-test run command for the default app build.
                Prefer binaries/scripts installed under {venv_dir}/bin when applicable.
                The workspace venv currently contains binaries like: {venv_dir}/bin/ior and {venv_dir}/bin/mdtest.

                Response contract (mandatory):
                - Do NOT ask follow-up questions.
                - Do NOT return only analysis.
                - You MUST emit the DFTRACER_RUN directive even if uncertain.
                - If unsure, emit a best-effort command using {venv_dir}/bin/ior.

                At the very end of your response append this line exactly:
                DFTRACER_RUN: "full shell command to run the baseline app test"
                """
            ).strip(),
            "annotate": textwrap.dedent(
                f"""
                {context}

                Call execute_pipeline_stage with:
                  stage='annotate'
                  workspace_root='{str(layout.root) if layout else ''}'
                  repo_dir='{repo_dir}'
                  language='{self.config_value('language')}'
                  auto_apply_annotations=true
                  patch_build_files=true
                  dry_run=false

                Return:
                - modified source files and what annotations were injected
                - modified build files and link flags added
                - any files skipped and why
                """
            ).strip(),
            "build_with_dftracer": textwrap.dedent(
                f"""
                {context}

                Revisit build docs and regenerate build commands after DFTracer annotations are applied.
                Ensure the updated build links against DFTracer and installs the app into {venv_dir}.

                At the very end append this JSON command list:
                DFTRACER_EXEC: ["cmd1", "cmd2", ...]

                Command requirements:
                - NEVER install under app/install, workspace/install, or /usr/local.
                - For autotools configure, use: --prefix={venv_dir}
                - For cmake configure, include: -DCMAKE_INSTALL_PREFIX={venv_dir}
                - For cmake --install, include: --prefix {venv_dir}
                - For make install, include: prefix={venv_dir} (unless DESTDIR is explicitly required)

                Source dir: {repo_dir}
                Install prefix (must use this): {venv_dir}
                """
            ).strip(),
            "postprocess": textwrap.dedent(
                f"""
                {context}

                Call generate_postprocess_plan for: {trace_dir}
                Use dftracer-split as the canonical dftracer-utils flow to compact traces and build the index.

                At the very end of your response append — on its own line — a JSON array of all commands:
                DFTRACER_EXEC: ["cmd1", "cmd2", ...]

                Use absolute paths. Trace dir: {trace_dir}  Output dir: {post_dir}
                """
            ).strip(),
            "dfanalyzer": textwrap.dedent(
                f"""
                {context}

                Call generate_layered_analysis_plan for compacted traces at: {compacted_trace_dir}
                Use the documented Python API workflow based on init_with_hydra and analyze_trace.

                At the very end of your response append — on its own line — a JSON array of all commands:
                DFTRACER_EXEC: ["cmd1", "cmd2", ...]

                Use absolute paths. Compacted trace dir: {compacted_trace_dir}  Analysis output dir: {analysis_dir}
                """
            ).strip(),
        }

        if stage_name not in prompts:
            raise ValueError(f"Unknown stage: {stage_name!r}. Valid: {list(prompts)}")

        response = await self.ns["ask_agent"](prompts[stage_name])
        self.pipeline_results[stage_name] = response

        exec_data: dict[str, Any] = {}
        if stage_name in {"test_default_build_setup", "build_with_dftracer", "postprocess", "dfanalyzer"}:
            commands = self._parse_exec_tag("EXEC", response)
            if isinstance(commands, list):
                exec_data["commands"] = commands
            else:
                exec_data["commands"] = self._extract_command_list_fallback(response)
            if not exec_data.get("commands"):
                exec_data["commands"] = self._synthesize_default_build_commands(
                    self.app_state.get("workspace"),
                    uses_mpi=self._infer_uses_mpi(self._effective_config()),
                )
        elif stage_name == "test_default_run":
            exec_data["env"] = self._parse_exec_tag("ENV", response) or {}
            parsed_run_cmd = (self._parse_exec_tag("RUN", response) or "").strip()
            if self._is_placeholder_dftracer_run(parsed_run_cmd):
                fallback = self._guess_baseline_run_command()
                if fallback:
                    parsed_run_cmd = fallback
            exec_data["run_cmd"] = parsed_run_cmd

        self.pipeline_exec[stage_name] = exec_data
        return response

    def _apply_mpi_exports_to_configure(self, commands: list[str]) -> list[str]:
        mpicc = self._resolve_compiler_with_modules("mpicc")
        mpicxx = self._resolve_compiler_with_modules("mpic++") or self._resolve_compiler_with_modules("mpicxx")
        if not mpicc or not mpicxx:
            return commands
        patched: list[str] = []
        for command in commands:
            if "configure" in command and "CC=" not in command and "export CC=" not in command:
                patched.append(f"export CC=$(which mpicc) && export CXX=$(which mpic++) && {command}")
            else:
                patched.append(command)
        return patched

    def execute_stage(self, stage_name: str, out_fn: Callable[[str], None] = print) -> bool:
        layout = self.app_state.get("workspace")
        exec_data = self.pipeline_exec.setdefault(stage_name, {})

        if stage_name == "install_dftracer":
            return self._run_dftracer_pip_install(out_fn=out_fn)

        if stage_name in {"test_default_build_setup", "build_with_dftracer"}:
            original_commands = exec_data.get("commands", [])
            commands = self._enforce_install_prefix_commands(original_commands, layout.venv) if layout else original_commands
            uses_mpi = self._infer_uses_mpi(self._effective_config(), out_fn=out_fn)
            commands = self._apply_mpi_exports_to_configure(commands)
            env = self._workspace_env(layout) if layout else None
            if env and not self._apply_compiler_env(env, uses_mpi=uses_mpi, out_fn=out_fn):
                return False
            workdir = layout.repo if layout else None
            out_fn(f"Running build commands from repo dir: {workdir}\n")
            if layout and commands != original_commands:
                out_fn(f"Enforcing install prefix to workspace venv: {layout.venv}\n")
                for before, after in zip(original_commands, commands):
                    if before != after:
                        out_fn(f"  rewritten: {before}\n")
                        out_fn(f"        -> {after}\n")
            return self._run_shell_commands(commands, cwd=workdir, env=env, out_fn=out_fn)

        if stage_name == "test_default_run":
            if not layout:
                out_fn("  [skip] workspace not prepared\n")
                return False
            run_cmd = (exec_data.get("run_cmd") or "").strip()
            if not run_cmd:
                run_cmd = self._guess_baseline_run_command()
                exec_data["run_cmd"] = run_cmd
            if not run_cmd:
                out_fn("  ⚠  Agent did not emit a usable DFTRACER_RUN directive for baseline test run.\n")
                return False
            env = self._workspace_env(layout)
            uses_mpi = self._infer_uses_mpi(self._effective_config(), out_fn=out_fn)
            if not self._apply_compiler_env(env, uses_mpi=uses_mpi, out_fn=out_fn):
                return False
            exec_cmd = self._apply_module_setup(run_cmd)
            out_fn(f"\n  [cwd] {layout.repo}\n")
            out_fn(f"  $ {exec_cmd}\n")
            result = subprocess.run(
                exec_cmd,
                shell=True,
                executable="/bin/bash",
                cwd=str(layout.repo),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            for line in result.stdout.strip().splitlines():
                out_fn(f"    {line}\n")
            for line in result.stderr.strip().splitlines():
                out_fn(f"    [stderr] {line}\n")
            ok = result.returncode == 0
            out_fn("  ✓ baseline test run completed\n" if ok else f"  ✗ baseline test run failed (rc={result.returncode})\n")
            return ok

        if stage_name == "run_with_dftracer":
            if not layout:
                out_fn("  [skip] workspace not prepared\n")
                return False

            trace_dir = self._active_trace_dir()
            run_cmd = (exec_data.get("run_cmd") or "").strip()
            if self._is_placeholder_dftracer_run(run_cmd):
                baseline = (self.pipeline_exec.get("test_default_run") or {}).get("run_cmd", "")
                if baseline and not self._is_placeholder_dftracer_run(baseline):
                    run_cmd = baseline.strip()
                    exec_data["run_cmd"] = run_cmd
                else:
                    fallback = self._guess_baseline_run_command()
                    if fallback:
                        run_cmd = fallback
                        exec_data["run_cmd"] = run_cmd
                    else:
                        out_fn("  ⚠  Stage 07 had no usable cached run command and no runnable fallback binary was found.\n")
                        return False

            env = self._workspace_env(layout)
            uses_mpi = self._infer_uses_mpi(self._effective_config(), out_fn=out_fn)
            if not self._apply_compiler_env(env, uses_mpi=uses_mpi, out_fn=out_fn):
                return False

            dftracer_env = {
                "DFTRACER_ENABLE": "1",
                "DFTRACER_INC_METADATA": "1",
                "DFTRACER_METADATA_USE_POSIX": "1",
                "DFTRACER_LOG_FILE": str(trace_dir / "session"),
                "DFTRACER_DATA_DIR": "all",
            }
            if isinstance(exec_data.get("env"), dict):
                for key, value in exec_data["env"].items():
                    if key == "DFTRACER_DATA_DIR":
                        continue
                    dftracer_env[key] = value
            exec_data["env"] = dict(dftracer_env)
            env.update(dftracer_env)

            out_fn(f"  [patch] trace output dir for this run: {trace_dir}\n")
            out_fn("  DFTracer environment:\n")
            for key, value in dftracer_env.items():
                out_fn(f"    export {key}={value}\n")

            exec_cmd = self._apply_module_setup(run_cmd)
            out_fn(f"\n  [cwd] {layout.repo}\n")
            out_fn(f"  $ {exec_cmd}\n")

            result = subprocess.run(
                exec_cmd,
                shell=True,
                executable="/bin/bash",
                cwd=str(layout.repo),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            for line in result.stdout.strip().splitlines():
                out_fn(f"    {line}\n")
            for line in result.stderr.strip().splitlines():
                out_fn(f"    [stderr] {line}\n")

            trace_files = self._active_trace_files()
            soft_success = self._traced_run_completed_with_teardown_signal(result.returncode, result.stdout, trace_files)
            ok = result.returncode == 0 or soft_success
            exec_data["returncode"] = result.returncode
            exec_data["soft_success"] = soft_success
            self.pipeline_exec[stage_name] = exec_data

            if result.returncode == 0:
                out_fn("  ✓ app run completed\n")
            elif soft_success:
                out_fn(
                    "  ! app run produced complete benchmark output and trace files before a teardown signal (rc=-11); treating this attempt as completed\n"
                )
            else:
                out_fn(f"  ✗ app run failed (rc={result.returncode})\n")
            out_fn(f"  Trace files found: {len(trace_files)}\n")
            for path in trace_files[:5]:
                out_fn(f"    {path}\n")
            return ok

        if stage_name in {"postprocess", "dfanalyzer"}:
            commands = exec_data.get("commands", [])
            if not commands:
                out_fn("  ⚠  No commands extracted — check stage output above.\n")
                return False
            env = self._workspace_env(layout) if layout else None
            uses_mpi = self._infer_uses_mpi(self._effective_config(), out_fn=out_fn)
            if env and not self._apply_compiler_env(env, uses_mpi=uses_mpi, out_fn=out_fn):
                return False
            label = "post-processing" if stage_name == "postprocess" else "dfanalyzer"
            out_fn(f"  Running {len(commands)} {label} commands (failures are non-fatal):\n")
            return self._run_shell_commands(
                commands,
                cwd=layout.artifacts if layout else None,
                env=env,
                out_fn=out_fn,
                continue_on_failure=True,
            )

        return True

    def _reset_stage_cache(self, stage_names: list[str]) -> None:
        for stage in stage_names:
            self.pipeline_results.pop(stage, None)
            self.pipeline_exec.pop(stage, None)

    def _update_pipeline_status(self, stage_order: list[str]) -> str:
        failed_stage = next(
            (
                stage
                for stage in stage_order
                if self.pipeline_state.get("stages", {}).get(stage, {}).get("status") == "failed"
            ),
            None,
        )
        last_completed = None
        for stage in stage_order:
            if self.pipeline_state.get("stages", {}).get(stage, {}).get("status") == "completed":
                last_completed = stage

        if failed_stage:
            status = "failed"
        elif stage_order and all(
            self.pipeline_state.get("stages", {}).get(stage, {}).get("status") == "completed"
            for stage in stage_order
        ):
            status = "completed"
        elif any(
            self.pipeline_state.get("stages", {}).get(stage, {}).get("status") == "completed"
            for stage in stage_order
        ):
            status = "partial"
        else:
            status = "pending"

        next_pending = next(
            (
                stage
                for stage in stage_order
                if self.pipeline_state.get("stages", {}).get(stage, {}).get("status") in {"pending", "running"}
            ),
            None,
        )
        self.pipeline_state["status"] = status
        self.pipeline_state["last_failed_stage"] = failed_stage
        self.pipeline_state["last_completed_stage"] = last_completed
        self.pipeline_state["next_pending_stage"] = next_pending
        self.app_state["last_pipeline_status"] = status
        self.app_state["last_failed_stage"] = failed_stage
        self.app_state["next_pending_stage"] = next_pending
        self.app_state["last_stage_output_dir"] = str(self._stage_output_dir()) if self.app_state.get("workspace") else ""
        return status

    def _can_resume_from_cached_stage(self, stage_name: str) -> bool:
        exec_data = self.pipeline_exec.get(stage_name) or {}
        if stage_name == "install_dftracer":
            return True
        if stage_name == "run_with_dftracer":
            return bool((self.pipeline_exec.get(stage_name) or {}).get("run_cmd") or (self.pipeline_exec.get("test_default_run") or {}).get("run_cmd"))
        if stage_name == "test_default_run":
            return bool(exec_data.get("run_cmd"))
        if stage_name not in self.executable_stages:
            return False
        return bool(exec_data.get("commands"))

    async def _run_logged_stage(
        self,
        stage: str,
        stage_index: int,
        out_fn: Callable[[str], None],
    ) -> tuple[bool, str, pathlib.Path]:
        attempt = self._next_stage_attempt(stage)
        running_path, ok_path, fail_path = self._stage_file_paths(stage, stage_index, attempt)
        stage_dir = running_path.parent
        running_alias = stage_dir / "output_running.log"
        stage_state = self.pipeline_state.setdefault("stages", {}).setdefault(
            stage,
            {"index": stage_index, "status": "pending", "attempt_count": 0, "latest_log": "", "attempts": []},
        )
        attempt_record = {
            "attempt": attempt,
            "status": "running",
            "started_at": self._now_iso(),
            "running_log": str(running_path),
            "final_log": "",
            "executed": stage in self.executable_stages,
        }
        stage_state.setdefault("attempts", []).append(attempt_record)
        stage_state["index"] = stage_index
        stage_state["status"] = "running"
        stage_state["attempt_count"] = attempt
        stage_state["latest_log"] = str(running_alias)
        stage_state["started_at"] = attempt_record["started_at"]
        self.pipeline_state["status"] = "running"
        self._write_pipeline_state()

        ok = True
        response = ""

        with running_path.open("w", encoding="utf-8") as stage_fp, running_alias.open("w", encoding="utf-8") as alias_fp:
            def _stage_out(msg: str) -> None:
                stage_fp.write(msg)
                stage_fp.flush()
                alias_fp.write(msg)
                alias_fp.flush()
                out_fn(msg)

            _stage_out(f"\n{'=' * 60}\nStage: {stage}\nAttempt: {attempt}\n{'=' * 60}\n")

            try:
                response = await self.run_stage(stage)
                _stage_out(response + "\n")
                self._write_pipeline_state()
                if stage in self.executable_stages:
                    _stage_out(f"\n--- Executing: {stage} ---\n")
                    ok = self.execute_stage(stage, out_fn=_stage_out)
            except Exception as exc:
                import traceback as _tb

                ok = False
                response = f"Stage exception: {exc}"
                _stage_out(f"{response}\n{_tb.format_exc()}\n")

        final_path = ok_path if ok else fail_path
        if final_path.exists():
            final_path.unlink()
        running_path.replace(final_path)
        self._sync_stage_alias_logs(stage_dir, final_path, ok)

        attempt_record["status"] = "completed" if ok else "failed"
        attempt_record["completed_at"] = self._now_iso()
        attempt_record["final_log"] = str(final_path)
        stage_state["status"] = attempt_record["status"]
        stage_state["latest_log"] = str(final_path)
        stage_state["completed_at"] = attempt_record["completed_at"]
        self._write_pipeline_state()
        out_fn(f"[stage-output] {final_path}\n")
        return ok, response, final_path

    async def run_pipeline(
        self,
        stage_names: list[str] | None = None,
        out_fn: Callable[[str], None] = print,
        on_stage: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, str]:
        prepare_workspace_from_widgets = self._optional_callable("prepare_workspace_from_widgets")
        if prepare_workspace_from_widgets is not None:
            prepare_workspace_from_widgets()

        ensure_workspace_prepared = self._optional_callable("ensure_workspace_prepared")
        if ensure_workspace_prepared is not None:
            ensure_workspace_prepared()

        stages = stage_names if stage_names is not None else self.pipeline_stages
        self._reset_stage_cache(stages)
        self.app_state["current_run_id"] = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.app_state["last_failed_stage"] = None
        self.app_state["last_pipeline_status"] = "running"
        self.pipeline_state = self._init_pipeline_state(stages)
        self._write_pipeline_state()
        out: dict[str, str] = {}

        for idx, stage in enumerate(stages, 1):
            if on_stage:
                on_stage(stage, idx, len(stages))

            ok, response, _final_path = await self._run_logged_stage(stage, idx, out_fn)
            out[stage] = response
            self._update_pipeline_status(stages)
            self._write_pipeline_state()

            if not ok:
                break

        self._update_pipeline_status(stages)
        self._write_pipeline_state()
        return out

    async def run_last_failed_stage(
        self,
        workspace_root: str | None = None,
        out_fn: Callable[[str], None] = print,
    ) -> dict[str, str]:
        state_path = pathlib.Path(self.find_latest_pipeline_state(workspace_root))
        state = self._load_pipeline_state(state_path)
        self._restore_state_into_runtime(state)

        stage_order = list(self.pipeline_state.get("stage_order", self.pipeline_stages))
        failed_stage = self.pipeline_state.get("last_failed_stage") or next(
            (
                stage
                for stage in stage_order
                if self.pipeline_state.get("stages", {}).get(stage, {}).get("status") == "failed"
            ),
            None,
        )

        pending_stages = [
            stage
            for stage in stage_order
            if self.pipeline_state.get("stages", {}).get(stage, {}).get("status") in {"pending", "running"}
        ]

        if not failed_stage and not pending_stages:
            last_completed = self.pipeline_state.get("last_completed_stage")
            if last_completed in stage_order:
                next_index = stage_order.index(last_completed) + 1
                trailing_stages = stage_order[next_index:]
                pending_stages = [
                    stage
                    for stage in trailing_stages
                    if self.pipeline_state.get("stages", {}).get(stage, {}).get("status") != "completed"
                ]

        if not failed_stage and not pending_stages:
            raise RuntimeError("No failed or pending stages recorded in the latest pipeline state.")

        if not failed_stage:
            out: dict[str, str] = {}
            next_stage = pending_stages[0]
            out_fn(f"[resume] No failed stage recorded. Continuing pipeline from {next_stage} through the remaining stages.\n")
            self.pipeline_state["status"] = "running"
            self.app_state["last_pipeline_status"] = "running"
            self._write_pipeline_state()

            for stage in pending_stages:
                self._refresh_stage_commands_for_resume(stage)
                stage_index = stage_order.index(stage) + 1
                ok, response, _final_path = await self._run_logged_stage(stage, stage_index, out_fn)
                out[stage] = response
                status = self._update_pipeline_status(stage_order)
                self._write_pipeline_state()
                if not ok:
                    out_fn(f"[resume] Pipeline failed again at stage {stage}.\n")
                    return out

            status = self._update_pipeline_status(stage_order)
            self._write_pipeline_state()
            if status == "completed":
                out_fn("[resume] Remaining stages completed successfully.\n")
            return out

        stage_index = stage_order.index(failed_stage) + 1
        self.pipeline_state["status"] = "running"
        self.app_state["last_pipeline_status"] = "running"
        self._write_pipeline_state()

        if self._can_resume_from_cached_stage(failed_stage):
            self._refresh_stage_commands_for_resume(failed_stage)
            ok, response, _final_path = await self._rerun_cached_failed_stage(failed_stage, stage_index, out_fn)
        else:
            ok, response, _final_path = await self._run_logged_stage(failed_stage, stage_index, out_fn)
        status = self._update_pipeline_status(stage_order)
        self._write_pipeline_state()

        if ok and status == "partial":
            out_fn(
                f"[resume] Stage {failed_stage} succeeded. Later stages remain pending; rerun them explicitly if needed.\n"
            )
        elif not ok:
            out_fn(f"[resume] Stage {failed_stage} failed again.\n")

        return {failed_stage: response}

    async def _rerun_cached_failed_stage(
        self,
        stage: str,
        stage_index: int,
        out_fn: Callable[[str], None],
    ) -> tuple[bool, str, pathlib.Path]:
        attempt = self._next_stage_attempt(stage)
        running_path, ok_path, fail_path = self._stage_file_paths(stage, stage_index, attempt)
        stage_dir = running_path.parent
        running_alias = stage_dir / "output_running.log"
        stage_state = self.pipeline_state.setdefault("stages", {}).setdefault(
            stage,
            {"index": stage_index, "status": "pending", "attempt_count": 0, "latest_log": "", "attempts": []},
        )
        response = str(self.pipeline_results.get(stage, ""))
        attempt_record = {
            "attempt": attempt,
            "status": "running",
            "started_at": self._now_iso(),
            "running_log": str(running_path),
            "final_log": "",
            "executed": True,
            "reused_cached_plan": True,
        }
        stage_state.setdefault("attempts", []).append(attempt_record)
        stage_state["index"] = stage_index
        stage_state["status"] = "running"
        stage_state["attempt_count"] = attempt
        stage_state["latest_log"] = str(running_alias)
        self.pipeline_state["status"] = "running"
        self._write_pipeline_state()

        with running_path.open("w", encoding="utf-8") as stage_fp, running_alias.open("w", encoding="utf-8") as alias_fp:
            def _stage_out(msg: str) -> None:
                stage_fp.write(msg)
                stage_fp.flush()
                alias_fp.write(msg)
                alias_fp.flush()
                out_fn(msg)

            _stage_out(f"\n{'=' * 60}\nStage: {stage}\nAttempt: {attempt}\n{'=' * 60}\n")
            _stage_out("[resume] Reusing cached stage plan from pipeline_state.json; agent is not required for this retry.\n")
            if response:
                _stage_out(response + "\n")
            _stage_out(f"\n--- Executing cached stage: {stage} ---\n")
            ok = self.execute_stage(stage, out_fn=_stage_out)

        final_path = ok_path if ok else fail_path
        if final_path.exists():
            final_path.unlink()
        running_path.replace(final_path)
        self._sync_stage_alias_logs(stage_dir, final_path, ok)

        attempt_record["status"] = "completed" if ok else "failed"
        attempt_record["completed_at"] = self._now_iso()
        attempt_record["final_log"] = str(final_path)
        stage_state["status"] = attempt_record["status"]
        stage_state["latest_log"] = str(final_path)
        stage_state["completed_at"] = attempt_record["completed_at"]
        self._write_pipeline_state()
        out_fn(f"[stage-output] {final_path}\n")
        return ok, response, final_path


def install_notebook_pipeline(namespace: MutableMapping[str, Any]) -> NotebookPipelineRuntime:
    runtime = NotebookPipelineRuntime(namespace)
    runtime.install()
    return runtime