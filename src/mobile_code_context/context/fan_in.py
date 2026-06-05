"""
Fan-in analyzer — detects base architecture files by import frequency.

Files imported by a high percentage of the codebase are likely core
infrastructure (base classes, extensions, utilities).
"""

from __future__ import annotations

import os
import re
import structlog
from pathlib import Path
from dataclasses import dataclass

from mobile_code_context.detector.platform import PlatformInfo, IGNORE_DIRS, Language

logger = structlog.get_logger()

# Import patterns
_KOTLIN_IMPORT_RE = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)
_SWIFT_IMPORT_RE = re.compile(r"^import\s+(\w+)", re.MULTILINE)


@dataclass
class FanInResult:
    """Result of fan-in analysis for a file."""

    file_path: str
    fan_in_count: int  # How many files import this
    fan_in_ratio: float  # fan_in_count / total_files
    confidence: float  # 0-1 score


def analyze_fan_in(
    repo_path: Path,
    platform: PlatformInfo,
    max_results: int = 15,
    min_ratio: float = 0.02,
) -> list[FanInResult]:
    """Analyze import frequency to find base architecture files.

    Scans all source files, builds an import graph, and returns
    files with the highest fan-in (most imported by others).

    Args:
        repo_path: Repository root path
        platform: Detected platform info
        max_results: Maximum number of base files to return
        min_ratio: Minimum fan-in ratio threshold

    Returns:
        Sorted list of FanInResult (highest fan-in first)
    """
    extensions = set(platform.extensions)

    # Phase 1: Collect all file paths and their packages
    file_packages: dict[str, str] = {}  # rel_path → package/module
    package_to_file: dict[str, str] = {}  # package.ClassName → rel_path

    all_files: list[str] = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext not in extensions:
                continue

            abs_path = Path(root) / filename
            rel_path = str(abs_path.relative_to(repo_path))
            all_files.append(rel_path)

            # Quick package extraction (fast regex, no tree-sitter needed)
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
                if ext in (".kt", ".kts"):
                    pkg_match = re.search(r"^package\s+([\w.]+)", content, re.MULTILINE)
                    if pkg_match:
                        package = pkg_match.group(1)
                        file_packages[rel_path] = package
                        # Map package.ClassName to file
                        class_name = filename.replace(ext, "")
                        package_to_file[f"{package}.{class_name}"] = rel_path
                elif ext == ".swift":
                    # Swift uses module-level imports, map filename
                    file_packages[rel_path] = filename.replace(ext, "")
            except (OSError, UnicodeDecodeError):
                continue

    total_files = len(all_files)
    if total_files == 0:
        return []

    # Phase 2: Count imports (who imports whom)
    import_counts: dict[str, int] = {}  # rel_path → count of files that import it

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext not in extensions:
                continue

            abs_path = Path(root) / filename
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            if ext in (".kt", ".kts"):
                imports = _KOTLIN_IMPORT_RE.findall(content)
                for imp in imports:
                    # Try to resolve import to a file
                    if imp in package_to_file:
                        target = package_to_file[imp]
                        import_counts[target] = import_counts.get(target, 0) + 1
                    else:
                        # Try partial match (last segment = class name)
                        parts = imp.rsplit(".", 1)
                        if len(parts) == 2:
                            for fqn, path in package_to_file.items():
                                if fqn.endswith(f".{parts[1]}") and parts[0] in fqn:
                                    import_counts[path] = import_counts.get(path, 0) + 1
                                    break

    # Phase 3: Rank by fan-in
    results: list[FanInResult] = []
    for file_path, count in import_counts.items():
        ratio = count / total_files
        if ratio >= min_ratio:
            results.append(
                FanInResult(
                    file_path=file_path,
                    fan_in_count=count,
                    fan_in_ratio=ratio,
                    confidence=min(ratio * 10, 1.0),  # Normalize to 0-1
                )
            )

    # Sort by fan-in count descending
    results.sort(key=lambda r: r.fan_in_count, reverse=True)

    # Filter: prefer files in core/base/shared/common paths
    prioritized: list[FanInResult] = []
    others: list[FanInResult] = []

    for r in results:
        lower = r.file_path.lower()
        if any(seg in lower for seg in ("core/", "base/", "shared/", "common/", "foundation/")):
            prioritized.append(r)
        else:
            others.append(r)

    final = (prioritized + others)[:max_results]

    logger.info(
        "fan_in_analysis_complete",
        total_files=total_files,
        candidates=len(results),
        selected=len(final),
    )

    return final
