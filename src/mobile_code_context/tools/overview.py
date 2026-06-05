"""Overview tool — project-level summary."""

from __future__ import annotations

from pathlib import Path

from mobile_code_context.detector.platform import PlatformInfo
from mobile_code_context.indexer.store import VectorStore


def format_overview(repo_path: Path, platform: PlatformInfo, store: VectorStore) -> str:
    """Format a project overview for agents."""
    modules = store.get_modules()
    file_count = store.get_file_count()
    chunk_count = store.get_chunk_count()

    lines: list[str] = []
    lines.append("## Project Overview\n")
    lines.append(f"- **Platform:** {platform.type.value}")
    lines.append(f"- **Languages:** {', '.join(l.value for l in platform.languages)}")
    lines.append(f"- **Total indexed files:** {file_count}")
    lines.append(f"- **Total chunks:** {chunk_count}")
    lines.append(f"- **Modules:** {len(modules)}")

    # Categorize modules
    feature_modules = [m for m in modules if "feature" in m.lower()]
    core_modules = [m for m in modules if any(s in m.lower() for s in ("core", "shared", "common"))]
    other_modules = [m for m in modules if m not in feature_modules and m not in core_modules]

    if core_modules:
        lines.append(f"\n### Core/Shared Modules ({len(core_modules)}):")
        for m in sorted(core_modules):
            files = store.get_files_in_module(m)
            lines.append(f"  - `{m}` ({len(files)} files)")

    if feature_modules:
        lines.append(f"\n### Feature Modules ({len(feature_modules)}):")
        for m in sorted(feature_modules):
            files = store.get_files_in_module(m)
            lines.append(f"  - `{m}` ({len(files)} files)")

    if other_modules:
        lines.append(f"\n### Other Modules ({len(other_modules)}):")
        for m in sorted(other_modules)[:15]:
            files = store.get_files_in_module(m)
            lines.append(f"  - `{m}` ({len(files)} files)")
        if len(other_modules) > 15:
            lines.append(f"  ... and {len(other_modules) - 15} more")

    return "\n".join(lines)
