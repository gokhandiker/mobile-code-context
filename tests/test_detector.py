"""Tests for platform detection."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mobile_code_context.detector.platform import (
    PlatformInfo,
    PlatformType,
    Language,
    detect_platform,
    detect_module_for_file,
)


def test_detect_android_project(tmp_path: Path):
    """Should detect Android project from build.gradle.kts."""
    (tmp_path / "build.gradle.kts").touch()
    (tmp_path / "app" / "build.gradle.kts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "build.gradle.kts").touch()

    result = detect_platform(tmp_path)

    assert result.type == PlatformType.ANDROID
    assert Language.KOTLIN in result.languages
    assert ".kt" in result.extensions


def test_detect_ios_project(tmp_path: Path):
    """Should detect iOS project from .xcodeproj."""
    (tmp_path / "MyApp.xcodeproj").mkdir()

    result = detect_platform(tmp_path)

    assert result.type == PlatformType.IOS
    assert Language.SWIFT in result.languages
    assert ".swift" in result.extensions


def test_detect_unknown_project(tmp_path: Path):
    """Should return UNKNOWN for projects without build files."""
    (tmp_path / "README.md").touch()

    result = detect_platform(tmp_path)

    assert result.type == PlatformType.UNKNOWN


def test_detect_module_for_file(tmp_path: Path):
    """Should find the nearest build.gradle.kts walking up."""
    module_dir = tmp_path / "features" / "favorites" / "presentation"
    module_dir.mkdir(parents=True)
    (tmp_path / "features" / "favorites" / "presentation" / "build.gradle.kts").touch()

    source_file = module_dir / "src" / "main" / "kotlin" / "FavoritesViewModel.kt"
    source_file.parent.mkdir(parents=True)
    source_file.touch()

    result = detect_module_for_file(source_file, tmp_path)

    assert result == "features/favorites/presentation"
