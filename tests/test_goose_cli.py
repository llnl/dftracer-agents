import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dftracer_agents.cli import build_goose_extension_link, build_goose_session_command


class GooseCliIntegrationTests(unittest.TestCase):
    def test_goose_extension_link_encodes_dftracer_server_command(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        link = build_goose_extension_link(repo_root)
        parsed = urlparse(link)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "goose")
        self.assertEqual(parsed.netloc, "extension")
        self.assertEqual(query["cmd"], [str(repo_root / ".venv" / "bin" / "python")])
        self.assertEqual(query["arg"], ["-m", "dftracer_agents.mcp_servers.server"])
        self.assertEqual(query["id"], ["dftracer-pipeline-mcp"])
        self.assertEqual(query["name"], ["DFTracer Pipeline MCP"])

    def test_goose_session_command_uses_dftracer_extension(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        cmd = build_goose_session_command(repo_root)

        self.assertEqual(cmd[0], str(repo_root / "scripts" / "start_goose.sh"))
        self.assertEqual(cmd[1:3], ["session", "--with-extension"])
        self.assertEqual(cmd[3], f"{repo_root / '.venv' / 'bin' / 'python'} -m dftracer_agents.mcp_servers.server")


if __name__ == "__main__":
    unittest.main()