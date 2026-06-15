#!/usr/bin/env python3
"""Add/merge mobile-code-context MCP config into VS Code settings.json.

Usage examples:
  python scripts/setup-vscode-mcp.py
  python scripts/setup-vscode-mcp.py --project /path/to/mobile/project
  python scripts/setup-vscode-mcp.py --mode uv
    python scripts/setup-vscode-mcp.py --mode pip --command /abs/path/mobile-code-context
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_server_entry(mode: str, mcp_repo: Path, project: Path, command: str) -> dict:
    if mode == "uv":
        return {
            "command": "uv",
            "args": [
                "run",
                "--directory",
                str(mcp_repo),
                "mobile-code-context",
                "--repo",
                str(project),
            ],
        }

    return {
        "command": command,
        "args": ["--repo", str(project)],
        "env": {},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure VS Code MCP settings for mobile-code-context")
    parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="Target mobile project path (default: current directory)",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=None,
        help="Path to VS Code settings.json (default: <project>/.vscode/settings.json)",
    )
    parser.add_argument(
        "--mode",
        choices=["pip", "uv"],
        default="pip",
        help="Use installed CLI (pip) or uv run mode",
    )
    parser.add_argument(
        "--command",
        default="mobile-code-context",
        help="Command to use for pip mode (default: mobile-code-context)",
    )

    args = parser.parse_args()

    project = args.project.expanduser().resolve()
    if not project.exists() or not project.is_dir():
        print(f"Project path not found or not a directory: {project}", file=sys.stderr)
        return 1

    mcp_repo = Path(__file__).resolve().parents[1]
    settings_path = (
        args.settings.expanduser().resolve()
        if args.settings is not None
        else project / ".vscode" / "settings.json"
    )
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings: dict
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                settings = json.loads(raw)
            except json.JSONDecodeError:
                print(
                    "settings.json is not valid JSON (possibly JSONC with comments). "
                    "Please remove comments/trailing commas and run again.",
                    file=sys.stderr,
                )
                return 1
        else:
            settings = {}
    else:
        settings = {}

    mcp_servers = settings.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    mcp_servers["mobile-code-context"] = _build_server_entry(
        args.mode, mcp_repo, project, args.command
    )
    settings["mcpServers"] = mcp_servers

    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    print(f"Updated: {settings_path}")
    print("Configured MCP server: mobile-code-context")
    print(f"Project path: {project}")
    print(f"Mode: {args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
