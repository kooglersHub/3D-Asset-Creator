"""Offline source validation: no Modal account, GPU, or weight downloads required."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestStandaloneModalAssetFurnace(unittest.TestCase):
    def test_python_files_parse(self):
        for filename in ("tools/modal_assetfurnace.py", "tools/assetfurnace_modal_worker.py"):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=filename)
                self.assertGreater(len(tree.body), 0)

    def test_no_godot_dependency_in_modal_worker(self):
        source = (ROOT / "tools/assetfurnace_modal_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("from godot", source.lower())
        self.assertNotIn("godot.", source.lower())
        self.assertIn("mcp_server import Studio", source)

    def test_modal_has_explicit_setup_step(self):
        source = (ROOT / "tools/modal_assetfurnace.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("setup_assetfurnace", names)
        self.assertIn("generate_character", names)
        self.assertIn("main", names)


if __name__ == "__main__":
    unittest.main()
