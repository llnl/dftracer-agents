import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from dftracer_agents.notebook.session import NotebookSessionRuntime
from dftracer_agents.notebook.pipeline import NotebookPipelineRuntime
from dftracer_agents.workspace import WorkspaceLayout


class FakeNotebookPipelineRuntime(NotebookPipelineRuntime):
    def __init__(self, namespace, fail_counts=None, stages=None):
        super().__init__(namespace)
        self.pipeline_stages = stages or ["stage_one", "stage_two"]
        self.executable_stages = set(self.pipeline_stages)
        self.fail_counts = dict(fail_counts or {})

    async def run_stage(self, stage_name: str, extra_context: str = "") -> str:
        response = f"planned {stage_name}"
        self.pipeline_results[stage_name] = response
        self.pipeline_exec[stage_name] = {"commands": [f"echo {stage_name}"]}
        return response

    def execute_stage(self, stage_name: str, out_fn=print) -> bool:
        remaining_failures = self.fail_counts.get(stage_name, 0)
        if remaining_failures > 0:
            self.fail_counts[stage_name] = remaining_failures - 1
            out_fn(f"forced failure for {stage_name}\n")
            return False
        out_fn(f"forced success for {stage_name}\n")
        return True


class NoReplanResumeRuntime(FakeNotebookPipelineRuntime):
    async def run_stage(self, stage_name: str, extra_context: str = "") -> str:
        raise AssertionError(f"run_stage should not be called during cached resume for {stage_name}")


def make_layout(root: Path) -> WorkspaceLayout:
    repo = root / "source" / "repo"
    for path in [
        root,
        root / "source",
        repo,
        root / "external",
        root / "build",
        root / "install",
        root / "venv",
        root / "traces",
        root / "artifacts",
        root / "logs",
        root / ".cache",
        root / "venv" / "bin",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    return WorkspaceLayout(
        root=root,
        source=root / "source",
        repo=repo,
        external=root / "external",
        build=root / "build",
        install=root / "install",
        venv=root / "venv",
        traces=root / "traces",
        artifacts=root / "artifacts",
        logs=root / "logs",
        cache=root / ".cache",
    )


def make_namespace(layout: WorkspaceLayout | None) -> dict:
    workspace = layout
    return {
        "APP_STATE": {
            "results": {},
            "logs": [],
            "feedback": {},
            "workspace": workspace,
            "repo_url": "https://example.com/repo.git",
            "branch": "main",
            "repo_attrs": {"language": "cpp", "uses_mpi": False, "uses_hip": False},
            "selected_modules": [],
            "module_commands": "",
        },
        "effective_config": lambda: {
            "repo_url": "https://example.com/repo.git",
            "branch": "main",
            "repo_dir": str(workspace.repo if workspace else ""),
            "language": "cpp",
            "build_system": "cmake",
            "uses_mpi": False,
            "uses_hip": False,
            "workload_type": "hpc",
            "detail_level": "detailed",
            "notes": "",
            "trace_dir": str(workspace.traces if workspace else ""),
            "artifact_dir": str(workspace.artifacts if workspace else ""),
            "venv_dir": str(workspace.venv if workspace else ""),
        },
        "workspace_env": lambda _layout: {},
    }


class NotebookPipelineStateTests(unittest.TestCase):
    def test_session_stage_recipe_helper_forwards_language_and_repo_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = NotebookSessionRuntime(
                {
                    "APP_STATE": {"logs": []},
                    "PROJECT_ROOT": Path(tmp_dir),
                }
            )

            captured: dict[str, object] = {}

            def fake_run_goose_recipe(recipe_path, params=None, extra_args=None):
                captured["recipe_path"] = recipe_path
                captured["params"] = dict(params or {})
                return {"ok": True}

            runtime.run_goose_recipe = fake_run_goose_recipe  # type: ignore[method-assign]

            payload = runtime.run_goose_pipeline_stage_recipe(
                "detect",
                "pipeline context",
                venv_dir="/tmp/venv",
                trace_dir="/tmp/traces",
                post_dir="/tmp/post",
                compacted_trace_dir="/tmp/post/compacted",
                analysis_dir="/tmp/analysis",
                language="cpp",
                repo_dir="/tmp/repo",
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(captured["params"]["language"], "cpp")
            self.assertEqual(captured["params"]["repo_dir"], "/tmp/repo")
            self.assertEqual(captured["params"]["stage_name"], "detect")
            self.assertIn("pipeline_context_file", captured["params"])

    def test_detect_stage_recipe_updates_repo_attrs_and_optional_mpi_enables_dftracer(self) -> None:
        runtime = NotebookPipelineRuntime(make_namespace(None))
        runtime.ns["run_goose_pipeline_stage_recipe"] = lambda stage_name, context, **kwargs: {
            "summary": "Detected optional MPI support",
            "language": "cpp",
            "build_system": "autotools",
            "uses_mpi": True,
            "mpi_detection": "optional",
            "uses_hip": False,
            "dftracer_flags": {"DFTRACER_ENABLE_MPI": "ON"},
            "notes": ["MPI backend is optional but available"],
        }

        result = asyncio.run(runtime.run_stage("detect"))

        self.assertIn("Detected optional MPI support", result)
        self.assertTrue(runtime.app_state["repo_attrs"]["uses_mpi"])
        self.assertEqual(runtime.app_state["repo_attrs"]["mpi_detection"], "optional")
        self.assertTrue(runtime.app_state["repo_attrs"]["uses_mpi_optional"])
        self.assertTrue(runtime._infer_uses_mpi())

    def test_annotation_verification_accepts_string_build_patch_entries(self) -> None:
        runtime = NotebookPipelineRuntime(make_namespace(None))

        verification = runtime._verify_annotation_subagent(
            {
                "ok": True,
                "annotation": {
                    "modified": [{"file": "/tmp/source.c", "changes": ["inserted annotation"]}],
                    "build_link_patches": {"modified": ["/tmp/CMakeLists.txt"]},
                },
                "patch": {"patch": "diff --git a/src.c b/src.c"},
            }
        )

        self.assertTrue(verification["ok"])
        self.assertEqual(verification["modified_files"], ["/tmp/source.c"])
        self.assertEqual(verification["build_files"], ["/tmp/CMakeLists.txt"])

    def test_annotate_stage_uses_goose_recipe_payload_and_records_verification(self) -> None:
        runtime = NotebookPipelineRuntime(make_namespace(None))
        runtime.ns["run_goose_pipeline_stage_recipe"] = lambda stage_name, context, **kwargs: {
            "summary": "Applied Python annotations",
            "ok": True,
            "annotation": {
                "modified": [{"file": "/tmp/app.py", "changes": ["inserted annotation"]}],
                "build_link_patches": {"modified": ["/tmp/pyproject.toml"]},
                "skipped": [],
            },
            "patch": {"patch": "diff --git a/app.py b/app.py"},
            "notes": ["Used Python annotation subrecipe"],
        }

        message = asyncio.run(runtime.run_stage("annotate"))

        self.assertIn("Applied Python annotations", json.dumps(runtime.pipeline_exec["annotate"]["subagents"]["annotation"]))
        self.assertIn("/tmp/app.py", message)
        self.assertTrue(runtime.pipeline_exec["annotate"]["subagents"]["verification"]["ok"])

    def test_build_stage_recipe_populates_commands_from_goose_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            runtime = NotebookPipelineRuntime(make_namespace(layout))
            runtime.ns["run_goose_pipeline_stage_recipe"] = lambda stage_name, context, **kwargs: {
                "summary": "Generated default build commands",
                "needs_docs": False,
                "needs_docs_reason": "",
                "commands": [f"{layout.repo}/configure --prefix={layout.venv}", "make -j4"],
                "notes": ["Autotools detected"],
            }

            result = asyncio.run(runtime.run_stage("test_default_build_setup"))

            self.assertIn("Generated default build commands", result)
            self.assertEqual(
                runtime.pipeline_exec["test_default_build_setup"]["commands"],
                [f"{layout.repo}/configure --prefix={layout.venv}", "make -j4"],
            )

    def test_autotools_cleanup_is_injected_for_preconfigured_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            (layout.repo / "config.status").write_text("configured", encoding="utf-8")
            (layout.repo / "Makefile").write_text("distclean:\n\t@true\n", encoding="utf-8")

            runtime = NotebookPipelineRuntime(make_namespace(layout))
            commands = [f"{layout.repo}/configure --prefix={layout.venv}", "make -j4"]

            patched = runtime._autotools_source_cleanup_commands(commands, layout.repo)

            self.assertEqual(len(patched), 3)
            self.assertIn("make distclean", patched[0])
            self.assertIn("rm -f config.status config.log libtool", patched[0])
            self.assertEqual(patched[1:], commands)

    def test_nonempty_trace_files_filters_zero_length_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            runtime = NotebookPipelineRuntime(make_namespace(layout))
            runtime.app_state["current_run_id"] = "run_test_trace_sizes"

            trace_dir = layout.traces / "run_test_trace_sizes"
            trace_dir.mkdir(parents=True, exist_ok=True)
            empty_trace = trace_dir / "empty-app.pfw"
            empty_trace.write_text("", encoding="utf-8")
            nonempty_trace = trace_dir / "filled-app.pfw.gz"
            nonempty_trace.write_text("trace-data", encoding="utf-8")

            sizes = runtime._trace_file_sizes()
            nonempty = runtime._nonempty_trace_files()

            self.assertEqual(sizes[str(empty_trace)], 0)
            self.assertGreater(sizes[str(nonempty_trace)], 0)
            self.assertEqual(nonempty, [nonempty_trace])

    def test_traced_run_completed_with_teardown_signal_requires_summary_and_trace(self) -> None:
        runtime = NotebookPipelineRuntime(make_namespace(None))
        stdout = "\n".join(
            [
                "IOR-4.0.0: MPI Coordinated Test of Parallel I/O",
                "Results:",
                "Summary of all tests:",
            ]
        )

        self.assertTrue(
            runtime._traced_run_completed_with_teardown_signal(
                -11,
                stdout,
                [Path("/tmp/session-app.pfw.gz")],
            )
        )
        self.assertFalse(runtime._traced_run_completed_with_teardown_signal(0, stdout, [Path("/tmp/session-app.pfw.gz")]))
        self.assertFalse(runtime._traced_run_completed_with_teardown_signal(-11, stdout, []))
        self.assertFalse(runtime._traced_run_completed_with_teardown_signal(-11, "IOR-4.0.0\nResults:\n", [Path("/tmp/session-app.pfw.gz")]))

    def test_run_with_dftracer_verification_requires_nonempty_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            runtime = NotebookPipelineRuntime(make_namespace(layout))
            runtime.app_state["current_run_id"] = "run_test_verify_trace"
            runtime.pipeline_exec["run_with_dftracer"] = {"run_cmd": "echo test"}

            trace_dir = layout.traces / "run_test_verify_trace"
            trace_dir.mkdir(parents=True, exist_ok=True)
            empty_trace = trace_dir / "session-empty.pfw.gz"
            empty_trace.write_text("", encoding="utf-8")

            failed = runtime._verify_stage_subagent("run_with_dftracer", stage_ok=True)
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["nonempty_trace_files"], [])

            nonempty_trace = trace_dir / "session-filled.pfw.gz"
            nonempty_trace.write_text("trace-data", encoding="utf-8")

            passed = runtime._verify_stage_subagent("run_with_dftracer", stage_ok=True)
            self.assertTrue(passed["ok"])
            self.assertIn(str(nonempty_trace), passed["nonempty_trace_files"])

    def test_pipeline_state_tracks_failures_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            runtime = FakeNotebookPipelineRuntime(make_namespace(layout), fail_counts={"stage_two": 1})

            asyncio.run(runtime.run_pipeline(out_fn=lambda _msg: None))

            state_path = Path(runtime.app_state["last_pipeline_state_file"])
            self.assertTrue(state_path.exists())

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["last_failed_stage"], "stage_two")
            self.assertEqual(state["stages"]["stage_one"]["status"], "completed")
            self.assertEqual(state["stages"]["stage_two"]["status"], "failed")
            self.assertEqual(state["stages"]["stage_two"]["attempt_count"], 1)
            self.assertTrue((layout.artifacts / state["run_id"] / "stage_02_stage_two" / "output_failed.log").exists())

            resumed_runtime = NoReplanResumeRuntime(make_namespace(None))
            result = asyncio.run(
                resumed_runtime.run_last_failed_stage(
                    workspace_root=str(layout.root),
                    out_fn=lambda _msg: None,
                )
            )

            resumed_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result["stage_two"], "planned stage_two")
            self.assertEqual(resumed_state["status"], "completed")
            self.assertEqual(resumed_state["stages"]["stage_two"]["status"], "completed")
            self.assertEqual(resumed_state["stages"]["stage_two"]["attempt_count"], 2)
            self.assertTrue((layout.artifacts / resumed_state["run_id"] / "stage_02_stage_two" / "output.log").exists())
            self.assertIsNotNone(resumed_runtime.app_state["workspace"])

    def test_resume_continues_from_next_pending_stage_when_no_failure_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            runtime = FakeNotebookPipelineRuntime(
                make_namespace(layout),
                fail_counts={"stage_two": 1},
                stages=["stage_one", "stage_two", "stage_three"],
            )

            asyncio.run(runtime.run_pipeline(out_fn=lambda _msg: None))

            retry_runtime = FakeNotebookPipelineRuntime(
                make_namespace(None),
                stages=["stage_one", "stage_two", "stage_three"],
            )
            asyncio.run(
                retry_runtime.run_last_failed_stage(
                    workspace_root=str(layout.root),
                    out_fn=lambda _msg: None,
                )
            )

            resume_runtime = NoReplanResumeRuntime(
                make_namespace(None),
                stages=["stage_one", "stage_two", "stage_three"],
            )
            result = asyncio.run(
                resume_runtime.run_last_failed_stage(
                    workspace_root=str(layout.root),
                    out_fn=lambda _msg: None,
                )
            )

            state_path = Path(layout.artifacts).glob("run_*/pipeline_state.json")
            latest_state = max(state_path, key=lambda path: path.stat().st_mtime)
            resumed_state = json.loads(latest_state.read_text(encoding="utf-8"))
            self.assertEqual(result, {"stage_three": "planned stage_three"})
            self.assertEqual(resumed_state["status"], "completed")
            self.assertEqual(resumed_state["last_failed_stage"], None)
            self.assertEqual(resumed_state["stages"]["stage_three"]["status"], "completed")

    def test_resume_repairs_postprocess_trace_dir_to_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            run_id = "run_20260317_005117"
            trace_dir = layout.traces / run_id
            trace_dir.mkdir(parents=True, exist_ok=True)
            (trace_dir / "session-abc-app.pfw.gz").write_text("trace", encoding="utf-8")
            run_dir = layout.artifacts / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            stale_state = {
                "schema_version": 1,
                "run_id": run_id,
                "status": "failed",
                "started_at": "2026-03-17T01:10:25",
                "updated_at": "2026-03-17T01:10:42",
                "workspace": layout.as_dict(),
                "repo_url": "https://example.com/repo.git",
                "branch": "main",
                "docs_url": "",
                "selected_modules": [],
                "module_commands": "",
                "repo_attrs": {"language": "cpp", "uses_mpi": False, "uses_hip": False},
                "feedback": {},
                "mcp_docs_context": {},
                "trace_dir": str(trace_dir),
                "artifacts_dir": str(run_dir),
                "stage_order": ["detect", "run_with_dftracer", "postprocess", "dfanalyzer"],
                "pipeline_results": {"postprocess": "planned postprocess"},
                "pipeline_exec": {
                    "postprocess": {
                        "commands": [
                            f"dftracer-split -d {layout.traces} -o {run_dir / 'postprocess' / 'compacted'} -n traces --index-dir {run_dir / 'postprocess' / 'index'} --verify"
                        ]
                    }
                },
                "last_failed_stage": "postprocess",
                "last_completed_stage": "run_with_dftracer",
                "next_pending_stage": "dfanalyzer",
                "stages": {
                    "detect": {"index": 1, "status": "completed", "attempt_count": 1, "latest_log": "", "attempts": []},
                    "run_with_dftracer": {"index": 2, "status": "completed", "attempt_count": 1, "latest_log": "", "attempts": []},
                    "postprocess": {"index": 3, "status": "failed", "attempt_count": 1, "latest_log": "", "attempts": []},
                    "dfanalyzer": {"index": 4, "status": "pending", "attempt_count": 0, "latest_log": "", "attempts": []},
                },
            }
            state_path = run_dir / "pipeline_state.json"
            state_path.write_text(json.dumps(stale_state, indent=2), encoding="utf-8")

            runtime = FakeNotebookPipelineRuntime(make_namespace(None), stages=["detect", "run_with_dftracer", "postprocess", "dfanalyzer"])
            asyncio.run(runtime.run_last_failed_stage(workspace_root=str(layout.root), out_fn=lambda _msg: None))

            repaired_state = json.loads(state_path.read_text(encoding="utf-8"))
            repaired_commands = repaired_state["pipeline_exec"]["postprocess"]["commands"]
            self.assertIn(str(trace_dir), "\n".join(repaired_commands))
            self.assertNotIn(f"-d {layout.traces} ", "\n".join(repaired_commands))

    def test_resume_uses_last_completed_stage_when_pending_flags_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            layout = make_layout(Path(tmp_dir) / "workspace")
            runtime = FakeNotebookPipelineRuntime(
                make_namespace(layout),
                stages=["stage_one", "stage_two", "stage_three"],
            )

            asyncio.run(runtime.run_pipeline(stage_names=["stage_one", "stage_two"], out_fn=lambda _msg: None))

            state_path = Path(runtime.app_state["last_pipeline_state_file"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "partial"
            state["last_failed_stage"] = None
            state["last_completed_stage"] = "stage_two"
            state["stages"]["stage_three"]["status"] = "unknown"
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

            resume_runtime = FakeNotebookPipelineRuntime(
                make_namespace(None),
                stages=["stage_one", "stage_two", "stage_three"],
            )
            result = asyncio.run(
                resume_runtime.run_last_failed_stage(
                    workspace_root=str(layout.root),
                    out_fn=lambda _msg: None,
                )
            )

            resumed_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result, {"stage_three": "planned stage_three"})
            self.assertEqual(resumed_state["status"], "completed")
            self.assertEqual(resumed_state["stages"]["stage_three"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
