"""Search tool — semantic code search with formatted, token-aware output."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

# Output verbosity modes.
FORMAT_CONCISE = "concise"
FORMAT_DETAILED = "detailed"

_CONCISE_SNIPPET_LINES = 15
_DETAILED_SNIPPET_LINES = 50


def _primary_symbol(result: dict[str, Any]) -> str:
    """Best-effort primary declared symbol for grouping duplicates.

    Uses the first declaration, falling back to the file stem. Language-neutral:
    works for Kotlin classes/objects/functions and Swift types/protocols/funcs.
    """
    decls = result.get("declarations") or []
    if decls:
        return str(decls[0])
    return os.path.splitext(os.path.basename(result.get("file_path", "")))[0]


def _module_root(result: dict[str, Any]) -> str:
    """Top-level module root for a result (first path segment).

    A "module root" generically identifies parallel trees — e.g. separate
    Gradle modules / app flavors on Android or distinct targets on iOS — without
    hardcoding any project-specific names.
    """
    module = result.get("module") or ""
    if module:
        return module.replace("\\", "/").split("/", 1)[0]
    return result.get("file_path", "").replace("\\", "/").split("/", 1)[0]


def _relevance_label(score: float) -> str:
    """Map a raw L2 distance (lower = closer) to a human-friendly label.

    Normalized embeddings give distances roughly in [0, 2]. We expose a coarse
    qualitative bucket instead of the raw distance, which is noise to an agent.
    """
    if score <= 0.6:
        return "high"
    if score <= 1.0:
        return "medium"
    return "low"


def deduplicate_parallel_results(
    results: list[dict[str, Any]], preferred_prefix: str = ""
) -> list[dict[str, Any]]:
    """Collapse the same symbol declared across parallel module roots.

    Generic and project-agnostic: when an identical declared symbol appears in
    two or more distinct top-level module roots, only the single best-relevance
    copy is kept (preferring ``preferred_prefix`` when set), annotated with the
    other roots it also lives in. Symbols that appear in a single module root —
    including multiple chunks of one file — are left untouched.
    """
    if not results:
        return results

    groups: dict[str, list[int]] = defaultdict(list)
    for idx, r in enumerate(results):
        groups[_primary_symbol(r)].append(idx)

    keep: set[int] = set(range(len(results)))
    annotations: dict[int, list[str]] = {}

    for _symbol, idxs in groups.items():
        roots = {_module_root(results[i]) for i in idxs}
        if len(roots) < 2:
            continue  # not a cross-module duplicate

        def _rank(i: int) -> tuple[int, float, int]:
            r = results[i]
            module = (r.get("module") or r.get("file_path") or "").replace("\\", "/")
            preferred = bool(preferred_prefix) and module.startswith(preferred_prefix)
            # lower score (L2 distance) is better; preferred wins first
            return (0 if preferred else 1, float(r.get("score", 0.0)), i)

        best = min(idxs, key=_rank)
        best_root = _module_root(results[best])
        other_roots = sorted(roots - {best_root})
        annotations[best] = other_roots
        for i in idxs:
            if i != best:
                keep.discard(i)

    deduped: list[dict[str, Any]] = []
    for i, r in enumerate(results):
        if i not in keep:
            continue
        if i in annotations and annotations[i]:
            r = {**r, "_also_in": annotations[i]}
        deduped.append(r)
    return deduped


def format_search_results(
    results: list[dict[str, Any]],
    query: str,
    response_format: str = FORMAT_CONCISE,
    preferred_prefix: str = "",
    snippet_lines: int | None = None,
) -> str:
    """Format search results for agent consumption.

    Args:
        results: Raw store results (file_path, content, declarations, module, ...).
        query: The original query (echoed in the header).
        response_format: ``concise`` (compact snippet) or ``detailed`` (larger).
        preferred_prefix: Optional module prefix preferred when deduping parallels.
        snippet_lines: Explicit snippet cap; otherwise derived from the format.
    """
    if not results:
        return f"No results found for: '{query}'"

    results = deduplicate_parallel_results(results, preferred_prefix=preferred_prefix)

    detailed = response_format == FORMAT_DETAILED
    if snippet_lines is None:
        snippet_lines = _DETAILED_SNIPPET_LINES if detailed else _CONCISE_SNIPPET_LINES

    lines: list[str] = []
    lines.append(f"## Search Results for: '{query}'")
    lines.append(f"Found {len(results)} results:\n")

    for i, r in enumerate(results, 1):
        file_path = r["file_path"]
        start = r["start_line"]
        end = r["end_line"]
        declarations = r.get("declarations", [])
        module = r.get("module", "")
        arch_role = r.get("arch_role", "other")
        relevance = _relevance_label(float(r.get("score", 0.0)))

        # Header
        lines.append(f"### {i}. {file_path}:{start}-{end}")

        # Metadata line
        meta_parts = [f"Relevance: {relevance}"]
        if module:
            meta_parts.append(f"Module: `{module}`")
        if arch_role and arch_role != "other":
            meta_parts.append(f"Role: `{arch_role}`")
        if declarations:
            meta_parts.append(f"Declarations: {', '.join(declarations)}")
        lines.append(" | ".join(meta_parts))

        also_in = r.get("_also_in")
        if also_in:
            joined = ", ".join(also_in)
            lines.append(
                f"_Also declared in {len(also_in)} other module root(s): {joined} "
                f"(showing best match)_"
            )

        # Content snippet (truncate per response format)
        content = r.get("content", "")
        content_lines = content.splitlines()
        if len(content_lines) > snippet_lines:
            content = "\n".join(content_lines[:snippet_lines]) + "\n// ... (truncated)"

        lines.append(f"```\n{content}\n```")
        lines.append("")

    if not detailed:
        lines.append(
            "_Concise view. Call with response_format='detailed' for larger "
            "snippets, or increase top_k for more results._"
        )

    return "\n".join(lines)

