"""Engine — orchestrates indexing, search, and context for the MCP server."""

from __future__ import annotations

import asyncio
import structlog
from pathlib import Path
from typing import Optional

from mobile_code_context.config import Settings

logger = structlog.get_logger()


class Engine:
    """Central engine coordinating index, search, context, and reindex."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._initialized = False
        self._current_head: Optional[str] = None
        self._platform = None
        self._store = None
        self._embedder = None
        self._context_manager = None
        self._structure = None

    async def initialize(self) -> str:
        """Full initialization: detect platform, index, build context."""
        from mobile_code_context.detector.platform import detect_platform
        from mobile_code_context.indexer.store import VectorStore
        from mobile_code_context.indexer.embedder import CodeEmbedder
        from mobile_code_context.context.manager import ContextManager
        from mobile_code_context.reindex.delta import DeltaReindexer

        # Ensure data directory exists
        self.settings.data_path.mkdir(parents=True, exist_ok=True)

        # Detect platform
        self._platform = detect_platform(self.settings.repo_path)
        logger.info("platform_detected", platform=self._platform.name)

        # Initialize components
        self._embedder = CodeEmbedder(
            model_name=self.settings.embedding_model,
            batch_size=self.settings.embedding_batch_size,
        )
        self._store = VectorStore(self.settings.lancedb_path, self.settings.embedding_dim)
        self._reindexer = DeltaReindexer(self.settings, self._platform, self._embedder, self._store)
        self._context_manager = ContextManager(self.settings, self._store)

        # Run initial index or delta reindex
        result = await self._reindexer.run()
        logger.info("index_complete", **result)

        # Build mandatory context
        await self._context_manager.build_mandatory_context(self.settings.repo_path)

        self._current_head = self._reindexer.get_current_head()
        self._initialized = True

        return f"Initialized: {self._platform.name}, {result.get('total_files', 0)} files indexed"

    async def ensure_index_fresh(self) -> None:
        """Check if HEAD changed and reindex if needed."""
        if not self._initialized:
            await self.initialize()
            return

        current_head = self._reindexer.get_current_head()
        if current_head != self._current_head:
            logger.info("head_changed", old=self._current_head[:8], new=current_head[:8])
            result = await self._reindexer.run()
            self._current_head = current_head

            # Refresh mandatory context if base files changed
            if result.get("base_files_changed", False):
                await self._context_manager.build_mandatory_context(self.settings.repo_path)

        # Reflect uncommitted/working-tree edits (cheap: only hashes dirty files).
        if self.settings.reindex_dirty:
            await self._reindexer.reindex_dirty_files()

    async def search_code(
        self,
        query: str,
        top_k: Optional[int] = None,
        module_filter: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        """Semantic search over indexed code chunks."""
        from mobile_code_context.tools.search import format_search_results

        if top_k is None:
            top_k = self.settings.search_top_k
        if response_format is None:
            response_format = self.settings.search_response_format

        embedding = self._embedder.embed_query(query)
        results = self._store.search(embedding, top_k=top_k, module_filter=module_filter)
        return format_search_results(
            results,
            query,
            response_format=response_format,
            preferred_prefix=self.settings.preferred_module_prefix,
        )

    async def get_architecture_context(self, include_exemplar: Optional[bool] = None) -> str:
        """Return mandatory architecture context."""
        if include_exemplar is None:
            include_exemplar = self.settings.architecture_include_exemplar_default
        return self._context_manager.get_formatted_context(include_exemplar=include_exemplar)

    async def get_module_info(self, path: str) -> str:
        """Get module information for a given path."""
        from mobile_code_context.tools.module import format_module_info

        return format_module_info(self.settings.repo_path, path, self._store)

    async def find_feature_module(self, feature_name: str) -> str:
        """Find a feature module by name."""
        from mobile_code_context.tools.module import format_feature_search

        return format_feature_search(self.settings.repo_path, feature_name, self._store)

    async def expand_to_siblings(self, file_path: str) -> str:
        """Find architectural siblings for a given file."""
        from mobile_code_context.tools.siblings import find_siblings

        return find_siblings(self.settings.repo_path, file_path, self._platform)

    async def get_project_overview(self) -> str:
        """Get project overview."""
        from mobile_code_context.tools.overview import format_overview

        return format_overview(self.settings.repo_path, self._platform, self._store)

    async def suggest_mandatory_addition(self, file_path: str, reason: str) -> str:
        """Suggest adding a file to mandatory context."""
        return self._context_manager.suggest_addition(file_path, reason)
