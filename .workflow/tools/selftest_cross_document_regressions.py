#!/usr/bin/env python3
from pathlib import Path

import selftest_closure_defect_lifecycle
from generate_project_docs import claim_backlink_comment
from validate_cross_document_consistency import is_skill_reference_document, is_template_document


def main() -> int:
    specs = {
        "claims.json": {
            "claims": [
                {
                    "id": "TRUTH-TEST-BACKLINK",
                    "documents": ["SYSTEM_OVERVIEW.md", "docs/ROADMAP.md"],
                }
            ]
        }
    }
    overview = claim_backlink_comment(specs, "SYSTEM_OVERVIEW.md")
    roadmap = claim_backlink_comment(specs, "ROADMAP.md")
    other = claim_backlink_comment(specs, "CURRENT_STATE.md")
    if "TRUTH-TEST-BACKLINK" not in overview or "TRUTH-TEST-BACKLINK" not in roadmap:
        raise RuntimeError("declared claim backlink was not projected")
    if other:
        raise RuntimeError("claim backlink leaked into undeclared document")

    root = Path("/repo")
    if not is_template_document(root, root / "templates" / "PROJECT_TRUTH_SYNC.md"):
        raise RuntimeError("template document was not recognized")
    if is_template_document(root, root / "docs" / "PROJECT_TRUTH_SYNC.md"):
        raise RuntimeError("canonical document was misclassified as template")
    if not is_skill_reference_document(root, root / "references" / "project-truth-synchronization.md"):
        raise RuntimeError("bundled skill reference was not recognized")
    if is_skill_reference_document(root, root / "docs" / "PROJECT_TRUTH_SYNC.md"):
        raise RuntimeError("canonical project document was misclassified as a skill reference")

    if selftest_closure_defect_lifecycle.main() != 0:
        raise RuntimeError("closure/defect lifecycle regression failed")

    print("CLAIM_BACKLINK_PROJECTION=PASS")
    print("TEMPLATE_PATH_SYMBOL_SCOPE=PASS")
    print("SKILL_REFERENCE_CLAIM_SCOPE=PASS")
    print("CROSS_DOCUMENT_REGRESSION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
