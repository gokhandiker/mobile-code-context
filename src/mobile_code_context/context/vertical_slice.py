"""
Vertical slice scorer — finds the best feature module to use as exemplar.

Scores feature modules by layer completeness: a module with all
architectural layers (ViewModel, Screen, UseCase, Repository, Service)
is the best exemplar for showing how features should be structured.
"""

from __future__ import annotations

import os
import structlog
from dataclasses import dataclass, field
from pathlib import Path

from mobile_code_context.detector.platform import PlatformInfo, IGNORE_DIRS, Language

logger = structlog.get_logger()


# Layer definitions per platform
_ANDROID_LAYERS = {
    "viewmodel": ["ViewModel.kt"],
    "screen": ["Screen.kt", "Fragment.kt", "Activity.kt"],
    "state": ["State.kt", "ScreenState.kt", "UIState.kt"],
    "usecase": ["UseCase.kt", "Interactor.kt"],
    "repository": ["Repository.kt", "RepositoryImpl.kt"],
    "service": ["Service.kt", "Api.kt", "DataSource.kt"],
}

_IOS_LAYERS = {
    "viewmodel": ["ViewModel.swift"],
    "view": ["View.swift", "ViewController.swift"],
    "state": ["State.swift", "Reducer.swift"],
    "usecase": ["UseCase.swift", "Interactor.swift"],
    "repository": ["Repository.swift"],
    "service": ["Service.swift", "API.swift", "DataSource.swift"],
}


@dataclass
class LayerCoverage:
    """Coverage of a specific architectural layer."""

    layer_name: str
    files: list[str] = field(default_factory=list)

    @property
    def is_present(self) -> bool:
        return len(self.files) > 0


@dataclass
class VerticalSliceResult:
    """A scored feature module with its layer coverage."""

    module_path: str
    layers: list[LayerCoverage] = field(default_factory=list)
    total_files: int = 0
    total_lines: int = 0

    @property
    def score(self) -> float:
        """Layer completeness score (0-1)."""
        if not self.layers:
            return 0.0
        present = sum(1 for l in self.layers if l.is_present)
        return present / len(self.layers)

    @property
    def present_layers(self) -> list[str]:
        return [l.layer_name for l in self.layers if l.is_present]

    @property
    def missing_layers(self) -> list[str]:
        return [l.layer_name for l in self.layers if not l.is_present]


def find_best_exemplar(
    repo_path: Path,
    platform: PlatformInfo,
    max_exemplar_files: int = 12,
) -> VerticalSliceResult | None:
    """Find the best feature module to use as architectural exemplar.

    Scans feature modules and scores them by layer completeness.
    Prefers smaller modules (fewer total lines) among equally-scored ones.

    Args:
        repo_path: Repository root
        platform: Detected platform info
        max_exemplar_files: Maximum files to include in exemplar

    Returns:
        Best scoring VerticalSliceResult, or None if no features found
    """
    # Determine layer definitions
    if Language.KOTLIN in platform.languages:
        layer_defs = _ANDROID_LAYERS
        extensions = {".kt", ".kts"}
    elif Language.SWIFT in platform.languages:
        layer_defs = _IOS_LAYERS
        extensions = {".swift"}
    else:
        return None

    # Find feature modules
    feature_modules = [m for m in platform.modules if m.is_feature]

    if not feature_modules:
        # Fallback: look for directories named "feature*"
        for root, dirs, _ in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            rel = Path(root).relative_to(repo_path)
            if "feature" in str(rel).lower() and len(rel.parts) <= 3:
                from mobile_code_context.detector.platform import ModuleDetection

                feature_modules.append(
                    ModuleDetection(
                        name=rel.name,
                        path=str(rel),
                        build_file="",
                        is_feature=True,
                    )
                )

    if not feature_modules:
        return None

    # Score each feature module
    results: list[VerticalSliceResult] = []

    for module in feature_modules:
        module_path = repo_path / module.path
        if not module_path.exists():
            continue

        # Collect all source files in module
        all_files: list[str] = []
        total_lines = 0

        for root, dirs, files in os.walk(module_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for filename in files:
                _, ext = os.path.splitext(filename)
                if ext in extensions:
                    rel_path = str((Path(root) / filename).relative_to(repo_path))
                    all_files.append(rel_path)
                    try:
                        lines = (Path(root) / filename).read_text(errors="replace").count("\n") + 1
                        total_lines += lines
                    except OSError:
                        pass

        if not all_files:
            continue

        # Check layer coverage
        layers: list[LayerCoverage] = []
        for layer_name, suffixes in layer_defs.items():
            layer_files = [
                f for f in all_files if any(f.endswith(s) for s in suffixes)
            ]
            layers.append(LayerCoverage(layer_name=layer_name, files=layer_files))

        results.append(
            VerticalSliceResult(
                module_path=module.path,
                layers=layers,
                total_files=len(all_files),
                total_lines=total_lines,
            )
        )

    if not results:
        return None

    # Sort: highest score first, then smallest total_lines (compact exemplar)
    results.sort(key=lambda r: (-r.score, r.total_lines))

    best = results[0]
    logger.info(
        "exemplar_selected",
        module=best.module_path,
        score=best.score,
        layers=best.present_layers,
        files=best.total_files,
    )

    return best


def get_exemplar_files(
    repo_path: Path,
    exemplar: VerticalSliceResult,
    max_files: int = 12,
) -> list[str]:
    """Get the file paths to include from the exemplar module.

    Prioritizes one file per layer for a clean vertical slice.
    """
    selected: list[str] = []

    for layer in exemplar.layers:
        if layer.is_present:
            # Take the first (usually most canonical) file per layer
            selected.append(layer.files[0])
            if len(selected) >= max_files:
                break

    return selected
