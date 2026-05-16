"""Extraction orchestrator - coordinates file scanning, parsing, and storage."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Optional

from ..types import (
    Language, FileRecord, ExtractionResult, ExtractionError,
    IndexResult, Node, Edge, UnresolvedReference,
)
from ..config import CodeGraphConfig
from ..db.queries import QueryBuilder
from .extractor import TreeSitterExtractor
from .grammars import detect_language, is_language_supported


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def should_include_file(file_path: str, config: CodeGraphConfig) -> bool:
    from pathlib import PurePosixPath
    fp = file_path.replace("\\", "/")
    path = PurePosixPath(fp)

    for pattern in config.exclude:
        if _matches_glob(fp, pattern):
            return False
    for pattern in config.include:
        if _matches_glob(fp, pattern):
            return True
    return False


def _matches_glob(file_path: str, pattern: str) -> bool:
    """Match file path against a glob pattern, supporting ** for recursive matching."""
    from fnmatch import fnmatch as _fnmatch
    fp = file_path.replace("\\", "/")

    # Simple extension patterns like *.py
    if pattern.startswith("*.") and "/" not in pattern:
        if _fnmatch(fp, pattern):
            return True

    # **/prefix patterns - match at any depth
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        # Match against the filename part
        parts = fp.split("/")
        if _fnmatch(parts[-1], suffix):
            return True
        # Also try matching the full path
        if _fnmatch(fp, suffix):
            return True
        if _fnmatch(fp, pattern):
            return True
        return False

    # Directory patterns like **/dir/**
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if fp.startswith(prefix + "/") or fp == prefix:
            return True

    # Direct match
    return _fnmatch(fp, pattern)


def scan_directory(
    root_dir: str,
    config: CodeGraphConfig,
    on_progress: Optional[Callable] = None,
) -> list[str]:
    """Scan directory for source files. Uses git ls-files if available."""
    root = Path(root_dir).resolve()

    # Try git ls-files first
    git_files = _get_git_visible_files(root)
    if git_files is not None:
        files = []
        count = 0
        for fp in sorted(git_files):
            if should_include_file(fp, config):
                files.append(fp)
                count += 1
                if on_progress:
                    on_progress(count, fp)
        return files

    # Fallback: filesystem walk
    return _walk_filesystem(root, config, on_progress)


def _get_git_visible_files(root: Path) -> Optional[set[str]]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-c"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        tracked = set()
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line:
                tracked.add(line.replace("\\", "/"))

        # Untracked files
        result2 = subprocess.run(
            ["git", "ls-files", "-o", "--exclude-standard"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
        if result2.returncode == 0:
            for line in result2.stdout.split("\n"):
                line = line.strip()
                if line:
                    tracked.add(line.replace("\\", "/"))
        return tracked
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _walk_filesystem(
    root: Path,
    config: CodeGraphConfig,
    on_progress: Optional[Callable] = None,
) -> list[str]:
    files: list[str] = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Skip excluded directories in-place
        rel_dir = os.path.relpath(dirpath, str(root)).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            d for d in dirnames
            if not should_include_file(rel_dir + "/" + d + "/", config)
            and not os.path.exists(os.path.join(dirpath, d, ".codegraphignore"))
        ]

        for fname in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fname), str(root)).replace("\\", "/")
            if should_include_file(rel, config):
                files.append(rel)
                count += 1
                if on_progress:
                    on_progress(count, rel)
    return files


class ExtractionOrchestrator:
    def __init__(self, root_dir: str, config: CodeGraphConfig, queries: QueryBuilder):
        self.root_dir = root_dir
        self.config = config
        self.queries = queries

    def index_all(
        self,
        on_progress: Optional[Callable] = None,
    ) -> IndexResult:
        start = time.time()
        errors: list[ExtractionError] = []
        files_indexed = 0
        files_skipped = 0
        files_errored = 0
        total_nodes = 0
        total_edges = 0

        # Phase 1: Scan
        if on_progress:
            on_progress("scanning", 0, 0)
        files = scan_directory(self.root_dir, self.config)
        total = len(files)

        # Phase 2: Parse + Store
        for i, rel_path in enumerate(files):
            if on_progress:
                on_progress("parsing", i, total, rel_path)

            full_path = Path(self.root_dir) / rel_path
            try:
                stat = full_path.stat()
                if stat.st_size > self.config.max_file_size:
                    files_skipped += 1
                    continue

                content = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                files_errored += 1
                errors.append(ExtractionError(
                    message=f"Failed to read file: {e}",
                    file_path=rel_path,
                    code="read_error",
                ))
                continue

            language = detect_language(rel_path)
            if not is_language_supported(language):
                files_skipped += 1
                continue

            # Parse
            extractor = TreeSitterExtractor(rel_path, content, language)
            result = extractor.extract()

            if result.errors:
                for err in result.errors:
                    if not err.file_path:
                        err.file_path = rel_path
                errors.extend(result.errors)

            # Store
            if result.nodes or not result.errors:
                self._store_result(rel_path, content, language, stat, result)

            if result.nodes:
                files_indexed += 1
                total_nodes += len(result.nodes)
                total_edges += len(result.edges)
            elif any(e.severity == "error" for e in result.errors):
                files_errored += 1
            else:
                files_skipped += 1

        if on_progress:
            on_progress("parsing", total, total)

        return IndexResult(
            success=files_indexed > 0 or not any(e.severity == "error" for e in errors),
            files_indexed=files_indexed,
            files_skipped=files_skipped,
            files_errored=files_errored,
            nodes_created=total_nodes,
            edges_created=total_edges,
            errors=errors,
            duration_ms=int((time.time() - start) * 1000),
        )

    def index_file(self, rel_path: str) -> ExtractionResult:
        full_path = Path(self.root_dir) / rel_path
        try:
            stat = full_path.stat()
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ExtractionResult(
                errors=[ExtractionError(message=str(e), file_path=rel_path, code="read_error")],
            )

        if stat.st_size > self.config.max_file_size:
            return ExtractionResult(
                errors=[ExtractionError(
                    message=f"File too large: {stat.st_size}",
                    file_path=rel_path, code="size_exceeded", severity="warning",
                )],
            )

        language = detect_language(rel_path)
        if not is_language_supported(language):
            return ExtractionResult()

        extractor = TreeSitterExtractor(rel_path, content, language)
        result = extractor.extract()

        if result.nodes or not result.errors:
            self._store_result(rel_path, content, language, stat, result)

        return result

    def _store_result(
        self,
        file_path: str,
        content: str,
        language: Language,
        stat: os.stat_result,
        result: ExtractionResult,
    ) -> None:
        content_hash = hash_content(content)

        # Skip if unchanged
        existing = self.queries.get_file_by_path(file_path)
        if existing and existing.content_hash == content_hash:
            return

        if existing:
            self.queries.delete_file(file_path)

        # Filter valid nodes
        valid_nodes = [n for n in result.nodes if n.id and n.kind and n.name and n.file_path]

        if valid_nodes:
            self.queries.insert_nodes(valid_nodes)

        if result.edges:
            inserted_ids = {n.id for n in valid_nodes}
            valid_edges = [e for e in result.edges if e.source in inserted_ids and e.target in inserted_ids]
            if valid_edges:
                self.queries.insert_edges(valid_edges)

        if result.unresolved_references:
            inserted_ids = {n.id for n in valid_nodes}
            refs = [
                UnresolvedReference(
                    from_node_id=r.from_node_id,
                    reference_name=r.reference_name,
                    reference_kind=r.reference_kind,
                    line=r.line,
                    column=r.column,
                    file_path=r.file_path or file_path,
                    language=r.language or language.value if isinstance(language, Language) else str(language),
                )
                for r in result.unresolved_references
                if r.from_node_id in inserted_ids
            ]
            if refs:
                self.queries.insert_unresolved_refs_batch(refs)

        self.queries.upsert_file(FileRecord(
            path=file_path,
            content_hash=content_hash,
            language=language,
            size=stat.st_size,
            modified_at=stat.st_mtime,
            indexed_at=int(time.time() * 1000),
            node_count=len(result.nodes),
        ))
