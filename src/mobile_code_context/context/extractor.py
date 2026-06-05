"""
Anchored extractor — extracts key methods from large base files.

For files that are too large to include in full, this extracts only
the most important methods using a comment/string-aware brace-balance
state machine.
"""

from __future__ import annotations

import re
import structlog
from pathlib import Path

logger = structlog.get_logger()

# Default caps
MAX_METHOD_LINES = 40
MAX_TOTAL_LINES = 100


def extract_anchored_content(
    file_path: Path,
    max_method_lines: int = MAX_METHOD_LINES,
    max_total_lines: int = MAX_TOTAL_LINES,
) -> str:
    """Extract key methods from a large file using auto-detected anchors.

    For files > 200 lines, this:
    1. Finds all public/open function signatures
    2. Extracts each method body using brace-balance
    3. Caps at max_method_lines per method and max_total_lines total
    4. Joins with "// ... (truncated)" markers

    Args:
        file_path: Path to source file
        max_method_lines: Max lines per extracted method
        max_total_lines: Max total lines for output

    Returns:
        Extracted content string with markers between gaps
    """
    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    if len(lines) <= 200:
        # Small enough to include in full
        return content

    # Auto-detect anchors: public/open/abstract function/class signatures
    ext = file_path.suffix
    if ext in (".kt", ".kts"):
        anchor_pattern = re.compile(
            r"^\s*(public|open|abstract|override|internal)?\s*(fun|class|interface|abstract class)\s+\w+",
        )
    elif ext == ".swift":
        anchor_pattern = re.compile(
            r"^\s*(public|open|internal)?\s*(func|class|struct|protocol)\s+\w+",
        )
    else:
        return content[:max_total_lines * 80]  # Fallback: first N lines worth

    # Find anchor lines
    anchor_lines: list[int] = []
    for i, line in enumerate(lines):
        if anchor_pattern.match(line):
            anchor_lines.append(i)

    if not anchor_lines:
        # No anchors found — return first max_total_lines
        return "\n".join(lines[:max_total_lines])

    # Extract methods from anchors using brace-balance
    extracted_ranges: list[tuple[int, int]] = []
    total_extracted = 0

    for anchor_line in anchor_lines:
        if total_extracted >= max_total_lines:
            break

        end_line = _find_method_end(lines, anchor_line)
        method_lines = end_line - anchor_line + 1

        # Cap method size
        if method_lines > max_method_lines:
            end_line = anchor_line + max_method_lines - 1

        # Check total budget
        actual_lines = end_line - anchor_line + 1
        if total_extracted + actual_lines > max_total_lines:
            break

        extracted_ranges.append((anchor_line, end_line))
        total_extracted += actual_lines

    # Merge overlapping ranges
    merged = _merge_ranges(extracted_ranges)

    # Build output with gap markers
    output_lines: list[str] = []
    last_end = -1

    for start, end in merged:
        if last_end >= 0 and start > last_end + 1:
            output_lines.append("// ... (truncated)")
            output_lines.append("")
        output_lines.extend(lines[start : end + 1])
        last_end = end

    return "\n".join(output_lines)


def _find_method_end(lines: list[str], start: int) -> int:
    """Find the end of a method/class body using brace-balance.

    Uses a state machine aware of comments and strings.
    """
    brace_count = 0
    found_first_brace = False
    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_triple_string = False

    for i in range(start, len(lines)):
        line = lines[i]
        j = 0
        while j < len(line):
            # Check for triple-quoted string
            if not in_block_comment and not in_line_comment:
                if line[j:j+3] == '"""':
                    if in_triple_string:
                        in_triple_string = False
                        j += 3
                        continue
                    elif not in_string:
                        in_triple_string = True
                        j += 3
                        continue

            if in_triple_string:
                j += 1
                continue

            # Block comment
            if not in_string and line[j:j+2] == "/*":
                in_block_comment = True
                j += 2
                continue
            if in_block_comment and line[j:j+2] == "*/":
                in_block_comment = False
                j += 2
                continue
            if in_block_comment:
                j += 1
                continue

            # Line comment
            if not in_string and line[j:j+2] == "//":
                break  # Rest of line is comment

            # String handling
            if not in_string and line[j] == '"':
                in_string = True
                j += 1
                continue
            if in_string:
                if line[j] == '\\':
                    j += 2  # Skip escaped char
                    continue
                if line[j] == '"':
                    in_string = False
                j += 1
                continue

            # Brace counting
            if line[j] == '{':
                brace_count += 1
                found_first_brace = True
            elif line[j] == '}':
                brace_count -= 1
                if found_first_brace and brace_count == 0:
                    return i

            j += 1

    return min(start + MAX_METHOD_LINES, len(lines) - 1)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent ranges."""
    if not ranges:
        return []

    sorted_ranges = sorted(ranges)
    merged: list[tuple[int, int]] = [sorted_ranges[0]]

    for start, end in sorted_ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 2:  # Adjacent (gap <= 1 line)
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged
