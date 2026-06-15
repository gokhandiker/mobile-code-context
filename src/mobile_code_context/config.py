"""Configuration for the Mobile Code Context MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """MCP server settings — loaded from environment variables."""

    # Required: path to the mobile project repository
    repo_path: Path = Field(description="Path to the mobile project repository")

    # Data directory for index, mandatory context, and learnings
    data_dir: str = Field(
        default=".mcp-context",
        description="Directory name for MCP data (relative to repo_path)",
    )

    # Embedding model
    embedding_model: str = Field(
        default="nomic-ai/CodeRankEmbed",
        description="Sentence-transformers model for code embeddings",
    )
    embedding_batch_size: int = Field(default=32, description="Batch size for embedding generation")
    embedding_dim: int = Field(default=768, description="Embedding dimension")

    # Chunking
    chunk_small_threshold: int = Field(
        default=100, description="Files <= this many lines become a single chunk"
    )
    chunk_medium_threshold: int = Field(
        default=300, description="Files <= this many lines get medium-sized chunks"
    )
    chunk_target_lines_medium: int = Field(
        default=125, description="Target chunk size for medium files"
    )
    chunk_target_lines_large: int = Field(
        default=100, description="Target chunk size for large files"
    )
    chunk_overlap_lines: int = Field(default=20, description="Overlap between chunks")

    # Search
    search_top_k: int = Field(default=5, description="Default number of search results")
    search_response_format: str = Field(
        default="concise",
        description="Default search output verbosity: 'concise' (compact snippet) "
        "or 'detailed' (larger snippet). Concise saves tokens.",
    )
    search_snippet_lines_concise: int = Field(
        default=15, description="Max snippet lines per result in concise mode"
    )
    search_snippet_lines_detailed: int = Field(
        default=50, description="Max snippet lines per result in detailed mode"
    )

    # Parallel-duplicate handling (generic / project-agnostic).
    # When the same symbol is declared in 2+ top-level module roots (e.g. a
    # multi-app Android repo or multi-target iOS project), results are collapsed
    # to the single best copy and annotated. All settings default to neutral
    # (no project-specific names baked in).
    preferred_module_prefix: str = Field(
        default="",
        description="Optional module/path prefix to prefer when collapsing "
        "duplicate symbols across parallel module roots (empty = pure relevance)",
    )
    exclude_module_prefixes: str = Field(
        default="",
        description="Comma-separated path prefixes to exclude from indexing "
        "(e.g. a legacy/duplicated tree). Empty = index everything.",
    )
    include_module_prefixes: str = Field(
        default="",
        description="Comma-separated path-prefix allowlist for indexing. "
        "Empty = no allowlist (index everything not excluded).",
    )

    # Working-tree freshness
    reindex_dirty: bool = Field(
        default=True,
        description="Reindex uncommitted/working-tree changes (git status) on each "
        "tool call so search reflects unsaved-to-git edits",
    )

    # Exemplar override
    exemplar_module: str = Field(
        default="",
        description="Optional module path to pin as the architecture exemplar "
        "(empty = auto-select the most complete feature module)",
    )

    # Mandatory context
    mandatory_fan_in_threshold: float = Field(
        default=0.02,
        description="Minimum fan-in ratio to consider a file as base architecture",
    )
    mandatory_contract_min_ratio: float = Field(
        default=0.005,
        description="Relaxed fan-in ratio for architectural-contract roles "
        "(base ViewModels, marker interfaces, MVI state/effect/action, extensions)",
    )
    mandatory_max_base_files: int = Field(
        default=15, description="Maximum number of auto-detected base files"
    )
    mandatory_max_contract_files: int = Field(
        default=12,
        description="Additional budget for exemplar-dependency and supertype "
        "contracts merged on top of fan-in base files",
    )
    mandatory_supertype_depth: int = Field(
        default=2, description="How many inheritance levels to walk for supertype contracts"
    )
    mandatory_include_exemplar_deps: bool = Field(
        default=True,
        description="Include the exemplar module's dependency closure in mandatory context",
    )
    mandatory_max_lines_per_file: int = Field(
        default=100, description="Max lines per file in mandatory context"
    )
    mandatory_max_method_lines: int = Field(
        default=40, description="Max lines per method in anchored extraction"
    )
    architecture_include_exemplar_default: bool = Field(
        default=False,
        description="Whether get_architecture_context includes the full exemplar "
        "by default. False = base architecture only (fewer tokens); exemplar opt-in.",
    )

    # Reindexing
    reindex_on_tool_call: bool = Field(
        default=True, description="Auto-reindex when HEAD changes before tool calls"
    )

    model_config = {"env_prefix": "MCC_"}

    @property
    def exclude_module_prefixes_list(self) -> list[str]:
        """Parsed, normalized exclude prefixes (forward-slash separated)."""
        return [p.strip().strip("/") for p in self.exclude_module_prefixes.split(",") if p.strip()]

    @property
    def include_module_prefixes_list(self) -> list[str]:
        """Parsed, normalized include (allowlist) prefixes."""
        return [p.strip().strip("/") for p in self.include_module_prefixes.split(",") if p.strip()]

    @property
    def data_path(self) -> Path:
        """Absolute path to the data directory."""
        return self.repo_path / self.data_dir

    @property
    def lancedb_path(self) -> Path:
        """Path to LanceDB storage."""
        return self.data_path / "index.lance"

    @property
    def mandatory_path(self) -> Path:
        """Path to mandatory context JSON."""
        return self.data_path / "mandatory.json"

    @property
    def learnings_path(self) -> Path:
        """Path to learnings JSON."""
        return self.data_path / "learnings.json"

    @property
    def last_commit_path(self) -> Path:
        """Path to last indexed commit file."""
        return self.data_path / "last_indexed_commit"
