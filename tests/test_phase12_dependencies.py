from __future__ import annotations

import pathlib
import tomllib
import unittest


class Phase12DependencyPinsTests(unittest.TestCase):
    def test_service_dependencies_are_exactly_pinned(self) -> None:
        data = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
        extras = data["project"]["optional-dependencies"]
        self.assertIn("fastapi==0.141.1", extras["service"])
        self.assertIn("uvicorn==0.54.0", extras["service"])
        self.assertIn("mcp==2.2.0", extras["service"])
        self.assertIn("httpx==0.28.1", extras["test"])


if __name__ == "__main__":
    unittest.main()
