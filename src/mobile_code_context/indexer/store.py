"""
Vector store using LanceDB for code chunk storage and similarity search.
"""

from __future__ import annotations

import json
import structlog
from pathlib import Path
from typing import Any, Optional

import lancedb
import numpy as np
import pyarrow as pa

logger = structlog.get_logger()

CODE_CHUNKS_TABLE = "code_chunks"
FILE_REGISTRY_TABLE = "file_registry"


class VectorStore:
    """LanceDB-based vector store for code chunks."""

    def __init__(self, db_path: Path, embedding_dim: int = 768) -> None:
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self._db = None

    @property
    def db(self):
        """Lazy-open database connection."""
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self.db_path))
        return self._db

    def _ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        existing = self.db.table_names()

        if CODE_CHUNKS_TABLE not in existing:
            schema = pa.schema([
                pa.field("file_path", pa.string()),
                pa.field("content", pa.string()),
                pa.field("start_line", pa.int32()),
                pa.field("end_line", pa.int32()),
                pa.field("chunk_index", pa.int32()),
                pa.field("total_chunks", pa.int32()),
                pa.field("declarations", pa.string()),  # JSON list
                pa.field("package_name", pa.string()),
                pa.field("module", pa.string()),
                pa.field("chunk_type", pa.string()),
                pa.field("arch_role", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self.embedding_dim)),
            ])
            self.db.create_table(CODE_CHUNKS_TABLE, schema=schema)
            logger.info("created_table", table=CODE_CHUNKS_TABLE)

        if FILE_REGISTRY_TABLE not in existing:
            schema = pa.schema([
                pa.field("file_path", pa.string()),
                pa.field("content_hash", pa.string()),
                pa.field("line_count", pa.int32()),
                pa.field("module", pa.string()),
            ])
            self.db.create_table(FILE_REGISTRY_TABLE, schema=schema)
            logger.info("created_table", table=FILE_REGISTRY_TABLE)

    def insert_chunks(
        self,
        chunks: list[dict],
        embeddings: np.ndarray,
    ) -> int:
        """Insert code chunks with their embeddings.

        Args:
            chunks: List of chunk metadata dicts
            embeddings: numpy array of shape (len(chunks), embedding_dim)

        Returns:
            Number of chunks inserted
        """
        if not chunks:
            return 0

        self._ensure_tables()
        table = self.db.open_table(CODE_CHUNKS_TABLE)

        records = []
        for i, chunk in enumerate(chunks):
            records.append({
                "file_path": chunk["file_path"],
                "content": chunk["content"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "chunk_index": chunk["chunk_index"],
                "total_chunks": chunk["total_chunks"],
                "declarations": json.dumps(chunk["declarations"]),
                "package_name": chunk["package_name"],
                "module": chunk["module"],
                "chunk_type": chunk["chunk_type"],
                "arch_role": chunk["arch_role"],
                "vector": embeddings[i].tolist(),
            })

        table.add(records)
        return len(records)

    def remove_by_file(self, file_path: str) -> None:
        """Remove all chunks for a given file path."""
        self._ensure_tables()
        table = self.db.open_table(CODE_CHUNKS_TABLE)
        table.delete(f"file_path = '{file_path}'")

        reg_table = self.db.open_table(FILE_REGISTRY_TABLE)
        reg_table.delete(f"file_path = '{file_path}'")

    def register_file(self, file_path: str, content_hash: str, line_count: int, module: str) -> None:
        """Register a file in the file registry."""
        self._ensure_tables()
        table = self.db.open_table(FILE_REGISTRY_TABLE)
        table.add([{
            "file_path": file_path,
            "content_hash": content_hash,
            "line_count": line_count,
            "module": module,
        }])

    def register_files_batch(self, records: list[dict]) -> None:
        """Register multiple files at once."""
        if not records:
            return
        self._ensure_tables()
        table = self.db.open_table(FILE_REGISTRY_TABLE)
        table.add(records)

    def get_all_file_hashes(self) -> dict[str, str]:
        """Get all registered file paths and their content hashes.

        Returns:
            Dict of {file_path: content_hash}
        """
        self._ensure_tables()
        table = self.db.open_table(FILE_REGISTRY_TABLE)
        try:
            df = table.to_pandas()
            if df.empty:
                return {}
            return dict(zip(df["file_path"], df["content_hash"]))
        except Exception:
            return {}

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        module_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Search for similar code chunks.

        Args:
            query_embedding: Query vector
            top_k: Number of results
            module_filter: Optional module path filter

        Returns:
            List of result dicts with metadata + score
        """
        self._ensure_tables()
        table = self.db.open_table(CODE_CHUNKS_TABLE)

        query = table.search(query_embedding.tolist()).limit(top_k)

        if module_filter:
            query = query.where(f"module LIKE '%{module_filter}%'")

        try:
            results = query.to_pandas()
        except Exception:
            return []

        if results.empty:
            return []

        output: list[dict[str, Any]] = []
        for _, row in results.iterrows():
            output.append({
                "file_path": row["file_path"],
                "content": row["content"],
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "chunk_index": int(row["chunk_index"]),
                "total_chunks": int(row["total_chunks"]),
                "declarations": json.loads(row["declarations"]) if row["declarations"] else [],
                "package_name": row["package_name"],
                "module": row["module"],
                "arch_role": row["arch_role"],
                "score": float(row.get("_distance", 0.0)),
            })

        return output

    def get_file_count(self) -> int:
        """Get total number of registered files."""
        self._ensure_tables()
        table = self.db.open_table(FILE_REGISTRY_TABLE)
        try:
            return table.count_rows()
        except Exception:
            return 0

    def get_chunk_count(self) -> int:
        """Get total number of stored chunks."""
        self._ensure_tables()
        table = self.db.open_table(CODE_CHUNKS_TABLE)
        try:
            return table.count_rows()
        except Exception:
            return 0

    def get_modules(self) -> list[str]:
        """Get all unique module paths."""
        self._ensure_tables()
        table = self.db.open_table(FILE_REGISTRY_TABLE)
        try:
            df = table.to_pandas()
            if df.empty:
                return []
            return sorted(df["module"].unique().tolist())
        except Exception:
            return []

    def get_files_in_module(self, module: str) -> list[str]:
        """Get all files in a specific module."""
        self._ensure_tables()
        table = self.db.open_table(FILE_REGISTRY_TABLE)
        try:
            df = table.to_pandas()
            if df.empty:
                return []
            return df[df["module"] == module]["file_path"].tolist()
        except Exception:
            return []
