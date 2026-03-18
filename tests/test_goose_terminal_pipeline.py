import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class GooseTerminalPipelineIntegrationTests(unittest.TestCase):
    def test_terminal_runner_invokes_goose_recipe_with_ior_defaults(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_goose = tmp_path / "fake-goose"
            invocations_log = tmp_path / "invocations.jsonl"

            fake_goose.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import json
                    import os
                    import pathlib
                    import sys

                    log_path = pathlib.Path(os.environ["DFTRACER_GOOSE_INVOCATIONS"])
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(sys.argv[1:]) + "\\n")

                    params = {}
                    args = sys.argv[1:]
                    for index, arg in enumerate(args):
                        if arg == "--params" and index + 1 < len(args):
                            key, value = args[index + 1].split("=", 1)
                            params[key] = value

                    print(json.dumps({
                        "summary": "planned full pipeline",
                        "stages": {
                            "detect": {"ok": True},
                            "test_default_build_setup": {"ok": True},
                            "test_default_run": {"ok": True},
                            "annotate": {"ok": True},
                            "build_with_dftracer": {"ok": True},
                            "postprocess": {"ok": True},
                            "dfanalyzer": {"ok": True},
                        },
                        "notes": [params.get("name", "")],
                    }))
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            fake_goose.chmod(fake_goose.stat().st_mode | stat.S_IEXEC)

            env = os.environ.copy()
            env.update(
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://example.invalid/v1",
                    "OPENAI_MODEL": "test-model",
                    "DFTRACER_GOOSE_BIN": str(fake_goose),
                    "DFTRACER_GOOSE_INVOCATIONS": str(invocations_log),
                }
            )

            result = subprocess.run(
                ["bash", str(repo_root / "scripts" / "run_goose_pipeline.sh")],
                cwd=str(repo_root),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("[goose-pipeline] environment:", result.stderr)
            self.assertIn("[goose-pipeline] command:", result.stderr)
            self.assertIn("[goose-pipeline] run_text:", result.stderr)
            self.assertIn('"summary": "planned full pipeline"', result.stdout)
            self.assertIn('"dfanalyzer": {"ok": true}', result.stdout)

            lines = invocations_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)

            argv = json.loads(lines[0])
            self.assertIn("run", argv)
            self.assertIn("--with-builtin", argv)
            self.assertIn("summon", argv)
            self.assertIn("--recipe", argv)
            expected_recipe = str(repo_root / "goose" / "recipes" / "00_dftracer_pipeline.yaml")
            self.assertIn(expected_recipe, argv)
            self.assertIn("name=ior", argv)
            self.assertIn("repo_url=https://github.com/hpc/ior", argv)
            self.assertIn("repo_ref=4.0.0", argv)
            self.assertIn("language=cpp", argv)
            self.assertIn(f"repo_dir={repo_root / 'workspaces' / 'ior' / 'source' / 'ior'}", argv)

    def test_terminal_runner_maps_livai_env_for_python_entrypoint(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_goose = tmp_path / "fake-goose"
            invocations_log = tmp_path / "invocations.jsonl"

            fake_goose.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import json
                    import os
                    import pathlib
                    import sys

                    log_path = pathlib.Path(os.environ["DFTRACER_GOOSE_INVOCATIONS"])
                    payload = {
                        "argv": sys.argv[1:],
                        "env": {
                            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", ""),
                            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
                            "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", ""),
                            "GOOSE_PROVIDER": os.environ.get("GOOSE_PROVIDER", ""),
                            "GOOSE_MODEL": os.environ.get("GOOSE_MODEL", ""),
                        },
                    }
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(payload) + "\\n")

                    print(json.dumps({
                        "summary": "planned full pipeline",
                        "stages": {"detect": {"ok": True}},
                    }))
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            fake_goose.chmod(fake_goose.stat().st_mode | stat.S_IEXEC)

            env = os.environ.copy()
            env.pop("OPENAI_BASE_URL", None)
            env.pop("OPENAI_API_KEY", None)
            env.pop("OPENAI_MODEL", None)
            env.pop("GOOSE_PROVIDER", None)
            env.pop("GOOSE_MODEL", None)
            env.update(
                {
                    "LIVAI_BASE_URL": "https://livai-api.llnl.gov/v1",
                    "LIVAI_API_KEY": "livai-test-key",
                    "LIVAI_MODEL": "gpt-5.4",
                    "DFTRACER_GOOSE_BIN": str(fake_goose),
                    "DFTRACER_GOOSE_INVOCATIONS": str(invocations_log),
                    "DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS": "1",
                }
            )

            result = subprocess.run(
                ["bash", str(repo_root / "scripts" / "run_goose_pipeline.sh")],
                cwd=str(repo_root),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("[goose-pipeline] environment: OPENAI_BASE_URL=set", result.stderr)
            self.assertIn("OPENAI_MODEL=gpt-5.4", result.stderr)

            first_call = json.loads(invocations_log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first_call["env"]["OPENAI_BASE_URL"], "https://livai-api.llnl.gov/v1")
            self.assertTrue(first_call["env"]["OPENAI_API_KEY"])
            self.assertEqual(first_call["env"]["OPENAI_MODEL"], "gpt-5.4")
            self.assertEqual(first_call["env"]["GOOSE_PROVIDER"], "openai")
            self.assertEqual(first_call["env"]["GOOSE_MODEL"], "gpt-5.4")
