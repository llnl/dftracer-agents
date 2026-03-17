from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlparse

from agents import Agent, Runner, set_default_openai_api
from agents.mcp import MCPServerStdio

from ..workspace import (
    clone_or_update_repo,
    create_venv,
    create_workspace_layout,
    detect_repo_attributes,
    tree_summary,
    workspace_env,
)

DEFAULT_REPO_URL = "https://github.com/hpc/ior"
DEFAULT_REPO_REF = "4.0.0"
DEFAULT_COMPILER_MODULE = "PrgEnv-gnu/8.6.0"
DEFAULT_PYTHON_MODULE = "python/3.11.5"
DEFAULT_OPENAI_MODEL = "gpt-4o"
SYSTEM_PROMPT = """You are DFTracer Agent.
Work with the user step by step.
Always prefer asking short clarifying questions with explicit options when the repository context is incomplete.
Use the MCP tools to generate DFTracer build profiles, annotation plans, compile instructions, runtime environments, post-processing plans, and analysis plans.
When the user has already provided feedback in the notebook, respect it as the strongest signal.
"""


class NotebookSessionRuntime:
    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        self.ns = namespace

    @property
    def app_state(self) -> dict[str, Any]:
        return self.ns["APP_STATE"]

    @property
    def project_root(self) -> pathlib.Path:
        return self.ns["PROJECT_ROOT"]

    @property
    def workspaces_root(self) -> pathlib.Path:
        return self.ns["WORKSPACES_ROOT"]

    @property
    def use_widgets(self) -> bool:
        return bool(self.ns.get("USE_WIDGETS", False))

    @property
    def use_widgets_mcp(self) -> bool:
        return bool(self.ns.get("USE_WIDGETS_MCP", False))

    def install(self) -> None:
        self.ns["DEFAULT_REPO_URL"] = DEFAULT_REPO_URL
        self.ns["DEFAULT_REPO_REF"] = DEFAULT_REPO_REF
        self.ns["DEFAULT_COMPILER_MODULE"] = DEFAULT_COMPILER_MODULE
        self.ns["DEFAULT_PYTHON_MODULE"] = DEFAULT_PYTHON_MODULE
        self.ns["SYSTEM_PROMPT"] = SYSTEM_PROMPT
        self.ns["update_latest_agent_code"] = self.update_latest_agent_code
        self.ns["module_setup_lines"] = self.module_setup_lines
        self.ns["apply_module_setup_to_shell_command"] = self.apply_module_setup_to_shell_command
        self.ns["_fetch_remote_refs"] = self.fetch_remote_refs
        self.ns["_detect_modules_via_mcp"] = self.detect_modules_via_mcp
        self.ns["prepare_workspace"] = self.prepare_workspace
        self.ns["prepare_workspace_from_widgets"] = self.prepare_workspace_from_widgets
        self.ns["_workspace_python"] = self.workspace_python
        self.ns["_current_ref_from_widgets"] = self.current_ref_from_widgets
        self.ns["ensure_workspace_prepared"] = self.ensure_workspace_prepared
        self.ns["install_workspace_deps"] = self.install_workspace_deps
        self.ns["load_project_env"] = self.load_project_env
        self.ns["show_agent_env"] = self.show_agent_env
        self.ns["start_local_agent"] = self.start_local_agent
        self.ns["stop_local_agent"] = self.stop_local_agent
        self.ns["ask_agent"] = self.ask_agent
        self.ns["default_ref_option"] = self.default_ref_option
        self.ns["default_module_selection"] = self.default_module_selection

    def update_latest_agent_code(self) -> None:
        cmd = [sys.executable, "-m", "pip", "install", "-e", str(self.project_root)]
        print(f"$ {shlex.join(cmd)}")
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
        if result.stdout:
            print(result.stdout)
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr)
            raise RuntimeError("Failed to update dftracer-agents in notebook environment")
        print("Updated dftracer-agents code in notebook environment.")

    def append_log(self, title: str, payload: dict[str, Any] | str) -> None:
        self.app_state["logs"].append({"title": title, "payload": payload})

    def module_setup_lines(self) -> list[str]:
        lines: list[str] = []
        for mod in self.app_state.get("selected_modules", []):
            mod = str(mod).strip()
            if mod:
                lines.append(f"module load {mod}")
        extra = str(self.app_state.get("module_commands", "")).strip()
        if extra:
            lines.extend(line.strip() for line in extra.splitlines() if line.strip())
        return lines

    def apply_module_setup_to_shell_command(self, cmd: str) -> str:
        lines = self.module_setup_lines()
        if not lines:
            return cmd
        return f"{' && '.join(lines)} && {cmd}"

    def fetch_remote_refs(self, repo_url: str) -> list[tuple[str, str]]:
        if not repo_url.strip():
            return []
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "--tags", repo_url],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Failed to query remote refs")

        branches: list[str] = []
        tags: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            ref = parts[1]
            if ref.startswith("refs/heads/"):
                branches.append(ref.removeprefix("refs/heads/"))
            elif ref.startswith("refs/tags/"):
                tag = ref.removeprefix("refs/tags/")
                if tag.endswith("^{}"):
                    tag = tag[:-3]
                tags.append(tag)

        options: list[tuple[str, str]] = []
        options.extend((f"branch: {branch}", branch) for branch in sorted(set(branches)))
        options.extend((f"tag: {tag}", tag) for tag in sorted(set(tags)))
        return options

    def detect_modules_via_mcp(self) -> dict[str, Any]:
        try:
            from dftracer_agents.mcp_servers.server import detect_available_modules

            return detect_available_modules(limit=300)
        except Exception as exc:
            return {
                "ok": False,
                "module_count": 0,
                "modules": [],
                "loaded_modules": [],
                "compiler_candidates": [],
                "mpi_candidates": [],
                "error": str(exc),
            }

    def default_ref_option(self, options: list[tuple[str, str]]) -> str:
        for _label, value in options:
            if value == DEFAULT_REPO_REF:
                return value
        return ""

    def default_module_selection(self, modules: list[str]) -> tuple[str, ...]:
        selected: list[str] = []

        compiler_preferences = [DEFAULT_COMPILER_MODULE.lower(), "prgenv-gnu/8.6.0", "gcc/12.2"]
        python_preferences = [DEFAULT_PYTHON_MODULE.lower(), "python/3.11.5", "python/3.11"]

        for preferred in compiler_preferences:
            matches = [module for module in modules if preferred in module.lower()]
            if matches:
                selected.append(matches[0])
                break

        for preferred in python_preferences:
            matches = [module for module in modules if preferred in module.lower()]
            if matches:
                if matches[0] not in selected:
                    selected.append(matches[0])
                break

        return tuple(selected)

    def prepare_workspace(
        self,
        repo_url: str,
        git_ref: str,
        workspace_root: str | None = None,
        selected_modules: list[str] | None = None,
        module_commands: str = "",
    ) -> None:
        if not repo_url.strip():
            raise ValueError("GitHub URL is required")
        if not git_ref.strip():
            raise ValueError("Select a branch or tag before preparing workspace")

        root = pathlib.Path(workspace_root).expanduser().resolve() if workspace_root else self.workspaces_root
        layout = create_workspace_layout(root, repo_url)
        clone_info = clone_or_update_repo(repo_url, git_ref, layout.repo)
        venv_info = create_venv(layout.venv)
        attrs = detect_repo_attributes(layout.repo)
        summary = tree_summary(layout.repo)

        self.app_state.update(
            {
                "workspace": layout,
                "repo_url": repo_url,
                "branch": git_ref,
                "repo_attrs": attrs,
                "tree_summary": summary,
                "selected_modules": list(selected_modules if selected_modules is not None else [DEFAULT_COMPILER_MODULE, DEFAULT_PYTHON_MODULE]),
                "module_commands": (module_commands or "").strip(),
            }
        )
        self.append_log(
            "prepare_workspace",
            {
                "clone": clone_info,
                "venv": venv_info,
                "attrs": attrs,
                "git_ref": git_ref,
                "selected_modules": self.app_state.get("selected_modules", []),
                "module_commands": self.app_state.get("module_commands", ""),
            },
        )

        print(f"✓ Workspace ready: {layout.root}")
        print(f"✓ Repo action: {clone_info['action']}")
        print(f"✓ Selected ref: {git_ref}")
        print(f"✓ Workspace venv: {layout.venv}")
        print("\nSelected modules:")
        if self.app_state.get("selected_modules"):
            for mod in self.app_state["selected_modules"]:
                print(f"  - {mod}")
        else:
            print("  - (none)")
        if self.app_state.get("module_commands"):
            print("\nExtra module commands:")
            print(self.app_state["module_commands"])
        print("\nWorkspace layout:")
        for name, path in layout.as_dict().items():
            print(f"  {name:10s} -> {path}")

    def workspace_python(self) -> pathlib.Path:
        layout = self.app_state.get("workspace")
        if not layout:
            raise RuntimeError("Workspace is not prepared yet.")
        return layout.venv / "bin" / "python"

    def current_ref_from_widgets(self) -> str:
        ref_widget = self.ns.get("ref_widget")
        return str(getattr(ref_widget, "value", "") or "").strip()

    def ensure_workspace_prepared(self) -> None:
        if self.app_state.get("workspace") is not None:
            return

        if self.use_widgets and all(name in self.ns for name in ("repo_url_widget", "ws_root_widget", "ref_widget")):
            repo_url = self.ns["repo_url_widget"].value.strip()
            if not repo_url:
                raise RuntimeError("GitHub URL is empty. Fill section 1 first.")
            git_ref = self.current_ref_from_widgets() or DEFAULT_REPO_REF
            workspace_root = self.ns["ws_root_widget"].value.strip() or str(self.workspaces_root)
            selected_modules = list(self.ns.get("module_widget").value) if "module_widget" in self.ns else []
            module_commands = self.ns.get("module_cmds_widget").value if "module_cmds_widget" in self.ns else ""
            if not selected_modules and "module_widget" in self.ns:
                selected_modules = list(self.default_module_selection(list(self.ns["module_widget"].options)))
            print("Workspace missing. Auto-preparing from section 1 inputs...")
            self.prepare_workspace(
                repo_url,
                git_ref=git_ref,
                workspace_root=workspace_root,
                selected_modules=selected_modules,
                module_commands=module_commands,
            )
            return

        raise RuntimeError(
            "Workspace not prepared. Run section 1 first (or execute prepare_workspace(REPO_URL, REPO_REF, WORKSPACE_ROOT))."
        )

    def prepare_workspace_from_widgets(self) -> None:
        if not (self.use_widgets and all(name in self.ns for name in ("repo_url_widget", "ws_root_widget", "ref_widget"))):
            self.ensure_workspace_prepared()
            return

        repo_url = self.ns["repo_url_widget"].value.strip() or DEFAULT_REPO_URL
        git_ref = self.current_ref_from_widgets() or DEFAULT_REPO_REF
        workspace_root = self.ns["ws_root_widget"].value.strip() or str(self.workspaces_root)
        selected_modules = list(self.ns.get("module_widget").value) if "module_widget" in self.ns else []
        if not selected_modules and "module_widget" in self.ns:
            selected_modules = list(self.default_module_selection(list(self.ns["module_widget"].options)))
        module_commands = self.ns.get("module_cmds_widget").value if "module_cmds_widget" in self.ns else ""
        print("Preparing workspace from current section 1 inputs...")
        self.prepare_workspace(
            repo_url,
            git_ref=git_ref,
            workspace_root=workspace_root,
            selected_modules=selected_modules,
            module_commands=module_commands,
        )

    def install_workspace_deps(self) -> None:
        self.ensure_workspace_prepared()
        layout = self.app_state["workspace"]
        py = self.workspace_python()
        env = workspace_env(layout)
        shell_cmd = self.apply_module_setup_to_shell_command(
            shlex.join([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
        )
        print(f"$ {shell_cmd}")
        result = subprocess.run(
            shell_cmd,
            shell=True,
            executable="/bin/bash",
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
            raise RuntimeError(f"Command failed: {shell_cmd}")
        print("\nDependency setup complete.")
        print("DFTracer build/install is deferred to pipeline stage: install_dftracer")
        print("\nVerification:")
        print(f"  {'✓' if (layout.venv / 'bin' / 'python').exists() else '–'} {layout.venv / 'bin' / 'python'}")

    def _mask_secret(self, value: str | None, keep: int = 4) -> str:
        if not value:
            return "<missing>"
        if len(value) <= keep:
            return "*" * len(value)
        return f"{'*' * (len(value) - keep)}{value[-keep:]}"

    def _provider_label(self, base_url: str) -> str:
        lowered = base_url.lower()
        if "openai.azure.com" in lowered:
            return "azure"
        if "livai" in lowered:
            return "livai"
        if "openai" in lowered:
            return "openai-compatible"
        return "custom"

    def _select_openai_api(self, base_url: str) -> str:
        explicit = (os.environ.get("OPENAI_API_MODE") or os.environ.get("LIVAI_API_MODE") or "").strip().lower()
        if explicit in {"chat_completions", "responses"}:
            return explicit
        if self._provider_label(base_url) == "livai":
            return "chat_completions"
        return "responses"

    def _ensure_api_version(self, base_url: str) -> str:
        if not base_url or "api-version=" in base_url:
            return base_url
        provider = self._provider_label(base_url)
        explicit_version = os.environ.get("OPENAI_API_VERSION") or os.environ.get("LIVAI_API_VERSION")
        if explicit_version:
            join_char = "&" if "?" in base_url else "?"
            return f"{base_url}{join_char}api-version={explicit_version}"
        if provider == "azure":
            join_char = "&" if "?" in base_url else "?"
            return f"{base_url}{join_char}api-version=2025-03-01-preview"
        return base_url

    def load_project_env(self) -> None:
        env_file = self.project_root / ".env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key not in os.environ and value:
                    os.environ[key] = value

        if os.environ.get("LIVAI_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = os.environ["LIVAI_API_KEY"]
        if os.environ.get("LIVAI_BASE_URL") and not os.environ.get("OPENAI_BASE_URL"):
            os.environ["OPENAI_BASE_URL"] = os.environ["LIVAI_BASE_URL"]
        if os.environ.get("LIVAI_MODEL") and not os.environ.get("OPENAI_MODEL"):
            os.environ["OPENAI_MODEL"] = os.environ["LIVAI_MODEL"]
        if os.environ.get("LIVAI_API_VERSION") and not os.environ.get("OPENAI_API_VERSION"):
            os.environ["OPENAI_API_VERSION"] = os.environ["LIVAI_API_VERSION"]

        base_url = os.environ.get("OPENAI_BASE_URL", "")
        api_mode = self._select_openai_api(base_url)
        set_default_openai_api(api_mode)
        if api_mode == "responses":
            os.environ["OPENAI_BASE_URL"] = self._ensure_api_version(base_url)

    def show_agent_env(self) -> None:
        self.load_project_env()
        base_url = os.environ.get("OPENAI_BASE_URL", "")
        model = os.environ.get("OPENAI_MODEL", "<missing>")
        api_version = os.environ.get("OPENAI_API_VERSION") or os.environ.get("LIVAI_API_VERSION") or "<unset>"
        api_mode = self._select_openai_api(base_url)
        parsed = urlparse(base_url) if base_url else None
        print("Agent endpoint diagnostics:")
        print(f"  provider guess : {self._provider_label(base_url)}")
        print(f"  api mode       : {api_mode}")
        print(f"  host           : {parsed.netloc if parsed else '<missing>'}")
        print(f"  path           : {parsed.path if parsed else '<missing>'}")
        print(f"  api-version    : {api_version}")
        print(f"  model          : {model}")
        print(f"  api key        : {self._mask_secret(os.environ.get('OPENAI_API_KEY'))}")

    async def start_local_agent(self) -> None:
        if self.app_state.get("agent") is not None:
            print("Agent already running.")
            self.show_agent_env()
            return
        self.load_project_env()
        model = os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("OPENAI_BASE_URL"):
            raise RuntimeError("Missing OPENAI_API_KEY or OPENAI_BASE_URL. Fill in .env first.")

        self.show_agent_env()
        mcp = MCPServerStdio(
            params={"command": sys.executable, "args": ["-m", "dftracer_agents.mcp_servers.server"]},
            name="dftracer-tools",
            cache_tools_list=True,
            client_session_timeout_seconds=int(os.environ.get("DFTRACER_MCP_TIMEOUT_SECONDS", "60")),
            max_retry_attempts=int(os.environ.get("DFTRACER_MCP_MAX_RETRIES", "3")),
            retry_backoff_seconds_base=float(os.environ.get("DFTRACER_MCP_RETRY_BACKOFF_SECONDS", "1.5")),
        )
        await mcp.__aenter__()
        agent = Agent(name="DFTracer Agent", model=model, instructions=SYSTEM_PROMPT, mcp_servers=[mcp])
        self.app_state["mcp"] = mcp
        self.app_state["agent"] = agent
        tools = await mcp.list_tools()
        print(f"✓ Agent started with model={model}")
        print(f"✓ MCP tools loaded: {len(tools)}")
        for tool in tools:
            print(f"  • {tool.name}")

    async def stop_local_agent(self) -> None:
        mcp = self.app_state.get("mcp")
        if mcp is not None:
            await mcp.__aexit__(None, None, None)
        self.app_state["agent"] = None
        self.app_state["mcp"] = None
        print("Agent stopped.")

    async def ask_agent(self, prompt: str) -> str:
        retries = int(os.environ.get("DFTRACER_ASK_AGENT_RETRIES", "3"))
        for attempt in range(1, retries + 1):
            agent = self.app_state.get("agent")
            if agent is None:
                raise RuntimeError("Start the agent first in section 3.")
            try:
                result = await Runner.run(agent, prompt)
                return result.final_output
            except Exception as exc:
                msg = str(exc)
                if "Timed out while waiting for response to ClientRequest" in msg and attempt < retries:
                    print(f"MCP timeout during agent call (attempt {attempt}/{retries}); restarting MCP agent and retrying...")
                    try:
                        await self.stop_local_agent()
                    except Exception:
                        pass
                    await self.start_local_agent()
                    continue
                raise


def install_notebook_session(namespace: MutableMapping[str, Any]) -> NotebookSessionRuntime:
    runtime = NotebookSessionRuntime(namespace)
    runtime.install()
    return runtime