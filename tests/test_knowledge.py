import unittest

from dftracer_agents.knowledge import layered_analysis_commands


class KnowledgeCommandTests(unittest.TestCase):
    def test_layered_analysis_uses_dfanalyzer_cli_without_preset(self) -> None:
        commands = layered_analysis_commands(
            trace_path="/tmp/trace/compacted",
            view_types=["time_range"],
            output_dir="/tmp/analysis",
        )

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("dfanalyzer", command)
        self.assertIn("trace_path=/tmp/trace/compacted", command)
        self.assertIn('view_types=' + "'[\"time_range\"]'", command)
        self.assertIn("hydra.run.dir=/tmp/analysis", command)
        self.assertNotIn("analyzer/preset=dlio", command)
        self.assertNotIn("init_with_hydra", command)


if __name__ == "__main__":
    unittest.main()