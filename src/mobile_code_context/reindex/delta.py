"""
Delta reindexer — incremental indexing based on git diff.

Only processes files that have changed since the last indexed commit.
Falls back to hash comparison when git is unavailable.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import structlog
from pathlib import Path
from typing import Optional

from mobile_code_context.config import Settings
from mobile_code_context.detector.platform import PlatformInfo, detect_module_for_file
from mobile_code_context.indexer.chunker import CodeChunk, chunk_file
from mobile_code_context.indexer.embedder import CodeEmbedder
from mobile_code_context.indexer.scanner import scan_files, get_relative_path
from mobile_code_context.indexer.store import VectorStore

logger = structlog.get_logger()


def _file_hash(path: Path) -> str:
    """Compute SHA-256 hash of file content."""
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def _parse_file(file_path: Path, rel_path: str, platform: PlatformInfo):
    """Parse a source file based on its extension."""
    ext = file_path.suffix
    if ext in (".kt", ".kts"):
        from mobile_code_context.indexer.parser_kotlin import parse_kotlin_file

        parsed = parse_kotlin_file(file_path)
        parsed.file_path = rel_path
        return parsed
    elif ext == ".swift":
        from mobile_code_context.indexer.parser_swift import parse_swift_file

        parsed = parse_swift_file(file_path)
        parsed.file_path = rel_path
        return parsed
    return None


class DeltaReindexer:
    """Incremental indexer that only processes changed files."""

    def __init__(
        self,
        settings: Settings,
        platform: PlatformInfo,
        embedder: CodeEmbedder,
        store: VectorStore,
    ) -> None:
        self.settings = settings
        self.platform = platform
        self.embedder = embedder
        self.store = store

    def get_current_head(self) -> Optional[str]:
        """Get current HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.settings.repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def _get_last_indexed_commit(self) -> Optional[str]:
        """Read last indexed commit from disk."""
        path = self.settings.last_commit_path
        if path.exists():
            return path.read_text().strip()
        return None

    def _save_last_indexed_commit(self, commit: str) -> None:
        """Save current commit as last indexed."""
        self.settings.last_commit_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.last_commit_path.write_text(commit)

    def _get_changed_files_via_git(self, last_commit: str) -> Optional[set[str]]:
        """Get changed files since last commit via git diff."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", last_commit, "HEAD"],
                cwd=self.settings.repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                extensions = set(self.platform.extensions)
                files = set()
                for line in result.stdout.strip().splitlines():
                    _, ext = os.path.splitext(line)
                    if ext in extensions:
                        files.add(line)
                return files
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    async def run(self) -> dict:
        """Run incremental indexing.

        Returns:
            Dict with indexing stats (files_added, files_updated, files_removed, etc.)
        """
        repo_path = self.settings.repo_path
        last_commit = self._get_last_indexed_commit()
        current_head = self.get_current_head()

        # If same commit, nothing to do
        if last_commit and current_head and last_commit == current_head:
            file_count = self.store.get_file_count()
            if file_count > 0:
                logger.info("index_fresh", commit=current_head[:8], files=file_count)
                return {"status": "fresh", "total_files": file_count}

        # Get stored hashes for comparison
        stored_hashes = self.store.get_all_file_hashes()

        # Try git diff for fast change detection
        git_changed: Optional[set[str]] = None
        if last_commit and current_head:
            git_changed = self._get_changed_files_via_git(last_commit)

        # Scan all files
        all_files: dict[str, Path] = {}
        for file_path in scan_files(repo_path, self.platform):
            rel_path = get_relative_path(file_path, repo_path)
            all_files[rel_path] = file_path

        # Determine what changed
        to_add: list[str] = []
        to_update: list[str] = []
        to_remove: list[str] = []

        for rel_path, abs_path in all_files.items():
            if git_changed is not None and rel_path not in git_changed:
                # Git says unchanged and we have it indexed → skip
                if rel_path in stored_hashes:
                    continue

            # Compare hashes
            current_hash = _file_hash(abs_path)
            if rel_path not in stored_hashes:
                to_add.append(rel_path)
            elif stored_hashes[rel_path] != current_hash:
                to_update.append(rel_path)

        # Files that were indexed but no longer exist
        current_paths = set(all_files.keys())
        for rel_path in stored_hashes:
            if rel_path not in current_paths:
                to_remove.append(rel_path)

        logger.info(
            "change_detection_complete",
            to_add=len(to_add),
            to_update=len(to_update),
            to_remove=len(to_remove),
            unchanged=len(all_files) - len(to_add) - len(to_update),
        )

        # Remove deleted/updated files from index
        for rel_path in to_remove + to_update:
            self.store.remove_by_file(rel_path)

        # Process new and updated files
        files_to_process = to_add + to_update
        if files_to_process:
            await self._process_files(files_to_process, all_files)

        # Save commit
        if current_head:
            self._save_last_indexed_commit(current_head)

        total_files = self.store.get_file_count()
        return {
            "status": "reindexed",
            "files_added": len(to_add),
            "files_updated": len(to_update),
            "files_removed": len(to_remove),
            "total_files": total_files,
        }

    async def _process_files(self, rel_paths: list[str], all_files: dict[str, Path]) -> None:
        """Parse, chunk, embed, and store a batch of files."""
        all_chunks: list[CodeChunk] = []
        file_records: list[dict] = []

        for rel_path in rel_paths:
            abs_path = all_files[rel_path]
            try:
                parsed = _parse_file(abs_path, rel_path, self.platform)
                if parsed is None:
                    continue

                chunks = chunk_file(parsed, self.settings)
                all_chunks.extend(chunks)

                # File registry record
                content_hash = _file_hash(abs_path)
                module = detect_module_for_file(abs_path, self.settings.repo_path) or ""
                file_records.append({
                    "file_path": rel_path,
                    "content_hash": content_hash,
                    "line_count": parsed.line_count,
                    "module": module,
                })
            except Exception as e:
                logger.warning("parse_failed", file=rel_path, error=str(e))
                continue

        if not all_chunks:
            return

        # Batch embed
        texts = [c.content for c in all_chunks]
        logger.info("embedding_chunks", count=len(texts))
        embeddings = self.embedder.embed_chunks(texts)

        # Store chunks
        chunk_dicts = []
        for chunk in all_chunks:
            chunk_dicts.append({
                "file_path": chunk.file_path,
                "content": chunk.content,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "chunk_index": chunk.chunk_index,
                "total_chunks": chunk.total_chunks,
                "declarations": chunk.declarations,
                "package_name": chunk.package_name or "",
                "module": chunk.module,
                "chunk_type": chunk.chunk_type,
                "arch_role": chunk.arch_role,
            })

        self.store.insert_chunks(chunk_dicts, embeddings)

        # Register files
        self.store.register_files_batch(file_records)

        logger.info("files_processed", files=len(file_records), chunks=len(chunk_dicts))
