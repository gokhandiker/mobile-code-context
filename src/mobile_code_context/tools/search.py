"""Search tool — semantic code search with formatted output."""

from __future__ import annotations

from typing import Any


def format_search_results(results: list[dict[str, Any]], query: str) -> str:
    """Format search results for agent consumption.

    Produces a structured text output with file paths, line ranges,
    declarations, and relevance scores.
    """
    if not results:
        return f"No results found for: '{query}'"

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
        score = r.get("score", 0.0)

        # Header
        lines.append(f"### {i}. {file_path}:{start}-{end}")

        # Metadata line
        meta_parts = []
        if module:
            meta_parts.append(f"Module: `{module}`")
        if arch_role != "other":
            meta_parts.append(f"Role: `{arch_role}`")
        if declarations:
            meta_parts.append(f"Declarations: {', '.join(declarations)}")
        if meta_parts:
            lines.append(" | ".join(meta_parts))

        # Content (truncate if very long)
        content = r.get("content", "")
        content_lines = content.splitlines()
        if len(content_lines) > 50:
            content = "\n".join(content_lines[:50]) + "\n// ... (truncated)"

        lines.append(f"```\n{content}\n```")
        lines.append("")

    return "\n".join(lines)
