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


def _layer_defs_for(platform: PlatformInfo):
    """Return (layer_defs, extensions) for the platform, or (None, None)."""
    if Language.KOTLIN in platform.languages:
        return _ANDROID_LAYERS, {".kt", ".kts"}
    if Language.SWIFT in platform.languages:
        return _IOS_LAYERS, {".swift"}
    return None, None


def _score_module(
    repo_path: Path, module_path: str, layer_defs: dict, extensions: set[str]
) -> VerticalSliceResult | None:
    """Score a single module by architectural-layer coverage."""
    abs_module = repo_path / module_path
    if not abs_module.exists():
        return None

    all_files: list[str] = []
    total_lines = 0
    for root, dirs, files in os.walk(abs_module):
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
        return None

    layers: list[LayerCoverage] = []
    for layer_name, suffixes in layer_defs.items():
        layer_files = [f for f in all_files if any(f.endswith(s) for s in suffixes)]
        layers.append(LayerCoverage(layer_name=layer_name, files=layer_files))

    return VerticalSliceResult(
        module_path=module_path,
        layers=layers,
        total_files=len(all_files),
        total_lines=total_lines,
    )


def find_best_exemplar(
    repo_path: Path,
    platform: PlatformInfo,
    max_exemplar_files: int = 12,
    preferred_module: str = "",
) -> VerticalSliceResult | None:
    """Find the best feature module to use as architectural exemplar.

    Scans feature modules and scores them by layer completeness. Among
    equally-complete modules it prefers richer, more representative ones (more
    files) rather than the smallest. A non-empty ``preferred_module`` pins the
    exemplar to a specific module path when it resolves to real source files.

    Args:
        repo_path: Repository root
        platform: Detected platform info
        max_exemplar_files: Maximum files to include in exemplar
        preferred_module: Optional module path to force as the exemplar

    Returns:
        Best scoring VerticalSliceResult, or None if no features found
    """
    layer_defs, extensions = _layer_defs_for(platform)
    if layer_defs is None:
        return None

    # Explicit override wins when it resolves to a real module with sources.
    if preferred_module:
        forced = _score_module(repo_path, preferred_module.strip("/"), layer_defs, extensions)
        if forced is not None:
            logger.info(
                "exemplar_pinned",
                module=forced.module_path,
                score=forced.score,
                files=forced.total_files,
            )
            return forced
        logger.warning("exemplar_override_unresolved", module=preferred_module)

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
        scored = _score_module(repo_path, module.path, layer_defs, extensions)
        if scored is not None:
            results.append(scored)

    if not results:
        return None

    # Sort: most complete first, then the richer (more files) module as a more
    # representative exemplar — avoids picking a trivially small slice.
    results.sort(key=lambda r: (-r.score, -r.total_files))

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
