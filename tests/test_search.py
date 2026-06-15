"""Tests for search result formatting and generic parallel-duplicate dedup."""

from mobile_code_context.tools.search import (
    deduplicate_parallel_results,
    format_search_results,
    _relevance_label,
)


def _result(file_path, declarations, module, score, content="line\n" * 30):
    return {
        "file_path": file_path,
        "content": content,
        "start_line": 1,
        "end_line": 30,
        "chunk_index": 0,
        "total_chunks": 1,
        "declarations": declarations,
        "package_name": "com.example",
        "module": module,
        "arch_role": "other",
        "score": score,
    }


def test_relevance_label_buckets():
    assert _relevance_label(0.3) == "high"
    assert _relevance_label(0.8) == "medium"
    assert _relevance_label(1.5) == "low"


def test_dedup_collapses_same_symbol_across_module_roots():
    """Same symbol in two distinct module roots collapses to the best score."""
    results = [
        _result("appA/foo/Cart.kt", ["Cart"], "appA/foo", score=0.9),
        _result("appB/foo/Cart.kt", ["Cart"], "appB/foo", score=0.3),
    ]
    deduped = deduplicate_parallel_results(results)
    assert len(deduped) == 1
    # The lower-distance (better) copy survives.
    assert deduped[0]["file_path"] == "appB/foo/Cart.kt"
    assert deduped[0]["_also_in"] == ["appA"]


def test_dedup_keeps_distinct_symbols():
    results = [
        _result("appA/foo/Cart.kt", ["Cart"], "appA/foo", score=0.5),
        _result("appA/foo/Order.kt", ["Order"], "appA/foo", score=0.4),
    ]
    deduped = deduplicate_parallel_results(results)
    assert len(deduped) == 2


def test_dedup_does_not_collapse_within_single_root():
    """Same symbol in one module root (e.g. multiple chunks) is preserved."""
    results = [
        _result("appA/foo/Cart.kt", ["Cart"], "appA/foo", score=0.5),
        _result("appA/foo/Cart.kt", ["Cart"], "appA/foo", score=0.6),
    ]
    deduped = deduplicate_parallel_results(results)
    assert len(deduped) == 2


def test_dedup_preferred_prefix_wins_over_score():
    """A preferred module prefix is kept even when its score is worse."""
    results = [
        _result("legacy/foo/Cart.kt", ["Cart"], "legacy/foo", score=0.2),
        _result("main/foo/Cart.kt", ["Cart"], "main/foo", score=0.9),
    ]
    deduped = deduplicate_parallel_results(results, preferred_prefix="main")
    assert len(deduped) == 1
    assert deduped[0]["file_path"] == "main/foo/Cart.kt"


def test_concise_truncates_more_than_detailed():
    body = "val x = computeSomethingMeaningful()\n" * 100
    results = [_result("appA/foo/Cart.kt", ["Cart"], "appA/foo", score=0.3, content=body)]
    concise = format_search_results(results, "cart", response_format="concise")
    detailed = format_search_results(results, "cart", response_format="detailed")
    assert "truncated" in concise
    # Concise keeps fewer code lines than detailed (the real token saving).
    code_line = "val x = computeSomethingMeaningful()"
    assert concise.count(code_line) < detailed.count(code_line)
    assert len(concise) < len(detailed)
    assert "Relevance: high" in concise


def test_empty_results_message():
    assert "No results found" in format_search_results([], "nothing")
