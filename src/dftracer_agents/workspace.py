from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkspaceLayout:
    root: Path
    source: Path
    repo: Path
    external: Path
    build: Path
    install: Path
    venv: Path
    traces: Path
    artifacts: Path
    logs: Path
    cache: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "source": str(self.source),
            "repo": str(self.repo),
            "external": str(self.external),
            "build": str(self.build),
            "install": str(self.install),
            "venv": str(self.venv),
            "traces": str(self.traces),
            "artifacts": str(self.artifacts),
            "logs": str(self.logs),
            "cache": str(self.cache),
        }


def slugify_repo_url(repo_url: str) -> str:
    cleaned = repo_url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    tail = cleaned.split("/")[-1] or "repo"
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", tail).strip("-").lower() or "repo"


def create_workspace_layout(base_dir: str | Path, repo_url: str) -> WorkspaceLayout:
    base = Path(base_dir).expanduser().resolve()
    slug = slugify_repo_url(repo_url)
    root = base / slug
    layout = WorkspaceLayout(
        root=root,
        source=root / "source",
        repo=root / "source" / slug,
        external=root / "external",
        build=root / "build",
        install=root / "install",
        venv=root / "venv",
        traces=root / "traces",
        artifacts=root / "artifacts",
        logs=root / "logs",
        cache=root / ".cache",
    )
    for path in [
        layout.root,
        layout.source,
        layout.external,
        layout.build,
        layout.install,
        layout.traces,
        layout.artifacts,
        layout.logs,
        layout.cache,
    ]:
        path.mkdir(parents=True, exist_ok=True)
    return layout


def run_command(
    cmd: list[str],
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def clone_or_update_repo(repo_url: str, branch: str, repo_dir: str | Path) -> dict[str, str | int]:
    repo_path = Path(repo_dir)
    if (repo_path / ".git").exists():
        pull = run_command(["git", "pull", "--ff-only"], cwd=repo_path)
        checkout = run_command(["git", "checkout", branch], cwd=repo_path)
        return {
            "action": "updated",
            "pull_rc": pull.returncode,
            "checkout_rc": checkout.returncode,
            "stdout": (pull.stdout + "\n" + checkout.stdout).strip(),
            "stderr": (pull.stderr + "\n" + checkout.stderr).strip(),
        }

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    clone = run_command(["git", "clone", "--branch", branch, repo_url, str(repo_path)])
    return {
        "action": "cloned",
        "pull_rc": 0,
        "checkout_rc": 0,
        "stdout": clone.stdout.strip(),
        "stderr": clone.stderr.strip(),
        "returncode": clone.returncode,
    }


def create_venv(venv_dir: str | Path) -> dict[str, str | int]:
    venv_path = Path(venv_dir)
    if (venv_path / "bin" / "python").exists():
        return {"action": "exists", "returncode": 0}
    result = run_command(["python3", "-m", "venv", str(venv_path)])
    return {"action": "created", "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def detect_repo_attributes(repo_dir: str | Path) -> dict[str, bool | str]:
    root = Path(repo_dir)
    files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    sample_text = []
    for pattern in ["CMakeLists.txt", "*.py", "*.cpp", "*.cxx", "*.cc", "pyproject.toml", "setup.py", "requirements.txt"]:
        for path in root.rglob(pattern):
            try:
                sample_text.append(path.read_text(errors="ignore")[:5000])
            except Exception:
                pass
            if len(sample_text) >= 10:
                break
        if len(sample_text) >= 10:
            break
    blob = "\n".join(sample_text)

    has_cpp = any(file_name.endswith((".cpp", ".cxx", ".cc", ".c", ".cu", ".hip")) for file_name in files)
    has_python = any(file_name.endswith(".py") for file_name in files)
    has_cmake = any(file_name == "CMakeLists.txt" or file_name.endswith("/CMakeLists.txt") for file_name in files)
    has_pyproject = "pyproject.toml" in files
    has_mpi = bool(re.search(r"\bMPI\b|mpi", blob))
    has_hip = bool(re.search(r"\bHIP\b|rocm|hip", blob, re.IGNORECASE))
    language = "cpp" if has_cpp else "python" if has_python else "unknown"

    return {
        "language": language,
        "has_cpp": has_cpp,
        "has_python": has_python,
        "has_cmake": has_cmake,
        "has_pyproject": has_pyproject,
        "uses_mpi": has_mpi,
        "uses_hip": has_hip,
    }


def tree_summary(repo_dir: str | Path, max_entries: int = 120) -> list[str]:
    root = Path(repo_dir)
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part in {"build", "dist", "__pycache__"} for part in rel.parts):
            continue
        lines.append(str(rel) + ("/" if path.is_dir() else ""))
        if len(lines) >= max_entries:
            lines.append("... (truncated)")
            break
    return lines


def workspace_env(layout: WorkspaceLayout) -> dict[str, str]:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(layout.venv)
    env["PATH"] = f"{layout.venv / 'bin'}:{layout.install / 'bin'}:{env.get('PATH', '')}"
    env["CMAKE_PREFIX_PATH"] = f"{layout.install}:{env.get('CMAKE_PREFIX_PATH', '')}".rstrip(":")
    env["LD_LIBRARY_PATH"] = f"{layout.install / 'lib'}:{layout.install / 'lib64'}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
    env["PKG_CONFIG_PATH"] = f"{layout.install / 'lib' / 'pkgconfig'}:{layout.install / 'lib64' / 'pkgconfig'}:{env.get('PKG_CONFIG_PATH', '')}".rstrip(":")
    env["DFTRACER_INSTALL_PREFIX"] = str(layout.install)
    return env