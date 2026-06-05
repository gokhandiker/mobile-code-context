"""File scanner — discovers source files in the repository."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import structlog

from mobile_code_context.detector.platform import IGNORE_DIRS, PlatformInfo

logger = structlog.get_logger()


def scan_files(repo_path: Path, platform: PlatformInfo) -> Generator[Path, None, None]:
    """Scan repository for source files matching the detected platform.

    Yields absolute paths to source files.
    """
    extensions = set(platform.extensions)
    count = 0

    for root, dirs, files in os.walk(repo_path):
        # Prune ignored directories in-place for efficiency
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for filename in files:
            # Check extension
            _, ext = os.path.splitext(filename)
            if ext in extensions:
                count += 1
                yield Path(root) / filename

    logger.info("scan_complete", files_found=count)


def get_relative_path(file_path: Path, repo_path: Path) -> str:
    """Get the relative path of a file from the repo root."""
    return str(file_path.relative_to(repo_path))
