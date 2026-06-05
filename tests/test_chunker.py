"""Tests for the code chunker."""

import pytest

from mobile_code_context.indexer.chunker import chunk_file, CodeChunk
from mobile_code_context.indexer.parser_kotlin import parse_kotlin_source


def _make_kotlin_source(num_functions: int, lines_per_function: int = 10) -> str:
    """Generate Kotlin source with N functions."""
    lines = ["package com.example.test", "", "import com.example.base.BaseClass", ""]

    for i in range(num_functions):
        lines.append(f"fun function{i}() {{")
        for j in range(lines_per_function - 2):
            lines.append(f"    val x{j} = {j}")
        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def test_small_file_single_chunk():
    """Files ≤100 lines should be a single chunk."""
    source = _make_kotlin_source(5, lines_per_function=8)
    parsed = parse_kotlin_source(source, "com/example/Small.kt")

    chunks = chunk_file(parsed)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "whole_file"
    assert chunks[0].file_path == "com/example/Small.kt"


def test_medium_file_multiple_chunks():
    """Files 100-300 lines should be split into AST chunks."""
    source = _make_kotlin_source(15, lines_per_function=12)
    parsed = parse_kotlin_source(source, "com/example/Medium.kt")

    chunks = chunk_file(parsed)

    assert len(chunks) > 1
    assert all(c.chunk_type == "ast_chunk" for c in chunks)
    # No overlap for medium files
    assert not any(c.has_overlap for c in chunks)


def test_large_file_with_overlap():
    """Files >300 lines should have overlapping chunks."""
    source = _make_kotlin_source(40, lines_per_function=10)
    parsed = parse_kotlin_source(source, "com/example/Large.kt")

    chunks = chunk_file(parsed)

    assert len(chunks) > 1
    # Second+ chunks should have overlap
    if len(chunks) > 1:
        assert chunks[1].has_overlap


def test_chunk_metadata():
    """Chunks should have correct metadata."""
    source = _make_kotlin_source(3, lines_per_function=8)
    parsed = parse_kotlin_source(source, "features/favorites/FavoritesViewModel.kt")

    chunks = chunk_file(parsed)

    assert chunks[0].module == "features/favorites/FavoritesViewModel.kt"
    assert chunks[0].package_name == "com.example.test"
    assert chunks[0].arch_role == "viewmodel"


def test_empty_file():
    """Empty files should produce a single chunk."""
    parsed = parse_kotlin_source("", "Empty.kt")
    chunks = chunk_file(parsed)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "whole_file"
