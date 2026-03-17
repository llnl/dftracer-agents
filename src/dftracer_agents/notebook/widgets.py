from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import MutableMapping
from pprint import pprint
from typing import Any

from IPython.display import clear_output, display

PRESET_QUESTIONS = {
    "What else do you need from me?": "Review the repository context and ask me the next best clarifying question with explicit options.",
    "Summarize this repo for DFTracer": "Summarize the repository from a DFTracer instrumentation perspective and list the top 3 next actions.",
    "Recommend instrumentation points": "Recommend likely instrumentation points for this repository and explain why they matter.",
    "Find likely build issues": "Look at the repository context and predict likely build or integration issues for DFTracer.",
    "Explain previous output": "Explain the most recent pipeline output in simpler terms.",
}


class NotebookWidgetRuntime:
    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        self.ns = namespace

    @property
    def app_state(self) -> dict[str, Any]:
        return self.ns["APP_STATE"]

    @property
    def widgets(self) -> Any:
        return self.ns.get("widgets")

    @property
    def use_widgets(self) -> bool:
        return bool(self.ns.get("USE_WIDGETS", False)) and self.widgets is not None

    @property
    def use_widgets_mcp(self) -> bool:
        return self.use_widgets and bool(self.ns.get("USE_WIDGETS_MCP", False))

    def install(self) -> None:
        self.ns["PRESET_QUESTIONS"] = PRESET_QUESTIONS
        self.ns["render_workspace_setup_section"] = self.render_workspace_setup_section
        self.ns["render_install_section"] = self.render_install_section
        self.ns["render_agent_controls_section"] = self.render_agent_controls_section
        self.ns["render_feedback_section"] = self.render_feedback_section
        self.ns["render_pipeline_run_section"] = self.render_pipeline_run_section
        self.ns["render_chat_section"] = self.render_chat_section
        self.ns["render_outcome_feedback_section"] = self.render_outcome_feedback_section

    def _set_widget_globals(self, **values: Any) -> None:
        self.ns.update(values)

    def render_workspace_setup_section(self) -> None:
        if self.use_widgets:
            widgets = self.widgets
            repo_url_widget = widgets.Text(
                value=self.ns["DEFAULT_REPO_URL"],
                description="GitHub URL",
                layout=widgets.Layout(width="85%"),
            )
            ref_widget = widgets.Dropdown(
                options=[("None (select branch/tag)", "")],
                value="",
                description="Ref",
                layout=widgets.Layout(width="70%"),
            )
            refresh_refs_button = widgets.Button(description="Load branches/tags", button_style="info")
            module_widget = widgets.SelectMultiple(
                options=[],
                value=(),
                description="Modules",
                rows=8,
                layout=widgets.Layout(width="85%"),
            )
            module_cmds_widget = widgets.Textarea(
                value="",
                placeholder="Optional extra module commands, one per line.\nExample:\nmodule unload craype\nmodule load PrgEnv-gnu/8.6.0",
                description="Module cmds",
                layout=widgets.Layout(width="85%", height="100px"),
            )
            refresh_modules_button = widgets.Button(description="Detect modules", button_style="warning")
            ws_root_widget = widgets.Text(
                value=str(self.ns["WORKSPACES_ROOT"]),
                description="Workspace root",
                layout=widgets.Layout(width="85%"),
            )
            prepare_button = widgets.Button(description="Prepare workspace", button_style="success")
            prepare_output = widgets.Output()

            self._set_widget_globals(
                repo_url_widget=repo_url_widget,
                ref_widget=ref_widget,
                refresh_refs_button=refresh_refs_button,
                module_widget=module_widget,
                module_cmds_widget=module_cmds_widget,
                refresh_modules_button=refresh_modules_button,
                ws_root_widget=ws_root_widget,
                prepare_button=prepare_button,
                prepare_output=prepare_output,
            )

            def _load_refs(_=None):
                with prepare_output:
                    clear_output(wait=True)
                    try:
                        options = self.ns["_fetch_remote_refs"](repo_url_widget.value.strip())
                        ref_widget.options = [("None (select branch/tag)", "")] + options
                        ref_widget.value = self.ns["default_ref_option"](options) or ""
                        if ref_widget.value:
                            print(f"Loaded {len(options)} refs. Defaulted Ref to {ref_widget.value}.")
                        else:
                            print(f"Loaded {len(options)} refs. Select one before preparing workspace.")
                    except Exception as exc:
                        ref_widget.options = [("None (select branch/tag)", "")]
                        ref_widget.value = ""
                        print(f"Failed to load refs: {exc}")

            def _load_modules(_=None):
                with prepare_output:
                    clear_output(wait=True)
                    info = self.ns["_detect_modules_via_mcp"]()
                    if not info.get("ok"):
                        print(f"Module detection failed: {info.get('error', 'unknown error')}")
                        module_widget.options = []
                        module_widget.value = ()
                        return
                    modules = info.get("modules", [])
                    module_widget.options = modules
                    module_widget.value = self.ns["default_module_selection"](modules)
                    print(f"Detected {info.get('module_count', len(modules))} modules.")
                    if module_widget.value:
                        print(f"Default modules: {', '.join(module_widget.value)}")
                    if info.get("compiler_candidates"):
                        print("Compiler module candidates:")
                        for module_name in info["compiler_candidates"][:20]:
                            print(f"  - {module_name}")
                    if info.get("mpi_candidates"):
                        print("MPI module candidates:")
                        for module_name in info["mpi_candidates"][:20]:
                            print(f"  - {module_name}")

            def _on_prepare_click(_):
                with prepare_output:
                    clear_output(wait=True)
                    selected_ref = ref_widget.value or self.ns["DEFAULT_REPO_REF"]
                    self.ns["prepare_workspace"](
                        repo_url_widget.value,
                        git_ref=selected_ref,
                        workspace_root=ws_root_widget.value,
                        selected_modules=list(module_widget.value),
                        module_commands=module_cmds_widget.value,
                    )

            refresh_refs_button.on_click(_load_refs)
            refresh_modules_button.on_click(_load_modules)
            prepare_button.on_click(_on_prepare_click)

            display(
                widgets.VBox(
                    [
                        repo_url_widget,
                        widgets.HBox([ref_widget, refresh_refs_button]),
                        refresh_modules_button,
                        module_widget,
                        module_cmds_widget,
                        ws_root_widget,
                        prepare_button,
                        prepare_output,
                    ]
                )
            )

            _load_refs()
            _load_modules()
            return

        self.ns["REPO_URL"] = self.ns["DEFAULT_REPO_URL"]
        self.ns["REPO_REF"] = self.ns["DEFAULT_REPO_REF"]
        self.ns["WORKSPACE_ROOT"] = str(self.ns["WORKSPACES_ROOT"])
        print("Set values and run:")
        print("  refs = _fetch_remote_refs(REPO_URL)")
        print("  print(refs[:20])")
        print(f"  REPO_REF = '{self.ns['DEFAULT_REPO_REF']}'")
        print("  module_info = _detect_modules_via_mcp()")
        print("  print(module_info.get('compiler_candidates', [])[:10])")
        print(
            f"  prepare_workspace(REPO_URL, REPO_REF, WORKSPACE_ROOT, selected_modules=['{self.ns['DEFAULT_COMPILER_MODULE']}', '{self.ns['DEFAULT_PYTHON_MODULE']}'])"
        )

    def render_install_section(self) -> None:
        if self.use_widgets:
            widgets = self.widgets
            install_button = widgets.Button(description="Install workspace dependencies", button_style="primary")
            install_output = widgets.Output()
            self._set_widget_globals(install_button=install_button, install_output=install_output)

            def _on_install_click(_):
                with install_output:
                    clear_output(wait=True)
                    try:
                        self.ns["install_workspace_deps"]()
                    except Exception as exc:
                        print(f"Install failed: {exc}")
                        print("Tip: section 1 auto-loads IOR tag 4.0.0 and prefers PrgEnv-gnu/8.6.0 plus python/3.11.5 when available.")

            install_button.on_click(_on_install_click)
            display(widgets.VBox([install_button, install_output]))
            return

        print("Run one of the following:")
        print("prepare_workspace(REPO_URL, REPO_REF, WORKSPACE_ROOT)")
        print("install_workspace_deps()")

    def render_agent_controls_section(self) -> None:
        if self.use_widgets_mcp:
            widgets = self.widgets
            start_button = widgets.Button(description="Start agent", button_style="success")
            stop_button = widgets.Button(description="Stop agent", button_style="warning")
            env_button = widgets.Button(description="Show endpoint", button_style="info")
            agent_output = widgets.Output()
            self._set_widget_globals(
                start_button=start_button,
                stop_button=stop_button,
                env_button=env_button,
                agent_output=agent_output,
            )

            def _start_clicked(_):
                async def _run():
                    with agent_output:
                        clear_output(wait=True)
                        await self.ns["start_local_agent"]()

                asyncio.create_task(_run())

            def _stop_clicked(_):
                async def _run():
                    with agent_output:
                        clear_output(wait=True)
                        await self.ns["stop_local_agent"]()

                asyncio.create_task(_run())

            def _env_clicked(_):
                with agent_output:
                    clear_output(wait=True)
                    self.ns["show_agent_env"]()

            start_button.on_click(_start_clicked)
            stop_button.on_click(_stop_clicked)
            env_button.on_click(_env_clicked)
            display(self.widgets.VBox([self.widgets.HBox([start_button, stop_button, env_button]), agent_output]))
            return

        print("MCP widget controls disabled for stability. Run manually:")
        print("  show_agent_env()")
        print("  await start_local_agent()")
        print("  await stop_local_agent()")

    def render_feedback_section(self) -> None:
        if self.use_widgets:
            widgets = self.widgets
            language_widget = widgets.Dropdown(
                options=[("Auto-detect", "auto"), ("C/C++", "cpp"), ("Python", "python")],
                value="auto",
                description="Language",
            )
            build_widget = widgets.Dropdown(
                options=[("Auto-detect", "auto"), ("CMake", "cmake"), ("Setuptools", "setuptools"), ("PyProject", "pyproject"), ("Make", "make"), ("Other", "other")],
                value="auto",
                description="Build",
            )
            mpi_widget = widgets.Dropdown(options=[("Auto-detect", "auto"), ("Yes", True), ("No", False)], value="auto", description="MPI")
            hip_widget = widgets.Dropdown(options=[("Auto-detect", "auto"), ("Yes", True), ("No", False)], value="auto", description="HIP/ROCm")
            workload_widget = widgets.Dropdown(
                options=["general", "hpc", "deep-learning", "data-prep", "analytics"],
                value="hpc",
                description="Workload",
            )
            goals_widget = widgets.SelectMultiple(
                options=[
                    "build profile",
                    "annotation plan",
                    "compile instructions",
                    "runtime env",
                    "postprocess plan",
                    "analysis plan",
                ],
                value=("build profile", "annotation plan", "runtime env"),
                description="Goals",
                rows=6,
            )
            detail_widget = widgets.RadioButtons(
                options=[("Concise", "concise"), ("Detailed", "detailed")],
                value="detailed",
                description="Detail",
            )
            notes_widget = widgets.Textarea(
                value="",
                placeholder="Optional context: known bottlenecks, expected build issues, tracing goals...",
                description="Notes",
                layout=widgets.Layout(width="85%", height="120px"),
            )
            feedback_button = widgets.Button(description="Save answers", button_style="info")
            feedback_output = widgets.Output()
            self._set_widget_globals(
                language_widget=language_widget,
                build_widget=build_widget,
                mpi_widget=mpi_widget,
                hip_widget=hip_widget,
                workload_widget=workload_widget,
                goals_widget=goals_widget,
                detail_widget=detail_widget,
                notes_widget=notes_widget,
                feedback_button=feedback_button,
                feedback_output=feedback_output,
            )

            def collect_feedback() -> dict[str, Any]:
                attrs = self.app_state.get("repo_attrs") or {}
                return {
                    "language": attrs.get("language") if language_widget.value == "auto" else language_widget.value,
                    "build_system": build_widget.value,
                    "uses_mpi": attrs.get("uses_mpi") if mpi_widget.value == "auto" else mpi_widget.value,
                    "uses_hip": attrs.get("uses_hip") if hip_widget.value == "auto" else hip_widget.value,
                    "workload_type": workload_widget.value,
                    "goals": list(goals_widget.value),
                    "detail_level": detail_widget.value,
                    "notes": notes_widget.value.strip(),
                }

            self.ns["collect_feedback"] = collect_feedback

            def _save_feedback(_):
                self.app_state["feedback"] = collect_feedback()
                with feedback_output:
                    clear_output(wait=True)
                    print("✓ Saved pipeline preferences")
                    pprint(self.app_state["feedback"])

            feedback_button.on_click(_save_feedback)
            display(
                widgets.VBox(
                    [
                        widgets.HBox([language_widget, build_widget]),
                        widgets.HBox([mpi_widget, hip_widget]),
                        workload_widget,
                        goals_widget,
                        detail_widget,
                        notes_widget,
                        feedback_button,
                        feedback_output,
                    ]
                )
            )
            return

        self.app_state["feedback"] = {
            "language": "auto",
            "build_system": "auto",
            "uses_mpi": "auto",
            "uses_hip": "auto",
            "workload_type": "hpc",
            "goals": ["build profile", "annotation plan", "runtime env"],
            "detail_level": "detailed",
            "notes": "",
        }
        print("Widget UI disabled. Using default feedback values below (edit manually if needed):")
        pprint(self.app_state["feedback"])

    def render_pipeline_run_section(self) -> None:
        if self.use_widgets:
            widgets = self.widgets
            docs_url_widget = widgets.Text(
                value=self.app_state.get("docs_url", ""),
                placeholder="https://... (optional — paste app docs URL if build instructions are unclear)",
                description="App docs URL",
                layout=widgets.Layout(width="90%"),
            )
            run_pipeline_button = widgets.Button(
                description="Run Full Pipeline",
                button_style="success",
                tooltip="Runs all stages: detect -> test_default_build_setup -> test_default_run -> install_dftracer -> annotate -> build_with_dftracer -> run_with_dftracer -> postprocess -> dfanalyzer",
            )
            rerun_failed_button = widgets.Button(
                description="Run Last Failed Stage",
                button_style="warning",
                tooltip="Reload the latest pipeline_state.json for this workspace and rerun only the most recent failed stage.",
            )
            run_status = widgets.HTML(value="<b>Status:</b> idle")
            stage_status = widgets.HTML(value="<b>Stage:</b> -")
            timer_status = widgets.HTML(value="<b>Elapsed:</b> 0s")
            output_dir_status = widgets.HTML(value="<b>Run Artifacts:</b> -")
            pipeline_text = widgets.Textarea(
                value="",
                placeholder="Pipeline status will appear here...",
                layout=widgets.Layout(width="100%", height="260px"),
                disabled=True,
            )
            self._set_widget_globals(
                docs_url_widget=docs_url_widget,
                run_pipeline_button=run_pipeline_button,
                rerun_failed_button=rerun_failed_button,
                run_status=run_status,
                stage_status=stage_status,
                timer_status=timer_status,
                output_dir_status=output_dir_status,
                pipeline_text=pipeline_text,
            )

            def _out(msg: str) -> None:
                pipeline_text.value += msg

            def _set_status(state: str, stage: str = "-") -> None:
                color = {
                    "idle": "#666",
                    "running": "#0b6",
                    "failed": "#c00",
                    "completed": "#0a5",
                    "partial": "#b36b00",
                }.get(state, "#333")
                run_status.value = f"<b>Status:</b> <span style='color:{color}'>{state}</span>"
                stage_status.value = f"<b>Stage:</b> {stage}"

            def _refresh_output_status() -> None:
                stage_dir = self.app_state.get("last_stage_output_dir", "")
                state_file = self.app_state.get("last_pipeline_state_file", "")
                parts = []
                if stage_dir:
                    parts.append(f"<b>Run Dir:</b> {stage_dir}")
                if state_file:
                    parts.append(f"<b>State JSON:</b> {state_file}")
                output_dir_status.value = "<br>".join(parts) if parts else "<b>Run Artifacts:</b> -"

            async def _call_run_pipeline_with_compat(out_fn: Any, on_stage: Any) -> Any:
                params = inspect.signature(self.ns["run_pipeline"]).parameters
                if "on_stage" in params:
                    return await self.ns["run_pipeline"](out_fn=out_fn, on_stage=on_stage)
                return await self.ns["run_pipeline"](out_fn=out_fn)

            def _run_pipeline_clicked(_):
                self.app_state["docs_url"] = docs_url_widget.value.strip()
                pipeline_text.value = ""
                _set_status("idle", "-")
                timer_status.value = "<b>Elapsed:</b> 0s"
                output_dir_status.value = "<b>Run Artifacts:</b> -"

                if self.app_state.get("agent") is None:
                    _set_status("failed", "startup")
                    _out("Agent is not running. Start it in section 3 first (await start_local_agent()).\n")
                    return

                if self.app_state.get("workspace") is None:
                    _set_status("failed", "startup")
                    _out("Workspace is not prepared. Run section 1 first.\n")
                    return

                _out("Pipeline started. Per-stage logs and pipeline_state.json will be written under the run directory in artifacts/.\n")

                async def _run():
                    start_time = time.monotonic()
                    done = False

                    async def _heartbeat():
                        while not done:
                            elapsed = int(time.monotonic() - start_time)
                            timer_status.value = f"<b>Elapsed:</b> {elapsed}s"
                            await asyncio.sleep(1)

                    hb_task = asyncio.create_task(_heartbeat())
                    _set_status("running", "starting")

                    def _on_stage(stage: str, idx: int, total: int) -> None:
                        _set_status("running", f"{stage} ({idx}/{total})")

                    try:
                        run_pipeline_button.disabled = True
                        rerun_failed_button.disabled = True
                        run_pipeline_button.description = "Running..."
                        await _call_run_pipeline_with_compat(out_fn=_out, on_stage=_on_stage)
                        _refresh_output_status()
                        if self.app_state.get("last_pipeline_status") == "completed":
                            _set_status("completed", "all stages")
                            _out("\n✓ Full pipeline finished. Check per-stage log files for details.\n")
                        else:
                            failed_stage = self.app_state.get("last_failed_stage") or "unknown"
                            _set_status("failed", failed_stage)
                            _out(f"\n✗ Pipeline failed at stage: {failed_stage}. Check that stage log file.\n")
                    except Exception as exc:
                        import traceback as _tb

                        _set_status("failed", "exception")
                        _out(f"Pipeline run failed: {exc}\n{_tb.format_exc()}\n")
                    finally:
                        done = True
                        await hb_task
                        run_pipeline_button.disabled = False
                        rerun_failed_button.disabled = False
                        run_pipeline_button.description = "Run Full Pipeline"

                try:
                    from tornado.ioloop import IOLoop

                    IOLoop.current().spawn_callback(_run)
                except Exception:
                    asyncio.ensure_future(_run())

            def _run_last_failed_clicked(_):
                pipeline_text.value = ""
                _set_status("idle", "-")
                timer_status.value = "<b>Elapsed:</b> 0s"

                if self.app_state.get("workspace") is None:
                    _set_status("failed", "startup")
                    _out("Workspace is not prepared. Run section 1 first.\n")
                    return

                _out(
                    "Reloading the latest pipeline_state.json and rerunning the last failed stage only. "
                    "If the stage already has cached plan data, the agent is not required for this retry.\n"
                )

                async def _run_failed_stage():
                    start_time = time.monotonic()
                    done = False

                    async def _heartbeat():
                        while not done:
                            elapsed = int(time.monotonic() - start_time)
                            timer_status.value = f"<b>Elapsed:</b> {elapsed}s"
                            await asyncio.sleep(1)

                    hb_task = asyncio.create_task(_heartbeat())
                    _set_status("running", "resume")

                    try:
                        run_pipeline_button.disabled = True
                        rerun_failed_button.disabled = True
                        rerun_failed_button.description = "Resuming..."
                        workspace_root = str(self.app_state["workspace"].root)
                        result = await self.ns["run_last_failed_stage"](workspace_root=workspace_root, out_fn=_out)
                        _refresh_output_status()
                        resumed_stages = list(result.keys())
                        rerun_stage = resumed_stages[-1] if resumed_stages else (self.app_state.get("last_failed_stage") or "unknown")
                        status = self.app_state.get("last_pipeline_status", "partial")
                        if status == "failed":
                            _set_status("failed", rerun_stage)
                            _out(f"\n✗ Stage {rerun_stage} failed again.\n")
                        elif status == "completed":
                            _set_status("completed", rerun_stage)
                            if len(resumed_stages) > 1:
                                _out(
                                    "\n✓ Resume run completed the remaining stages: "
                                    + " -> ".join(resumed_stages)
                                    + ".\n"
                                )
                            else:
                                _out(f"\n✓ Stage {rerun_stage} succeeded and the pipeline has no remaining failed stages.\n")
                        else:
                            _set_status("partial", rerun_stage)
                            if len(resumed_stages) > 1:
                                _out(
                                    "\n✓ Resume run advanced these stages: "
                                    + " -> ".join(resumed_stages)
                                    + ". Later stages remain pending.\n"
                                )
                            else:
                                _out(f"\n✓ Stage {rerun_stage} succeeded. Later stages remain pending.\n")
                    except Exception as exc:
                        import traceback as _tb

                        _set_status("failed", "resume")
                        _out(f"Resume run failed: {exc}\n{_tb.format_exc()}\n")
                    finally:
                        done = True
                        await hb_task
                        run_pipeline_button.disabled = False
                        rerun_failed_button.disabled = False
                        rerun_failed_button.description = "Run Last Failed Stage"

                try:
                    from tornado.ioloop import IOLoop

                    IOLoop.current().spawn_callback(_run_failed_stage)
                except Exception:
                    asyncio.ensure_future(_run_failed_stage())

                return

            rerun_failed_button.on_click(_run_last_failed_clicked)

            run_pipeline_button.on_click(_run_pipeline_clicked)
            display(
                widgets.VBox(
                    [
                        docs_url_widget,
                        widgets.HBox([run_pipeline_button, rerun_failed_button]),
                        widgets.HBox([run_status, stage_status, timer_status]),
                        output_dir_status,
                        pipeline_text,
                    ]
                )
            )
            print("Stages (run in order): " + " -> ".join(self.ns["PIPELINE_STAGES"]))
            print("Each run writes stage logs plus pipeline_state.json under: <workspace>/artifacts/run_<timestamp>")
            return

        print("Widget UI disabled. Run the full pipeline manually:")
        print("  await run_pipeline()")

    def render_chat_section(self) -> None:
        if self.use_widgets:
            widgets = self.widgets
            preset_widget = widgets.Dropdown(options=list(PRESET_QUESTIONS.keys()), description="Preset")
            user_question_widget = widgets.Textarea(
                value="",
                placeholder="Optional follow-up question or feedback...",
                description="Question",
                layout=widgets.Layout(width="90%", height="120px"),
            )
            ask_button = widgets.Button(description="Ask agent", button_style="primary")
            chat_output = widgets.Output()
            self._set_widget_globals(
                preset_widget=preset_widget,
                user_question_widget=user_question_widget,
                ask_button=ask_button,
                chat_output=chat_output,
            )

            def _ask_clicked(_):
                async def _run():
                    context = self.ns["prompt_context"]()
                    preset = PRESET_QUESTIONS[preset_widget.value]
                    follow_up = user_question_widget.value.strip()
                    prompt = f"{context}\n\n{preset}"
                    if follow_up:
                        prompt += f"\n\nUser follow-up: {follow_up}"
                    with chat_output:
                        clear_output(wait=True)
                        answer = await self.ns["ask_agent"](prompt)
                        print(answer)
                        self.app_state["results"]["latest_question"] = answer

                asyncio.create_task(_run())

            ask_button.on_click(_ask_clicked)
            display(widgets.VBox([preset_widget, user_question_widget, ask_button, chat_output]))
            return

        print("Widget UI disabled. Manual example:")
        print("await ask_agent(prompt_context() + '\n\nReview the repository context and ask the next best clarifying question with explicit options.')")

    def render_outcome_feedback_section(self) -> None:
        if self.use_widgets:
            widgets = self.widgets
            outcome_widget = widgets.Dropdown(
                options=[
                    "Looks good",
                    "Need more detail",
                    "Need shorter answer",
                    "Wrong language/build guess",
                    "Need runtime guidance",
                    "Need build help",
                ],
                description="Feedback",
            )
            next_step_button = widgets.Button(description="Store feedback", button_style="info")
            next_step_output = widgets.Output()
            self._set_widget_globals(
                outcome_widget=outcome_widget,
                next_step_button=next_step_button,
                next_step_output=next_step_output,
            )

            def _store_feedback(_):
                self.app_state.setdefault("feedback", {})["last_feedback"] = outcome_widget.value
                with next_step_output:
                    clear_output(wait=True)
                    print("✓ Feedback stored")
                    print("Current feedback state:")
                    pprint(self.app_state["feedback"])

            next_step_button.on_click(_store_feedback)
            display(widgets.VBox([outcome_widget, next_step_button, next_step_output]))
            return

        self.app_state.setdefault("feedback", {})["last_feedback"] = "Looks good"
        print("Widget UI disabled. Stored default feedback: Looks good")
        pprint(self.app_state["feedback"])


def install_notebook_widgets(namespace: MutableMapping[str, Any]) -> NotebookWidgetRuntime:
    runtime = NotebookWidgetRuntime(namespace)
    runtime.install()
    return runtime