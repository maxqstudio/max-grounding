#!/usr/bin/env python3
"""Immutable source snapshot for one governance-engine execution.

The snapshot captures the accepted source inventory and LF-normalized bytes once,
then memoizes derived machine facts for reuse inside the same process. Snapshot
or memoized state is acceleration only; final acceptance must create a fresh
snapshot from the exact candidate HEAD.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")

SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".cs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs", ".go",
    ".swift", ".m", ".mm", ".php", ".rb", ".scala", ".sh", ".ps1", ".sql",
    ".proto", ".graphql", ".gql", ".xml", ".gradle",
}
SOURCE_EXCLUDED_PARTS = {
    ".git", ".workflow", ".idea", ".vscode", ".venv", "venv", "node_modules", "dist",
    "build", "coverage", "vendor", "__pycache__",
}


class SnapshotError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _git_optional(root: Path, *args: str) -> str:
    try:
        return _git(root, *args)
    except Exception:
        return ""


def discover_source_paths(root: Path) -> list[Path]:
    """Return the accepted Git-aware source inventory without reading files."""
    root = root.resolve()
    try:
        repo_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, subprocess.CalledProcessError):
        repo_root = None

    inside_git_metadata = False
    if repo_root is not None:
        try:
            root.relative_to(repo_root / ".git")
            inside_git_metadata = True
        except ValueError:
            pass

    if repo_root is not None and not inside_git_metadata:
        try:
            indexed_paths = subprocess.check_output(
                [
                    "git", "-C", str(root), "ls-files", "--cached", "--others",
                    "--exclude-standard", "-z",
                ],
                stderr=subprocess.STDOUT,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SnapshotError("SOURCE_INVENTORY_GIT_QUERY_FAILED") from exc
        candidates = (
            root / Path(os.fsdecode(item))
            for item in indexed_paths.split(b"\0")
            if item
        )
    else:
        candidates = root.rglob("*")

    result: list[Path] = []
    for path in candidates:
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SOURCE_EXCLUDED_PARTS for part in rel.parts):
            continue
        result.append(path)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def canonical_source_bytes(path: Path) -> bytes:
    """Preserve the accepted cross-platform digest rule: CRLF materializes as LF."""
    return path.read_bytes().replace(b"\r\n", b"\n")


@dataclass(frozen=True)
class SnapshotFile:
    relative_path: str
    absolute_path: Path
    canonical_bytes: bytes

    def text(self, *, errors: str = "strict") -> str:
        return self.canonical_bytes.decode("utf-8", errors=errors)


@dataclass(frozen=True)
class ProjectSnapshot:
    root: Path
    files: tuple[SnapshotFile, ...]
    source_digest: str
    git_head: str
    git_status: str
    capture_seconds: float
    source_enumerations: int
    file_reads: int
    _derived: dict[str, object] = field(default_factory=dict, compare=False, repr=False)
    _derived_hits: list[int] = field(default_factory=lambda: [0], compare=False, repr=False)
    _derived_misses: list[int] = field(default_factory=lambda: [0], compare=False, repr=False)

    @classmethod
    def capture(cls, root: Path) -> "ProjectSnapshot":
        started = time.perf_counter()
        root = root.resolve()
        candidates = discover_source_paths(root)
        digest = hashlib.sha256()
        captured: list[SnapshotFile] = []

        for path in candidates:
            rel = path.relative_to(root).as_posix()
            try:
                canonical = canonical_source_bytes(path)
            except OSError as exc:
                raise SnapshotError(f"SOURCE_READ_FAILED:{rel}:{type(exc).__name__}") from exc
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(canonical)
            digest.update(b"\0")
            captured.append(
                SnapshotFile(
                    relative_path=rel,
                    absolute_path=path,
                    canonical_bytes=canonical,
                )
            )

        return cls(
            root=root,
            files=tuple(captured),
            source_digest=digest.hexdigest(),
            git_head=_git_optional(root, "rev-parse", "HEAD"),
            git_status=_git_optional(root, "status", "--porcelain", "--untracked-files=all"),
            capture_seconds=time.perf_counter() - started,
            source_enumerations=1,
            file_reads=len(captured),
        )

    def source_files(self) -> list[Path]:
        return [item.absolute_path for item in self.files]

    def entry(self, path_or_relative: Path | str) -> SnapshotFile:
        value = Path(path_or_relative)
        if value.is_absolute():
            try:
                rel = value.resolve().relative_to(self.root).as_posix()
            except ValueError as exc:
                raise SnapshotError(f"PATH_OUTSIDE_SNAPSHOT:{value}") from exc
        else:
            rel = value.as_posix()
        for item in self.files:
            if item.relative_path == rel:
                return item
        raise SnapshotError(f"PATH_NOT_IN_SNAPSHOT:{rel}")

    def read_bytes(self, path_or_relative: Path | str) -> bytes:
        return self.entry(path_or_relative).canonical_bytes

    def read_text(self, path_or_relative: Path | str, *, errors: str = "strict") -> str:
        return self.entry(path_or_relative).text(errors=errors)

    def memoized(self, key: str, factory: Callable[[], T]) -> T:
        if key in self._derived:
            self._derived_hits[0] += 1
            return self._derived[key]  # type: ignore[return-value]
        value = factory()
        self._derived[key] = value
        self._derived_misses[0] += 1
        return value

    def metrics(self) -> dict[str, object]:
        return {
            "source_digest": self.source_digest,
            "files": len(self.files),
            "canonical_bytes": sum(len(item.canonical_bytes) for item in self.files),
            "source_enumerations": self.source_enumerations,
            "file_reads": self.file_reads,
            "capture_seconds": self.capture_seconds,
            "derived_cache_entries": len(self._derived),
            "derived_cache_hits": self._derived_hits[0],
            "derived_cache_misses": self._derived_misses[0],
            "git_head": self.git_head or "NOT_AVAILABLE",
        }


_ACTIVE_SNAPSHOT: ContextVar[ProjectSnapshot | None] = ContextVar(
    "skill_workflow_active_project_snapshot", default=None
)


@contextmanager
def active_project_snapshot(snapshot: ProjectSnapshot) -> Iterator[ProjectSnapshot]:
    token = _ACTIVE_SNAPSHOT.set(snapshot)
    try:
        yield snapshot
    finally:
        _ACTIVE_SNAPSHOT.reset(token)


def active_snapshot_for(root: Path) -> ProjectSnapshot | None:
    snapshot = _ACTIVE_SNAPSHOT.get()
    if snapshot is None:
        return None
    return snapshot if snapshot.root == root.resolve() else None


def resolve_snapshot(root: Path, snapshot: ProjectSnapshot | None = None) -> ProjectSnapshot:
    root = root.resolve()
    if snapshot is not None:
        if snapshot.root != root:
            raise SnapshotError(
                f"SNAPSHOT_ROOT_MISMATCH:{snapshot.root.as_posix()}:{root.as_posix()}"
            )
        return snapshot
    active = active_snapshot_for(root)
    return active if active is not None else ProjectSnapshot.capture(root)
