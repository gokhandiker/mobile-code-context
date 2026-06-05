"""Siblings tool — find architecturally related files."""

from __future__ import annotations

import os
from pathlib import Path

from mobile_code_context.detector.platform import PlatformInfo, Language


# Sibling patterns: given a suffix, look for these related suffixes
_KOTLIN_SIBLING_MAP: dict[str, list[str]] = {
    "ViewModel.kt": ["Screen.kt", "ScreenState.kt", "ScreenAction.kt", "UseCase.kt", "Graph.kt"],
    "Screen.kt": ["ViewModel.kt", "ScreenState.kt", "ScreenAction.kt"],
    "ScreenState.kt": ["ViewModel.kt", "Screen.kt", "ScreenAction.kt"],
    "UseCase.kt": ["ViewModel.kt", "Repository.kt", "RepositoryImpl.kt"],
    "Repository.kt": ["RepositoryImpl.kt", "UseCase.kt", "Service.kt"],
    "RepositoryImpl.kt": ["Repository.kt", "Service.kt", "DataSource.kt"],
    "Service.kt": ["Repository.kt", "RepositoryImpl.kt"],
    "Fragment.kt": ["ViewModel.kt", "Adapter.kt"],
}

_SWIFT_SIBLING_MAP: dict[str, list[str]] = {
    "ViewModel.swift": ["View.swift", "State.swift", "UseCase.swift", "Reducer.swift"],
    "View.swift": ["ViewModel.swift", "State.swift"],
    "ViewController.swift": ["ViewModel.swift", "Router.swift", "Coordinator.swift"],
    "UseCase.swift": ["ViewModel.swift", "Repository.swift"],
    "Repository.swift": ["UseCase.swift", "Service.swift"],
    "Service.swift": ["Repository.swift"],
    "Reducer.swift": ["State.swift", "ViewModel.swift", "View.swift"],
}


def find_siblings(repo_path: Path, file_path: str, platform: PlatformInfo) -> str:
    """Find architectural siblings for a given file.

    Given 'FavoritesViewModel.kt', finds:
    - FavoritesScreen.kt
    - FavoritesScreenState.kt
    - FavoritesUseCase.kt
    - etc.
    """
    # Determine which sibling map to use
    if Language.KOTLIN in platform.languages and file_path.endswith(".kt"):
        sibling_map = _KOTLIN_SIBLING_MAP
        ext = ".kt"
    elif Language.SWIFT in platform.languages and file_path.endswith(".swift"):
        sibling_map = _SWIFT_SIBLING_MAP
        ext = ".swift"
    else:
        return f"Cannot determine siblings for: {file_path}"

    filename = os.path.basename(file_path)

    # Find which pattern this file matches
    matching_suffix: str | None = None
    base_name: str = ""

    for suffix in sibling_map:
        if filename.endswith(suffix):
            matching_suffix = suffix
            base_name = filename[: -len(suffix)]
            break

    if not matching_suffix or not base_name:
        return f"Cannot determine architectural role of: {filename}"

    # Look for siblings
    target_suffixes = sibling_map[matching_suffix]
    found: list[tuple[str, str]] = []  # (role, path)
    not_found: list[str] = []

    # Search in same directory and nearby directories
    file_dir = os.path.dirname(file_path)
    search_dirs = _get_search_dirs(repo_path, file_dir)

    for suffix in target_suffixes:
        target_name = base_name + suffix
        role = suffix.replace(ext, "").replace(".", "")

        found_path = _find_file_in_dirs(repo_path, search_dirs, target_name)
        if found_path:
            found.append((role, found_path))
        else:
            not_found.append(role)

    # Format output
    lines: list[str] = []
    lines.append(f"## Siblings for: {filename}")
    lines.append(f"Base name: `{base_name}` | Role: `{matching_suffix.replace(ext, '')}`\n")

    if found:
        lines.append("### Found:")
        for role, path in found:
            lines.append(f"  - **{role}**: {path}")

    if not_found:
        lines.append("\n### Not found (may need to be created):")
        for role in not_found:
            lines.append(f"  - **{role}**: {base_name}{role}{ext}")

    return "\n".join(lines)


def _get_search_dirs(repo_path: Path, file_dir: str) -> list[str]:
    """Get directories to search for siblings.

    Searches: same dir, parent dir, and common layer-based siblings dirs.
    """
    dirs = [file_dir]

    # Walk up and across for multi-module projects
    # e.g., features/favorites/presentation → also check features/favorites/domain, /data
    parts = file_dir.split("/")
    if len(parts) >= 2:
        parent = "/".join(parts[:-1])
        dirs.append(parent)

        # Check sibling layer directories
        for layer in ("presentation", "domain", "data", "ui", "di"):
            sibling_dir = f"{parent}/{layer}"
            if (repo_path / sibling_dir).exists():
                dirs.append(sibling_dir)

    return dirs


def _find_file_in_dirs(repo_path: Path, search_dirs: list[str], filename: str) -> str | None:
    """Search for a file in multiple directories (recursively)."""
    for dir_path in search_dirs:
        abs_dir = repo_path / dir_path
        if not abs_dir.exists():
            continue

        for root, _, files in os.walk(abs_dir):
            if filename in files:
                return str((Path(root) / filename).relative_to(repo_path))

    return None
