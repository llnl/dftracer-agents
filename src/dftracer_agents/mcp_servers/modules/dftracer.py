from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP

from ...knowledge import cpp_annotation_patterns, infer_build_profile, python_annotation_patterns, runtime_env_template
from .annotations import (
    candidate_source_files,
    git_diff_patch,
    inject_c_hotpath_regions,
    inject_cpp_or_c_annotations,
    inject_python_annotations,
    patch_build_linking,
    remove_stale_region_annotations,
    safe_read_text,
    safe_write_text,
    source_language,
)
from .shared import docs_context


def _annotation_backup_dir(repo: pathlib.Path) -> pathlib.Path:
    return repo.parent / f".{repo.name}.dftracer_agents" / "annotation_backup"


def _build_patch_targets(repo: pathlib.Path) -> list[pathlib.Path]:
    targets: set[pathlib.Path] = set(repo.rglob("CMakeLists.txt"))
    targets.update(repo.rglob("Makefile.am"))
    targets.update(path for path in repo.rglob("Makefile") if path.is_file())

    configure_ac = repo / "configure.ac"
    if configure_ac.exists():
        targets.add(configure_ac)

    return sorted(path.resolve() for path in targets if path.is_file())


def _annotation_targets(repo: pathlib.Path, source_files: list[pathlib.Path], patch_build_files: bool) -> list[pathlib.Path]:
    targets = {path.resolve() for path in source_files if path.exists()}
    if patch_build_files:
        targets.update(_build_patch_targets(repo))
    return sorted(path for path in targets if path.is_file())


def _tracked_repo_paths(repo: pathlib.Path, targets: list[pathlib.Path]) -> list[pathlib.Path]:
    if not (repo / ".git").exists() or not targets:
        return []

    rel_paths = [str(path.relative_to(repo)) for path in targets]
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--", *rel_paths],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    tracked = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return [path for path in targets if str(path.relative_to(repo)) in tracked]


def _restore_tracked_targets(repo: pathlib.Path, targets: list[pathlib.Path]) -> tuple[list[pathlib.Path], list[str]]:
    tracked = _tracked_repo_paths(repo, targets)
    if not tracked:
        return [], []

    rel_paths = [str(path.relative_to(repo)) for path in tracked]
    result = subprocess.run(
        ["git", "-C", str(repo), "restore", "--source=HEAD", "--", *rel_paths],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        note = result.stderr.strip() or result.stdout.strip() or "git restore failed"
        return [], [note]

    return tracked, []


def _write_annotation_backup(repo: pathlib.Path, targets: list[pathlib.Path]) -> None:
    backup_dir = _annotation_backup_dir(repo)
    backup_dir.mkdir(parents=True, exist_ok=True)

    manifest_paths: list[str] = []
    for path in targets:
        rel_path = path.relative_to(repo)
        backup_path = backup_dir / rel_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_text(backup_path, safe_read_text(path))
        manifest_paths.append(str(rel_path))

    safe_write_text(backup_dir / "manifest.json", json.dumps({"paths": manifest_paths}, indent=2))


def _restore_from_annotation_backup(repo: pathlib.Path, targets: list[pathlib.Path]) -> list[pathlib.Path]:
    backup_dir = _annotation_backup_dir(repo)
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        return []

    try:
        manifest = json.loads(safe_read_text(manifest_path))
    except Exception:
        return []

    target_set = {path.resolve() for path in targets}
    restored: list[pathlib.Path] = []
    for rel_path in manifest.get("paths", []):
        repo_path = (repo / rel_path).resolve()
        if repo_path not in target_set:
            continue
        backup_path = backup_dir / rel_path
        if not backup_path.exists():
            continue
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_text(repo_path, safe_read_text(backup_path))
        restored.append(repo_path)
    return restored


def _prepare_annotation_baseline(
    repo: pathlib.Path,
    source_files: list[pathlib.Path],
    patch_build_files: bool,
) -> dict[str, Any]:
    targets = _annotation_targets(repo, source_files, patch_build_files)
    restore_notes: list[str] = []
    restored_by_git, git_notes = _restore_tracked_targets(repo, targets)
    restore_notes.extend(git_notes)

    remaining = [path for path in targets if path not in set(restored_by_git)]
    restored_from_backup = _restore_from_annotation_backup(repo, remaining)

    _write_annotation_backup(repo, targets)

    strategies: list[str] = []
    if restored_by_git:
        strategies.append("git_restore")
    if restored_from_backup:
        strategies.append("backup_cache")
    if not strategies:
        strategies.append("snapshot_only")

    return {
        "strategy": strategies,
        "target_files": [str(path) for path in targets],
        "restored_files": [str(path) for path in [*restored_by_git, *restored_from_backup]],
        "notes": restore_notes,
        "backup_dir": str(_annotation_backup_dir(repo)),
    }


def detect_dftracer_profile(
    language: str,
    uses_mpi: bool = False,
    uses_hip: bool = False,
    auto_detect: bool = True,
    include_python_bindings: bool = True,
    enable_function_tracing: bool = True,
) -> dict[str, Any]:
    """Return an application-centric DFTracer build/dependency profile."""
    profile = infer_build_profile(
        language=language,
        uses_mpi=uses_mpi,
        uses_hip=uses_hip,
        auto_detect=auto_detect,
        include_python_bindings=include_python_bindings,
        enable_function_tracing=enable_function_tracing,
    )
    return {
        "name": profile.name,
        "cmake_flags": profile.cmake_flags,
        "env": profile.env,
        "notes": profile.notes,
        "docs_context": docs_context(),
    }


def generate_annotation_plan(language: str, workload_type: str = "general") -> dict[str, Any]:
    """Return instrumentation guidance for C++ or Python workloads."""
    is_cpp = language.lower() in {"cpp", "c++", "c"}
    annotations = cpp_annotation_patterns() if is_cpp else python_annotation_patterns()
    annotations["workload_type"] = workload_type
    annotations["docs_context"] = docs_context()
    return annotations


def generate_cpp_compile_instructions(
    source_dir: str = ".",
    build_dir: str = "build",
    cmake_flags: list[str] | None = None,
) -> dict[str, str]:
    """Generate compile instructions for C++ workloads."""
    flags = cmake_flags or []
    flag_text = " ".join(flags)
    return {
        "configure": f"cmake -S {source_dir} -B {build_dir} {flag_text}".strip(),
        "build": f"cmake --build {build_dir} -j",
        "install": f"cmake --install {build_dir}",
    }


def generate_runtime_env(
    app_name: str,
    data_dirs: list[str],
    output_prefix: str = "./traces",
) -> dict[str, str]:
    """Generate DFTRACER_* runtime environment variables for workload runs."""
    return runtime_env_template(app_name=app_name, data_dirs=data_dirs, output_prefix=output_prefix)


def auto_annotate_application(
    repo_dir: str,
    language: str = "auto",
    max_files: int = 20,
    patch_build_files: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Automatically inject DFTracer annotations into app sources and patch build linking."""
    repo = pathlib.Path(repo_dir).expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        return {"ok": False, "error": f"Invalid repo_dir: {repo_dir}"}

    files = candidate_source_files(repo, language=language)
    if max_files > 0:
        files = files[:max_files]

    restore_info: dict[str, Any] = {
        "strategy": [],
        "target_files": [],
        "restored_files": [],
        "notes": [],
    }
    if not dry_run:
        restore_info = _prepare_annotation_baseline(repo, files, patch_build_files)

    modified: list[dict[str, Any]] = []
    skipped: list[str] = []

    for path in files:
        original = safe_read_text(path)
        lang = source_language(path)
        if lang in {"cpp", "c"}:
            cleaned, cleaned_changed, cleaned_changes = remove_stale_region_annotations(path, original)
            updated, changed, changes = inject_cpp_or_c_annotations(path, cleaned)
            changed = cleaned_changed or changed
            changes = [*cleaned_changes, *changes]
        elif lang == "python":
            updated, changed, changes = inject_python_annotations(path, original)
        else:
            skipped.append(str(path))
            continue

        if changed:
            if not dry_run:
                safe_write_text(path, updated)
            modified.append({"file": str(path), "changes": changes})

    link_patch: dict[str, Any] = {"modified": [], "notes": []}
    if patch_build_files:
        if dry_run:
            link_patch["notes"].append("dry_run=True: build files were not changed")
        else:
            link_patch = patch_build_linking(repo)

    return {
        "ok": True,
        "repo_dir": str(repo),
        "language": language,
        "dry_run": dry_run,
        "restore": restore_info,
        "scanned_files": len(files),
        "modified_files": len(modified),
        "modified": modified,
        "skipped": skipped,
        "build_link_patches": link_patch,
        "required_link_flags": {
            "cflags": ["-I${DFTRACER_INSTALL_DIR}/include"],
            "ldflags": ["-L${DFTRACER_INSTALL_DIR}/lib", "-Wl,-rpath,${DFTRACER_INSTALL_DIR}/lib", "-ldftracer_core"],
        },
    }


def annotate_and_create_patch(
    repo_dir: str,
    language: str = "auto",
    max_files: int = 50,
    patch_build_files: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Annotate the cloned app and return a git patch for applied changes."""
    apply_out = auto_annotate_application(
        repo_dir=repo_dir,
        language=language,
        max_files=max_files,
        patch_build_files=patch_build_files,
        dry_run=dry_run,
    )
    if not apply_out.get("ok"):
        return {"ok": False, "error": apply_out.get("error", "annotation failed"), "annotation": apply_out}

    repo = pathlib.Path(repo_dir).expanduser().resolve()
    patch_out = git_diff_patch(repo)
    return {
        "ok": bool(patch_out.get("ok", False)),
        "workspace_layout_hint": {
            "source_root": str(repo.parent),
            "venv_root": str(repo.parent.parent / "venv"),
        },
        "annotation": apply_out,
        "patch": patch_out,
    }


def register(mcp: FastMCP) -> None:
    mcp.tool()(detect_dftracer_profile)
    mcp.tool()(generate_annotation_plan)
    mcp.tool()(generate_cpp_compile_instructions)
    mcp.tool()(generate_runtime_env)
    mcp.tool()(auto_annotate_application)
    mcp.tool()(annotate_and_create_patch)
