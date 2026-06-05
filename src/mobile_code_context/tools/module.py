"""Module tools — module info and feature module discovery."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mobile_code_context.detector.platform import detect_module_for_file, IGNORE_DIRS
from mobile_code_context.indexer.store import VectorStore


def format_module_info(repo_path: Path, path: str, store: VectorStore) -> str:
    """Get detailed information about the module containing a given path."""
    abs_path = repo_path / path
    if not abs_path.exists():
        # Try as a relative path pattern match
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for f in files:
                if path in str(Path(root) / f):
                    abs_path = Path(root) / f
                    break

    module = detect_module_for_file(abs_path, repo_path)
    if module is None:
        return f"Could not detect module for: {path}"

    # Get files in this module from store
    files = store.get_files_in_module(module)

    # Detect build.gradle for dependencies
    module_path = repo_path / module
    build_file = None
    for name in ("build.gradle.kts", "build.gradle"):
        if (module_path / name).exists():
            build_file = module_path / name
            break

    deps: list[str] = []
    if build_file:
        try:
            content = build_file.read_text(errors="replace")
            # Extract implementation/api dependencies
            import re

            dep_pattern = re.compile(
                r'(implementation|api)\s*\(?(?:project\()?["\':](.*?)["\']'
            )
            for match in dep_pattern.finditer(content):
                deps.append(f"{match.group(1)}: {match.group(2)}")
        except OSError:
            pass

    # Detect packages
    packages: set[str] = set()
    for file_path in files:
        parts = file_path.split("/")
        # Find src/main/java or src/main/kotlin pattern
        for i, part in enumerate(parts):
            if part in ("java", "kotlin") and i > 0 and parts[i - 1] == "main":
                pkg_parts = parts[i + 1 : -1]  # Exclude filename
                if pkg_parts:
                    packages.add(".".join(pkg_parts))
                break

    lines: list[str] = []
    lines.append(f"## Module: {module}")
    lines.append(f"- **Path:** {module}")
    lines.append(f"- **Files:** {len(files)}")

    if packages:
        lines.append(f"- **Packages:** {', '.join(sorted(packages)[:10])}")

    if deps:
        lines.append("\n### Dependencies:")
        for d in deps[:20]:
            lines.append(f"  - {d}")

    if files:
        lines.append(f"\n### Files ({len(files)}):")
        for f in sorted(files)[:30]:
            lines.append(f"  - {f}")
        if len(files) > 30:
            lines.append(f"  ... and {len(files) - 30} more")

    return "\n".join(lines)


def format_feature_search(repo_path: Path, feature_name: str, store: VectorStore) -> str:
    """Find feature modules matching a name."""
    modules = store.get_modules()

    # Fuzzy match
    feature_lower = feature_name.lower().replace("_", "").replace("-", "")
    matches: list[tuple[str, float]] = []

    for module in modules:
        module_lower = module.lower().replace("_", "").replace("-", "").replace("/", "")
        if feature_lower in module_lower:
            # Higher score for more specific match
            score = len(feature_lower) / len(module_lower) if module_lower else 0
            matches.append((module, score))

    matches.sort(key=lambda x: -x[1])

    if not matches:
        return f"No feature module found matching: '{feature_name}'\n\nAvailable modules:\n" + "\n".join(
            f"  - {m}" for m in modules[:20]
        )

    lines: list[str] = []
    lines.append(f"## Feature modules matching '{feature_name}':\n")

    for module, score in matches[:5]:
        files = store.get_files_in_module(module)
        lines.append(f"### {module}")
        lines.append(f"- Files: {len(files)}")

        # Layer coverage
        layers = {
            "ViewModel": any("ViewModel" in f for f in files),
            "Screen": any("Screen" in f or "Fragment" in f for f in files),
            "UseCase": any("UseCase" in f or "Interactor" in f for f in files),
            "Repository": any("Repository" in f for f in files),
            "Service": any("Service" in f or "DataSource" in f for f in files),
        }
        present = [k for k, v in layers.items() if v]
        missing = [k for k, v in layers.items() if not v]

        if present:
            lines.append(f"- Layers present: {', '.join(present)}")
        if missing:
            lines.append(f"- Layers missing: {', '.join(missing)}")
        lines.append("")

    return "\n".join(lines)
