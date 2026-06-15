"""MCP Server entry point — registers tools and handles lifecycle."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mobile_code_context.config import Settings

logger = structlog.get_logger()

# Global state
_server = Server("mobile-code-context")
_state: dict = {}


def _get_settings() -> Settings:
    """Get or create settings from environment."""
    if "settings" not in _state:
        _state["settings"] = Settings()
    return _state["settings"]


def _get_engine():
    """Get or create the indexing/search engine."""
    from mobile_code_context.engine import Engine

    if "engine" not in _state:
        settings = _get_settings()
        _state["engine"] = Engine(settings)
    return _state["engine"]


@_server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="search_code",
            description=(
                "Semantic search over the indexed codebase. Use this to find existing "
                "patterns, implementations, and usage examples before writing or "
                "refactoring code. Returns ranked chunks with file path, line range, "
                "declarations, module, and a qualitative relevance label. Identical "
                "symbols found in parallel module roots are collapsed to the best match. "
                "Skip for trivial single-file edits where you already know the location. "
                "Defaults to a concise view; ask for 'detailed' only when you need more "
                "of each chunk, and raise top_k to page through more matches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query describing what code to find",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)",
                    },
                    "module_filter": {
                        "type": "string",
                        "description": "Filter results to a specific module path (e.g. 'features/favorites')",
                    },
                    "response_format": {
                        "type": "string",
                        "enum": ["concise", "detailed"],
                        "description": (
                            "Output verbosity. 'concise' (default) returns compact "
                            "snippets to save tokens; 'detailed' returns larger snippets."
                        ),
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_architecture_context",
            description=(
                "Returns the project's mandatory base architecture — the core classes, "
                "contracts, and patterns that new or refactored code MUST conform to. "
                "Call this once before implementing a new feature or making structural "
                "changes. By default returns base architecture only (token-efficient); "
                "set include_exemplar=true to also get a complete feature exemplar slice."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "include_exemplar": {
                        "type": "boolean",
                        "description": (
                            "Also include the full vertical-slice exemplar feature "
                            "(default: false — base architecture only)."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="get_module_info",
            description=(
                "Inspect a single module by any file/dir path inside it: its "
                "dependencies, packages, file count, and architectural layer "
                "structure. Use when you need to understand where a module sits and "
                "what it depends on before adding or moving code into it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to a file or directory within the module",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="find_feature_module",
            description=(
                "Locate the existing feature module for a name and see which "
                "architectural layers it already has. Call this BEFORE creating a new "
                "feature to check whether it exists and which layers are missing, so you "
                "extend rather than duplicate it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_name": {
                        "type": "string",
                        "description": "Feature name to search for (e.g. 'favorites', 'product_detail')",
                    },
                },
                "required": ["feature_name"],
            },
        ),
        Tool(
            name="expand_to_siblings",
            description=(
                "From one source file, find its architectural siblings (e.g. given a "
                "ViewModel, return its Screen/View, State, UseCase, Repository, Service). "
                "Use to gather the full vertical slice you must touch when editing one "
                "layer of a feature."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to a source file",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="get_project_overview",
            description=(
                "High-level project map: platform, module/file counts, top-level module "
                "tree, and layer distribution. Call once at the start of a task to orient "
                "yourself; prefer search_code or get_module_info for specifics."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="suggest_mandatory_addition",
            description=(
                "Promote a file into the mandatory architecture context so it appears in "
                "all future get_architecture_context responses. Use sparingly — only for "
                "a genuinely core, widely-referenced base file that the auto-detection "
                "missed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to add to mandatory context",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this file should be in mandatory context",
                    },
                },
                "required": ["file_path", "reason"],
            },
        ),
    ]


@_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch tool calls to the engine."""
    engine = _get_engine()

    # Auto-reindex if HEAD changed
    settings = _get_settings()
    if settings.reindex_on_tool_call:
        await engine.ensure_index_fresh()

    try:
        if name == "search_code":
            result = await engine.search_code(
                query=arguments["query"],
                top_k=arguments.get("top_k"),
                module_filter=arguments.get("module_filter"),
                response_format=arguments.get("response_format"),
            )
        elif name == "get_architecture_context":
            result = await engine.get_architecture_context(
                include_exemplar=arguments.get("include_exemplar"),
            )
        elif name == "get_module_info":
            result = await engine.get_module_info(path=arguments["path"])
        elif name == "find_feature_module":
            result = await engine.find_feature_module(feature_name=arguments["feature_name"])
        elif name == "expand_to_siblings":
            result = await engine.expand_to_siblings(file_path=arguments["file_path"])
        elif name == "get_project_overview":
            result = await engine.get_project_overview()
        elif name == "suggest_mandatory_addition":
            result = await engine.suggest_mandatory_addition(
                file_path=arguments["file_path"],
                reason=arguments["reason"],
            )
        else:
            result = f"Unknown tool: {name}"

        return [TextContent(type="text", text=result)]

    except Exception as e:
        logger.error("tool_call_failed", tool=name, error=str(e))
        return [TextContent(type="text", text=f"Error: {e}")]


def main():
    """Entry point for the MCP server."""
    # Allow repo_path from CLI argument if not set in env
    if len(sys.argv) > 1 and sys.argv[1] == "--repo":
        import os

        os.environ["MCC_REPO_PATH"] = sys.argv[2]

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await _server.run(read_stream, write_stream, _server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
