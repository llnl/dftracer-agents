from __future__ import annotations

import os
import pathlib
from collections.abc import MutableMapping
from typing import Any

try:
    import ipywidgets as widgets
except ImportError:
    widgets = None


DEFAULT_APP_STATE = {
    "workspace": None,
    "repo_url": None,
    "branch": None,
    "repo_attrs": None,
    "tree_summary": [],
    "agent": None,
    "mcp": None,
    "results": {},
    "feedback": {},
    "docs_url": "",
    "logs": [],
    "selected_modules": [],
    "module_commands": "",
}


def workspace_path(path: pathlib.Path) -> pathlib.Path:
    text = str(path)
    if text.startswith("/usr/WS2/"):
        return pathlib.Path(text.replace("/usr/WS2/", "/usr/workspace/", 1))
    return path


class NotebookConfigRuntime:
    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        self.ns = namespace

    def _project_root(self) -> pathlib.Path:
        cwd = workspace_path(pathlib.Path.cwd())
        return workspace_path(cwd.parent) if cwd.name == "notebooks" else cwd

    def _workspaces_root(self) -> pathlib.Path:
        return self._project_root() / "workspaces"

    def _app_state(self) -> dict[str, Any]:
        existing = self.ns.get("APP_STATE")
        state = dict(DEFAULT_APP_STATE)
        if isinstance(existing, dict):
            state.update(existing)
        return state

    def effective_config(self) -> dict[str, Any]:
        app_state = self.ns["APP_STATE"]
        attrs = app_state.get("repo_attrs") or {}
        feedback = app_state.get("feedback") or {}
        language = feedback.get("language")
        if language in (None, "auto"):
            language = attrs.get("language", "python")

        layout = app_state.get("workspace")
        return {
            "repo_url": app_state.get("repo_url"),
            "branch": app_state.get("branch"),
            "repo_dir": str(layout.repo) if layout else None,
            "language": language,
            "build_system": feedback.get("build_system", "auto"),
            "uses_mpi": attrs.get("uses_mpi") if feedback.get("uses_mpi") == "auto" else feedback.get("uses_mpi", False),
            "uses_hip": attrs.get("uses_hip") if feedback.get("uses_hip") == "auto" else feedback.get("uses_hip", False),
            "workload_type": feedback.get("workload_type", "general"),
            "goals": feedback.get("goals", []),
            "detail_level": feedback.get("detail_level", "detailed"),
            "notes": feedback.get("notes", ""),
            "trace_dir": str(layout.traces) if layout else None,
            "artifact_dir": str(layout.artifacts) if layout else None,
            "install_dir": str(layout.install) if layout else None,
            "venv_dir": str(layout.venv) if layout else None,
        }

    def install(self) -> None:
        self.ns["widgets"] = widgets
        self.ns["PROJECT_ROOT"] = self._project_root()
        self.ns["WORKSPACES_ROOT"] = self._workspaces_root()
        self.ns["APP_STATE"] = self._app_state()
        self.ns["USE_WIDGETS"] = widgets is not None and os.environ.get("DFTRACER_NOTEBOOK_WIDGETS", "1") == "1"
        self.ns["USE_WIDGETS_MCP"] = os.environ.get("DFTRACER_NOTEBOOK_WIDGETS_MCP", "0") == "1"
        self.ns["effective_config"] = self.effective_config


def install_notebook_config(namespace: MutableMapping[str, Any]) -> NotebookConfigRuntime:
    runtime = NotebookConfigRuntime(namespace)
    runtime.install()
    return runtime