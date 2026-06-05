"""
AST-aware chunker for mobile source files (Kotlin & Swift).

Strategy (size-adaptive):
- ≤100 lines → whole file as single chunk
- 100–300 lines → AST-split ~125 lines/chunk
- >300 lines → AST-split ~100 lines/chunk with 20-line overlap

Chunks are split at AST boundaries (class, function, property) to
preserve semantic coherence.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from typing import Optional

from mobile_code_context.config import Settings

logger = structlog.get_logger()


# ── Chunk Model ──────────────────────────────────────────────────────────────


@dataclass
class CodeChunk:
    """A semantically coherent chunk of source code."""

    file_path: str
    chunk_index: int
    total_chunks: int
    content: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    declarations: list[str]
    package_name: Optional[str]
    module: str
    chunk_type: str  # "whole_file" | "ast_chunk"
    has_overlap: bool = False
    arch_role: str = "other"

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    @property
    def metadata(self) -> dict:
        return {
            "file_path": self.file_path,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "declarations": self.declarations,
            "package_name": self.package_name or "",
            "module": self.module,
            "chunk_type": self.chunk_type,
            "arch_role": self.arch_role,
        }


# ── Architectural role detection ─────────────────────────────────────────────

_ARCH_ROLE_SUFFIXES: list[tuple[str, str]] = [
    # Android (Kotlin)
    ("ScreenState.kt", "screenstate"),
    ("ScreenAction.kt", "screenaction"),
    ("ViewModel.kt", "viewmodel"),
    ("Screen.kt", "screen"),
    ("Fragment.kt", "screen"),
    ("UseCase.kt", "usecase"),
    ("Interactor.kt", "usecase"),
    ("RepositoryImpl.kt", "repository"),
    ("Repository.kt", "repository"),
    ("DataSource.kt", "datasource"),
    ("Service.kt", "service"),
    ("Module.kt", "di_module"),
    ("Graph.kt", "nav_graph"),
    ("Model.kt", "model"),
    ("Response.kt", "model"),
    # iOS (Swift)
    ("ViewModel.swift", "viewmodel"),
    ("View.swift", "screen"),
    ("ViewController.swift", "screen"),
    ("Interactor.swift", "usecase"),
    ("UseCase.swift", "usecase"),
    ("Repository.swift", "repository"),
    ("Service.swift", "service"),
    ("Router.swift", "nav_graph"),
    ("Coordinator.swift", "nav_graph"),
    ("Model.swift", "model"),
    ("State.swift", "screenstate"),
    ("Reducer.swift", "reducer"),
]


def _detect_arch_role(file_path: str) -> str:
    filename = file_path.split("/")[-1]
    for suffix, role in _ARCH_ROLE_SUFFIXES:
        if filename.endswith(suffix):
            return role
    return "other"


def _detect_module(file_path: str) -> str:
    """Extract module path (first 3 segments)."""
    parts = file_path.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 else file_path


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_declaration_ranges(
    declarations: list, source_lines: int
) -> list[tuple[int, int, list[str]]]:
    """Get (start_line_1, end_line_1, [names]) for declarations."""
    ranges: list[tuple[int, int, list[str]]] = []
    for decl in declarations:
        start = decl.span.start_line + 1  # Convert to 1-indexed
        end = decl.span.end_line + 1
        ranges.append((start, end, [decl.name]))
    return ranges


def _merge_ranges(
    ranges: list[tuple[int, int, list[str]]], target_lines: int
) -> list[tuple[int, int, list[str]]]:
    """Merge consecutive ranges up to target line count."""
    if not ranges:
        return []

    merged: list[tuple[int, int, list[str]]] = []
    cur_start, cur_end, cur_names = ranges[0]

    for start, end, names in ranges[1:]:
        combined = end - cur_start + 1
        if combined <= target_lines:
            cur_end = end
            cur_names = cur_names + names
        else:
            merged.append((cur_start, cur_end, cur_names))
            cur_start, cur_end, cur_names = start, end, list(names)

    merged.append((cur_start, cur_end, cur_names))
    return merged


def _add_overlap(
    ranges: list[tuple[int, int, list[str]]], overlap: int
) -> list[tuple[int, int, list[str], bool]]:
    """Add overlap lines from previous chunk."""
    result: list[tuple[int, int, list[str], bool]] = []
    for i, (start, end, names) in enumerate(ranges):
        if i == 0:
            result.append((start, end, names, False))
        else:
            overlap_start = max(1, start - overlap)
            result.append((overlap_start, end, names, True))
    return result


def _build_header_kotlin(package_name: Optional[str], imports: list) -> str:
    """Build header for Kotlin chunks."""
    lines: list[str] = []
    if package_name:
        lines.append(f"package {package_name}")
        lines.append("")
    for imp in imports:
        path = imp.path
        if imp.is_wildcard:
            path += ".*"
        if imp.alias:
            lines.append(f"import {path} as {imp.alias}")
        else:
            lines.append(f"import {path}")
    if lines:
        lines.append("")
    return "\n".join(lines)


def _build_header_swift(imports: list) -> str:
    """Build header for Swift chunks."""
    lines: list[str] = []
    for imp in imports:
        lines.append(f"import {imp.module}")
    if lines:
        lines.append("")
    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────────────


def chunk_file(
    parsed_file,
    settings: Optional[Settings] = None,
) -> list[CodeChunk]:
    """
    Chunk a parsed file (Kotlin or Swift) into semantically coherent pieces.

    Args:
        parsed_file: KotlinFile or SwiftFile instance
        settings: Optional settings for chunk sizes

    Returns:
        List of CodeChunk instances
    """
    # Determine thresholds
    small_threshold = 100
    medium_threshold = 300
    medium_target = 125
    large_target = 100
    overlap = 20

    if settings:
        small_threshold = settings.chunk_small_threshold
        medium_threshold = settings.chunk_medium_threshold
        medium_target = settings.chunk_target_lines_medium
        large_target = settings.chunk_target_lines_large
        overlap = settings.chunk_overlap_lines

    source_lines = parsed_file.source_text.splitlines()
    total_lines = len(source_lines)
    file_path = parsed_file.file_path
    module = _detect_module(file_path)
    arch_role = _detect_arch_role(file_path)

    # Determine package/header based on file type
    from mobile_code_context.indexer.parser_kotlin import KotlinFile
    from mobile_code_context.indexer.parser_swift import SwiftFile

    if isinstance(parsed_file, KotlinFile):
        package_name = parsed_file.package_name
        header = _build_header_kotlin(package_name, parsed_file.imports)
    elif isinstance(parsed_file, SwiftFile):
        package_name = None
        header = _build_header_swift(parsed_file.imports)
    else:
        package_name = None
        header = ""

    # ── Small file: single chunk ──
    if total_lines <= small_threshold:
        return [
            CodeChunk(
                file_path=file_path,
                chunk_index=0,
                total_chunks=1,
                content=parsed_file.source_text,
                start_line=1,
                end_line=total_lines,
                declarations=parsed_file.declaration_names,
                package_name=package_name,
                module=module,
                chunk_type="whole_file",
                arch_role=arch_role,
            )
        ]

    # ── Get declaration ranges ──
    decl_ranges = _get_declaration_ranges(parsed_file.declarations, total_lines)

    if not decl_ranges:
        return [
            CodeChunk(
                file_path=file_path,
                chunk_index=0,
                total_chunks=1,
                content=parsed_file.source_text,
                start_line=1,
                end_line=total_lines,
                declarations=[],
                package_name=package_name,
                module=module,
                chunk_type="whole_file",
                arch_role=arch_role,
            )
        ]

    # ── Determine target chunk size ──
    if total_lines <= medium_threshold:
        target = medium_target
        use_overlap = False
    else:
        target = large_target
        use_overlap = True

    merged = _merge_ranges(decl_ranges, target)

    if use_overlap and len(merged) > 1:
        with_overlap = _add_overlap(merged, overlap)
    else:
        with_overlap = [(s, e, n, False) for s, e, n in merged]

    # ── Build chunks ──
    total_chunks = len(with_overlap)
    chunks: list[CodeChunk] = []

    for i, (start, end, names, has_overlap) in enumerate(with_overlap):
        chunk_lines = source_lines[start - 1 : end]
        chunk_text = "\n".join(chunk_lines)

        # Prepend header for non-first chunks
        if i > 0 and header:
            chunk_text = header + chunk_text

        chunks.append(
            CodeChunk(
                file_path=file_path,
                chunk_index=i,
                total_chunks=total_chunks,
                content=chunk_text,
                start_line=start,
                end_line=end,
                declarations=names,
                package_name=package_name,
                module=module,
                chunk_type="ast_chunk",
                has_overlap=has_overlap,
                arch_role=arch_role,
            )
        )

    return chunks
