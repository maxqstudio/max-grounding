"""Prove ignored operator/runtime scratch cannot contaminate project truth."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / ".workflow" / "tools"
sys.path.insert(0, str(TOOLS))

from generate_module_map import EXCLUDED as MODULE_EXCLUDED
from generate_sequence_actual import collect_python
from generate_symbol_index import python_symbols
from sequence_contract import compute_source_digest, source_files
from validate_cross_document_consistency import EXCLUDED as CONSISTENCY_EXCLUDED


SCRATCH_DIRS = (
    ".runtime",
    ".evidence",
    ".local-acceptance",
    ".pytest_cache",
)


class SourceInventoryIsolationTests(unittest.TestCase):
    def test_operator_scratch_does_not_enter_structural_code_facts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text(
                "def accepted_entry():\n    return True\n", encoding="utf-8"
            )
            for name in SCRATCH_DIRS:
                scratch = root / name
                scratch.mkdir()
                (scratch / "probe.py").write_text(
                    "def scratch_only():\n    return False\n", encoding="utf-8"
                )

            files = [path.relative_to(root).as_posix() for path in source_files(root)]
            self.assertEqual(files, ["src/app.py"])

            nodes, _edges, _warnings = collect_python(root)
            self.assertFalse(
                any("scratch_only" in node["id"] for node in nodes)
            )

            symbols = python_symbols(root)
            self.assertFalse(any("scratch_only" in symbol["symbol"] for symbol in symbols))
            self.assertTrue(all(name in MODULE_EXCLUDED for name in SCRATCH_DIRS))
            self.assertTrue(all(name in CONSISTENCY_EXCLUDED for name in SCRATCH_DIRS))

    def test_scratch_files_do_not_change_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
            before = compute_source_digest(root)
            for name in SCRATCH_DIRS:
                scratch = root / name
                scratch.mkdir()
                (scratch / "untracked.py").write_text("VALUE = 99\n", encoding="utf-8")
                (scratch / "report.xml").write_text("<result />\n", encoding="utf-8")
            self.assertEqual(compute_source_digest(root), before)


if __name__ == "__main__":
    unittest.main()
