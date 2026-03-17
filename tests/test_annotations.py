import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from dftracer_agents.mcp_servers.modules import annotations
from dftracer_agents.mcp_servers.modules import dftracer


@unittest.skipIf(annotations.cindex is None, "clang.cindex is required for annotation tests")
class AnnotationInjectionTests(unittest.TestCase):
    def test_c_entrypoint_uses_function_cleanup_before_return_and_finalize(self) -> None:
        source = """
int ior_main(int argc, char **argv)
{
    MPI_Init(&argc, &argv);
    if (argc < 2) return 2;
    MPI_Finalize();
    return 0;
}
"""

        updated, changed, changes = annotations.inject_cpp_or_c_annotations(Path("ior.c"), source)

        self.assertTrue(changed)
        self.assertIn("DFTRACER_C_FUNCTION_START();", updated)
        self.assertIn("DFTRACER_C_INIT(nullptr, nullptr, nullptr);", updated)
        self.assertLess(updated.index("DFTRACER_C_INIT(nullptr, nullptr, nullptr);"), updated.index("DFTRACER_C_FUNCTION_START();"))
        self.assertIn("if (argc < 2) do {", updated)
        self.assertIn("DFTRACER_C_FUNCTION_END();", updated)
        self.assertRegex(updated, r"DFTRACER_C_FUNCTION_END\(\);\s+if \(dftracer_init == 1\) \{\s+DFTRACER_C_FINI\(\);\s+dftracer_init = 0;\s+\}\s+MPI_Finalize\(\);")
        self.assertIn("added DFTracer init in ior_main()", changes)

    def test_c_function_cleanup_is_inserted_before_return(self) -> None:
        source = """
static int helper(int value)
{
    if (value < 0)
        return -1;
    return value;
}
"""

        updated, changed, _changes = annotations.inject_cpp_or_c_annotations(Path("helper.c"), source)

        self.assertTrue(changed)
        self.assertIn("DFTRACER_C_FUNCTION_START();", updated)
        self.assertIn("if (value < 0)\n        do {", updated)
        self.assertEqual(updated.count("DFTRACER_C_FUNCTION_END();"), 2)

    def test_stale_region_annotations_are_removed(self) -> None:
        source = """
static int WriteOrRead(void)
{
    DFTRACER_C_REGION_START(dftracer_io_region);
    DFTRACER_C_REGION_UPDATE_STR(dftracer_io_region, \"function\", \"WriteOrRead\");
    return 0;
    DFTRACER_C_REGION_END(dftracer_io_region);
}
"""

        cleaned, changed, changes = annotations.remove_stale_region_annotations(Path("ior.c"), source)

        self.assertTrue(changed)
        self.assertNotIn("DFTRACER_C_REGION_START", cleaned)
        self.assertNotIn("DFTRACER_C_REGION_END", cleaned)
        self.assertIn("removed stale DFTRACER_C_REGION annotations", changes)

    def test_auto_annotate_restores_from_backup_cache_before_reapplying(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "repo"
            repo.mkdir()
            source_path = repo / "helper.c"
            source_path.write_text(
                """
/* original marker */
static int helper(int value)
{
    return value;
}
""",
                encoding="utf-8",
            )

            first = dftracer.auto_annotate_application(
                repo_dir=str(repo),
                language="c",
                max_files=10,
                patch_build_files=False,
                dry_run=False,
            )

            self.assertTrue(first["ok"])
            self.assertIn("snapshot_only", first["restore"]["strategy"])
            self.assertIn("DFTRACER_C_FUNCTION_START();", source_path.read_text(encoding="utf-8"))

            source_path.write_text(
                """
/* changed marker */
static int helper(int value)
{
    return value + 1;
}
""",
                encoding="utf-8",
            )

            second = dftracer.auto_annotate_application(
                repo_dir=str(repo),
                language="c",
                max_files=10,
                patch_build_files=False,
                dry_run=False,
            )

            updated = source_path.read_text(encoding="utf-8")
            self.assertTrue(second["ok"])
            self.assertIn("backup_cache", second["restore"]["strategy"])
            self.assertIn("original marker", updated)
            self.assertNotIn("changed marker", updated)
            self.assertIn("DFTRACER_C_FUNCTION_START();", updated)