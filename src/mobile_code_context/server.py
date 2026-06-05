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
                "Semantic search over the indexed codebase. Returns ranked code chunks "
                "with file path, line range, declarations, and relevance score. "
                "Use this to find patterns, implementations, and usage examples."
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
                        "description": "Number of results to return (default: 10)",
                        "default": 10,
                    },
                    "module_filter": {
                        "type": "string",
                        "description": "Filter results to a specific module path (e.g. 'features/favorites')",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_architecture_context",
            description=(
                "Returns the project's base architecture context — core classes, patterns, "
                "and a complete feature exemplar. This is the mandatory context that shows "
                "how code MUST be structured in this project."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "include_exemplar": {
                        "type": "boolean",
                        "description": "Include full vertical slice exemplar (default: true)",
                        "default": True,
                    },
                },
            },
        ),
        Tool(
            name="get_module_info",
            description=(
                "Get information about a specific module — its dependencies, packages, "
                "file count, and layer structure."
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
                "Find the feature module that matches a given name. Returns module path, "
                "existing files, and layer coverage (which architectural layers are present)."
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
                "Given a file path, find related architectural siblings. For example, "
                "given a ViewModel, find its Screen, State, UseCase, Repository, and Service files."
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
                "Get a high-level overview of the project — platform, module count, "
                "file count, top-level module tree, and architectural layer distribution."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="suggest_mandatory_addition",
            description=(
                "Suggest adding a file to the mandatory architecture context. "
                "The file will be included in all future architecture context responses. "
                "Use this when you notice a frequently-referenced base file that isn't "
                "already in the mandatory context."
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
                top_k=arguments.get("top_k", 10),
                module_filter=arguments.get("module_filter"),
            )
        elif name == "get_architecture_context":
            result = await engine.get_architecture_context(
                include_exemplar=arguments.get("include_exemplar", True),
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
