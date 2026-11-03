#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from sequence_contract import validate_before_implementation_lineage


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()


def commit_file(root: Path, path: str, content: str, message: str) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        git(root, "init", "-b", "main")
        git(root, "config", "user.name", "Sequence Selftest")
        git(root, "config", "user.email", "sequence@example.invalid")
        main_base = commit_file(root, "base.txt", "base\n", "base")
        git(root, "checkout", "-b", "feature")
        frozen = commit_file(root, "plan.json", "{\"status\":\"FROZEN\"}\n", "freeze plan")
        implementation_base = commit_file(root, "implementation.txt", "start\n", "implementation base")
        accepted_branch_head = commit_file(root, "implementation.txt", "final\n", "accepted branch")
        git(root, "checkout", "main")
        git(root, "checkout", accepted_branch_head, "--", ".")
        git(root, "add", "-A")
        git(root, "commit", "-m", "squash product")
        product_merge_sha = git(root, "rev-parse", "HEAD")
        direct = validate_before_implementation_lineage(root, implementation_base, product_merge_sha, None)
        assert direct["failures"] == ["IMPLEMENTATION_BASE_NOT_ANCESTOR_OF_HEAD"], direct
        provenance = {"strategy": "SQUASH", "accepted_branch_head": accepted_branch_head, "product_merge_sha": product_merge_sha}
        repaired = validate_before_implementation_lineage(root, implementation_base, product_merge_sha, provenance)
        assert repaired["failures"] == [], repaired
        assert repaired["accepted_branch_tree"] == repaired["product_merge_tree"], repaired
        closure = commit_file(root, "closure.txt", "governance only\n", "closure")
        historical = validate_before_implementation_lineage(root, implementation_base, closure, provenance)
        assert historical["failures"] == [], historical
        wrong_tree = dict(provenance, product_merge_sha=main_base)
        mismatch = validate_before_implementation_lineage(root, implementation_base, closure, wrong_tree)
        assert "SQUASH_TREE_MISMATCH" in mismatch["failures"], mismatch
        wrong_branch = dict(provenance, accepted_branch_head=frozen)
        wrong_lineage = validate_before_implementation_lineage(root, implementation_base, closure, wrong_branch)
        assert "SQUASH_ACCEPTED_BRANCH_NOT_DESCENDANT_OF_IMPLEMENTATION_BASE" in wrong_lineage["failures"], wrong_lineage
        not_ancestor = commit_file(root, "after.txt", "after\n", "after")
        git(root, "checkout", "--detach", product_merge_sha)
        detached = validate_before_implementation_lineage(root, implementation_base, product_merge_sha, dict(provenance, product_merge_sha=not_ancestor))
        assert "SQUASH_PRODUCT_MERGE_NOT_ANCESTOR_OF_HEAD" in detached["failures"], detached
    print("SEQUENCE_SQUASH_PROVENANCE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
