#!/usr/bin/env python3
"""Regression tests for public documentation separation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import selftest_documentation_contract
from validate_doc_quality import GENERATED_MARKER
from validate_public_docs import (
    GENERATED_REFERENCE_FILES,
    HANDBOOK_LINKS,
    PUBLIC_CONTENT_REQUIREMENTS,
    PUBLIC_MARKER,
    README_REQUIRED_HEADINGS,
    REQUIRED_PUBLIC_FILES,
    validate,
)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        readme = "\n\n".join(README_REQUIRED_HEADINGS) + "\n\n[Handbook](docs/handbook/README.md)\n"
        (root / "README.md").write_text(readme, encoding="utf-8", newline="\n")

        for relative in REQUIRED_PUBLIC_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            body = f"<!-- {PUBLIC_MARKER} -->\n\n# Public doc\n"
            if relative == "docs/README.md":
                body += "\n[Handbook](handbook/README.md)\n\nSYSTEM_OVERVIEW.md CURRENT_STATE.md ROADMAP.md PROJECT_TRUTH_SYNC.md\n"
            if relative == "docs/handbook/README.md":
                body += "\n" + "\n".join(f"[{link}]({link})" for link in HANDBOOK_LINKS) + "\n"
            required_content = PUBLIC_CONTENT_REQUIREMENTS.get(relative, ())
            if required_content:
                body += "\n" + "\n\n".join(required_content) + "\n"
            path.write_text(body, encoding="utf-8", newline="\n")

        for relative in GENERATED_REFERENCE_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"<!-- {GENERATED_MARKER} -->\n\n# Generated\n", encoding="utf-8", newline="\n")

        assert validate(root)["result"] == "PASS"

        target = root / REQUIRED_PUBLIC_FILES[-1]
        original = target.read_text(encoding="utf-8")
        target.write_text(original.replace(PUBLIC_MARKER, "MARKER REMOVED"), encoding="utf-8", newline="\n")
        assert validate(root)["result"] == "FAIL"
        target.write_text(original, encoding="utf-8", newline="\n")

        readme_path = root / "README.md"
        original_readme = readme_path.read_text(encoding="utf-8")
        readme_path.write_text(original_readme + "\n## Project Truth Compiler\n", encoding="utf-8", newline="\n")
        assert validate(root)["result"] == "FAIL"
        readme_path.write_text(original_readme, encoding="utf-8", newline="\n")

        rollback_path = root / "docs/handbook/reference/release-process.md"
        rollback_text = rollback_path.read_text(encoding="utf-8")
        rollback_path.write_text(
            rollback_text.replace("## Release rollback", "## Recovery notes"),
            encoding="utf-8",
            newline="\n",
        )
        assert validate(root)["result"] == "FAIL"
        rollback_path.write_text(rollback_text, encoding="utf-8", newline="\n")

        missing = root / "docs/handbook/guides/troubleshooting.md"
        missing.unlink()
        assert validate(root)["result"] == "FAIL"

    assert selftest_documentation_contract.main() == 0
    print("PUBLIC_DOC_BASELINE=PASS")
    print("PUBLIC_DOC_MARKER_TAMPER_REJECTION=PASS")
    print("README_REFERENCE_MANUAL_REGRESSION_REJECTION=PASS")
    print("ROLLBACK_GUIDANCE_REGRESSION_REJECTION=PASS")
    print("PUBLIC_DOC_MISSING_FILE_REJECTION=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
