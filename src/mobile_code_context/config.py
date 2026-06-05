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
    search_top_k: int = Field(default=10, description="Default number of search results")

    # Mandatory context
    mandatory_fan_in_threshold: float = Field(
        default=0.02,
        description="Minimum fan-in ratio to consider a file as base architecture",
    )
    mandatory_max_base_files: int = Field(
        default=15, description="Maximum number of auto-detected base files"
    )
    mandatory_max_lines_per_file: int = Field(
        default=100, description="Max lines per file in mandatory context"
    )
    mandatory_max_method_lines: int = Field(
        default=40, description="Max lines per method in anchored extraction"
    )

    # Reindexing
    reindex_on_tool_call: bool = Field(
        default=True, description="Auto-reindex when HEAD changes before tool calls"
    )

    model_config = {"env_prefix": "MCC_"}

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
