"""
Context manager — coordinates mandatory context building and learning.

Manages the lifecycle of mandatory context:
1. Auto-detection via fan-in analysis
2. Vertical slice exemplar selection
3. Persistent storage of user confirmations/additions
4. Staleness checking and re-extraction
"""

from __future__ import annotations

import json
import structlog
from datetime import datetime
from pathlib import Path
from typing import Optional

from mobile_code_context.config import Settings
from mobile_code_context.context.extractor import extract_anchored_content
from mobile_code_context.context.fan_in import (
    FanInResult,
    analyze_fan_in,
    build_symbol_index,
    resolve_exemplar_dependencies,
    resolve_supertype_closure,
)
from mobile_code_context.context.vertical_slice import (
    VerticalSliceResult,
    find_best_exemplar,
    get_exemplar_files,
)
from mobile_code_context.detector.platform import PlatformInfo
from mobile_code_context.indexer.store import VectorStore

logger = structlog.get_logger()


class ContextManager:
    """Manages mandatory architecture context with learning."""

    def __init__(self, settings: Settings, store: VectorStore) -> None:
        self.settings = settings
        self.store = store
        self._mandatory_data: Optional[dict] = None
        self._formatted_context: Optional[str] = None
        self._platform: Optional[PlatformInfo] = None

    def _load_mandatory(self) -> dict:
        """Load mandatory context data from disk."""
        path = self.settings.mandatory_path
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "base_files": [],
            "exemplar_module": None,
            "exemplar_files": [],
            "user_additions": [],
            "user_removals": [],
            "last_updated": None,
        }

    def _save_mandatory(self, data: dict) -> None:
        """Persist mandatory context data."""
        data["last_updated"] = datetime.now().isoformat()
        self.settings.mandatory_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.mandatory_path.write_text(json.dumps(data, indent=2))

    async def build_mandatory_context(
        self, repo_path: Path, platform: Optional[PlatformInfo] = None
    ) -> None:
        """Build or refresh mandatory context.

        If mandatory.json exists and has user confirmations, preserves those.
        Runs fan-in analysis and vertical slice scoring to detect/update base files.
        """
        if platform:
            self._platform = platform
        elif self._platform is None:
            from mobile_code_context.detector.platform import detect_platform

            self._platform = detect_platform(repo_path)

        existing = self._load_mandatory()

        # Check if we have user-confirmed base files already
        has_user_data = bool(existing.get("user_additions") or existing.get("base_files"))

        # Build the symbol index once and reuse it across all analyses.
        index = build_symbol_index(repo_path, self._platform)

        # Run fan-in analysis (frequency ranking)
        fan_in_results = analyze_fan_in(
            repo_path,
            self._platform,
            max_results=self.settings.mandatory_max_base_files,
            min_ratio=self.settings.mandatory_fan_in_threshold,
            contract_min_ratio=self.settings.mandatory_contract_min_ratio,
            index=index,
        )

        # Find best exemplar (also feeds the dependency-closure analysis below).
        exemplar = find_best_exemplar(repo_path, self._platform)
        exemplar_files: list[str] = []
        exemplar_module: Optional[str] = None
        if exemplar:
            exemplar_module = exemplar.module_path
            exemplar_files = get_exemplar_files(repo_path, exemplar)

        # Exemplar dependency closure — the contracts a new feature must conform
        # to (base ViewModels, scoped services, MVI contracts, extensions) that
        # plain frequency ranking tends to miss.
        contract_results: list[FanInResult] = []
        if self.settings.mandatory_include_exemplar_deps and exemplar_files:
            contract_results.extend(
                resolve_exemplar_dependencies(
                    index,
                    exemplar_files,
                    max_results=self.settings.mandatory_max_contract_files,
                )
            )

        # Transitive supertypes of fan-in + exemplar-dep files (e.g. a parent
        # base class referenced only indirectly).
        seed_paths = [r.file_path for r in fan_in_results] + [
            r.file_path for r in contract_results
        ]
        if seed_paths:
            contract_results.extend(
                resolve_supertype_closure(
                    index,
                    seed_paths,
                    depth=self.settings.mandatory_supertype_depth,
                    max_results=self.settings.mandatory_max_contract_files,
                )
            )

        # Build base files list — merge fan-in + contract signals, de-duped.
        base_files: list[dict] = []
        seen_paths: set[str] = set()
        removed_paths = {r["file_path"] for r in existing.get("user_removals", [])}

        for result in fan_in_results + contract_results:
            if result.file_path in removed_paths or result.file_path in seen_paths:
                continue
            seen_paths.add(result.file_path)
            base_files.append({
                "file_path": result.file_path,
                "source": result.source,
                "role": result.role,
                "fan_in": result.fan_in_count,
                "confidence": result.confidence,
                "confirmed": False,
            })

        # Preserve user additions
        for addition in existing.get("user_additions", []):
            path = addition["file_path"]
            if path not in removed_paths and path not in seen_paths:
                seen_paths.add(path)
                base_files.append({
                    "file_path": path,
                    "source": "user",
                    "role": None,
                    "fan_in": 0,
                    "confidence": 1.0,
                    "confirmed": True,
                })

        # Preserve confirmation status from existing
        existing_confirmed = {
            b["file_path"] for b in existing.get("base_files", []) if b.get("confirmed")
        }
        for bf in base_files:
            if bf["file_path"] in existing_confirmed:
                bf["confirmed"] = True

        # Save
        data = {
            "base_files": base_files,
            "exemplar_module": exemplar_module,
            "exemplar_files": exemplar_files,
            "user_additions": existing.get("user_additions", []),
            "user_removals": existing.get("user_removals", []),
            "last_updated": None,
        }
        self._save_mandatory(data)
        self._mandatory_data = data
        self._formatted_context = None  # Invalidate cache

        logger.info(
            "mandatory_context_built",
            base_files=len(base_files),
            exemplar_module=exemplar_module,
            exemplar_files=len(exemplar_files),
        )

    def get_formatted_context(self, include_exemplar: bool = True) -> str:
        """Get formatted mandatory context string for agents.

        Returns:
            Formatted string with [MANDATORY] and [EXEMPLAR] sections
        """
        if self._formatted_context is not None and include_exemplar:
            return self._formatted_context

        data = self._mandatory_data or self._load_mandatory()
        repo_path = self.settings.repo_path
        sections: list[str] = []

        # Base architecture files
        for bf in data.get("base_files", []):
            file_path = repo_path / bf["file_path"]
            if not file_path.exists():
                continue

            try:
                content = extract_anchored_content(
                    file_path,
                    max_method_lines=self.settings.mandatory_max_method_lines,
                    max_total_lines=self.settings.mandatory_max_lines_per_file,
                )
                sections.append(f"=== [MANDATORY] {bf['file_path']} ===\n{content}")
            except OSError:
                continue

        # Exemplar files
        if include_exemplar:
            for rel_path in data.get("exemplar_files", []):
                file_path = repo_path / rel_path
                if not file_path.exists():
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    # Truncate long exemplar files
                    lines = content.splitlines()
                    if len(lines) > self.settings.mandatory_max_lines_per_file:
                        content = "\n".join(
                            lines[: self.settings.mandatory_max_lines_per_file]
                        )
                        content += "\n// ... (truncated)"
                    sections.append(f"=== [EXEMPLAR] {rel_path} ===\n{content}")
                except OSError:
                    continue

        formatted = "\n\n".join(sections)

        if include_exemplar:
            self._formatted_context = formatted

        return formatted

    def suggest_addition(self, file_path: str, reason: str) -> str:
        """Suggest adding a file to mandatory context.

        The suggestion is persisted immediately (user confirmed via agent interaction).
        """
        data = self._mandatory_data or self._load_mandatory()

        # Check if already in base files
        existing_paths = {b["file_path"] for b in data.get("base_files", [])}
        if file_path in existing_paths:
            return f"'{file_path}' is already in mandatory context."

        # Check if file exists
        full_path = self.settings.repo_path / file_path
        if not full_path.exists():
            return f"File not found: {file_path}"

        # Add to user additions
        addition = {
            "file_path": file_path,
            "reason": reason,
            "date": datetime.now().isoformat(),
        }

        if "user_additions" not in data:
            data["user_additions"] = []
        data["user_additions"].append(addition)

        # Also add to base_files
        data["base_files"].append({
            "file_path": file_path,
            "source": "user",
            "fan_in": 0,
            "confidence": 1.0,
            "confirmed": True,
        })

        self._save_mandatory(data)
        self._mandatory_data = data
        self._formatted_context = None  # Invalidate cache

        logger.info("mandatory_addition", file=file_path, reason=reason)
        return f"Added '{file_path}' to mandatory context. Reason: {reason}"

    def remove_from_mandatory(self, file_path: str, reason: str) -> str:
        """Remove a file from mandatory context."""
        data = self._mandatory_data or self._load_mandatory()

        # Remove from base_files
        data["base_files"] = [b for b in data["base_files"] if b["file_path"] != file_path]

        # Track removal
        if "user_removals" not in data:
            data["user_removals"] = []
        data["user_removals"].append({
            "file_path": file_path,
            "reason": reason,
            "date": datetime.now().isoformat(),
        })

        self._save_mandatory(data)
        self._mandatory_data = data
        self._formatted_context = None

        return f"Removed '{file_path}' from mandatory context."

    def get_gap_hints(self, search_results: list[dict]) -> list[str]:
        """Check search results for potential mandatory context gaps.

        If a frequently-imported file appears in results but isn't in
        mandatory context, suggest it as an addition.
        """
        data = self._mandatory_data or self._load_mandatory()
        mandatory_paths = {b["file_path"] for b in data.get("base_files", [])}

        hints: list[str] = []
        for result in search_results:
            path = result.get("file_path", "")
            # Check if this looks like a base file but isn't mandatory
            lower = path.lower()
            if any(seg in lower for seg in ("core/", "base/", "shared/", "common/")):
                if path not in mandatory_paths:
                    hints.append(
                        f"ℹ️ '{path}' appears to be base architecture but isn't in "
                        f"mandatory context. Consider adding with suggest_mandatory_addition."
                    )

        return hints[:2]  # Max 2 hints per search
