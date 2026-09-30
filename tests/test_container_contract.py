from __future__ import annotations

import pathlib
import unittest


class Phase12ContainerContractTests(unittest.TestCase):
    def test_production_dockerfile_is_non_root_and_exposes_only_service_runtime(self) -> None:
        text = pathlib.Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim-bookworm", text)
        self.assertIn("USER maxgrounding", text)
        self.assertIn("EXPOSE 8080", text)
        self.assertIn("HEALTHCHECK", text)
        self.assertIn("max_grounding.api:create_production_app", text)
        self.assertIn("--factory", text)
        self.assertNotIn("MAX_GROUNDING_API_KEY=", text)
        self.assertNotIn("pytest", text)

    def test_dockerignore_excludes_governance_tests_and_repository_metadata(self) -> None:
        ignored = {
            line.strip()
            for line in pathlib.Path(".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        for required in {".git", ".github", ".workflow", "tests", "integration", "docs"}:
            self.assertIn(required, ignored)


if __name__ == "__main__":
    unittest.main()
