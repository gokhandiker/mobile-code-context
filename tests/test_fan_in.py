"""Tests for fan-in analysis."""

import pytest
from pathlib import Path

from mobile_code_context.context.fan_in import analyze_fan_in
from mobile_code_context.detector.platform import PlatformInfo, PlatformType, Language


def _create_kotlin_project(tmp_path: Path) -> None:
    """Create a minimal Kotlin project structure for testing."""
    # Base class (should have high fan-in)
    base_dir = tmp_path / "core" / "base" / "src" / "main" / "kotlin"
    base_dir.mkdir(parents=True)
    (base_dir / "BaseViewModel.kt").write_text(
        "package com.example.core.base\n\nabstract class BaseViewModel {}\n"
    )
    (base_dir / "BaseUseCase.kt").write_text(
        "package com.example.core.base\n\nabstract class BaseUseCase {}\n"
    )

    # Feature files that import base classes
    for i in range(10):
        feat_dir = tmp_path / f"features/feat{i}/src/main/kotlin"
        feat_dir.mkdir(parents=True)
        (feat_dir / f"Feat{i}ViewModel.kt").write_text(
            f"package com.example.features.feat{i}\n\n"
            f"import com.example.core.base.BaseViewModel\n\n"
            f"class Feat{i}ViewModel : BaseViewModel() {{}}\n"
        )


def test_fan_in_detects_base_classes(tmp_path: Path):
    """Fan-in analysis should detect frequently imported base classes."""
    _create_kotlin_project(tmp_path)

    platform = PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt", ".kts"],
    )

    results = analyze_fan_in(tmp_path, platform, max_results=5, min_ratio=0.01)

    # BaseViewModel should be found (imported by 10 files)
    assert len(results) > 0
    file_paths = [r.file_path for r in results]
    assert any("BaseViewModel" in p for p in file_paths)


def test_fan_in_empty_project(tmp_path: Path):
    """Empty project should return no results."""
    platform = PlatformInfo(
        type=PlatformType.ANDROID,
        languages=[Language.KOTLIN],
        extensions=[".kt"],
    )

    results = analyze_fan_in(tmp_path, platform)

    assert results == []
