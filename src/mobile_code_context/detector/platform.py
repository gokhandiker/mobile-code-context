"""Auto-detect mobile platform (Android/iOS) from project structure."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger()


class PlatformType(Enum):
    ANDROID = "android"
    IOS = "ios"
    MULTI = "multi"
    UNKNOWN = "unknown"


class Language(Enum):
    KOTLIN = "kotlin"
    SWIFT = "swift"


@dataclass
class ModuleDetection:
    """A detected build module (Gradle or Xcode/SPM)."""

    name: str
    path: str
    build_file: str
    is_feature: bool = False


@dataclass
class PlatformInfo:
    """Detected platform information."""

    type: PlatformType
    languages: list[Language] = field(default_factory=list)
    modules: list[ModuleDetection] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.type.value

    @property
    def is_android(self) -> bool:
        return self.type in (PlatformType.ANDROID, PlatformType.MULTI)

    @property
    def is_ios(self) -> bool:
        return self.type in (PlatformType.IOS, PlatformType.MULTI)


# Directories to always skip during scanning
IGNORE_DIRS = frozenset({
    ".git",
    ".gradle",
    ".idea",
    "build",
    "node_modules",
    "Pods",
    "DerivedData",
    ".mcp-context",
    "__pycache__",
    ".build",
    "Carthage",
})


def detect_platform(repo_path: Path) -> PlatformInfo:
    """Auto-detect mobile platform from project structure."""
    has_gradle = False
    has_xcode = False
    has_spm = False
    gradle_modules: list[ModuleDetection] = []

    # Walk only top 3 levels for build files (performance)
    for root, dirs, files in os.walk(repo_path):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        rel_root = Path(root).relative_to(repo_path)
        depth = len(rel_root.parts)
        if depth > 5:
            dirs.clear()
            continue

        # Check directories for Xcode projects
        for d in list(dirs):
            if d.endswith(".xcodeproj") or d.endswith(".xcworkspace"):
                has_xcode = True

        for f in files:
            if f in ("build.gradle.kts", "build.gradle"):
                has_gradle = True
                module_path = str(rel_root) if str(rel_root) != "." else ""
                is_feature = "feature" in module_path.lower()
                gradle_modules.append(
                    ModuleDetection(
                        name=rel_root.name if module_path else "root",
                        path=module_path,
                        build_file=f,
                        is_feature=is_feature,
                    )
                )
            elif f.endswith(".xcodeproj") or f.endswith(".xcworkspace"):
                has_xcode = True
            elif f == "Package.swift":
                has_spm = True

    # Determine platform
    if has_gradle and (has_xcode or has_spm):
        platform_type = PlatformType.MULTI
        languages = [Language.KOTLIN, Language.SWIFT]
        extensions = [".kt", ".kts", ".swift"]
    elif has_gradle:
        platform_type = PlatformType.ANDROID
        languages = [Language.KOTLIN]
        extensions = [".kt", ".kts"]
    elif has_xcode or has_spm:
        platform_type = PlatformType.IOS
        languages = [Language.SWIFT]
        extensions = [".swift"]
    else:
        platform_type = PlatformType.UNKNOWN
        languages = []
        extensions = [".kt", ".kts", ".swift"]

    info = PlatformInfo(
        type=platform_type,
        languages=languages,
        modules=gradle_modules,
        extensions=extensions,
    )

    logger.info(
        "platform_detected",
        type=platform_type.value,
        modules=len(gradle_modules),
        languages=[l.value for l in languages],
    )

    return info


def detect_module_for_file(file_path: Path, repo_path: Path) -> Optional[str]:
    """Detect which module a file belongs to by walking up to find build.gradle."""
    current = file_path.parent
    while current != repo_path and current != current.parent:
        if (current / "build.gradle.kts").exists() or (current / "build.gradle").exists():
            rel = current.relative_to(repo_path)
            return str(rel)
        current = current.parent
    return None
