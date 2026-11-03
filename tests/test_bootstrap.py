from __future__ import annotations

import os
import sys
import unittest
from importlib.metadata import version

from max_grounding import project_identity


class BootstrapTests(unittest.TestCase):
    def test_project_identity_is_stable_and_cross_platform(self) -> None:
        identity = project_identity()
        self.assertEqual(identity["name"], "max-grounding")
        self.assertEqual(identity["version"], "0.0.1")
        self.assertEqual(version("max-grounding"), identity["version"])
        self.assertIs(identity["cross_platform"], True)
        self.assertEqual(identity["interfaces"], ("python",))

    def test_runtime_uses_supported_python(self) -> None:
        self.assertGreaterEqual(sys.version_info, (3, 11))
        self.assertIn(os.name, {"nt", "posix"})


if __name__ == "__main__":
    unittest.main()
